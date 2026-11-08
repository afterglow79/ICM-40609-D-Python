# ICM-40609-D Python driver

A Python 3.9+ driver for reading acceleration, angular velocity, and
sensor temperature from the TDK InvenSense ICM-40609-D over I2C or SPI.
The Python API supports calibration and configurable INT1/INT2 GPIO inputs.
Terminal commands are available for quick checks.

The core driver has no third-party dependencies. The included Linux adapters
use `smbus2` for I2C, `spidev` for SPI, and `gpiozero` for GPIO interrupts.
Other hosts can supply a custom transport.

## Installation

On a Raspberry Pi, enable I2C or SPI in `raspi-config`. From the library
checkout, create an environment and install the required adapters:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[i2c,gpio]'
python examples/read_i2c.py
```

Use `'.[spi]'` for SPI alone or `'.[i2c,spi,gpio]'` for all adapters.
The editable install (`-e`) picks up changes to Python source files when
the application restarts.

### Use with uv

From an application project, add the library checkout as an editable
dependency. For a new project without a `pyproject.toml`, run `uv init` first.
These examples assume the library is in a sibling folder named `ICM-40609-D`.
Adjust the path to match the checkout location.

On Windows:

```powershell
uv add --editable '..\ICM-40609-D'
uv run python main.py
```

On a Raspberry Pi, include the hardware adapters:

```sh
uv add --editable ../ICM-40609-D --extra i2c --extra gpio
uv run python main.py
```

For SPI, add `--extra spi`. Replace `main.py` with the application script.
Select the application's `.venv` interpreter in the editor or IDE.

Run `uv add` in each project that needs the library. uv records the local
source in `pyproject.toml` and manages a separate environment for each
project. `uv tool install` creates a tool environment for terminal commands;
it does not add an importable dependency to application projects.

A shared directory layout allows relative dependency paths to work across
machines. Each machine still needs its own copy of the library source.
See [uv's dependency guide](https://docs.astral.sh/uv/concepts/dependencies/).

### Use with pip across projects

An installed package can be imported from any folder when using the Python
environment containing it. Each virtual environment has its own packages.
Existing project environments need their own installation; alternatively,
several projects can use one shared environment.

On Linux, replace `/path/to/ICM-40609-D` with the checkout location:

```sh
python3 -m venv ~/.venvs/sensors
~/.venvs/sensors/bin/python -m pip install -e '/path/to/ICM-40609-D[i2c,gpio]'
. ~/.venvs/sensors/bin/activate
python /path/to/project/main.py
```

On Windows, from PowerShell:

```powershell
py -m venv "$env:USERPROFILE\.venvs\sensors"
& "$env:USERPROFILE\.venvs\sensors\Scripts\python.exe" -m pip install -e 'C:\path\to\ICM-40609-D[test]'
& "$env:USERPROFILE\.venvs\sensors\Scripts\python.exe" 'C:\path\to\project\main.py'
```

Select that environment's interpreter in the editor. Windows supports
library development and simulated tests. Access to Raspberry Pi I2C, SPI,
and GPIO pins requires running hardware-facing code on the Pi, for example
through an editor's SSH connection. The Linux adapters do not provide
remote pin access from Windows. Local hardware access through a compatible
USB adapter requires a custom transport.

Editable installs reference the local checkout. Source changes must be
copied or pulled onto other machines before those installations see them.

## I2C addresses: 0x68 and 0x69

AD0 selects the sensor's 7-bit I2C address:

| AD0 connection | I2C address |
| --- | --- |
| Ground / logic low | `0x68` |
| VDDIO / logic high | `0x69` |

For a single ICM-40609-D, automatic address selection is available:

```python
transport = I2CTransport(bus, address=None)
imu = ICM40609(transport)
imu.initialize()
print(f'Connected at 0x{transport.address:02X}')
```

**Passing `address=None` enables the library's internal address handling.**
It checks both addresses for the expected `WHO_AM_I` value, `0x3B`, and
selects a matching sensor. During normal bank-0 transfers it checks the
selected address again. If communication fails, it checks both addresses
and retries, up to three attempts with 10 ms between attempts. This handles
address changes during startup, after reset, or between measurements.
No application-level address-switching loop is needed in this mode.

**Omitting `address` uses a fixed `0x68`; it does not enable auto mode.**
A known, stable address can also be supplied explicitly:

```python
transport = I2CTransport(bus, address=0x68)
# Or: I2CTransport(bus, address=0x69)
```

Use fixed addresses for two ICM-40609-Ds on the same bus. Auto discovery
raises an error if both addresses report `0x3B`. Identity probes read
register `0x75`, so check for read side effects if another device occupies
either address. Address recovery during diagnostic access in another
register bank requires reinitialization.

AD0 should have a defined logic level. Leaving it unconnected can make
address selection unstable. Auto mode is a software workaround for address
changes, not a substitute for a stable electrical connection. It cannot
resolve address collisions or guarantee a transfer if AD0 changes level
mid-transaction. If neither address responds, check power, wiring, bus
selection, pull-ups, and nCS.

## Read measurements

```python
from smbus2 import SMBus
from icm40609 import ICM40609, I2CTransport

