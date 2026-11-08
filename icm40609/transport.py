"""Connect over Linux I2C or SPI, or supply an adapter for your own board."""

from __future__ import annotations
import time
from typing import Optional, Protocol


class RegisterTransport(Protocol):
    """Implement this interface to use a different board or bus adapter.

    Set interface to 'i2c' or 'spi' so the driver can choose the slew rates.
    Read exactly length bytes, or write data, starting at register. Let the
    chip advance the register address during the transfer. Raise OSError if
    a transfer fails, and leave bank selection to the driver.
    """
    interface: str

    def read(self, register: int, length: int) -> bytes: ...
    def write(self, register: int, data: bytes) -> None: ...


class I2CTransport:
    """Use an open smbus2.SMBus. Close the bus yourself when you're done.

    Pass address=None to find the chip at 0x68 or 0x69 and follow address
    changes. Use a fixed address if you have two ICM-40609-Ds on the bus.
    """
    interface = 'i2c'

    def __init__(self, bus, address: Optional[int] = 0x68):
        if address not in (None, 0x68, 0x69):
            raise ValueError('ICM-40609-D address must be 0x68, 0x69, or None for auto')
        from smbus2 import i2c_msg
        self.bus = bus
        self.address = address
        self._msg = i2c_msg
        self._auto = address is None
        self._bank = 0

    def _read_at(self, address: int, register: int, length: int) -> bytes:
        pointer = self._msg.write(address, [register])
        data = self._msg.read(address, length)
        # Keep the address write and read together, with a repeated START.
        self.bus.i2c_rdwr(pointer, data)
        result = bytes(data)
        if len(result) != length:
            raise OSError(f'Expected {length} I2C bytes, got {len(result)}')
        return result

    def _find_address(self) -> None:
        # Check identity before using a cached address too: another sensor
        # might answer there after our chip moves away.
        if self.address is not None:
            try:
                if self._read_at(self.address, 0x75, 1) == b'\x3b':
                    return
            except OSError:
                pass
        self.address = None
        matches = []
        for candidate in (0x68, 0x69):
            try:
                if self._read_at(candidate, 0x75, 1) == b'\x3b':
                    matches.append(candidate)
            except OSError:
                continue
        if len(matches) > 1:
            raise OSError('Both addresses report ID 0x3B; use a fixed address')
        if not matches:
            raise OSError('No ICM-40609-D with ID 0x3B at 0x68 or 0x69')
        self.address = matches[0]

    def _transfer(self, operation):
        if not self._auto:
            return operation()
        for attempt in range(3):
            try:
                # WHO_AM_I lives in bank 0. In other banks, use the already
                # selected chip and report errors without guessing its identity.
                if self._bank == 0:
                    self._find_address()
                return operation()
            except OSError as exc:
                if self._bank != 0:
                    raise OSError('Address recovery needs bank 0; reinitialize the sensor') from exc
                self.address = None
                if attempt == 2:
                    raise OSError(f'I2C auto-address transfer failed after 3 attempts: {exc}') from exc
                time.sleep(0.01)

    def read(self, register: int, length: int) -> bytes:
        return self._transfer(lambda: self._read_at(self.address, register, length))

    def write(self, register: int, data: bytes) -> None:
        self._transfer(lambda: self.bus.i2c_rdwr(
            self._msg.write(self.address, bytes([register]) + data)))
        if register == 0x76 and data:
            self._bank = data[0] & 7
        elif register == 0x11 and data and data[0] & 1:
            self._bank = 0


class SPITransport:
    """Use an open spidev.SpiDev in 4-wire mode, with SPI mode 0 or 3.

    xfer2 keeps chip select active for the whole transfer. Close the SPI
    device yourself when you're done.
    """
    interface = 'spi'

    def __init__(self, spi, *, speed_hz: int = 1_000_000, mode: int = 0):
        if mode not in (0, 3):
            raise ValueError('Use SPI mode 0 or 3')
        if not isinstance(speed_hz, int) or not 1 <= speed_hz <= 24_000_000:
            raise ValueError('SPI speed must be 1..24000000 Hz')
        spi.mode = mode
        spi.max_speed_hz = speed_hz
        spi.bits_per_word = 8
        spi.lsbfirst = False
        spi.cshigh = False
        spi.threewire = False
        self.spi = spi

    def read(self, register: int, length: int) -> bytes:
        result = self.spi.xfer2([register | 0x80] + [0] * length)
        if len(result) != length + 1:
            raise OSError('Short SPI transaction')
        return bytes(result[1:])

    def write(self, register: int, data: bytes) -> None:
        result = self.spi.xfer2([register & 0x7F] + list(data))
        if len(result) != len(data) + 1:
            raise OSError('Short SPI transaction')
