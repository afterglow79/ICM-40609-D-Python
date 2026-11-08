"""Print live I2C readings. First install with: python -m pip install '.[i2c]'"""
import time
from smbus2 import SMBus
from icm40609 import ICM40609, I2CTransport, InvalidSampleError


def main():
    with SMBus(1) as bus:
        # Check both possible addresses, including after the chip resets.
        transport = I2CTransport(bus, address=None)
        imu = ICM40609(transport)
        imu.initialize(accel_range_g=16, gyro_range_dps=2000, odr_hz=100)
        print(f"Connected to ICM-40609-D at 0x{transport.address:02X}")
        try:
            while True:
                if imu.data_ready():
                    try:
                        sample = imu.read_sample()
                    except InvalidSampleError:
                        continue
                    print(f"accel (g): {sample.acceleration_g}  "
                          f"gyro (deg/s): {sample.gyroscope_dps}  "
                          f"temp (C): {sample.temperature_c:.2f}")
                time.sleep(0.002)
        except KeyboardInterrupt:
            pass
        finally:
            imu.stop()


if __name__ == '__main__':
    main()
