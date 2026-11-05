"""RP1 PWM0 channel 2 on GPIO18; never select the cooling fan's PWM1."""
import fcntl
import math
import os
import time
from pathlib import Path

from services.buzzer_service import BuzzerService


def find_buzzer_pwm(sysfs_root=Path('/sys/class/pwm')):
    for chip in Path(sysfs_root).glob('pwmchip*'):
        node = (chip / 'device/of_node').resolve()
        if node.name == 'pwm@98000' and node.parent.name == 'rp1':
            if int((chip / 'npwm').read_text()) >= 3:
                return chip
    raise RuntimeError('GPIO18 하드웨어 PWM이 준비되지 않았습니다. PWM 설정 적용 후 재부팅해 주세요.')


class HardwarePWMDevice:
    """A value-compatible PWM device. Only duty changes during a beep."""
    def __init__(self, frequency_hz=2000, pin=18, sysfs_root=Path('/sys/class/pwm'),
                 lock_dir=None):
        if int(pin) != 18 or not 0 < float(frequency_hz) <= 10000:
            raise ValueError('이 드라이버는 GPIO18의 0~10000 Hz PWM만 지원합니다.')
        self.chip = find_buzzer_pwm(sysfs_root)
        self.channel = self.chip / 'pwm2'
        self.period_ns = round(1e9 / float(frequency_hz))
        self._value = 0.0
        self._closed = False
        self._claimed = False
        runtime = Path(lock_dir or os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        self._lock_fd = os.open(runtime / 'poco-buzzer-hardware-pwm.lock', flags, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self.channel.exists():
                self._write(self.chip / 'export', 2)
            deadline = time.monotonic() + 2.0
            while not all(os.access(self.channel / name, os.W_OK)
                          for name in ('enable', 'period', 'duty_cycle', 'polarity')):
                if time.monotonic() >= deadline:
                    raise PermissionError('PWM 쓰기 권한을 확인해 주세요(gpio 그룹/udev).')
                time.sleep(.01)
            if int((self.channel / 'enable').read_text()) and int(
                    (self.channel / 'duty_cycle').read_text()):
                raise RuntimeError('GPIO18 PWM이 이미 출력 중입니다. 기존 부저 작업을 먼저 종료해 주세요.')
            self._claimed = True
            self._write(self.channel / 'duty_cycle', 0)
            self._write(self.channel / 'enable', 0)
            self._write(self.channel / 'polarity', 'normal')
            self._write(self.channel / 'period', self.period_ns)
            # Keep enabled at 0% to guarantee an inactive/LOW output.
            self._write(self.channel / 'enable', 1)
        except BaseException:
            if self._claimed:
                try:
                    self._write(self.channel / 'duty_cycle', 0)
                except OSError:
                    pass
            os.close(self._lock_fd)
            self._lock_fd = None
            raise

    @staticmethod
    def _write(path, value):
        Path(path).write_text(str(value))

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        if self._closed:
            raise RuntimeError('PWM 장치가 닫혀 있습니다.')
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('PWM duty는 0~1 사이의 유한한 값이어야 합니다.')
        self._write(self.channel / 'duty_cycle', round(self.period_ns * value))
        self._value = value

    def close(self):
        if self._closed:
            return
        try:
            self.value = 0.0
        finally:
            self._closed = True
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None


class HardwarePWMBuzzerService(BuzzerService):
    def open(self):
        if self.available:
            return True
        try:
            self._device = HardwarePWMDevice(self.frequency_hz, self.pin)
            self.available = True
            self.last_error = None
            self.gpiochip_label = 'RP1 PWM0 channel 2'
            self._diag_record('opened', backend='hardware_pwm',
                              pwmchip=str(self._device.chip), channel=2)
            self._diag_flush()
            return True
        except Exception as error:
            self.available = False
            self.last_error = str(error)
            return False

    def get_state(self):
        state = super().get_state()
        state.update(backend='hardware_pwm', pwm_channel=2,
                     pwmchip=str(self._device.chip) if self._device is not None else None)
        return state
