"""Exercise real controllers/planner with a fake bus; never open hardware."""
import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from services.monitor_arm_absence_controller import MonitorArmAbsenceController
from services.monitor_arm_distance_setup import calibrated_limits
from services.monitor_arm_safety_supervisor import MonitorArmSafetySupervisor
from services.motor12_controller import Motor12Controller
from services.motor34_controller import Motor34Controller


ROOT = Path(__file__).resolve().parent
SETTINGS = json.loads((ROOT.parent / "config/monitor_arm_settings.json").read_text())
CALIBRATION = json.loads((ROOT.parent / "hardware/servo_calibration_result.json").read_text())
RANGES = {
    servo["joint"]: tuple(sorted(
        (servo[key] - servo["zero_position"]) * servo["direction"] * (-360 / 4096)
        for key in ("safe_position_at_min_angle", "safe_position_at_max_angle")))
    for servo in CALIBRATION["servos"].values()
}


class FakeBus:
    available = True
    last_error = "simulated write failure"

    def __init__(self):
        self.angles = {joint: SETTINGS["postures"]["working_start"][joint + "_deg"]
                       for joint in MonitorArmAbsenceController.JOINTS}
        self.goals = dict(self.angles)
        self.commands = []
        self.failures = {"arm": 0, "gimbal": 0}

    def get_joint_angle(self, joint):
        return self.angles[joint]

    def get_safe_angle_range(self, joint):
        return RANGES[joint]

    def move_joints(self, targets, **kwargs):
        group = "arm" if "shoulder_lift" in targets else "gimbal"
        self.commands.append((group, dict(targets)))
        if self.failures[group]:
            self.failures[group] -= 1
            return False
        self.goals.update(targets)
        return True

    move_joints_special = move_joints

    def arrive(self):
        self.angles.update(self.goals)


class AutomaticReturnTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.arm = Motor12Controller(self.bus, SETTINGS)
        self.gimbal = Motor34Controller(self.bus)
        self.arm.available = self.gimbal.available = True
        for axis in (self.arm.motor1, self.arm.motor2, self.gimbal.motor3, self.gimbal.motor4):
            axis.ready = True
            axis.max_speed = 1000
            axis.safe_min_deg, axis.safe_max_deg = RANGES[axis.joint]
        self.arm.planner.limits = calibrated_limits(self.arm.planner.limits, RANGES)
        self.arm.planner.reference_z_m = .271
        self.rest = {joint: SETTINGS["postures"]["rest"][joint + "_deg"]
                     for joint in ("wrist_flex", "wrist_roll")}
        self.controller = MonitorArmAbsenceController(self.bus, self.arm, self.gimbal, self.rest)
        self.safety = MonitorArmSafetySupervisor()
        self.original = dict(self.bus.angles)

        # Execute the actual integration block, without importing hardware_process.
        tree = ast.parse((ROOT / "processes/hardware_process.py").read_text())
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.While))
        first = next(i for i, node in enumerate(loop.body)
                     if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                     and node.targets[0].id == "automatic_return_enabled")
        last = next(i for i in range(first, len(loop.body))
                    if isinstance(loop.body[i], ast.For)
                    and isinstance(loop.body[i].target, ast.Name)
                    and loop.body[i].target.id == "return_event")
        self.integration = compile(ast.Module(body=loop.body[first:last + 1], type_ignores=[]),
                                   "hardware_absence_integration", "exec")
        gates = [node for node in loop.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in ("motor12_requested", "motor34_requested")]
        self.gates = compile(ast.Module(body=gates, type_ignores=[]), "motor_gates", "exec")
        self.events = []

    def tick(self, now, present=True, tof=True, enabled=True, posture="Optimal", stopping=False):
        scope = {
            "main_mode": "MEASURING" if enabled else "IDLE", "stop_rest_pending": False,
            "measurement_stopping": stopping,
            "monitor_arm_preparation": SimpleNamespace(active=False), "profile_apply_pending": False,
            "safety_supervisor": self.safety, "absence_controller": self.controller,
            "tof_presence_valid": tof, "latest_pose_landmark_valid": present,
            "pose_inference": {"posture_type": posture, "confidence": .95}, "now": now,
            "hw_to_main_event_queue": None,
            "_put_hardware_event": lambda queue, kind, **event: self.events.append(dict(event, type=kind)),
        }
        exec(self.integration, scope)
        scope.update(preparation_active=False, preparation_recovery=False,
                     latest_monitor_arm_input_state={"valid": True})
        exec(self.gates, scope)
        if self.controller.blocks_tracking:
            self.assertFalse(scope["motor12_requested"])
            self.assertFalse(scope["motor34_requested"])
        return scope["latest_safety_state"]

    def enter_rest(self):
        self.tick(0)
        self.tick(1, present=False)
        self.tick(6, present=False)
        self.bus.arrive()
        self.tick(6.3, present=False)
        self.tick(6.6, present=False)
        self.assertEqual(self.controller.phase, "RESTING")

    def test_session_pose_survives_sensor_only_calibration_and_returns_all_four(self):
        self.enter_rest()
        self.assertEqual(self.bus.goals["shoulder_lift"], self.arm.rest_command.shoulder_lift_deg)
        self.assertTrue(self.arm.rest_mode)
        self.assertFalse(self.tick(7)["tracking_allowed"])
        self.assertFalse(self.tick(8.1)["tracking_allowed"])
        self.assertEqual(self.controller.phase, "MOVING_TO_WORK")
        self.assertEqual(self.bus.goals, self.original)
        self.assertFalse(self.arm.rest_mode)
        self.bus.arrive()
        self.assertFalse(self.tick(8.4)["tracking_allowed"])
        self.assertTrue(self.tick(8.7)["tracking_allowed"])
        self.assertEqual(self.arm.planner.reference_z_m, .271)
        self.assertEqual(len(self.bus.commands), 4)

    def test_absence_grace_and_partial_return_do_not_start_a_move(self):
        self.tick(0)
        self.tick(1, present=False)
        self.tick(5.9, present=False)
        self.assertFalse(self.bus.commands)
        self.tick(6)
        self.tick(10)
        self.assertFalse(self.bus.commands)

    def test_early_reappearance_waits_for_rest_arrival(self):
        self.tick(0)
        self.tick(1, present=False)
        self.tick(6, present=False)
        self.tick(7)
        self.tick(9)
        self.assertEqual(len(self.bus.commands), 2)
        self.assertFalse(self.tick(10)["tracking_allowed"])
        self.bus.arrive()
        self.tick(10.3)
        self.tick(10.6)
        self.assertEqual(self.controller.phase, "RESTING")
        self.tick(11)
        self.tick(12.1)
        self.assertEqual(self.controller.phase, "MOVING_TO_WORK")

    def test_bad_posture_allows_work_return_but_keeps_tracking_held(self):
        self.enter_rest()
        self.tick(7, posture="Forward Head")
        self.tick(8.1, posture="Forward Head")
        self.bus.arrive()
        self.tick(8.4, posture="Forward Head")
        state = self.tick(8.7, posture="Forward Head")
        self.assertEqual(self.controller.phase, "ARMED")
        self.assertFalse(state["tracking_allowed"])

    def test_reacquisition_must_be_continuous(self):
        self.enter_rest()
        self.tick(7)
        self.tick(7.5, present=False)
        self.tick(8)
        self.tick(8.5)
        self.assertEqual(self.controller.phase, "RESTING")
        self.tick(9.1)
        self.assertEqual(self.controller.phase, "MOVING_TO_WORK")

    def test_partial_write_failure_retries_only_failed_group(self):
        self.bus.failures["gimbal"] = 1
        self.tick(0)
        self.tick(1, present=False)
        self.tick(6, present=False)
        self.tick(6.5, present=False)
        self.assertEqual(len(self.bus.commands), 2)
        self.tick(7.1, present=False)
        self.assertEqual([group for group, _ in self.bus.commands], ["arm", "gimbal", "gimbal"])
        self.bus.arrive()
        self.tick(7.4, present=False)
        self.tick(7.7, present=False)
        self.assertEqual(self.controller.phase, "RESTING")

    def test_repeated_failure_blocks_tracking_and_emits_one_error(self):
        self.bus.failures["arm"] = 10
        self.tick(0)
        self.tick(1, present=False)
        for now in (6, 7.1, 8.2, 9.3, 10.4):
            self.tick(now, present=False)
        self.assertEqual(self.controller.phase, "FAILED")
        self.assertEqual(self.controller.attempts["arm"], 3)
        self.assertFalse(self.tick(11)["tracking_allowed"])
        self.assertEqual(sum(e["type"].endswith("FAILED") for e in self.events), 1)
        self.bus.failures["arm"] = 0
        self.controller.retry(12)
        self.tick(12)
        self.bus.arrive()
        self.tick(12.3)
        self.tick(12.6)
        self.assertEqual(self.controller.phase, "RESTING")

    def test_timeout_is_not_success_or_automatic_retry(self):
        self.tick(0)
        self.tick(1, present=False)
        self.tick(6, present=False)
        self.tick(31.1, present=False)
        self.assertEqual(self.controller.phase, "FAILED")
        for now in (32, 34, 40):
            self.assertFalse(self.tick(now)["tracking_allowed"])
        self.assertEqual(len(self.bus.commands), 2)

    def test_invalid_feedback_never_counts_as_arrival(self):
        self.tick(0)
        self.tick(1, present=False)
        self.tick(6, present=False)
        self.bus.arrive()
        self.bus.angles["wrist_roll"] = float("nan")
        self.tick(6.3, present=False)
        self.tick(6.6, present=False)
        self.assertEqual(self.controller.phase, "MOVING_TO_REST")

    def test_stop_disarms_without_issuing_more_commands(self):
        self.enter_rest()
        count = len(self.bus.commands)
        self.tick(7, enabled=False)
        self.tick(10, enabled=False)
        self.assertEqual(self.controller.phase, "DISARMED")
        self.assertEqual(len(self.bus.commands), count)

    def test_invalid_initial_pose_fails_without_motion(self):
        self.bus.angles["shoulder_lift"] = 999
        state = self.tick(0)
        self.assertEqual(self.controller.phase, "FAILED")
        self.assertFalse(state["tracking_allowed"])
        self.assertFalse(self.bus.commands)

    def test_stop_ack_wait_does_not_recapture_the_rest_pose(self):
        self.enter_rest()
        self.tick(7, stopping=True)
        self.tick(8, stopping=True)
        self.assertEqual(self.controller.phase, "DISARMED")
        self.assertFalse(any(event["type"].endswith("FAILED") for event in self.events))

    def test_tof_missing_keeps_existing_absence_policy(self):
        self.tick(0)
        self.tick(1, tof=False)
        self.tick(6, tof=False)
        self.assertEqual(self.controller.phase, "MOVING_TO_REST")


class ReturnUiTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / "mainpyQt.py").read_text())
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                   and node.name in ("on_hardware_changed", "on_hardware_event_changed")]
        self.messages, self.commands, self.warnings = [], [], []
        box = SimpleNamespace(Retry=1, Close=2)
        box.warning = lambda *args: self.warnings.append(args) or box.Retry
        self.scope = {"QMessageBox": box}
        exec(compile(ast.Module(body=methods, type_ignores=[]), "ui_return", "exec"), self.scope)
        self.window = SimpleNamespace(
            _camera_shutdown_in_progress=False, _app_closing=False,
            monitor_arm_preparation_dialog=None, _last_safety_message=None,
            hardware_state_store=SimpleNamespace(update=lambda state: None),
            update_sensor_monitor=lambda state: None,
            set_status=self.messages.append, send_hardware_command=self.commands.append,
        )

    def test_return_reason_takes_priority_over_missing_sensor_alert(self):
        self.scope["on_hardware_changed"](self.window, {
            "main_mode": "MEASURING", "monitor_arm": {"safety": {
                "tof_alert": "ToF missing", "landmark_alert": "Pose missing",
                "automatic_return": {"blocks_tracking": True, "reason": "휴식자세로 이동 중"},
            }}})
        self.assertEqual(self.messages, ["휴식자세로 이동 중"])

    def test_failure_is_visible_and_retry_sends_explicit_command(self):
        self.scope["on_hardware_event_changed"](self.window, {
            "type": "MONITOR_ARM_AUTOMATIC_RETURN_FAILED", "success": False,
            "message": "목표각 도착 확인 시간 초과",
        })
        self.assertEqual(len(self.warnings), 1)
        self.assertEqual(self.commands, [{"type": "MONITOR_ARM_AUTOMATIC_RETURN_RETRY"}])


if __name__ == "__main__":
    unittest.main()
