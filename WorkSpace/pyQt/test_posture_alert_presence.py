"""Run the hardware-process alert block with real services and fake PWM."""
import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.buzzer_service import BuzzerService
from services.posture_alert_service import PostureAlertService


class PostureAlertPresenceTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).parent / "processes/hardware_process.py").read_text())
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_pose_state_is_fresh")
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.While))
        first = next(i for i, node in enumerate(loop.body)
                     if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                     and node.targets[0].id == "measuring_for_alert")
        last = next(i for i in range(first, len(loop.body))
                    if isinstance(loop.body[i], ast.Assign)
                    and isinstance(loop.body[i].targets[0], ast.Subscript)
                    and isinstance(loop.body[i].targets[0].slice, ast.Constant)
                    and loop.body[i].targets[0].slice.value == "alert_suppressed")
        self.code = compile(ast.Module(body=loop.body[first:last + 1], type_ignores=[]),
                            "posture_alert_integration", "exec")
        self.pwm = SimpleNamespace(value=0.0)
        self.buzzer = BuzzerService()
        self.buzzer._diag_enabled = False  # No GPIO or diagnostic file writes.
        self.buzzer._device = self.pwm
        self.buzzer.available = True
        self.alert = PostureAlertService(posture_hold_seconds=5)
        self.scope = {
            "time": time, "enable_pose": True, "main_mode": "MEASURING",
            "measurement_stopping": False, "posture_alert": self.alert,
            "posture_alert_count": 5, "buzzer": self.buzzer,
            "last_alert_pose_frame_id": None,
            "safety_supervisor": SimpleNamespace(posture_stale_sec=1.0),
            "latest_safety_state": {"tracking_allowed": False},
        }
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "pose_freshness", "exec"), self.scope)
        self.frame = 0

    def tick(self, now, person=True, tof=True, index=2, frame=None, frame_time=None):
        self.frame = self.frame + 1 if frame is None else frame
        self.scope.update(
            now=now, tof_presence_valid=tof, latest_pose_landmark_valid=person,
            latest_pose_frame_id=self.frame,
            latest_pose_state={
                "frame_id": self.frame,
                "timestamp_ns": int((now if frame_time is None else frame_time) * 1e9),
                "inference": {"pose_index": index},
            },
        )
        with patch("time.monotonic", return_value=now), patch(
                "time.perf_counter_ns", return_value=int(now * 1e9)):
            exec(self.code, self.scope)
        return self.scope["latest_buzzer_state"]

    def start_alert(self):
        self.tick(10)
        self.tick(15)
        self.assertGreater(self.pwm.value, 0)

    def test_absent_bad_posture_never_sounds(self):
        for now in (10, 15, 20, 30):
            state = self.tick(now, person=False)
            self.assertTrue(state["alert_suppressed"])
            self.assertFalse(state["busy"])
        self.assertIsNone(self.alert.bad_start_time)
        self.assertEqual(self.pwm.value, 0)

    def test_present_bad_posture_still_sounds_with_tracking_blocked(self):
        self.start_alert()
        self.assertEqual(self.buzzer.get_state()["active_command"], "ForwardHead")
        self.assertFalse(self.scope["latest_buzzer_state"]["alert_suppressed"])

    def test_detection_loss_cancels_current_and_queued_alerts_immediately(self):
        self.start_alert()
        self.buzzer.play_command("StrongAlert", 5)
        self.assertGreater(self.buzzer.get_state()["pending_count"], 0)
        state = self.tick(15.01, person=False)
        self.assertEqual(self.pwm.value, 0)
        self.assertFalse(state["busy"])
        self.assertEqual(state["pending_count"], 0)
        self.assertFalse(self.tick(16, person=False)["busy"])

    def test_return_restarts_hold_time_and_repeat_counts(self):
        self.start_alert()
        self.tick(15.1, person=False)
        self.assertFalse(self.alert.continuous_alert_counts)
        self.tick(30)
        self.assertEqual(self.pwm.value, 0)
        self.tick(34.9)
        self.assertEqual(self.pwm.value, 0)
        self.tick(35)
        self.assertGreater(self.pwm.value, 0)
        self.assertEqual(self.alert.continuous_alert_counts["ForwardHead"], 1)

    def test_short_detection_loss_also_resets_unfinished_hold_time(self):
        self.tick(10)
        self.tick(14.9)
        self.tick(14.91, person=False)
        self.tick(15)
        self.assertEqual(self.pwm.value, 0)
        self.tick(19.9)
        self.assertEqual(self.pwm.value, 0)
        self.tick(20)
        self.assertGreater(self.pwm.value, 0)

    def test_invalid_tof_silences_existing_alert(self):
        self.start_alert()
        self.assertTrue(self.tick(15.01, tof=False)["alert_suppressed"])
        self.assertEqual(self.pwm.value, 0)

    def test_stalled_camera_cancels_even_without_new_frame(self):
        self.start_alert()
        state = self.tick(16.01, frame=self.frame, frame_time=15)
        self.assertTrue(state["alert_suppressed"])
        self.assertFalse(state["busy"])
        self.assertEqual(self.pwm.value, 0)
        self.assertIsNone(self.alert.bad_start_time)

    def test_strong_alert_is_cancelled_without_clearing_cooldown(self):
        for now in (10, 15, 20, 25):
            self.tick(now)
        cooldowns = dict(self.alert.cooldown_until)
        self.assertTrue(cooldowns)
        self.tick(25.01, person=False)
        self.assertFalse(self.buzzer.get_state()["busy"])
        self.assertEqual(self.alert.cooldown_until, cooldowns)

    def test_measurement_stop_immediately_cancels_alert(self):
        self.start_alert()
        self.scope["measurement_stopping"] = True
        self.assertFalse(self.tick(15.01)["busy"])
        self.assertEqual(self.pwm.value, 0)

    def test_invalid_pose_timestamp_is_not_presence(self):
        fresh = self.scope["_pose_state_is_fresh"]
        for state in (None, {}, {"timestamp_ns": None}, {"timestamp_ns": float("nan")}):
            self.assertFalse(fresh(state, 1.0))


if __name__ == "__main__":
    unittest.main()
