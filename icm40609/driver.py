"""Read sensor registers in low-noise mode, with FIFO and FSYNC tagging off."""

from __future__ import annotations

from dataclasses import dataclass
from math import pi
import struct
from threading import RLock
import time
from typing import Callable, Optional, Tuple, TYPE_CHECKING

from .transport import RegisterTransport

if TYPE_CHECKING:
    from .calibration import Calibration

Vector3 = Tuple[float, float, float]
RawVector3 = Tuple[int, int, int]

# Values come from DS-000330 tables 1/2 and sections 13.30/13.31.
# Keep the gyro scale factors as published: the datasheet rounds them.
ACCEL_RANGES = {32: (0, 1024), 16: (1, 2048), 8: (2, 4096), 4: (3, 8192)}
GYRO_RANGES = {2000: (0, 16.4), 1000: (1, 32.8), 500: (2, 65.5),
               250: (3, 131), 125: (4, 262), 62.5: (5, 524.3),
               31.25: (6, 1048.6), 15.625: (7, 2097.2)}
ODRS = {32000: 1, 16000: 2, 8000: 3, 4000: 4, 2000: 5, 1000: 6,
        200: 7, 100: 8, 50: 9, 25: 10, 12.5: 11, 500: 15}


class DeviceNotFoundError(OSError):
    """WHO_AM_I does not identify an ICM-40609-D."""


class InvalidSampleError(ValueError):
    """The chip marked a reading as invalid. Try again after the next sample."""


@dataclass(frozen=True)
class RawSample:
    temperature: int
    acceleration: RawVector3
    gyroscope: RawVector3


@dataclass(frozen=True)
class Sample:
    temperature_c: float
    acceleration_g: Vector3
    gyroscope_dps: Vector3

    @property
    def acceleration_m_s2(self) -> Vector3:
        return tuple(v * 9.80665 for v in self.acceleration_g)

    @property
    def gyroscope_rad_s(self) -> Vector3:
        return tuple(v * pi / 180 for v in self.gyroscope_dps)


