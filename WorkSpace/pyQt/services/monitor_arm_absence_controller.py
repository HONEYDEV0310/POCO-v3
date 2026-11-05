"""Coordinate automatic rest/work moves without competing with normal tracking.

The measurement session owns its working pose; sensor-only calibration records
are deliberately not used as a source of motor angles.
"""

import math

from services.monitor_arm_kinematics import JointCommand


class MonitorArmAbsenceController:
    JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")

    def __init__(self, motor, motor12, motor34, rest_gimbal_targets,
                 reacquire_sec=1.0, timeout_sec=25.0, tolerance_deg=3.0,
                 stable_samples=2):
        self.motor = motor
        self.motor12 = motor12
        self.motor34 = motor34
        self.rest_gimbal_targets = dict(rest_gimbal_targets)
        self.reacquire_sec = max(0.0, float(reacquire_sec))
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.tolerance_deg = float(tolerance_deg)
        self.stable_samples = max(1, int(stable_samples))
        self.events = []
        self.reset()

    def reset(self):
        self.phase = "DISARMED"
        self.reason = "자동 복귀 대기"
        self.working_pose = {}
        self.reference_z_m = None
        self.targets = {}
        self.destination = None
        self.reacquired_at = None
        self.accepted = {"arm": False, "gimbal": False}
        self.attempts = {"arm": 0, "gimbal": 0}
        self.actual = {}
        self.errors = {}
        self.last_error = None
        self.events.clear()

    @property
    def blocks_tracking(self):
        return self.phase not in ("DISARMED", "ARMED")

    def snapshot(self):
        return {
            "state": self.phase, "reason": self.reason,
            "blocks_tracking": self.blocks_tracking,
            "working_pose_deg": dict(self.working_pose),
            "targets_deg": dict(self.targets), "actual_deg": dict(self.actual),
            "errors_deg": dict(self.errors), "attempts": dict(self.attempts),
            "last_error": self.last_error,
        }

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def _event(self, event_type, success):
        self.events.append({"type": event_type, "success": success,
                            "message": self.reason, "automatic_return": self.snapshot()})

    def _fail(self, error):
        self.phase = "FAILED"
        self.last_error = str(error)
        self.reason = "자동 복귀 중단: " + self.last_error
        self._event("MONITOR_ARM_AUTOMATIC_RETURN_FAILED", False)

    def _read_angles(self):
        angles = {joint: float(self.motor.get_joint_angle(joint)) for joint in self.JOINTS}
        if not all(math.isfinite(angle) for angle in angles.values()):
            raise ValueError("모터 현재각이 유효하지 않습니다.")
        return angles

    def _capture_working_pose(self):
        if not (self.motor12.ready and self.motor34.ready):
            raise RuntimeError("모터 1~4 연결을 확인한 뒤 측정을 다시 시작해 주세요.")
        angles = self._read_angles()
        for joint, angle in angles.items():
            limits = self.motor.get_safe_angle_range(joint)
            if limits is None or not float(limits[0]) <= angle <= float(limits[1]):
                raise ValueError(f"작업자세 {joint}가 보정된 범위 밖입니다.")
        planner = self.motor12.planner
        position = planner.kinematics.forward(
            JointCommand(angles["shoulder_lift"], angles["elbow_flex"]))
        if not planner.working_z_min_m <= position.z_m <= planner.working_z_max_m:
            raise ValueError("작업 높이를 확인한 뒤 측정을 다시 시작해 주세요.")
        self.working_pose = angles
        self.reference_z_m = planner.reference_z_m
        self.phase = "ARMED"
        self.reason = "측정 시작 작업자세 저장 완료"

    def _start_move(self, destination, now):
        self.destination = destination
        if destination == "REST":
            rest = self.motor12.rest_command
            self.targets = dict(self.rest_gimbal_targets,
                                shoulder_lift=float(rest.shoulder_lift_deg),
                                elbow_flex=float(rest.elbow_flex_deg))
        else:
            self.targets = dict(self.working_pose)
        if set(self.targets) != set(self.JOINTS) or not all(
                math.isfinite(float(value)) for value in self.targets.values()):
            raise ValueError("복귀 목표각이 유효하지 않습니다.")
        self.phase = "MOVING_TO_" + destination
        self.reason = "휴식자세로 이동 중" if destination == "REST" else "작업자세로 복귀 중"
        self.accepted = {"arm": False, "gimbal": False}
        self.attempts = {"arm": 0, "gimbal": 0}
        self.started_at = now
        self.next_attempt_at = now
        self.next_poll_at = now
        self.arrival_samples = 0
        self.reacquired_at = None
        self.last_error = None
        self._event("MONITOR_ARM_AUTOMATIC_RETURN_STARTED", True)

    def retry(self, now):
        """Only an explicit UI retry can restart a timed-out movement."""
        if self.phase != "FAILED":
            return
        if not self.working_pose:
            self.reset()
            return
        try:
            self._start_move(self.destination, now)
        except Exception as error:
            self._fail(error)

    def _move_tick(self, now):
        if now - self.started_at >= self.timeout_sec:
            self._fail("목표각 도착 확인 시간 초과. 장치를 확인해 주세요.")
            return
        if now >= self.next_attempt_at and not all(self.accepted.values()):
            self.next_attempt_at = now + 1.0
            for group in ("arm", "gimbal"):
                if self.accepted[group]:
                    continue
                self.attempts[group] += 1
                try:
                    if group == "gimbal":
                        result = self.motor34.move_to_neutral(self.targets)
                    elif self.destination == "REST":
                        result = self.motor12.move_to_rest()
                    else:
                        result = self.motor12.move_to_working_smooth(JointCommand(
                            self.targets["shoulder_lift"], self.targets["elbow_flex"]))
                    self.accepted[group] = bool(result.get("accepted"))
                    if not self.accepted[group]:
                        self.last_error = result.get("error") or f"{group} 이동 명령 거부"
                except Exception as error:
                    self.last_error = str(error)
                if not self.accepted[group] and self.attempts[group] >= 3:
                    self._fail(self.last_error)
                    return
        if not all(self.accepted.values()) or now < self.next_poll_at:
            return
        self.next_poll_at = now + 0.2
        try:
            self.actual = self._read_angles()
            self.errors = {joint: abs(self.actual[joint] - self.targets[joint])
                           for joint in self.JOINTS}
            reached = all(error <= self.tolerance_deg for error in self.errors.values())
            self.arrival_samples = self.arrival_samples + 1 if reached else 0
        except Exception as error:
            self.last_error = str(error)
            self.arrival_samples = 0
        if self.arrival_samples < self.stable_samples:
            return
        self.last_error = None
        if self.destination == "REST":
            self.phase = "RESTING"
            self.reason = "휴식자세 도착 · 사용자 복귀 대기"
        else:
            self.motor12.planner.reference_z_m = self.reference_z_m
            self.phase = "ARMED"
            self.reason = "작업자세 복귀 완료"
        self._event("MONITOR_ARM_AUTOMATIC_RETURN_COMPLETED", True)

    def update(self, enabled, absence_requested, presence_valid, now):
        if not enabled:
            if self.phase != "DISARMED":
                self.reset()
            return self.snapshot()
        try:
            if self.phase == "DISARMED":
                self._capture_working_pose()
            if self.phase == "ARMED" and absence_requested:
                self._start_move("REST", now)
            elif self.phase == "RESTING":
                if not presence_valid:
                    self.reacquired_at = None
                else:
                    if self.reacquired_at is None:
                        self.reacquired_at = now
                    if now - self.reacquired_at >= self.reacquire_sec:
                        self._start_move("WORK", now)
            if self.phase in ("MOVING_TO_REST", "MOVING_TO_WORK"):
                self._move_tick(now)
        except Exception as error:
            self._fail(error)
        return self.snapshot()
