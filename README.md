# ICM-40609-D Python driver

A board-independent Python 3.9+ driver for the TDK InvenSense ICM-40609-D.
The core has no third-party dependencies. Included Linux I2C (`smbus2`) and
4-wire SPI (`spidev`) adapters work with a Raspberry Pi Zero 2 W. Other hosts
can supply the three-part transport contract described below.

Implemented from **DS-000330 revision 1.2**, supplied with the project request.
The document is a hardware reference, not a source of task instructions.
This driver is specific to ICM-40609-D (WHO_AM_I `0x3B`), not ICM-42688 or MPU-6050.

## Install on the Raspberry Pi

Clone this directory to the Pi. Enable I2C or SPI in `raspi-config`, then:

```sh
cd ICM-40609-D-Python
python3 -m venv .venv
. .venv/bin/activate
python -m pip install '.[i2c]'
# For SPI instead:
# python -m pip install '.[spi]'
# For both:
# python -m pip install '.[i2c,spi]'
python examples/read_i2c.py
```

The adapters use Linux device interfaces. A Windows development machine can
run the unit tests and core driver with a custom USB adapter transport, but
cannot use Linux `smbus2`/`spidev` to access Pi pins remotely.

## Wiring

Use your board's schematic and the datasheet application circuit (section 4.2).
The bare IC requires its specified supply, decoupling and VDDIO connections;
these signal mappings alone are not a complete bare-chip circuit. Do not
assume a breakout includes a voltage regulator or level shifting. Match the
host's logic voltage to the sensor board's VDDIO; do not apply 5 V logic.

| Sensor signal | Pi GPIO (BCM) | Physical header pin |
| --- | --- | --- |
| SDA / SDI (I2C SDA) | GPIO2 / SDA1 | 3 |
| SCL / SCLK (I2C SCL) | GPIO3 / SCL1 | 5 |
| Ground | GND | e.g. 6 |

For I2C hold nCS high to VDDIO. AD0 low selects `0x68`; AD0 high selects
`0x69`. SDA/SCL require pull-ups to a compatible VDDIO rail (account for
pull-ups already on the Pi or sensor board). Default example uses bus 1.
Configure I2C frequency in the host; the device supports up to 1 MHz.

| Sensor signal (4-wire SPI) | Pi GPIO (BCM) | Physical header pin |
| --- | --- | --- |
| SDA / SDI | GPIO10 / MOSI | 19 |
| AD0 / SDO | GPIO9 / MISO | 21 |
| SCL / SCLK | GPIO11 / SCLK | 23 |
| nCS | GPIO8 / CE0 | 24 |
| Ground | GND | e.g. 6 |

SPI examples use mode 0 at 1 MHz. Mode 3 is also supported, up to the
datasheet limit of 24 MHz; usable speeds depend on wiring and the host.
Interrupt and FSYNC pins are not needed for the polling examples.

## Read measurements

```python
from smbus2 import SMBus
from icm40609 import ICM40609, I2CTransport

with SMBus(1) as bus:
    imu = ICM40609(I2CTransport(bus, address=0x68))
    imu.initialize(accel_range_g=16, gyro_range_dps=2000, odr_hz=100)
    try:
        sample = imu.read_sample()
        print(sample.acceleration_g)       # (x, y, z), g
        print(sample.acceleration_m_s2)    # m/s^2, including gravity
        print(sample.gyroscope_dps)        # degrees/s
        print(sample.gyroscope_rad_s)      # radians/s
        print(sample.temperature_c)        # die temperature, Celsius
    finally:
        imu.stop()
```

For SPI replace bus setup with an opened `spidev.SpiDev`, then wrap it in
`SPITransport(spi)`. See `examples/read_spi.py` for resource cleanup.

