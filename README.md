# ICM-40609-D Python driver

Read acceleration, angular velocity, and temperature from an ICM-40609-D over
I2C or SPI. You can configure the sensor, calibrate it, and wait for GPIO
interrupts from Python. There are terminal commands for quick checks, too.

The library supports Python 3.9+ and has no required third-party dependencies
in its core. The supplied hardware adapters use `smbus2`, `spidev`, and
`gpiozero` on Linux. It has been tested over I2C on a Raspberry Pi Zero 2 W,
including INT1 on GPIO27 and gyro calibration. SPI and INT2 still need
physical hardware testing.

## Get started on the Pi

Enable I2C or SPI in `raspi-config`, then open a terminal in this repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[i2c,gpio]'
python examples/read_i2c.py
```

This installs I2C and GPIO support. Use `'.[spi]'` for SPI alone or
`'.[i2c,spi,gpio]'` for all three adapters. The `-e` makes this an editable
install: changes to the library's Python files take effect the next time
you start your program.

The polling example checks both I2C addresses. For a complete program using
INT1 on GPIO27 and a saved calibration file, run:

```sh
python examples/interrupt_calibration.py
```

Keep the board still on its first run. The example measures gyro offsets
and saves `calibration.json` in your current directory. Later runs load that
file. Edit the GPIO constants at the top of the example to match your board.

## Use it from other projects

Once you install the package, you can `import icm40609` from any folder.
You do not need to copy the library into each project or change `sys.path`.
The important part is using the Python environment where you installed it.
Each virtual environment has its own packages.

### With uv

From the project that will use the sensor library, add your checkout as an
editable dependency. If it is a new project without a `pyproject.toml`, run
`uv init` there first.

On the Windows PC:

```powershell
uv add --editable 'D:\sensor libs\ICM-40609-D'
uv run python main.py
```

On the Pi, include the hardware adapters you need:

```sh
uv add --editable /home/flyhigh/sensors/ICM-40609-D-Python --extra i2c --extra gpio
uv run python main.py
```

For SPI, add `--extra spi`. Use your own script name in place of `main.py`.
Select the consuming project's `.venv` interpreter in your editor. uv records
the dependency and its local source in `pyproject.toml`, so later `uv sync`
and `uv run` calls keep it available. Edits to the library's Python files take
effect when you restart your program.

Run `uv add` in each project that needs the library. uv keeps project
environments separate; one installation does not make imports available in
all of them. `uv tool install` is for running commands in a separate tool
environment, not for adding an importable dependency to your projects.

The absolute checkout paths above differ between Windows and the Pi. If you
share a project's configuration between machines, keep the library in the
same relative location and use a relative source, such as
`uv add --editable ../ICM-40609-D-Python`. Copy or pull library changes onto
the Pi before running the hardware code there.

See [uv's dependency guide](https://docs.astral.sh/uv/concepts/dependencies/)
for editable local dependencies.

### On the Pi

For several sensor projects, you can keep one shared environment:

```sh
python3 -m venv ~/.venvs/sensors
~/.venvs/sensors/bin/python -m pip install -e '/home/flyhigh/sensors/ICM-40609-D-Python[i2c,gpio]'
. ~/.venvs/sensors/bin/activate
```

After activating it, go to any project folder and run `python your_script.py`.
You can also call the interpreter directly without activating it:

```sh
~/.venvs/sensors/bin/python /path/to/your_project/main.py
```

If a project already has its own environment, activate that environment and
run the same editable-install command with its `python -m pip`. This keeps
that project's dependencies separate. A shared environment avoids repeated
installs, but its projects share dependency versions.

Use a virtual environment on Raspberry Pi OS rather than forcing pip to
modify the OS-managed Python installation.

### On the Windows PC

In PowerShell, create an environment for local development and install the
PC checkout into it:

```powershell
py -m venv "$env:USERPROFILE\.venvs\sensors"
& "$env:USERPROFILE\.venvs\sensors\Scripts\python.exe" -m pip install -e 'D:\sensor libs\ICM-40609-D[test]'
```

Point your editor or IDE at this interpreter:

```text
C:\Users\cakeb\.venvs\sensors\Scripts\python.exe
```

You can now import the library in other projects using that interpreter.
To run a script from PowerShell:

```powershell
& "$env:USERPROFILE\.venvs\sensors\Scripts\python.exe" 'D:\your project\main.py'
```

You can write code and run the simulated tests on Windows. To access the
sensor through the Pi's I2C, SPI, or GPIO pins, run the hardware-facing code
on the Pi, for example through your editor's SSH connection. The Linux
adapters do not give a Windows process remote access to those pins. A
compatible USB adapter with a custom transport is another option for local
hardware access.

The PC and Pi have separate copies of the library. An editable install uses
the checkout on that machine; transfer or pull your changes to the other
machine when you want both copies to match. Existing project environments
do not inherit packages from the shared environment.

## Read measurements in Python

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

`initialize()` checks the chip ID, resets the device, and starts both sensors
in low-noise mode. It waits for startup before returning. Both sensors use
the same output rate. Calling it again replaces the chip's configuration,
including interrupt routing.

For repeated readings, wait for `imu.data_ready()` or a GPIO interrupt.
Calling `read_sample()` again before new data arrives can return the same
sample. If the chip marks a reading as invalid, the library raises
`InvalidSampleError`; skip that reading and wait for the next one.

`read_raw()` returns signed sensor counts in a `RawSample` with `temperature`,
`acceleration`, and `gyroscope` fields. Calibration does not alter raw counts.
`read_sample()` returns a `Sample` in the units shown above, with any loaded
calibration applied.

For SPI, wrap an open `spidev.SpiDev` in `SPITransport(spi)` instead. See
[the SPI example](examples/read_spi.py) for bus setup and cleanup.

## Set up interrupts

Configure interrupts after `initialize()`. For the PCB tested here, INT1
connects to **BCM GPIO27**, which is physical header pin 13. INT2 is unwired.
Change either mapping to an input GPIO supported by your host:

```python
# imu has already been initialized.
with imu.interrupt(int1_gpio=27, int2_gpio=None) as irq:
    if irq.wait(timeout=2) and imu.data_ready():
        sample = imu.read_sample()