with SMBus(1) as bus:
    imu = ICM40609(I2CTransport(bus, address=None))
    imu.initialize(accel_range_g=16, gyro_range_dps=2000, odr_hz=100)
    try:
        sample = imu.read_sample()
        print(sample.acceleration_g)     # (x, y, z), including gravity
        print(sample.acceleration_m_s2)  # m/s^2
        print(sample.gyroscope_dps)      # degrees per second
        print(sample.gyroscope_rad_s)    # radians per second
        print(sample.temperature_c)      # sensor die temperature, Celsius
    finally:
        imu.stop()
```

`initialize()` checks identity, resets the device, and starts both sensors
in low-noise mode at the same output rate. It waits for startup before
returning. Calling it again replaces the chip's configuration, including
interrupt routing.

For repeated readings, wait for `imu.data_ready()` or a GPIO interrupt.
Repeated reads without fresh data can return the same sample.
`InvalidSampleError` means the chip marked a channel as invalid; skip that
reading and wait for the next sample.

`read_raw()` returns signed sensor counts in a `RawSample` with temperature,
acceleration, and gyroscope fields. `read_sample()` returns a `Sample` in
engineering units and applies any loaded calibration. Both use one
contiguous 14-byte burst. Calibration leaves raw counts unchanged.

For SPI, wrap an open `spidev.SpiDev` in `SPITransport(spi)`. The
[SPI example](examples/read_spi.py) includes setup and cleanup.

## Configure INT1 and INT2

Configure GPIO interrupts after `initialize()`. Either sensor output can
connect to a host GPIO supported by the GPIO adapter. Pi GPIO numbers use
BCM numbering, not physical header positions.

The example below assumes INT1 is wired to BCM GPIO17 and INT2 is unused.
The numbers are examples and must match the actual wiring:

```python
# imu has already been initialized.
with imu.interrupt(int1_gpio=17, int2_gpio=None) as irq:
    if irq.wait(timeout=2) and imu.data_ready():
        sample = imu.read_sample()
```

Either pin or both can be mapped. For example, `int1_gpio=None, int2_gpio=22`
uses only INT2 on GPIO22. `irq.wait()` returns `True` when either mapped input
becomes active, or `False` when its timeout expires. Read `imu.data_ready()`
to acknowledge the latched interrupt before taking the sample.

The handler uses push-pull, latched, active-high outputs by default. Pass
`active_high=False` for active-low outputs. Both pins report data-ready;
separate event types are not routed to each pin. GPIO callbacks wake the
waiting thread, which performs sensor reads.

A `with` block releases the host GPIOs on exit. Without a context manager,
call `irq.close()` explicitly. Setup briefly stops and restarts the sensors
because the interrupt registers must be changed while they are off. Close
the handler before reinitializing, then configure interrupts again afterward.

For a different host GPIO library, use
`imu.configure_data_ready_interrupt(1, ...)` or `(2, ...)` to configure the
sensor output and handle host input separately. The supplied handler also
accepts a gpiozero `pin_factory`.

## Calibration

Keep the board still while measuring gyro zero-rate offsets:

```python
# imu has already been initialized.
calibration = imu.calibrate(samples=500, settle=2, output='calibration.json')
```

This waits two seconds, collects 500 fresh readings, and saves the offsets.
At 100 Hz, capture takes about five seconds. The result applies to subsequent
`read_sample()` calls. Omit `output` to keep it in memory without saving.
The method returns a `Calibration` object.

To collect through a configured interrupt handler:

```python
with imu.interrupt(int1_gpio=17) as irq:
    calibration = imu.calibrate(output='calibration.json', interrupts=irq)