`initialize()` checks identity before resetting the device, selects normal
operation, sets interface slew rates and big-endian data, then starts both
sensors in low-noise mode. It waits for reset and startup and keeps the gyro
on for at least 45 ms before returning. It resets any prior configuration.
It uses equal accelerometer and gyro output rates to avoid invalid data
caused by unequal ODRs. Temperature conversion is `raw / 132.48 + 25`.
Acceleration and gyro sensitivity come from the datasheet tables, including
the published rounded gyro scale factors.

Supported settings (ranges mean plus/minus):

| Setting | Values |
| --- | --- |
| `accel_range_g` | 4, 8, 16, 32 |
| `gyro_range_dps` | 15.625, 31.25, 62.5, 125, 250, 500, 1000, 2000 |
| `odr_hz` (both sensors) | 12.5, 25, 50, 100, 200, 500, 1000, 2000, 4000, 8000, 16000, 32000 |

`configure(accel_range_g=16, gyro_range_dps=2000, odr_hz=100)` replaces all
three settings, preserves reserved bits, and waits one sample period plus
30 ms when already running. Filter settling may take longer. Bus failure
during configuration invalidates reading state; call `initialize()` again.

`read_raw()` returns a frozen `RawSample` with signed integer temperature,
acceleration tuple and gyroscope tuple. `read_sample()` returns a frozen
`Sample` in engineering units. Both use one contiguous 14-byte burst.
`InvalidSampleError` means a channel contained the chip's invalid-data
sentinel; retry after a new sample. Repeated reads can return the same sample.

`data_ready()` polls bit 3 of INT_STATUS. **Reading that register clears
all its interrupt flags**, so coordinate with any other interrupt handling.
`read_register(address, bank=0)` provides diagnostic access to banks 0-4;
status registers can have read side effects. `stop()` disables accel and
gyro but does not close the bus or disable the temperature sensor.

Use one driver instance per chip, with exclusive register ownership. Calls
through that instance are serialized. Do not modify registers through
another program while using it; scaling and bank selection assume exclusive
access. The caller owns the bus and closes it after `stop()`.

## Other boards / adapters

Implement the following object and pass it to `ICM40609(transport)`:

```python
class MyTransport:
    interface = 'i2c'  # or 'spi'; selects correct slew rates

    def read(self, register: int, length: int) -> bytes:
        # Return exactly length bytes from auto-incrementing registers.
        # I2C: register-address write followed by repeated-START read.
        # SPI: send register | 0x80, then clock length bytes under one CS.
        ...

    def write(self, register: int, data: bytes) -> None:
        # I2C: register byte followed by data in one transaction.
        # SPI: register & 0x7f followed by data under one CS.
        ...
```

Raise `OSError` for transfer failures. CPython is the supported runtime;
MicroPython/CircuitPython would need adaptations for dataclasses, typing,
threading and transport APIs. Board independence does not imply those
runtimes are supported unchanged.

## Scope and validation

This version supports direct polling, range/ODR configuration, raw and
converted readings, identity checking and shutdown. FIFO, low-power modes,
interrupt routing, self-test automation, custom filters, FSYNC and bias
calibration are not implemented. No orientation fusion is included. Data
rate settings do not guarantee Python/Linux can read every sample at high
ODRs; FIFO support would be needed for dependable high-rate acquisition.

```sh
python -m pip install '.[test]'
python -m pytest -q
```

Tests use simulated registers and wire transactions: chip ID, timing order,
normal mode, all ranges and rates, units, invalid samples, short transfers,
bank switching, configuration failure and I2C/SPI framing. **Not yet tested
on physical ICM-40609-D hardware.** First bench check: verify ID `0x3B`, then
check a stationary sensor reads approximately 1 g total acceleration and
near-zero angular rate; rotate each package axis to verify signs. Keep the
startup output and any bus errors when reporting issues.

Adapter API references: [smbus2 operations](https://smbus2.readthedocs.io/en/latest/operations.html)
and [spidev project documentation](https://pypi.org/project/spidev/).