```

You can map either pin or both. For example, `int1_gpio=None, int2_gpio=22`
uses only INT2 on BCM GPIO22. `irq.wait()` returns `True` when a mapped input
becomes active, or `False` when its timeout expires. Read `imu.data_ready()`
to acknowledge the latched interrupt before taking the sample.

The handler configures push-pull, latched, active-high outputs. Pass
`active_high=False` to `imu.interrupt()` for active-low outputs. Both pins
report data-ready; they do not select different event types. GPIO callbacks
wake the waiting thread, which handles the sensor read.

Use a `with` block to release the host GPIOs when finished, or call
`irq.close()` yourself. Interrupt setup briefly stops and restarts the
sensors because the datasheet requires those register changes while they
are off. Close the handler before reinitializing the chip, then configure
interrupts again afterward.

For another GPIO library, use `imu.configure_data_ready_interrupt(1, ...)`
or `(2, ...)` to configure the sensor output and handle the host input
yourself. The supplied handler also accepts a gpiozero `pin_factory`.

## Calibrate and save offsets

Keep the board still and call:

```python
# imu has already been initialized.
calibration = imu.calibrate(samples=500, settle=2, output='calibration.json')
```

This waits two seconds, collects 500 fresh readings, and measures gyro
zero-rate offsets. At 100 Hz, capture takes about five seconds. The method
applies the result to subsequent `read_sample()` calls and saves it to JSON.
It returns a `Calibration` object. Omit `output` if you only want to keep the
result in memory.

To collect through your interrupt handler, pass it in:

```python
with imu.interrupt(int1_gpio=27) as irq:
    calibration = imu.calibrate(output='calibration.json', interrupts=irq)
```

On later runs, load the saved offsets when you create the sensor object:

```python
from icm40609 import Calibration