```

On later runs, load the file when creating the driver:

```python
from icm40609 import Calibration

calibration = Calibration.load('calibration.json')
imu = ICM40609(transport, calibration=calibration)
imu.initialize()
```

For an existing driver, assign `imu.calibration = calibration`. Assign `None`
to disable corrections. An in-memory result can be saved later with
`calibration.save('calibration.json')`. Recalibration measures uncorrected
readings so offsets do not stack. If capture or saving fails, the previous
calibration and output file stay intact.

The [interrupt and calibration example](examples/interrupt_calibration.py)
loads `calibration.json` if present and captures gyro offsets on its first
run. Set its GPIO constants to match the board before running it.

### Accelerometer offsets

Gyro calibration works in any stationary orientation. Accelerometer offset
calibration needs a known pose to account for gravity:

```python
# Position the sensor so Z reads +1 g and X/Y read 0 g.
imu.calibrate(gravity_axis='+z', output='calibration.json')
```

Supported directions are `+x`, `-x`, `+y`, `-y`, `+z`, and `-z`, using the
sensor package axes. For `-z`, the expected Z reading is -1 g. Calibration
preserves gravity in measurements.

This single-pose method estimates offsets. It does not correct scale,
alignment, or temperature drift. Calibrate near the operating temperature
and keep the saved file with the physical sensor it belongs to. See
[Analog Devices AN-1057](https://www.analog.com/en/resources/app-notes/an-1057.html)
for background on gravity-based calibration.

Calibration requires at least 100 readings. It rejects obvious motion using
per-axis standard deviation limits of 0.02 g and 0.5 deg/s, mean gyro magnitude
no greater than 5 deg/s, and mean acceleration between 0.85 and 1.15 g.
Slow constant rotation can still look like gyro bias, so the board must
remain still throughout capture.

## Ranges and output rates

| Argument | Supported values |
| --- | --- |
| `accel_range_g` | 4, 8, 16, 32 |
| `gyro_range_dps` | 15.625, 31.25, 62.5, 125, 250, 500, 1000, 2000 |
| `odr_hz` | 12.5, 25, 50, 100, 200, 500, 1000, 2000, 4000, 8000, 16000, 32000 |

Ranges mean plus/minus. Defaults are ±16 g, ±2000 deg/s, and 100 Hz.
To change them after initialization:

```python
imu.configure(accel_range_g=8, gyro_range_dps=500, odr_hz=200)
```

Each call sets all three values; omitted arguments use their defaults.
When running, the driver waits one sample period plus 30 ms after the change.
Filters may need longer to settle. If a bus error interrupts configuration,
call `initialize()` before reading again.

A supported output rate does not guarantee that Python can capture every
sample at that rate. This version reads registers directly and has no FIFO
support.

## Wiring

Follow the board schematic and the datasheet's application circuit for
supply, decoupling, and pin connections. These tables cover bus signals,
not a complete bare-chip circuit. Match VDDIO and host logic levels;
do not apply 5 V logic to the sensor.

| I2C signal | Pi GPIO (BCM) | Physical header pin |
| --- | --- | --- |
| SDA | GPIO2 / SDA1 | 3 |
| SCL | GPIO3 / SCL1 | 5 |
| Ground | GND | e.g. 6 |

For I2C, hold nCS high to VDDIO. SDA and SCL need pull-ups to a compatible
VDDIO rail; account for pull-ups already on the connected boards. Examples
use bus 1. Set bus speed on the host; the chip supports up to 1 MHz.
See [I2C address selection](#i2c-addresses-0x68-and-0x69) for AD0 connections
and automatic address handling.

| 4-wire SPI signal | Pi GPIO (BCM) | Physical header pin |
| --- | --- | --- |
| SDI | GPIO10 / MOSI | 19 |
| SDO | GPIO9 / MISO | 21 |
| SCLK | GPIO11 / SCLK | 23 |
| nCS | GPIO8 / CE0 | 24 |
| Ground | GND | e.g. 6 |

The SPI adapter defaults to mode 0 at 1 MHz and also supports mode 3. Its
upper limit is 24 MHz; usable speeds depend on the wiring and host.
Interrupt and FSYNC connections are optional for polling examples.

## Terminal commands

```sh
python -m icm40609 calibrate --output calibration.json
python -m icm40609 read --calibration calibration.json
python -m icm40609 read --count 100
```

Both commands default to automatic I2C address selection. Use
`--address 0x68` or `--address 0x69` for a fixed address. Add `--int1-gpio 17`
or `--int2-gpio 22` when those example pins match the wiring; omit GPIO
arguments to poll data-ready. Both commands accept `--interface spi`,
`--bus`, `--cs`, and `--active-low`. For accelerometer calibration in a
known pose, add `--gravity-axis=+z`.

Run `python -m icm40609 calibrate --help` or `read --help` for the options.
The installed `icm40609` command is equivalent to `python -m icm40609`.

## Troubleshooting and ownership

| Symptom | What to check |
| --- | --- |
| `ModuleNotFoundError: icm40609` | Install into the application's Python environment. Check `python -m pip show icm40609`. |
| I2C input/output error | Check power, ground, bus number, nCS, pull-ups, and AD0. Enable `address=None` for automatic address handling. |
| GPIO interrupt timeout | Check BCM versus header numbering, the INT1/INT2 mapping, wiring, and polarity. |
| Calibration rejects movement | Hold the board still and check orientation for accelerometer calibration. |
| `InvalidSampleError` | Wait for the next data-ready event and retry the reading. |

Use one driver instance per chip and let it manage the registers. Calls
through that instance use a lock, but other processes or drivers can still
change the chip's configuration.

`data_ready()` reads INT_STATUS and clears all its interrupt flags.
Coordinate that read with other interrupt handling. `read_register(address,
bank=0)` provides diagnostic access to banks 0-4; some registers have read
side effects. `stop()` turns off the accelerometer and gyro. The application
owns and closes the bus; the temperature sensor remains enabled.

## Other boards and testing

A custom transport needs this interface:

```python
class MyTransport:
    interface = 'i2c'  # Or 'spi'; this selects the sensor's slew rates.

    def read(self, register: int, length: int) -> bytes:
        # Read exactly length bytes, starting at register.
        ...

    def write(self, register: int, data: bytes) -> None:
        # Write data starting at register.
        ...
