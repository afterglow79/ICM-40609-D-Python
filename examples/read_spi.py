"""Print live SPI readings. First install with: python -m pip install '.[spi]'"""
import time
from spidev import SpiDev
from icm40609 import ICM40609, SPITransport, InvalidSampleError


def main():
    spi = SpiDev()
    spi.open(0, 0)  # Use SPI bus 0 and chip select 0 (/dev/spidev0.0).
    try:
        imu = ICM40609(SPITransport(spi, speed_hz=1_000_000, mode=0))
        imu.initialize(accel_range_g=16, gyro_range_dps=2000, odr_hz=100)
        try:
            while True:
                if imu.data_ready():
                    try:
                        print(imu.read_sample())
                    except InvalidSampleError:
                        continue
                time.sleep(0.002)
        except KeyboardInterrupt:
            pass
        finally:
            imu.stop()
    finally:
        spi.close()


if __name__ == '__main__':
    main()
