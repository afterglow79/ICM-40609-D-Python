# ICM-40609-D Python driver

A board-independent Python 3.9+ driver for the TDK InvenSense ICM-40609-D.
The core has no third-party dependencies. Included Linux I2C (`smbus2`) and
4-wire SPI (`spidev`) adapters work with a Raspberry Pi Zero 2 W. Other hosts
can supply the three-part transport contract described below.

Implemented from **DS-000330 revision 1.2**, supplied with the project request.
The document is a hardware reference, not a source of task instructions.
This driver is specific to ICM-40609-D (WHO_AM_I `0x3B`), not ICM-42688 or MPU-6050.

## Install on the Raspberry Pi

Copy this directory to the Pi. Enable I2C or SPI in `raspi-config`, then:

```sh
cd ICM-40609-D
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

The I2C example uses `I2CTransport(bus, address=None)` to check both addresses.
Auto mode verifies WHO_AM_I `0x3B` before selecting an address and checks the
cached address before each bank-0 transfer. If an operation fails, it checks
both addresses again and retries, with a maximum of three attempts and 10 ms
between attempts. This covers address changes during startup, reset and
normal readings. `transport.address` reports the most recently selected address.
Passing `address=0x68` or `address=0x69` keeps the original fixed-address behavior.

Auto mode assumes one ICM-40609-D on the two addresses. If both respond with
ID `0x3B` during discovery, it asks you to choose a fixed address. Devices
with other IDs are not selected for register writes. Identity probes read
register `0x75`, so account for that if another device at either address has
read side effects. Bank-0 operation is required for discovery; an address
failure during diagnostics in another bank requires reinitialization.

AD0 should have a defined logic level. On the custom PCB tested here, a
floating AD0 caused the address to change after reset. Auto mode is a
workaround for that board; it cannot resolve address collisions or guarantee
communication if the pin changes level during a transaction.

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

## Calibration and GPIO interrupts in Python

Install the optional GPIO adapter on the Pi with `python -m pip install '.[i2c,gpio]'`.
You can configure everything in Python; command-line arguments are optional.
For your board, INT1 connects to BCM GPIO27 (physical header pin 13), and INT2
is unwired. Both mappings accept any input GPIO supported by your host and
gpiozero pin factory. Leave an unused interrupt as `None`.

```python
from smbus2 import SMBus
from icm40609 import ICM40609, I2CTransport, Calibration

with SMBus(1) as bus:
    imu = ICM40609(I2CTransport(bus, address=None))
    imu.initialize(odr_hz=100)
    try:
        with imu.interrupt(int1_gpio=27, int2_gpio=None) as irq:
            # Keep the board still. This applies the result and saves it.
            calibration = imu.calibrate(
                samples=500, settle=2, output='calibration.json', interrupts=irq
            )
            if irq.wait(timeout=2) and imu.data_ready():
                print(imu.read_sample())
    finally:
        imu.stop()
```

On later runs, load the file instead of recalibrating:

```python
calibration = Calibration.load('calibration.json')
imu = ICM40609(transport, calibration=calibration)
imu.initialize()
```

You can also set `imu.calibration = calibration` on an existing driver, or
`imu.calibration = None` to turn corrections off. `imu.calibrate()` works
without interrupts too: omit `interrupts=irq` to poll data-ready instead.
It returns a `Calibration`; omit `output` for an in-memory result, then call
`calibration.save(path)` when you want to persist it. Failed calibration
leaves the previous offsets and output file intact.

By default, calibration measures only gyro zero-rate bias, in any stationary
orientation. For accelerometer offsets as well, supply `gravity_axis='+z'`
(or `'-z'`, `'+x'`, `'-x'`, `'+y'`, `'-y'`). Position the sensor so the chosen
axis has the specified +1 g or -1 g reading, with the other two axes at 0 g.
Use the sensor package axes, not the Pi axes. The correction preserves gravity.
This single-pose method estimates offsets, not scale, alignment or temperature
compensation; repeat calibration at the operating temperature when needed.
See [Analog Devices AN-1057](https://www.analog.com/en/resources/app-notes/an-1057.html)
for the gravity reference used by single-point accelerometer calibration.

Calibration needs at least 100 fresh readings (default 500, approximately five
seconds at 100 Hz), after a default two-second settling wait. It rejects
obvious motion using per-axis standard deviation limits of 0.02 g and
0.5 deg/s, mean gyro magnitude over 5 deg/s, and mean acceleration outside
0.85-1.15 g. These checks cannot prove the board is stationary: slow constant
rotation can look like gyro bias. Keep it still throughout capture.

`imu.interrupt(...)` returns a `GPIOInterrupts` handler. Use it in a `with`
block, or call `irq.close()` yourself. `irq.wait(timeout)` waits for either
mapped pin; check `imu.data_ready()` before reading to acknowledge the
latched interrupt. GPIO callbacks only wake the waiting thread. Both pins
report data-ready; this version does not route separate event types to each.
The driver configures push-pull, latched, active-high outputs by default;
pass `active_high=False` for active-low outputs. Match the sensor's output
logic voltage to the host GPIO input requirements.

Interrupt setup briefly stops and restarts the sensors because the datasheet
requires those register changes while they are off. Configure it after
`initialize()`. Resetting the sensor clears its interrupt routing, so close
the handler before reinitializing and configure it again afterward. Advanced
users can call `imu.configure_data_ready_interrupt(1, ...)` or `(2, ...)`
and use a different host GPIO library instead. GPIO support is optional and
does not affect the board-independent register driver.

`examples/interrupt_calibration.py` shows a full program with editable GPIO
constants. It loads `calibration.json` if present, or captures gyro calibration
on its first run. Calibration JSON is specific to the physical sensor; move
it only with that sensor, not to another board.

## Calibration from the terminal

```sh
python -m icm40609 calibrate --int1-gpio 27 --output calibration.json
python -m icm40609 read --int1-gpio 27 --calibration calibration.json
# Optional accelerometer calibration in a known pose:
python -m icm40609 calibrate --int1-gpio 27 --gravity-axis=+z
```

The same commands are available as `icm40609` after installation. Both support
`--interface spi`, `--bus`, `--cs`, `--int2-gpio`, and `--active-low`. Without
GPIO arguments they poll I2C/SPI for data-ready. `read --count 100` stops after
100 readings. A missing interrupt times out with a wiring/mapping error.

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
FIFO interrupt routing, self-test automation, custom filters and FSYNC are
not implemented. No orientation fusion is included. Data
rate settings do not guarantee Python/Linux can read every sample at high
ODRs; FIFO support would be needed for dependable high-rate acquisition.

```sh
python -m pip install '.[test]'
python -m pytest -q
```

Tests use simulated registers and wire transactions: chip ID, timing order,
normal mode, all ranges and rates, units, invalid samples, short transfers,
bank switching, configuration failure, I2C/SPI framing and automatic address
recovery. I2C initialization and live measurements have been checked on a
physical ICM-40609-D connected to a Raspberry Pi. SPI still needs hardware
testing. First bench check: verify ID `0x3B`, then
check a stationary sensor reads approximately 1 g total acceleration and
near-zero angular rate; rotate each package axis to verify signs. Keep the
startup output and any bus errors when reporting issues.

Adapter API references: [smbus2 operations](https://smbus2.readthedocs.io/en/latest/operations.html)
and [spidev project documentation](https://pypi.org/project/spidev/).