```

Pass it to `ICM40609(transport)`. Preserve register auto-increment behavior
and raise `OSError` for bus errors. I2C reads need a register-pointer write
followed by a repeated START and read. For SPI, send `register | 0x80` to
read or `register & 0x7F` to write, with address and data under one chip-select
assertion.

The supported runtime is CPython. MicroPython and CircuitPython need changes
for dataclasses, typing, threading, and bus APIs.

To run simulated tests from the checkout:

```sh
python -m pip install -e '.[test]'
python -m pytest -q
```

Tests cover register configuration, ranges, rates, conversions, address
recovery, calibration, interrupt routing, and GPIO cleanup. Physical testing
has covered I2C readings, address recovery, gyro calibration, and INT1 on a
Raspberry Pi. INT2 and SPI have simulated coverage only. FIFO, low-power
modes, FIFO interrupt routing, automated self-test, custom filters, FSYNC,
and orientation fusion are not implemented.

The register map and conversions follow **TDK DS-000330, revision 1.2**.
The expected ICM-40609-D chip ID is `0x3B`.

References: [TDK datasheet](https://invensense.tdk.com/wp-content/uploads/2022/07/DS-000330-ICM-40609-D-v1.2.pdf),
[smbus2](https://smbus2.readthedocs.io/en/latest/operations.html),
[spidev](https://pypi.org/project/spidev/), and
[gpiozero inputs](https://gpiozero.readthedocs.io/en/stable/api_input.html).
