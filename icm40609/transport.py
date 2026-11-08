"""Connect over Linux I2C or SPI, or supply an adapter for your own board."""

from __future__ import annotations
from typing import Protocol


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
    """Use an open smbus2.SMBus. Close the bus yourself when you're done."""
    interface = 'i2c'

    def __init__(self, bus, address: int = 0x68):
        if address not in (0x68, 0x69):
            raise ValueError('ICM-40609-D address must be 0x68 or 0x69')
        from smbus2 import i2c_msg
        self.bus = bus
        self.address = address
        self._msg = i2c_msg

    def read(self, register: int, length: int) -> bytes:
        address = self._msg.write(self.address, [register])
        data = self._msg.read(self.address, length)
        # Keep the address write and read together, with a repeated START.
        self.bus.i2c_rdwr(address, data)
        return bytes(data)

    def write(self, register: int, data: bytes) -> None:
        self.bus.i2c_rdwr(self._msg.write(self.address, bytes([register]) + data))


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