class ICM40609:
    """Use an I2C or SPI transport to communicate with the sensor.

    Creating this object doesn't access the chip; call initialize() first.
    Use one driver instance per sensor and let it manage the registers.
    A lock keeps calls from different threads from overlapping. You are
    responsible for closing the transport when you're done.
    """

    def __init__(self, transport: RegisterTransport, *,
                 sleep: Callable[[float], None] = time.sleep,
                 calibration: Optional[Calibration] = None):
        if transport.interface not in ('i2c', 'spi'):
            raise ValueError("transport.interface must be 'i2c' or 'spi'")
        self.transport = transport
        self.calibration = calibration
        self._sleep = sleep
        self._lock = RLock()
        self._bank = None
        self._running = False
        self._accel_scale = 2048
        self._gyro_scale = 16.4

    def _select(self, bank: int) -> None:
        if bank != self._bank:
            self._bank = None
            self.transport.write(0x76, bytes([bank]))
            self._bank = bank

    def _read(self, register: int, length: int = 1, bank: int = 0) -> bytes:
        self._select(bank)
        result = bytes(self.transport.read(register, length))
        if len(result) != length:
            raise OSError(f"Expected {length} bytes at 0x{register:02X}, got {len(result)}")
        return result

    def _write(self, register: int, value: int) -> None:
        self._select(0)
        self.transport.write(register, bytes([value]))

    def _update(self, register: int, mask: int, value: int) -> None:
        current = self._read(register)[0]
        self._write(register, (current & ~mask) | (value & mask))

    @staticmethod
    def _validate(accel_range_g, gyro_range_dps, odr_hz) -> None:
        for name, value, allowed in (
            ('accel_range_g', accel_range_g, ACCEL_RANGES),
            ('gyro_range_dps', gyro_range_dps, GYRO_RANGES),
            ('odr_hz', odr_hz, ODRS),
        ):
            if value not in allowed:
                raise ValueError(f"{name} must be one of {list(allowed)}")

    def initialize(self, *, accel_range_g: float = 16,
                   gyro_range_dps: float = 2000, odr_hz: float = 100) -> None:
        """Identify, reset, configure and start both sensors in low-noise mode.

        This replaces the chip's previous settings and gives both sensors the
        same output rate. It waits for the chip to start, finish its reset,
        and produce a sample before returning.
        """
        self._validate(accel_range_g, gyro_range_dps, odr_hz)
        with self._lock:
            self._running = False
            # The gyro may already be running. Give it its required 45 ms
            # of on-time before resetting the chip.
            self._sleep(0.045)
            self._bank = None
            identity = self._read(0x75)[0]
            if identity != 0x3B:
                raise DeviceNotFoundError(f"Expected ICM-40609-D ID 0x3B, got 0x{identity:02X}")
            self._running = False
            self._write(0x11, 0x01)
            self._bank = None
            self._sleep(0.002)  # Reset needs at least 1 ms without register access (13.1).
            if self._read(0x75)[0] != 0x3B:
                raise DeviceNotFoundError('Device identity changed after reset')
            # Normal operation needs EN_TEST_MODE=01, per section 13.28.
            self._update(0x4D, 0xC0, 0x40)
            self._update(0x13, 0x3F, 0x09 if self.transport.interface == 'i2c' else 0x05)
            # Use big-endian readings and counts, and keep the invalid-data
            # marker enabled. Preserve interface settings and reserved bits.
            # Reset leaves FSYNC tagging off.
            self._update(0x4C, 0xB0, 0x30)
            self.configure(accel_range_g=accel_range_g,
                           gyro_range_dps=gyro_range_dps, odr_hz=odr_hz)
            self._write(0x4E, 0x0F)
            # Leave registers alone for the required 200 us after enabling.
            # Also allow 30 ms for gyro startup, a full sample period, and
            # at least 45 ms of gyro on-time before we return.
            self._sleep(max(0.045, 1 / odr_hz + 0.030))
            self._running = True

    def configure(self, *, accel_range_g: float = 16,
                  gyro_range_dps: float = 2000, odr_hz: float = 100) -> None:
        """Set each sensor's range and their shared output rate (12.5 Hz to 32 kHz).

        The datasheet allows these settings to change while the sensors run.
        Each call sets all three values, including defaults for omitted ones.
        If the sensors are running, wait one sample period plus 30 ms for
        fresh readings. Your application may need more time for filters to settle.
        """
        self._validate(accel_range_g, gyro_range_dps, odr_hz)
        with self._lock:
            try:
                a, accel_scale = ACCEL_RANGES[accel_range_g]
                g, gyro_scale = GYRO_RANGES[gyro_range_dps]
                self._update(0x4F, 0xEF, g << 5 | ODRS[odr_hz])
                self._update(0x50, 0xEF, a << 5 | ODRS[odr_hz])
            except OSError:
                # Some settings may have changed. Require initialize() again
                # so we don't convert readings with the wrong scale factors.
                self._running = False
                raise
            self._accel_scale = accel_scale
            self._gyro_scale = gyro_scale
            if self._running:
                self._sleep(1 / odr_hz + 0.030)

    def configure_data_ready_interrupt(self, pin: int, *, enabled: bool = True,
                                       active_high: bool = True, latched: bool = True,
                                       push_pull: bool = True) -> None:
        """Route fresh-data interrupts to sensor INT1 or INT2.

        This briefly stops both sensors because interrupt registers must be
        changed while they are off. Other settings on the second pin stay
        intact. In latched mode, read data_ready() to clear the interrupt.
        Host GPIO numbers belong to your GPIO adapter, not this method.
        """
        if type(pin) is not int or pin not in (1, 2):
            raise ValueError('Interrupt pin must be 1 or 2')
        with self._lock:
            self._require_running()
            power = self._read(0x4E)[0]
            self._running = False
            try:
                self._write(0x4E, power & ~0x0F)
                shift = 0 if pin == 1 else 3
                value = (int(latched) << 2 | int(push_pull) << 1 | int(active_high)) << shift
                self._update(0x14, 7 << shift, value)
                # Short pulses also work at low rates, so later ODR changes
                # won't need another interrupt-register write while running.
                # Clearing INT_ASYNC_RESET is required for both output pins.
                self._update(0x64, 0x70, 0x60)
                self._update(0x63, 0x30, 0)  # Clear latched data-ready on status reads.
                source = 0x65 if pin == 1 else 0x68
                # Reset-done is enabled on INT1 at reset. Remove it so that
                # this pin reports fresh data rather than the earlier reset.
                self._update(source, 0x18, 0x08 if enabled else 0)
                self._read(0x2D)
                self._write(0x4E, power)
                self._sleep(0.12)  # Covers startup, minimum on-time and the slowest ODR.
            except OSError:
                # Leave reads disabled until initialize() restores a known state.
                raise
            self._running = True

    def _require_running(self) -> None:
        if not self._running:
            raise RuntimeError('Call initialize() before reading samples')

    def interrupt(self, *, int1_gpio=None, int2_gpio=None, active_high=True,
                  pin_factory=None):
        """Map INT1 and/or INT2 to host GPIOs and return a waitable handler.

        Example: with imu.interrupt(int1_gpio=27) as irq: ...
        Pi GPIOs use BCM numbering. The context manager releases the inputs;
        if you don't use a with block, call the handler's close() yourself.
        """
        from .interrupts import GPIOInterrupts
        return GPIOInterrupts(self, int1_gpio=int1_gpio, int2_gpio=int2_gpio,
                              active_high=active_high, pin_factory=pin_factory)

    def calibrate(self, *, samples=500, settle=2.0, gravity_axis=None,
                  output=None, interrupts=None, timeout=None):
        """Measure, apply and optionally save stationary offsets.

        Keep the board still. Without gravity_axis, only gyro offsets change.
        Pass '+z', '-z', '+x', '-x', '+y' or '-y' for accelerometer offsets
        in a known pose. Raw readings stay unchanged. If capture or saving
        fails, the previous calibration remains in use.
        """
        from math import isfinite
        from .calibration import Calibration, GRAVITY_AXES, collect_samples
        if type(samples) is not int or samples < 100:
            raise ValueError('Collect at least 100 samples')
        if not isfinite(settle) or settle < 0:
            raise ValueError('settle must be finite and nonnegative')
        if gravity_axis is not None and gravity_axis not in GRAVITY_AXES:
            raise ValueError('Unknown gravity axis')
        if timeout is None:
            timeout = max(10, samples / 100 * 3)
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        with self._lock:
            self._require_running()
            previous = self.calibration
            self.calibration = None
            try:
                self._sleep(settle)
                readings = collect_samples(self, samples, timeout=timeout, interrupts=interrupts)
                result = Calibration.from_samples(readings, gravity_axis=gravity_axis)
                if output is not None:
                    result.save(output)
            finally:
                self.calibration = previous
            self.calibration = result
            return result

    def read_raw(self) -> RawSample:
        """Read temperature, XYZ acceleration and XYZ gyro in one 14-byte burst.

        Raise InvalidSampleError if the chip marks any channel as invalid.
        Values are signed sensor counts. See datasheet section 9 for the
        package's axis directions.
        """
        with self._lock:
            self._require_running()
            values = struct.unpack('>7h', self._read(0x1D, 14))
            if -32768 in values:
                raise InvalidSampleError('Sensor data is not valid yet; retry on next ODR')
            return RawSample(values[0], values[1:4], values[4:7])

    def read_sample(self) -> Sample:
        """Return temperature (C), acceleration (g), and angular rate (deg/s)."""
        with self._lock:
            raw = self.read_raw()
            sample = Sample(raw.temperature / 132.48 + 25,
                            tuple(v / self._accel_scale for v in raw.acceleration),
                            tuple(v / self._gyro_scale for v in raw.gyroscope))
            return self.calibration.apply(sample) if self.calibration is not None else sample

    def read_register(self, register: int, *, bank: int = 0) -> int:
        """Read a register for diagnostics. Reading status registers clears their flags."""
        if not isinstance(bank, int) or not 0 <= bank <= 4:
            raise ValueError('bank must be 0..4')
        if not isinstance(register, int) or not 0 <= register <= 0x7F:
            raise ValueError('register must be 0..127')
        with self._lock:
            return self._read(register, bank=bank)[0]

    def data_ready(self) -> bool:
        """Check for new data. This also clears all interrupt flags in INT_STATUS."""
        with self._lock:
            self._require_running()
            return bool(self._read(0x2D)[0] & 0x08)

    def stop(self) -> None:
        """Turn off the accelerometer and gyro. Close the transport yourself when done."""
        with self._lock:
            self._update(0x4E, 0x0F, 0)
            self._running = False