calibration = Calibration.load('calibration.json')
imu = ICM40609(transport, calibration=calibration)
imu.initialize()
```

For an existing object, assign `imu.calibration = calibration`. Assign `None`
to turn corrections off. You can also save an in-memory result later with
`calibration.save('calibration.json')`. Recalibrating measures uncorrected
readings, so offsets do not stack. If capture or saving fails, the previous
calibration and output file stay intact.

### Accelerometer calibration

Gyro calibration works in any stationary orientation. Accelerometer offset
calibration needs a known pose so it can account for gravity:

```python
# Position the sensor so Z reads +1 g and X/Y read 0 g.
imu.calibrate(gravity_axis='+z', output='calibration.json')
```

Supported directions are `+x`, `-x`, `+y`, `-y`, `+z`, and `-z`. These refer to
the sensor package axes. For `-z`, the expected Z reading is -1 g. The
correction preserves gravity in your measurements.

This is a single-pose offset calibration. It does not correct scale,
alignment, or temperature drift. Calibrate near the operating temperature
and keep the file with the physical sensor it belongs to. See
[Analog Devices AN-1057](https://www.analog.com/en/resources/app-notes/an-1057.html)
for background on using gravity as the reference.

Calibration requires at least 100 readings and rejects obvious motion.
The checks use per-axis standard deviation limits of 0.02 g and 0.5 deg/s,
mean gyro magnitude no greater than 5 deg/s, and mean acceleration between
0.85 and 1.15 g. Slow constant rotation can still look like gyro bias, so
these checks do not replace keeping the board still.

## Ranges and output rates

| Argument | Supported values |
| --- | --- |
| `accel_range_g` | 4, 8, 16, 32 |
| `gyro_range_dps` | 15.625, 31.25, 62.5, 125, 250, 500, 1000, 2000 |
| `odr_hz` | 12.5, 25, 50, 100, 200, 500, 1000, 2000, 4000, 8000, 16000, 32000 |

Ranges mean plus/minus. Defaults are ±16 g, ±2000 deg/s, and 100 Hz. To
change them after initialization:

```python
imu.configure(accel_range_g=8, gyro_range_dps=500, odr_hz=200)
```

Each call sets all three values; omitted arguments use their defaults.
When running, the driver waits one sample period plus 30 ms after the change.
Your filters may need longer to settle. If a bus error interrupts a
configuration change, call `initialize()` before reading again.

A supported output rate does not mean Python can capture every sample at
that rate. This version reads registers directly and has no FIFO support.

## Wiring and I2C addresses

Use the supply, decoupling, and pin connections in your schematic and the
datasheet's application circuit. The tables below cover bus signals, not a
complete bare-chip circuit. Match VDDIO and host logic levels; do not apply
5 V logic to the sensor.

| I2C signal | Pi GPIO (BCM) | Physical header pin |
| --- | --- | --- |
| SDA | GPIO2 / SDA1 | 3 |
| SCL | GPIO3 / SCL1 | 5 |
| Ground | GND | e.g. 6 |

For I2C, hold nCS high to VDDIO. SDA and SCL need pull-ups to a compatible
VDDIO rail; account for pull-ups already on your boards. The examples use
bus 1. Set the bus speed on the host; the chip supports up to 1 MHz.

AD0 low selects `0x68`; AD0 high selects `0x69`. Give AD0 a defined logic
level when designing a board. On the custom PCB tested here, leaving AD0
unconnected caused the address to change after reset.

Use `I2CTransport(bus, address=None)` to check both addresses. Auto mode
verifies chip ID `0x3B` before choosing an address and checks it before each
bank-0 transfer. On a transfer error, it checks again and retries, up to
three attempts with 10 ms between attempts. `transport.address` reports the
most recently selected address.

For two ICM-40609-Ds on one bus, use fixed addresses: `address=0x68` and
`address=0x69`. Auto discovery raises an error if both addresses report the
expected ID. It reads register `0x75` while probing; check for read side
effects if another device uses either address. Address recovery during
register diagnostics in another bank requires reinitialization.

Auto mode handled the address changes observed on this PCB. It cannot
resolve address collisions or guarantee a successful transfer if AD0 changes
level in the middle of one.

| 4-wire SPI signal | Pi GPIO (BCM) | Physical header pin |
| --- | --- | --- |
| SDI | GPIO10 / MOSI | 19 |
| SDO | GPIO9 / MISO | 21 |
| SCLK | GPIO11 / SCLK | 23 |
| nCS | GPIO8 / CE0 | 24 |
| Ground | GND | e.g. 6 |

The SPI adapter defaults to mode 0 at 1 MHz and also supports mode 3. Its
upper limit is 24 MHz; usable speeds depend on your wiring and host.
Interrupt and FSYNC connections are optional for the polling examples.

## Terminal commands

The same features are available for quick checks without writing a script:

```sh
python -m icm40609 calibrate --int1-gpio 27 --output calibration.json
python -m icm40609 read --int1-gpio 27 --calibration calibration.json
python -m icm40609 read --count 100
```

Leave out GPIO arguments to poll for data-ready. Both commands accept
`--interface spi`, `--bus`, `--cs`, `--int2-gpio`, and `--active-low`.
For accelerometer calibration in a known pose, add `--gravity-axis=+z`.
Run `python -m icm40609 calibrate --help` or `read --help` for the options.
The installed `icm40609` command is equivalent to `python -m icm40609`.

## Troubleshooting and ownership

| Symptom | What to check |
| --- | --- |
| `ModuleNotFoundError: icm40609` | Install into the interpreter your project uses. Check `python -m pip show icm40609`. |
| I2C input/output error | Check power, ground, bus number, nCS, pull-ups, and AD0. Use `address=None` if the address can change. |
| GPIO interrupt timeout | Check BCM versus header numbering, the INT1/INT2 mapping, wiring, and polarity. |
| Calibration rejects movement | Hold the board still; for accelerometer calibration, check its orientation. |
| `InvalidSampleError` | Wait for the next data-ready event and retry the reading. |

Use one driver instance per chip and let it manage the registers. Calls
through that instance use a lock, but another process or driver can still
change the chip behind it.

`data_ready()` reads INT_STATUS and clears all its interrupt flags. Coordinate
that read with any other interrupt handling. `read_register(address, bank=0)`
provides diagnostic access to banks 0-4; some registers have read side effects.
`stop()` turns off the accelerometer and gyro. You still own and close the
bus; the temperature sensor remains enabled.

## Other boards and testing

To use a different bus adapter, pass an object with this interface to
`ICM40609(transport)`:

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

Preserve the chip's register auto-increment behavior and raise `OSError` on
bus errors. I2C reads need a register-pointer write followed by a repeated
START and read. For SPI, send `register | 0x80` to read or `register & 0x7F`
to write, with address and data under one chip-select assertion.

The supported runtime is CPython. MicroPython and CircuitPython need changes
for dataclasses, typing, threading, and bus APIs.

To run the simulated tests, from this checkout:

```sh
python -m pip install -e '.[test]'
python -m pytest -q
```

Tests cover register configuration, ranges, rates, conversions, address
recovery, calibration, interrupt routing, and GPIO cleanup. Physical tests
have covered I2C readings, address changes, gyro calibration, and INT1 on
GPIO27. INT2 and SPI have simulated coverage only. FIFO, low-power modes,
FIFO interrupt routing, automated self-test, custom filters, FSYNC, and
orientation fusion are not implemented.

The register map and conversions follow **TDK DS-000330, revision 1.2**.
This driver expects an ICM-40609-D with chip ID `0x3B`.

References: [TDK datasheet](https://invensense.tdk.com/wp-content/uploads/2022/07/DS-000330-ICM-40609-D-v1.2.pdf),
[smbus2](https://smbus2.readthedocs.io/en/latest/operations.html),
[spidev](https://pypi.org/project/spidev/), and
[gpiozero inputs](https://gpiozero.readthedocs.io/en/stable/api_input.html).
