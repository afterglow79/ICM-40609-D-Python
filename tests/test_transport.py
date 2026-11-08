from types import SimpleNamespace
import sys
import pytest

from icm40609 import I2CTransport, SPITransport


class SPIWire:
    def __init__(self):
        self.frames = []

    def xfer2(self, frame):
        self.frames.append(frame)
        return [0] + [0x3B] * (len(frame) - 1)


def test_spi_read_bit_and_one_cs_frame():
    wire = SPIWire()
    bus = SPITransport(wire)
    assert bus.read(0x75, 1) == b'\x3b'
    assert wire.frames == [[0xF5, 0]]
    bus.write(0x4F, b'\x08\x28')
    assert wire.frames[-1] == [0x4F, 0x08, 0x28]
    assert wire.mode == 0 and wire.max_speed_hz == 1_000_000
    assert not wire.threewire and not wire.cshigh and not wire.lsbfirst


@pytest.mark.parametrize('kwargs', [dict(mode=1), dict(mode=2), dict(speed_hz=0),
                                   dict(speed_hz=24_000_001)])
def test_spi_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        SPITransport(SPIWire(), **kwargs)


def test_spi_short_transfer():
    wire = SPIWire()
    bus = SPITransport(wire)
    wire.xfer2 = lambda frame: []
    with pytest.raises(OSError):
        bus.read(0x75, 1)
    with pytest.raises(OSError):
        bus.write(0x4E, b'\x00')


def test_i2c_combined_read_and_address(monkeypatch):
    # Stand in for Linux's I2C buffers so this test can run without hardware.
    # Check the address and bytes that would go over the bus.
    class Message:
        def __init__(self, address, data, read=False):
            self.address, self.data, self.read = address, bytearray(data), read

        def __bytes__(self):
            return bytes(self.data)

    factory = SimpleNamespace(
        write=lambda address, data: Message(address, data),
        read=lambda address, count: Message(address, bytes(count), True),
    )
    monkeypatch.setitem(sys.modules, 'smbus2', SimpleNamespace(i2c_msg=factory))

    class Wire:
        frames = []

        def i2c_rdwr(self, *messages):
            self.frames.append(messages)
            if len(messages) == 2:
                assert bytes(messages[0]) == b'\x75'
                assert messages[1].read
                messages[1].data[:] = b'\x3b'

    wire = Wire()
    bus = I2CTransport(wire, 0x69)
    assert bus.read(0x75, 1) == b'\x3b'
    assert all(m.address == 0x69 for m in wire.frames[0])
    bus.write(0x4E, b'\x0f')
    assert bytes(wire.frames[1][0]) == b'\x4e\x0f'


def test_i2c_invalid_address():
    with pytest.raises(ValueError):
        I2CTransport(None, 0x3B)
