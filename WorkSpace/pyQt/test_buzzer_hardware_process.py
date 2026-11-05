import ast
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from services.buzzer_service import BuzzerService
from services.buzzer_process_service import BuzzerProcessService
from services.hardware_pwm_buzzer import HardwarePWMDevice, find_buzzer_pwm


class FakePWM:
    def __init__(self):
        self._value = 0

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
        with open(os.environ['POCO_TEST_PWM_LOG'], 'a') as output:
            output.write(json.dumps({'time': time.monotonic(), 'value': value,
                                     'pid': os.getpid()}) + '\n')

    def close(self):
        self.value = 0


class FakeBuzzer(BuzzerService):
    def open(self):
        self._device = FakePWM()
        self._diag_enabled = False
        self.available = True
        return True


class UnavailableBuzzer(FakeBuzzer):
    def open(self):
        self.last_error = 'PWM unavailable (test)'
        return False


def abruptly_exit_parent():
    service = BuzzerProcessService(service_factory=FakeBuzzer)
    assert service.open()
    service.play_command('StrongAlert', 5)
    deadline = time.monotonic() + 2
    while not service.update().get('active') and time.monotonic() < deadline:
        time.sleep(.005)
    os._exit(0)


class HardwarePWMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.chip = self.root / 'pwmchip7'
        self.chip.mkdir()
        node = self.root / 'rp1/pwm@98000'
        node.mkdir(parents=True)
        (self.chip / 'device').mkdir()
        (self.chip / 'device/of_node').symlink_to(node)
        (self.chip / 'npwm').write_text('4')
        self.channel = self.chip / 'pwm2'
        self.channel.mkdir()
        for name, value in dict(enable='0', duty_cycle='0', period='0', polarity='normal').items():
            (self.channel / name).write_text(value)

    def open(self):
        return HardwarePWMDevice(sysfs_root=self.root, lock_dir=self.root)

    def test_gpio18_channel2_2khz_and_silent_close(self):
        device = self.open()
        self.assertEqual((self.channel/'period').read_text(), '500000')
        device.value = .5
        self.assertEqual((self.channel/'duty_cycle').read_text(), '250000')
        device.close()
        self.assertEqual((self.channel/'duty_cycle').read_text(), '0')
        self.assertEqual((self.channel/'enable').read_text(), '1')

    def test_fan_controller_is_never_selected(self):
        (self.chip/'device/of_node').unlink()
        fan = self.root/'rp1/pwm@9c000'
        fan.mkdir()
        (self.chip/'device/of_node').symlink_to(fan)
        with self.assertRaises(RuntimeError):
            find_buzzer_pwm(self.root)

    def test_another_owner_is_not_interrupted(self):
        first = self.open()
        try:
            first.value = .5
            with self.assertRaises(BlockingIOError):
                self.open()
            self.assertEqual((self.channel/'duty_cycle').read_text(), '250000')
        finally:
            first.close()

    def test_unowned_active_output_is_not_overwritten(self):
        (self.channel/'enable').write_text('1')
        (self.channel/'duty_cycle').write_text('12345')
        with self.assertRaises(RuntimeError):
            self.open()
        self.assertEqual((self.channel/'duty_cycle').read_text(), '12345')

    def test_invalid_duty_is_rejected(self):
        device = self.open()
        try:
            for value in (float('nan'), float('inf'), -1, 2):
                with self.assertRaises(ValueError):
                    device.value = value
        finally:
            device.close()


class BuzzerProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name)/'pwm.jsonl'
        self.log.touch()
        self.env = patch.dict(os.environ, {'POCO_TEST_PWM_LOG': str(self.log),
                                          'POCO_BUZZER_DIAGNOSTICS': '0'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.service = BuzzerProcessService(service_factory=FakeBuzzer)
        self.addCleanup(self.service.close)
        self.assertTrue(self.service.open())

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.service.update()
            if predicate(state):
                return state
            time.sleep(.005)
        self.fail('Buzzer worker timed out: ' + str(self.service.get_state()))

    def test_beep_deadline_runs_while_parent_is_blocked(self):
        self.service.play_command('ForwardHead', 1)
        self.wait_for(lambda state: state.get('active'))
        # No update() calls during a simulated blocking sensor/motor operation.
        time.sleep(.55)
        records = self.records()
        on = next(row for row in records if row['value'] > 0)
        off = next(row for row in records if row['value'] == 0 and row['time'] > on['time'])
        self.assertNotEqual(on['pid'], os.getpid())
        self.assertGreater(off['time'] - on['time'], .17)
        self.assertLess(off['time'] - on['time'], .28)
        self.assertFalse(self.service.update().get('busy'))

    def test_stop_cancels_queued_patterns_without_replaying_them(self):
        self.service.play_command('ForwardHead', 5)
        self.wait_for(lambda state: state.get('active'))
        for _ in range(8):
            self.service.play_command('StrongAlert', 5)
        stopped_at = time.monotonic()
        self.service.stop(clear_pending=True)
        self.wait_for(lambda state: not state.get('busy'))
        time.sleep(.25)
        after = [r for r in self.records() if r['time'] >= stopped_at + .04]
        self.assertFalse(any(row['value'] > 0 for row in after))
        self.assertEqual(self.records()[-1]['value'], 0)

    def test_new_alert_after_stop_still_runs(self):
        self.service.play_command('ForwardHead', 5)
        self.service.stop()
        self.service.play_command('Asymmetric', 1)
        state = self.wait_for(lambda state: state.get('active_command') == 'Asymmetric')
        self.assertTrue(state['busy'])

    def test_close_silences_and_reaps_worker(self):
        self.service.play_command('StrongAlert', 5)
        self.wait_for(lambda state: state.get('active'))
        process = self.service._process
        self.service.close()
        self.assertFalse(process.is_alive())
        self.assertEqual(self.records()[-1]['value'], 0)

    def test_heartbeat_loss_silences_pending_output(self):
        self.service.close()
        self.service = BuzzerProcessService(service_factory=FakeBuzzer, heartbeat_timeout=.15)
        self.addCleanup(self.service.close)
        self.assertTrue(self.service.open())
        self.service.play_command('ForwardHead', 5)
        self.wait_for(lambda state: state.get('active'))
        time.sleep(.4)
        self.assertEqual(self.records()[-1]['value'], 0)
        self.assertFalse(self.service.get_state().get('busy'))

    def test_failed_pwm_open_has_no_software_fallback(self):
        service = BuzzerProcessService(service_factory=UnavailableBuzzer)
        try:
            self.assertFalse(service.open())
            self.assertIn('PWM unavailable', service.last_error)
            self.assertFalse(service.play_command('ForwardHead', 1))
        finally:
            service.close()

    def test_parent_exit_silences_orphan_worker(self):
        import subprocess
        import sys
        # Isolate the intentional hard exit and its resource tracker cleanup.
        actor = subprocess.run([sys.executable, str(Path(__file__)), '--abrupt-parent'],
                               capture_output=True, text=True, timeout=6)
        self.assertEqual(actor.returncode, 0, actor.stderr)
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            rows = self.records()
            if any(r['value'] > 0 for r in rows) and rows[-1]['value'] == 0:
                break
            time.sleep(.01)
        self.assertEqual(self.records()[-1]['value'], 0)


class VersionMappingTests(unittest.TestCase):
    def test_v5_uses_v3_control_and_v6_uses_v4_control(self):
        source = Path(__file__).parent/'mainpyQt_v3.py'
        tree = ast.parse(source.read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        setup = compile(ast.Module(body=main.body[:2], type_ignores=[]), str(source), 'exec')
        with patch.dict(os.environ):
            for variant, control in (('v5', 'v3'), ('v6', 'v4')):
                exec(setup, {'os': os, 'variant': variant})
                self.assertEqual(os.environ['POCO_CONTROL_VARIANT'], control)
                self.assertEqual(os.environ['POCO_APP_VARIANT'], variant)


if __name__ == '__main__':
    import sys
    if '--abrupt-parent' in sys.argv:
        abruptly_exit_parent()
    else:
        unittest.main()
