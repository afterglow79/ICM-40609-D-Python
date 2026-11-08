import struct
import pytest

from icm40609 import ICM40609, DeviceNotFoundError, InvalidSampleError


class MemoryTransport:
    interface = 'i2c'

    def __init__(self):
        self.bank = 0
        self.regs = {(0, 0x75): 0x3B, (0, 0x4D): 0x91,
                     (0, 0x4C): 0x30}
        self.events = []

    def read(self, register, length):
        self.events.append(('read', self.bank, register, length))
        return bytes(self.regs.get((self.bank, register + i), 0) for i in range(length))

    def write(self, register, data):
        self.events.append(('write', self.bank, register, bytes(data)))
        if register == 0x76:
            self.bank = data[0]
        else:
            for i, value in enumerate(data):
                self.regs[self.bank, register + i] = value

    def sample(self, *values):
        for i, value in enumerate(struct.pack('>7h', *values)):
            self.regs[0, 0x1D + i] = value


@pytest.fixture
def hardware():
    bus = MemoryTransport()
    driver = ICM40609(bus, sleep=lambda t: bus.events.append(('sleep', t)))
    return driver, bus


def test_initialization_identifies_chip_and_sets_normal_mode(hardware):
    driver, bus = hardware
    driver.initialize()
    assert bus.regs[0, 0x4D] == 0x51  # Normal mode needs EN_TEST_MODE=01; reset gives 10.
    assert bus.regs[0, 0x13] == 0x09
    assert bus.regs[0, 0x4E] == 0x0F
    assert bus.regs[0, 0x4F] == 0x08
    assert bus.regs[0, 0x50] == 0x28  # 16 g, 100 Hz
    events = bus.events
    reset = next(i for i, e in enumerate(events) if e == ('write', 0, 0x11, b'\x01'))
    assert events[reset + 1] == ('sleep', 0.002)
    on = next(i for i, e in enumerate(events) if e == ('write', 0, 0x4E, b'\x0f'))
    assert events[on + 1][0] == 'sleep'
    assert events[on + 1][1] >= 0.045


def test_wrong_chip_is_rejected_before_reset(hardware):
    driver, bus = hardware
    bus.regs[0, 0x75] = 0x47
    with pytest.raises(DeviceNotFoundError, match='0x47'):
        driver.initialize()
    assert not any(e[:3] == ('write', 0, 0x11) for e in bus.events)


def test_coherent_signed_burst_and_units(hardware):
    driver, bus = hardware
    driver.initialize(accel_range_g=8, gyro_range_dps=250, odr_hz=200)
    bus.sample(13248, 4096, -8192, 0, 131, -262, 32767)
    sample = driver.read_sample()
    assert sample.temperature_c == pytest.approx(125)
    assert sample.acceleration_g == (1, -2, 0)
    assert sample.acceleration_m_s2 == pytest.approx((9.80665, -19.6133, 0))
    assert sample.gyroscope_dps == pytest.approx((1, -2, 32767 / 131))
    assert sample.gyroscope_rad_s[0] == pytest.approx(0.017453292519943295)
    assert ('read', 0, 0x1D, 14) in bus.events


@pytest.mark.parametrize('axis', range(7))
def test_invalid_samples_rejected(hardware, axis):
    driver, bus = hardware
    driver.initialize()
    values = [0] * 7
    values[axis] = -32768
    bus.sample(*values)
    with pytest.raises(InvalidSampleError):
        driver.read_sample()


@pytest.mark.parametrize('settings', [dict(accel_range_g=2), dict(gyro_range_dps=300),
                                    dict(odr_hz=6.25), dict(odr_hz=123)])
def test_bad_configuration_does_not_touch_hardware(hardware, settings):
    driver, bus = hardware
    with pytest.raises(ValueError):
        driver.initialize(**settings)
    assert not bus.events


def test_short_read_reports_bus_error(hardware):
    driver, bus = hardware
    driver.initialize()
    bus.read = lambda r, n: b''
    with pytest.raises(OSError, match='Expected'):
        driver.read_sample()


def test_read_before_initialization_and_after_stop(hardware):
    driver, bus = hardware
    with pytest.raises(RuntimeError):
        driver.read_sample()
    driver.initialize()
    driver.stop()
    assert bus.regs[0, 0x4E] & 0x0F == 0
    with pytest.raises(RuntimeError):
        driver.read_sample()


def test_bank_read_does_not_corrupt_following_sample(hardware):
    driver, bus = hardware
    driver.initialize()
    bus.regs[2, 3] = 0xAA
    assert driver.read_register(3, bank=2) == 0xAA
    bus.sample(0, 2048, 0, 0, 0, 0, 0)
    assert driver.read_sample().acceleration_g == (1, 0, 0)


def test_data_ready_bit(hardware):
    driver, bus = hardware
    driver.initialize()
    bus.regs[0, 0x2D] = 0x10
    assert not driver.data_ready()
    bus.regs[0, 0x2D] = 0x08
    assert driver.data_ready()


def test_spi_initialization_uses_spi_slew_rates(hardware):
    driver, bus = hardware
    bus.interface = 'spi'
    driver.initialize()
    assert bus.regs[0, 0x13] == 0x05


def test_failed_reinitialization_invalidates_old_state(hardware):
    driver, bus = hardware
    driver.initialize()
    bus.regs[0, 0x75] = 0
    with pytest.raises(DeviceNotFoundError):
        driver.initialize()
    with pytest.raises(RuntimeError):
        driver.read_sample()


def test_runtime_configuration_waits_for_new_scale_samples(hardware):
    driver, bus = hardware
    driver.initialize()
    bus.events.clear()
    driver.configure(accel_range_g=4, odr_hz=12.5)
    assert bus.events[-1][0] == 'sleep'
    assert bus.events[-1][1] >= 1 / 12.5


def test_partial_configuration_failure_requires_reinitialization(hardware):
    driver, bus = hardware
    driver.initialize()
    original = bus.write
    def fail_on_accel(register, data):
        if register == 0x50:
            raise OSError('bus failed')
        original(register, data)
    bus.write = fail_on_accel
    with pytest.raises(OSError):
        driver.configure()
    with pytest.raises(RuntimeError):
        driver.read_sample()


@pytest.mark.parametrize('hz,code', [(32000,1),(16000,2),(8000,3),(4000,4),
                                  (2000,5),(1000,6),(200,7),(100,8),(50,9),
                                  (25,10),(12.5,11),(500,15)])
def test_output_rates(hardware, hz, code):
    driver, bus = hardware
    driver.initialize(odr_hz=hz)
    assert bus.regs[0, 0x4F] & 15 == code
    assert bus.regs[0, 0x50] & 15 == code


@pytest.mark.parametrize('g', [4, 8, 16, 32])
@pytest.mark.parametrize('dps,lsb', [(2000,16.4),(1000,32.8),(500,65.5),(250,131),
                                 (125,262),(62.5,524.3),(31.25,1048.6),(15.625,2097.2)])
def test_all_scale_factors(hardware, g, dps, lsb):
    driver, bus = hardware
    driver.initialize(accel_range_g=g, gyro_range_dps=dps)
    bus.sample(0, 1024, 0, 0, 1000, 0, 0)
    s = driver.read_sample()
    assert s.acceleration_g[0] == pytest.approx(g / 32)
    assert s.gyroscope_dps[0] == pytest.approx(1000 / lsb)
