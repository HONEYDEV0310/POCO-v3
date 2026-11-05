"""Presentation-only replacements; command and validation methods are inherited."""
import math
import time
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget,
    QDoubleSpinBox, QSpinBox, QSlider, QProgressBar, QLineEdit, QDialog,
    QScrollArea, QMessageBox,
)
from monitor_arm_preparation_dialog import MonitorArmPreparationDialog, ArmPreparationCanvas
from user_profile_dialog import UserProfileDialog
from .theme import STYLE, label, button, card, role, close_button


class PreparationDialogV3(MonitorArmPreparationDialog):
    def __init__(self, *args, **kwargs):
        parent = kwargs.get("parent")
        self._show_main_calibration = getattr(parent, "show_main_calibration", None)
        self._combined_capture_start = getattr(
            parent, "start_combined_preparation_capture", None
        )
        self._combined_capture_cancel = getattr(
            parent, "cancel_combined_preparation_capture", None
        )
        self._combined_capture_state = getattr(
            parent, "combined_preparation_capture_state", None
        )
        self._distance_bounds = (3.0, 200.0)
        self._distance_revision = None
        self._distance_request_pending = False
        self._distance_request_at = 0.0
        self._distance_feedback = ''
        self._v3_ready = False
        self._startup_cancelled = False
        self._finish_requested = False
        self._auto_connect_attempts = 0
        self._auto_connect_acknowledged = False
        self._auto_rest_send_attempts = 0
        self._auto_rest_command_sent = False
        self._auto_rest_acknowledged = False
        self._auto_rest_complete = False
        self._startup_error = ""
        super().__init__(*args, **kwargs)
        self.setStyleSheet(STYLE)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumSize(800, 480)
        self.setMaximumSize(16777215, 16777215)
        self.resize(800, 480)
        self._auto_connect_watchdog = QTimer(self)
        self._auto_connect_watchdog.setSingleShot(True)
        self._auto_connect_watchdog.timeout.connect(self._auto_connect_timed_out)
        self._auto_rest_watchdog = QTimer(self)
        self._auto_rest_watchdog.setSingleShot(True)
        self._auto_rest_watchdog.timeout.connect(self._auto_rest_timed_out)
        self._v3_ready = True
        self.refresh_state()
        QTimer.singleShot(250, self._start_auto_connection_check)

    def show(self):
        # Every route (initial setup, manual adjustment and camera-back) fills
        # the Pi display. There is no fixed-size modal floating over the camera.
        self.showFullScreen()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(label("화면 위치 조절" if self.manual_only else "나에게 맞는 화면 준비", "heading"))
        header.addStretch(1)
        self.x_button = close_button(self.close)
        header.addWidget(self.x_button)
        outer.addLayout(header)
        steps = QHBoxLayout()
        self.step_buttons = []
        names = ("1  시작 준비", "2  화면 위치")
        for idx, text in enumerate(names):
            item = button(text, lambda _=False, n=idx: self._go_step(n), "step", 30)
            item.setCheckable(True)
            self.step_buttons.append(item)
            steps.addWidget(item)
        outer.addLayout(steps)
        self.steps = QStackedWidget()
        self.steps.addWidget(self._build_connection())
        self.steps.addWidget(self._build_position())
        # Retain the shared capture controls as the workflow controller, while
        # displaying calibration feedback beside the camera in the main window.
        self._capture_panel = self._build_capture()
        self._capture_panel.setParent(self)
        self._capture_panel.hide()
        outer.addWidget(self.steps, 1)
        self.status_label = label("모터 연결을 자동으로 확인하고 있어요.", "muted", True)
        # Original handler writes all diagnostics here; show them in Details instead
        # of letting fast telemetry overwrite the current instruction.
        self.status_label.hide()
        self.step_hint = label("", "muted")
        outer.addWidget(self.step_hint)
        bottom = QHBoxLayout()
        self.tof_live_label = label("ToF  ·  측정 대기", "badge")
        self.tof_live_label.setMinimumWidth(142)
        self.tof_live_label.setToolTip("실시간 ToF 거리 센서값")
        bottom.addWidget(self.tof_live_label)
        self.cancel_button = button("닫기" if self.manual_only else "준비 취소", self.cancel_preparation, "subtle", 36)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(button("장치 상세", self._show_details, "subtle", 36))
        bottom.addStretch(1)
        self.back_button = button("이전", lambda: self._go_step(self.steps.currentIndex()-1), "subtle", 36)
        bottom.addWidget(self.back_button)
        self.next_button = button("위치 조절로", self._advance_step, "primary", 36)
        bottom.addWidget(self.next_button)
        self.finish_button = button("기준 저장 완료 · 계속", self._finish_combined_preparation, "primary", 36)
        self.finish_button.setEnabled(False)
        bottom.addWidget(self.finish_button)
        self.finish_button.hide()
        outer.addLayout(bottom)
        self._update_user_label()
        self._go_step(0)

    def _build_connection(self):
        panel, col = card(18)
        col.addWidget(label("모터 연결과 휴식 자세를 먼저 준비해요", "section"))
        col.addWidget(label(
            "앱을 켜면 모터 1~4 연결을 확인하고 저장된 휴식 자세로 이동합니다.\n"
            "필요하면 ‘휴식 자세로 이동’을 다시 누르고, 다음 버튼으로 작업 시작 위치에 보내세요.",
            "muted", True,
        ))
        actions = QHBoxLayout()
        # Keep the legacy widget for the shared refresh_state handler, but remove
        # the manual connection action from the user flow.
        self.connect_button = button("연결 상태 확인", lambda: None, "primary", 44)
        col.addWidget(self.connect_button)
        self.connect_button.hide()
        self.rest_button = button("휴식 자세로 이동", self.request_rest_pose, "subtle", 44)
        self.rest_button.setEnabled(False)
        actions.addWidget(self.rest_button)
        self.working_button = button("작업 시작 위치로 이동", lambda: self._send({"type": "MONITOR_ARM_MOVE_WORKING_START"}), "", 44)
        self.working_button.setEnabled(False)
        actions.addWidget(self.working_button)
        col.addLayout(actions)
        self.connection_summary = label("모터 연결을 자동으로 확인하고 있어요.", "notice", True)
        col.addWidget(self.connection_summary)
        self.connection_label = label("")
        self.connection_label.hide()
        col.addStretch(1)
        return panel

    def _start_auto_connection_check(self):
        if self._startup_cancelled or self._startup_error or self._auto_connect_acknowledged:
            return
        self._auto_connect_attempts += 1
        if self._send({"type": "MONITOR_ARM_CONNECT_ALL"}):
            print('[POCO startup] Checking motors 1-4.', flush=True)
            self.connection_summary.setText("모터 1~4 연결 상태를 확인하고 있어요.")
            self.status_label.setText("모터 연결 상태를 자동으로 확인하고 있어요.")
            self._auto_connect_watchdog.start(30000)
            return
        if self._auto_connect_attempts < 60:
            QTimer.singleShot(500, self._start_auto_connection_check)
            return
        self._set_startup_error(
            "하드웨어 연결을 시작하지 못했어요. USB 어댑터와 모터 전원을 확인한 뒤 다시 실행해 주세요."
        )

    def _auto_connect_timed_out(self):
        if not self._startup_cancelled and not self._auto_connect_acknowledged:
            self._set_startup_error(
                "모터 연결 응답이 없어요. USB 어댑터와 모터 전원을 확인한 뒤 다시 실행해 주세요."
            )

    def _request_auto_rest(self):
        if self._startup_cancelled or self._auto_rest_command_sent or self._startup_error:
            return
        if not self._auto_connect_acknowledged or self.manual_only:
            return
        self._auto_rest_send_attempts += 1
        if self._send({"type": "MONITOR_ARM_MOVE_REST", "confirmed": True}):
            self._auto_rest_command_sent = True
            print('[POCO startup] Saved rest pose requested.', flush=True)
            self.connection_summary.setText("연결됐어요. 휴식 자세로 이동하고 있어요.")
            self.status_label.setText("저장된 휴식 자세로 천천히 이동하고 있어요.")
            self._auto_rest_watchdog.start(32000)
            return
        if self._auto_rest_send_attempts < 12:
            QTimer.singleShot(300, self._request_auto_rest)
            return
        self._set_startup_error(
            "휴식 자세 이동 명령을 전달하지 못했어요. 모터 연결을 확인한 뒤 다시 실행해 주세요."
        )

    def _auto_rest_timed_out(self):
        if not self._startup_cancelled and not self._auto_rest_complete:
            self._set_startup_error(
                "휴식 자세에 도착했는지 확인하지 못했어요. 화면과 팔이 안전한지 확인해 주세요."
            )

    def _set_startup_error(self, message):
        self._startup_error = str(message)
        print('[POCO startup] Stopped: %s' % self._startup_error, flush=True)
        self._auto_connect_watchdog.stop()
        self._auto_rest_watchdog.stop()
        self.connection_summary.setText("자동 준비를 완료하지 못했어요. 장치 상세에서 상태를 확인해 주세요.")
        self.status_label.setText(self._startup_error)
        self._sync_navigation()

    def _sync_auto_start_status(self):
        if self._startup_cancelled or self._startup_error or self._auto_rest_complete:
            return
        if not self._auto_connect_acknowledged:
            self.connection_summary.setText("모터 1~4 연결 상태를 자동으로 확인하고 있어요.")
            return
        if self.manual_only:
            self.connection_summary.setText("모터 연결이 확인됐어요. 작업 위치로 이동할 수 있어요.")
            return
        if not self._auto_rest_command_sent:
            self.connection_summary.setText("연결됐어요. 휴식 자세 이동을 준비하고 있어요.")
            return
        if not self._auto_rest_acknowledged:
            return
        preparation = self._latest_preparation
        movement_status = str(preparation.get("movement_status", "idle"))
        if movement_status in {"timeout", "safety_error", "command_error", "telemetry_error"}:
            self._set_startup_error(
                str(preparation.get("last_error") or "휴식 자세 이동 상태를 확인하지 못했어요.")
            )
        elif self._auto_rest_acknowledged and preparation.get("movement_active"):
            self.connection_summary.setText("휴식 자세로 이동하고 있어요. 잠시 기다려 주세요.")
        elif movement_status == "completed" and self._at_saved_rest(preparation):
            self._auto_rest_complete = True
            print('[POCO startup] All four joints arrived at saved rest pose.', flush=True)
            self._auto_rest_watchdog.stop()
            self.connection_summary.setText("모터 연결 완료 · 휴식 자세에 도착했어요.")

    def _at_saved_rest(self, preparation):
        """Do not accept a stale completion for a different movement."""
        actual = preparation.get("motor_angles_deg", {})
        targets = self.settings["postures"]["rest"]
        try:
            tolerance = float(preparation["arrival_tolerance_deg"])
            return (
                math.isfinite(tolerance) and tolerance > 0
                and not preparation.get("movement_active", False)
                and all(
                    abs(float(actual[joint]) - float(targets[joint + "_deg"])) <= tolerance
                    for joint in ("shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
                )
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _sync_startup_navigation(self):
        if not hasattr(self, "working_button"):
            return
        preparation = self._latest_preparation
        connected = bool(preparation.get("all_motors_ready", False))
        moving = bool(preparation.get("movement_active", False))
        capture_running = bool(
            self._latest_calibration.get("running", False)
            or self._combined_state().get("active", False)
            or self._finish_requested
        )
        rest_ready = self._auto_connect_acknowledged and (self.manual_only or self._auto_rest_complete)
        enabled = connected and rest_ready and not self._startup_error and not self._startup_cancelled
        self.working_button.setEnabled(
            enabled and not moving and not capture_running
        )
        self.rest_button.setEnabled(
            enabled and not moving and not capture_running
        )
        if not enabled:
            for item in self.step_buttons[1:] + self.jog_buttons:
                item.setEnabled(False)
            for name in ("next_button", "finish_button", "capture_button", "user_slider",
                         "distance_spin", "height_spin", "apply_constants_button"):
                getattr(self, name).setEnabled(False)
        if self.steps.currentIndex() != 0:
            return
        if self._startup_error:
            self.step_hint.setText("자동 준비를 완료하지 못했어요. 장치 상세에서 상태를 확인해 주세요.")
        elif not self._auto_connect_acknowledged:
            self.step_hint.setText("모터 1~4 연결을 자동으로 확인하고 있어요.")
        elif self.manual_only:
            self.step_hint.setText("연결이 확인됐어요. 작업 위치로 이동한 뒤 화면을 조절해 주세요.")
        elif not self._auto_rest_complete:
            self.step_hint.setText("저장된 휴식 자세로 이동 중이에요. 도착할 때까지 기다려 주세요.")
        elif not preparation.get("working_start_completed") and not moving:
            self.step_hint.setText("휴식 자세에 도착했어요. 작업 시작 위치로 이동해 주세요.")

    def _handle_startup_hardware_event(self, event):
        if self._startup_cancelled or self._startup_error:
            return True
        event_type = str(event.get("type", "")).upper()
        success = event.get("success") is True
        message = str(event.get("message", ""))
        if event_type == "MONITOR_ARM_CONNECT_ALL_DONE":
            if self._auto_connect_acknowledged:
                return True
            self._auto_connect_watchdog.stop()
            if not success or not event.get("preparation", {}).get("all_motors_ready"):
                self._set_startup_error(message or "모터 연결 확인에 실패했어요.")
                return True
            self._auto_connect_acknowledged = True
            if not self.manual_only:
                self._request_auto_rest()
            self._sync_auto_start_status()
            self._sync_navigation()
            return True
        elif (event_type == "MONITOR_ARM_MOVE_REST_ACK"
              and self._auto_rest_command_sent and not self._auto_rest_complete):
            if not success:
                self._set_startup_error(message or "휴식 자세 이동에 실패했어요.")
                return True
            self._auto_rest_acknowledged = True
            self._sync_auto_start_status()
            self._sync_navigation()
            return True
        return False

    def _build_position(self):
        panel = QWidget()
        row = QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        coordinates, col = card(14)
        col.setSpacing(7)
        col.addWidget(label("몸과 화면 사이 거리", "section"))
        self.distance_capture_button = button("ToF 거리 기준 · 5초 측정", self._capture_distance, "", 36)
        col.addWidget(self.distance_capture_button)
        self.distance_status = label("편하게 앉은 뒤 거리 기준을 측정해 주세요.", "muted", True)
        col.addWidget(self.distance_status)
        self.distance_progress = QProgressBar()
        self.distance_progress.setRange(0, 100)
        self.distance_progress.setTextVisible(False)
        self.distance_progress.setFixedHeight(5)
        self.distance_progress.setStyleSheet("QProgressBar{min-height:5px;max-height:5px;border:0;border-radius:2px;}QProgressBar::chunk{border-radius:2px;}")
        col.addWidget(self.distance_progress)
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(3, 200)
        self.distance_spin.setDecimals(1)
        self.distance_spin.setSingleStep(1)
        self.distance_spin.setSuffix(" cm")
        self.distance_spin.setValue(self.distance_cm)
        self.distance_spin.setMinimumHeight(40)
        self.distance_spin.setStyleSheet("font-size:23px;font-weight:700;")
        self.distance_spin.valueChanged.connect(self._mark_constants_pending)
        col.addWidget(self.distance_spin)
        actions = QHBoxLayout()
        self.near_button = button("가까이 1cm", lambda: self._nudge_distance(-1), height=36)
        self.far_button = button("멀리 1cm", lambda: self._nudge_distance(1), height=36)
        actions.addWidget(self.near_button); actions.addWidget(self.far_button)
        col.addLayout(actions)
        self.distance_range_label = label("선택한 높이를 유지하며 이동해요.", "muted", True)
        col.addWidget(self.distance_range_label)
        col.addStretch(1)
        row.addWidget(coordinates, 5)
        heights, height_col = card(14)
        height_col.setSpacing(8)
        height_col.addWidget(label("화면 높이", "section"))
        height_col.addWidget(label("베이스 기준 높이", "muted"))
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(float(self.settings["manual_cartesian"]["monitor_z_min_m"])*100,
                                 float(self.settings["manual_cartesian"]["monitor_z_max_m"])*100)
        self.height_spin.setDecimals(1)
        self.height_spin.setSingleStep(.5)
        self.height_spin.setValue(self.default_z_cm)
        self.height_spin.setSuffix(" cm")
        self.height_spin.setMinimumHeight(40)
        self.height_spin.setStyleSheet("font-size:23px;font-weight:700;")
        self.height_spin.valueChanged.connect(self._mark_constants_pending)
        height_col.addWidget(self.height_spin)
        self.apply_constants_button = button("설정 적용 · 이동", self.apply_constants, "primary", 40)
        height_col.addWidget(self.apply_constants_button)
        height_col.addWidget(label("설정 높이를 계속 유지해요.\n기구 한계에서는 앞뒤 이동을 우선해요.", "muted", True))
        height_col.addStretch(1)
        row.addWidget(heights, 4)
        # Shared V1/V2 refresh uses these widgets; V3 no longer presents a
        # fictitious base-to-user position slider or its old 58–83 cm window.
        self.user_slider = QSlider(Qt.Horizontal, self)
        self.user_slider.setRange(0, 2000)
        self.user_slider.hide()
        self.user_value_label = label(""); self.user_value_label.hide()
        tilt, tilt_col = card(14)
        tilt_col.setSpacing(7)
        tilt_col.addWidget(label("화면 기울기", "section"))
        tilt_col.addWidget(label("누르는 동안 이동해요. 놓으면 멈춰요.", "muted"))
        self.jog_speed = QSpinBox()
        self.jog_speed.setRange(1, 1000)
        self.jog_speed.setValue(100)
        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(0.1, 3)
        self.jog_step.setValue(.5)
        self.jog_step.setSuffix(" °/tick")
        # Original defaults and ranges remain intact; advanced controls are in Details.
        self.jog_speed.hide(); self.jog_step.hide()
        self.gimbal_labels = {}
        self.jog_buttons = []
        for joint, title in (("wrist_flex", "위아래 기울기"), ("wrist_roll", "좌우 기울기")):
            tilt_col.addWidget(label(title, "muted"))
            actions = QHBoxLayout()
            # Do not invent physical direction labels: keep the calibrated +/- mapping.
            for text, direction in (("−", -1), ("+", 1)):
                item = button(text, height=38)
                item.setAccessibleName(title + (" 감소" if direction == -1 else " 증가"))
                item.pressed.connect(lambda n=joint, d=direction: self._start_jog(n, d))
                item.released.connect(self._stop_jog)
                self.jog_buttons.append(item)
                actions.addWidget(item)
            tilt_col.addLayout(actions)
            readout = label("")
            readout.hide()
            self.gimbal_labels[joint] = readout
        tilt_col.addStretch(1)
        row.addWidget(tilt, 4)
        self.ik_label = label("")
        self.ik_label.hide()
        self.canvas = ArmPreparationCanvas(self.kinematics)
        self.canvas.hide()
        return panel

    def _distance_state(self):
        return (self.get_hardware_state() or {}).get('monitor_arm', {}).get('distance_setup', {})

    def _distance_dirty(self):
        return (abs(self.distance_spin.value() - self.applied_distance_cm) > .06
                or abs(self.height_spin.value() - self.applied_height_cm) > .06)

    def _capture_distance(self):
        if self._distance_request_pending or self._latest_preparation.get('movement_active'):
            return
        self._stop_jog()
        if self._send({'type': 'START_MONITOR_ARM_DISTANCE_CAPTURE'}):
            self._distance_request_pending = True
            self._distance_request_at = time.monotonic()
            self._distance_feedback = 'ToF 측정을 시작하고 있어요.'
            self._sync_navigation()

    def _mark_constants_pending(self, _value=None):
        if not self._v3_ready:
            return
        self._distance_feedback = '변경한 거리·높이를 적용해 주세요.'
        self._sync_navigation()

    def apply_constants(self):
        state = self._distance_state()
        prep = self._latest_preparation
        if (not state.get('ready') or state.get('running') or self._distance_request_pending
                or not prep.get('working_start_completed') or prep.get('movement_active')):
            return
        if self._send({'type': 'MONITOR_ARM_DISTANCE_TARGET',
                       'distance_m': self.distance_spin.value() / 100,
                       'height_m': self.height_spin.value() / 100}):
            self._distance_request_pending = True
            self._distance_request_at = time.monotonic()
            self._distance_feedback = '화면 이동을 요청했어요.'
            self._sync_navigation()

    def send_manual_ik(self):
        self.apply_constants()

    def _nudge_distance(self, delta_cm):
        if self._distance_dirty():
            self._distance_feedback = '변경한 숫자를 먼저 적용해 주세요.'
            return
        value = self.distance_spin.value() + delta_cm
        if not self._distance_bounds[0] <= value <= self._distance_bounds[1]:
            self._distance_feedback = '이 높이에서 이동할 수 있는 한계예요.'
            return
        self.distance_spin.setValue(value)
        self.apply_constants()

    def _sync_distance_controls(self):
        state = self._distance_state()
        prep = self._latest_preparation
        if self._distance_request_pending and time.monotonic() - self._distance_request_at > 10:
            self._distance_request_pending = False
            self._distance_feedback = '장치 응답이 없어요. 연결 상태를 확인해 주세요.'
        if state.get('ready') and state.get('revision') != self._distance_revision:
            self._distance_revision = state.get('revision')
            self.distance_spin.blockSignals(True); self.height_spin.blockSignals(True)
            self.distance_spin.setRange(3, 200)
            self.distance_spin.setValue(float(state['preferred_distance_m']) * 100)
            self.height_spin.setValue(float(state['preferred_height_m']) * 100)
            self.applied_distance_cm = self.distance_spin.value()
            self.applied_height_cm = self.height_spin.value()
            self.distance_spin.blockSignals(False); self.height_spin.blockSignals(False)
        allowed = bool(prep.get('all_motors_ready') and prep.get('working_start_completed')
                       and not prep.get('movement_active') and not state.get('running')
                       and not self._distance_request_pending and not self._combined_state().get('active')
                       and not self._startup_error and not self._startup_cancelled)
        self.distance_capture_button.setEnabled(allowed)
        ready = bool(state.get('ready'))
        workspace_ok = False
        if ready:
            try:
                from services.monitor_arm_distance_setup import nearest_interval
                from services.monitor_arm_tracking_path import workspace_intervals, closest_at_x
                arm = (self.get_hardware_state() or {}).get('monitor_arm', {})
                ranges = arm['calibration_ranges']
                x = float(prep['current_pose']['x_m'])
                z_min = float(self.settings['manual_cartesian']['monitor_z_min_m'])
                z_max = float(self.settings['manual_cartesian']['monitor_z_max_m'])
                intervals = workspace_intervals(self.kinematics, ranges, z_min, z_max)
                low, high = nearest_interval(intervals, x)
                user = float(state['tof_user_x_baseline_m'])
                low_cm = math.ceil(max(.03, user - high) * 1000) / 10
                high_cm = math.floor((user - low) * 1000) / 10
                if low_cm > high_cm:
                    raise ValueError('가능한 거리가 없어요.')
                self._distance_bounds = (low_cm, high_cm)
                requested_x = user - self.distance_spin.value()/100
                note = '이동 범위  %.1f~%.1f cm' % (low_cm, high_cm)
                if low <= requested_x <= high:
                    q = closest_at_x(self.kinematics, requested_x, self.height_spin.value()/100,
                                     ranges, z_min, z_max)
                    reachable_z = self.kinematics.forward(q).z_m
                    if abs(reachable_z-self.height_spin.value()/100) > .001:
                        note += '\n한계 구간: 약 %.1f cm 높이 · 복귀 시 설정 높이 회복' % (reachable_z*100)
                    else:
                        note += '\n설정 높이를 유지하며 이동해요'
                self.distance_range_label.setText(note)
                workspace_ok = True
            except (KeyError, TypeError, ValueError) as error:
                self.distance_range_label.setText('높이 또는 장치 상태를 확인해 주세요.')
        self.distance_spin.setEnabled(allowed and ready and workspace_ok)
        self.height_spin.setEnabled(allowed and ready)
        value_allowed = self._distance_bounds[0] <= self.distance_spin.value() <= self._distance_bounds[1]
        self.apply_constants_button.setEnabled(allowed and ready and workspace_ok and value_allowed)
        if workspace_ok and not value_allowed:
            self.distance_range_label.setText('%.1f~%.1f cm로 조절해 주세요.' % self._distance_bounds)
        self.near_button.setEnabled(allowed and ready and workspace_ok and not self._distance_dirty()
                                    and self.distance_spin.value()-1 >= self._distance_bounds[0])
        self.far_button.setEnabled(allowed and ready and workspace_ok and not self._distance_dirty()
                                   and self.distance_spin.value()+1 <= self._distance_bounds[1])
        busy = state.get('running') or self._distance_request_pending
        if busy:
            for item in self.jog_buttons + self.step_buttons + [self.rest_button, self.working_button,
                                                               self.back_button, self.next_button]:
                item.setEnabled(False)
        if self.steps.currentIndex() == 1 and (not ready or self._distance_dirty() or busy or not workspace_ok or not value_allowed):
            self.next_button.setEnabled(False)
        movement_failed = prep.get('movement_status') in {'command_error', 'timeout', 'telemetry_error', 'safety_error'}
        if movement_failed:
            self.next_button.setEnabled(False)
        if not ready or busy or movement_failed:
            self.capture_button.setEnabled(False)
        self.distance_progress.setValue(round((5-float(state.get('remain_sec', 5)))*20)
                                        if state.get('running') else 100 if ready else 0)
        if state.get('running'):
            self.distance_status.setText('같은 자리를 유지해 주세요 · %d초' % math.ceil(state.get('remain_sec', 5)))
        elif prep.get('movement_active'):
            self.distance_status.setText('화면이 이동하고 있어요. 잠시 기다려 주세요.')
        elif state.get('last_error'):
            self.distance_status.setText(state['last_error'])
        elif self._distance_feedback:
            self.distance_status.setText(self._distance_feedback)
        elif ready:
            self.distance_status.setText('ToF 기준 %.1f cm · 유지 거리를 조절해 보세요.' % (state['tof_user_x_baseline_m']*100))
        if prep.get('last_error') and prep.get('movement_status') in {'command_error', 'timeout', 'telemetry_error'}:
            self.distance_status.setText(str(prep['last_error']))

    def _build_capture(self):
        panel, col = card(20)
        col.addWidget(label("카메라를 보며 자세 기준을 저장해요", "heading"))
        col.addWidget(label(
            "화면 위치를 맞춘 뒤 정면을 바라봐 주세요.\n"
            "눈 간격·IMU·카메라 자세 기준을 함께 저장합니다.",
            "muted", True,
        ))
        self.sensor_summary = label("거리·얼굴·카메라 AI 준비를 확인하고 있어요", "notice", True)
        col.addWidget(self.sensor_summary)
        col.addWidget(label("저장 중에는 5초 동안 편한 자세를 그대로 유지해 주세요.", "muted", True))
        self.capture_button = button("5초 자세 기준 저장", self._start_combined_capture, "primary", 42)
        self.capture_button.setEnabled(False)
        col.addWidget(self.capture_button)
        self.capture_progress = QProgressBar()
        self.capture_progress.setRange(0, 100)
        self.capture_progress.setTextVisible(False)
        col.addWidget(self.capture_progress)
        self.capture_summary = label("눈 간격·IMU·카메라 자세 기준을 함께 저장해요.", "muted", True)
        col.addWidget(self.capture_summary)
        self.live_sensor_label = label(""); self.live_sensor_label.hide()
        self.capture_result_label = label(""); self.capture_result_label.hide()
        col.addStretch(1)
        return panel

    def _combined_state(self):
        if callable(self._combined_capture_state):
            try:
                value = self._combined_capture_state()
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
        return {}

    def _start_combined_capture(self):
        if not callable(self._combined_capture_start):
            self.capture_summary.setText("통합 기준 저장 기능을 시작할 수 없어요. 앱을 다시 실행해 주세요.")
            return
        if self._combined_capture_start():
            self.capture_summary.setText("눈 간격·IMU·카메라 자세 기준을 저장하고 있어요. 정면을 유지해 주세요.")
            self.capture_progress.setValue(0)
        else:
            self.capture_summary.setText("카메라와 AI가 준비되면 다시 눌러 주세요.")

    def _finish_combined_preparation(self):
        state = self._combined_state()
        prep = self._latest_preparation
        if (self._finish_requested or self._startup_cancelled or not state.get("ready")
                or state.get("active") or not prep.get("all_motors_ready")
                or not prep.get("working_start_completed") or prep.get("movement_active")):
            return
        self._finish_requested = True
        if not self._send({"type": "FINISH_MONITOR_ARM_PREPARATION"}):
            self._finish_requested = False
        self._sync_navigation()

    def _go_step(self, index):
        maximum = 1
        index = min(maximum, max(0, index))
        if index > 0 and not self._latest_preparation.get("all_motors_ready", False):
            return
        if index > 0 and (self._startup_cancelled or self._startup_error
                          or not self._auto_connect_acknowledged
                          or not (self.manual_only or self._auto_rest_complete)):
            return
        if hasattr(self, "jog_timer"):
            self._stop_jog()
        self.steps.setCurrentIndex(index)
        if hasattr(self, "next_button"):
            self._sync_navigation()

    def _advance_step(self):
        if self.steps.currentIndex() == 0:
            self._go_step(1)
            return
        prep = self._latest_preparation
        if (self.manual_only or not callable(self._show_main_calibration)
                or not prep.get("all_motors_ready") or not prep.get("working_start_completed")
                or prep.get("movement_active") or self._startup_error
                or not self._auto_rest_complete):
            return
        if (not self._distance_state().get('ready') or self._distance_state().get('running')
                or self._distance_request_pending or self._distance_dirty()
                or prep.get('movement_status') in {'command_error', 'timeout', 'telemetry_error', 'safety_error'}):
            return
        self._stop_jog()
        self._show_main_calibration(self)

    def _sync_navigation(self):
        idx = self.steps.currentIndex()
        connected = self._latest_preparation.get("all_motors_ready", False)
        working = self._latest_preparation.get("working_start_completed", False)
        moving = self._latest_preparation.get("movement_active", False)
        combined = self._combined_state()
        capture_running = bool(
            self._latest_calibration.get("running", False)
            or combined.get("active", False)
            or self._finish_requested
        )
        for n, item in enumerate(self.step_buttons):
            item.setChecked(n == idx)
            item.setEnabled(not capture_running and (n == 0 or (connected and (n == 1 or working))))
        self.back_button.setVisible(idx > 0)
        self.back_button.setEnabled(not capture_running)
        self.next_button.setVisible(not self.manual_only or idx == 0)
        self.next_button.setText("위치 조절로" if idx == 0 else "위치 설정 완료 · 다음")
        self.next_button.setEnabled(bool(connected and working and not moving and not capture_running))
        self.finish_button.hide()
        self.step_hint.setText((
            "연결 확인 → 시작 위치 이동 순서로 진행해 주세요.",
            "위치 조절을 마치면 다음 화면에서 카메라를 보며 자세 기준을 저장해요."
            if not self.manual_only else "거리·높이를 적용하고 기울기를 조절해 주세요.",
        )[idx])
        for item in self.jog_buttons:
            item.setEnabled(bool(connected and not capture_running))
        camera_ready = bool(combined.get("camera_ready", False))
        self.capture_button.setEnabled(bool(
            connected and working and not moving and not capture_running and camera_ready
        ))
        self.finish_button.setEnabled(bool(
            connected and working and not moving and not capture_running
            and combined.get("ready", False)
        ))
        if capture_running:
            for item in self.step_buttons:
                item.setEnabled(False)
            self.back_button.setEnabled(False)
            self.next_button.setEnabled(False)
            for name in (
                "rest_button", "working_button", "user_slider", "distance_spin",
                "height_spin", "apply_constants_button",
            ):
                widget = getattr(self, name, None)
                if widget is not None:
                    widget.setEnabled(False)
        self._sync_startup_navigation()
        self._sync_distance_controls()

    def refresh_state(self):
        # V3 owns progress and completion controls; do not paint legacy 0..50
        # values before painting the combined 0..100 value on every timer tick.
        super().refresh_state(update_capture_ui=False)
        if not self._v3_ready:
            return
        prep = self._latest_preparation
        cal = self._latest_calibration
        if prep.get("last_error"):
            self.connection_summary.setText("이동을 완료하지 못했어요. 장치 상세에서 원인을 확인해 주세요.")
        elif prep.get("movement_active"):
            self.connection_summary.setText("화면이 이동하고 있어요. 도착할 때까지 기다려 주세요.")
        elif prep.get("working_start_completed"):
            self.connection_summary.setText("시작 위치에 도착했어요. 편안한 위치로 조절해 주세요.")
        elif prep.get("all_motors_ready"):
            self.connection_summary.setText("연결됐어요. 이제 시작 위치로 이동할 수 있어요.")
        else:
            self.connection_summary.setText("아직 연결을 확인하지 않았어요")
        hardware_state = self.get_hardware_state() or {}
        arm = hardware_state.get("monitor_arm", {})
        tof_state = hardware_state.get("tof", {})
        if not isinstance(tof_state, dict):
            tof_state = {}
        try:
            distance_cm = float(tof_state.get(
                "filtered_distance_m", tof_state.get("distance_m")
            )) * 100.0
        except (TypeError, ValueError):
            distance_cm = None
        if distance_cm is not None and math.isfinite(distance_cm):
            valid = bool(tof_state.get("valid", False))
            self.tof_live_label.setText("ToF  ·  %.1f cm" % distance_cm)
            background, color = ("#E8F7EF", "#1F7A4D") if valid else ("#FFF6E5", "#9A6700")
            self.tof_live_label.setStyleSheet(
                "QLabel{font-size:11px;font-weight:600;color:%s;background:%s;"
                "border-radius:9px;padding:4px 9px;}" % (color, background)
            )
        else:
            self.tof_live_label.setText("ToF  ·  신호 대기")
            self.tof_live_label.setStyleSheet(
                "QLabel{font-size:11px;font-weight:600;color:#6B7684;"
                "background:#E8EBEF;border-radius:9px;padding:4px 9px;}"
            )
        tof_ok = arm.get("live_tof_user_x_m") is not None
        eye_ok = arm.get("live_eye_gap_px") is not None
        self.sensor_summary.setText("거리 신호 %s    ·    얼굴 신호 %s" % (
            "확인됨" if tof_ok else "대기 중", "확인됨" if eye_ok else "대기 중"))
        combined = self._combined_state()
        sensor_ready = bool(
            cal.get("session_ready", False) and combined.get("sensor_done", False)
        )
        if combined.get("active"):
            started_at = combined.get("started_at")
            elapsed = max(0.0, time.monotonic() - started_at) if started_at else 0.0
            # The four baselines are collected in parallel. Keep the progress
            # bar honest while each service finishes its own sample validation.
            self.capture_progress.setValue(100 if combined.get("ready") else round(90 * min(1.0, elapsed / 5.0)))
            remaining = max(0, math.ceil(5.0 - elapsed))
            self.capture_summary.setText(
                "함께 저장 중 · 눈 간격 %s  /  IMU %s  /  카메라 자세 %s  ·  약 %d초"
                % (
                    "완료" if sensor_ready else "측정 중",
                    "완료" if combined.get("imu_success") else "측정 중",
                    "완료" if combined.get("pose_success") else "측정 중",
                    remaining,
                )
            )
        elif combined.get("failure"):
            self.capture_progress.setValue(0)
            self.capture_summary.setText(str(combined.get("failure")))
        elif combined.get("ready"):
            self.capture_progress.setValue(100)
            self.capture_summary.setText("눈 간격·IMU·카메라 자세 기준을 저장했어요. ‘기준 저장 완료 · 계속’을 눌러 주세요.")
            self.capture_result_label.setText(
                "ToF 평균 %.3f m · 눈 간격 평균 %.2f px · IMU 및 카메라 자세 기준 저장 완료"
                % (
                    float(cal.get("tof_user_x_baseline_m") or 0.0),
                    float(cal.get("eye_gap_baseline_px") or 0.0),
                )
            )
        else:
            self.capture_progress.setValue(0)
            if not combined.get("camera_ready", False):
                self.capture_summary.setText("카메라와 AI 준비가 끝나면 저장 버튼을 사용할 수 있어요.")
            elif sensor_ready:
                self.capture_summary.setText("일부 기준이 아직 준비되지 않았어요. 다시 저장해 주세요.")
            else:
                self.capture_summary.setText("눈 간격·IMU·카메라 자세 기준을 함께 저장해요.")
        self._sync_auto_start_status()
        self._sync_navigation()

    def _show_details(self):
        self._stop_jog()
        detail = QDialog(self)
        detail.setWindowTitle("장치 상세")
        detail.resize(620, 380)
        col = QVBoxLayout(detail)
        text = label("\n\n".join(x.text() for x in (self.status_label, self.connection_label,
                     self.ik_label, self.live_sensor_label, self.capture_result_label)), "muted", True)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(text)
        col.addWidget(scroll, 1)
        settings = QHBoxLayout()
        settings.addWidget(label("기울기 조절 속도"))
        settings.addWidget(self.jog_speed)
        settings.addWidget(label("한 번에 움직일 각도"))
        settings.addWidget(self.jog_step)
        self.jog_speed.show(); self.jog_step.show()
        col.addLayout(settings)
        col.addWidget(button("닫기", detail.accept, "primary"))
        detail.exec_()
        self.jog_speed.setParent(self); self.jog_speed.hide()
        self.jog_step.setParent(self); self.jog_step.hide()
        detail.deleteLater()

    def handle_hardware_event(self, event):
        if not isinstance(event, dict):
            return
        event_type = event.get('type')
        if event_type in {'START_MONITOR_ARM_DISTANCE_CAPTURE_ACK', 'MONITOR_ARM_DISTANCE_TARGET_ACK',
                          'MONITOR_ARM_DISTANCE_CAPTURE_DONE'}:
            self._distance_request_pending = False
            self._distance_feedback = str(event.get('message', ''))
            if event_type == 'MONITOR_ARM_DISTANCE_TARGET_ACK' and event.get('success'):
                self.applied_distance_cm = self.distance_spin.value()
                self.applied_height_cm = self.height_spin.value()
            self.refresh_state()
            return
        if event.get("type") == "MONITOR_ARM_PREPARATION_FINISHED":
            if not self._finish_requested or self._accepted_close or self._startup_cancelled:
                return
            self._finish_requested = False
            if event.get("success") is True:
                self._accepted_close = True
                # Close before notifying the parent; profile prompts can then
                # open without an active preparation dialog underneath them.
                self.accept()
                self.preparation_finished.emit(True, str(event.get("message", "")))
                return
            self._sync_navigation()
        if self._handle_startup_hardware_event(event):
            return
        super().handle_hardware_event(event)
        if isinstance(event, dict) and event.get("success") is False:
            self.step_hint.setText("요청을 완료하지 못했어요. 장치 상세에서 원인을 확인해 주세요.")

    def _stop_pending_ui(self):
        self._startup_cancelled = True
        for name in ("state_timer", "ik_debounce", "_auto_connect_watchdog", "_auto_rest_watchdog"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        if hasattr(self, "jog_timer"):
            self._stop_jog()

    def cancel_preparation(self):
        self._stop_pending_ui()
        if callable(self._combined_capture_cancel):
            self._combined_capture_cancel()
        super().cancel_preparation()

    def done(self, result):
        self._stop_pending_ui()
        super().done(result)

    def closeEvent(self, event):
        self._stop_pending_ui()
        if not self._accepted_close and callable(self._combined_capture_cancel):
            self._combined_capture_cancel()
        super().closeEvent(event)


class ProfileDialogV3(UserProfileDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet(STYLE)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setFixedSize(640, 422 if self.save_mode else 390)

    def _build_ui(self):
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 16, 22, 16)
        col.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(label("내 설정 저장" if self.save_mode else "내 설정 불러오기", "heading"))
        header.addStretch(1)
        header.addWidget(close_button(self.reject))
        col.addLayout(header)
        col.addWidget(label("다음에도 사용할 수 있도록 이름을 붙여 주세요." if self.save_mode else
                            "불러오면 모니터암이 저장된 작업 위치로 이동해요.", "muted", True))
        self.slot_buttons = []
        self.delete_buttons = []
        self.grid = QGridLayout()
        self.grid.setVerticalSpacing(7)
        for idx in range(4):
            slot = idx + 1
            item = button("", lambda _=False, n=slot: self._select(n), "profile", 38)
            item.setCheckable(True)
            delete = button("삭제", lambda _=False, n=slot: self._delete(n), "subtle", 32)
            delete.setFixedWidth(58)
            self.slot_buttons.append(item); self.delete_buttons.append(delete)
            self.grid.addWidget(item, idx, 0); self.grid.addWidget(delete, idx, 1)
        col.addLayout(self.grid)
        if self.save_mode:
            self.name_edit = QLineEdit()
            self.name_edit.setMaxLength(24)
            self.name_edit.setPlaceholderText("설정 이름 · 예: 내 책상")
            col.addWidget(self.name_edit)
        col.addStretch(1)
        footer = QHBoxLayout()
        footer.addWidget(button("취소", self.reject, "subtle", 38))
        footer.addStretch(1)
        self.confirm_button = button("내 설정 저장" if self.save_mode else "불러오고 화면 위치 맞추기", self._confirm, "primary", 38)
        self.confirm_button.setEnabled(False)
        footer.addWidget(self.confirm_button)
        col.addLayout(footer)

    def refresh(self):
        # The original service remains responsible for reading and applying profiles.
        for data, item, delete in zip(self.profile_service.list_profiles(), self.slot_buttons, self.delete_buttons):
            occupied = data.get("occupied", False)
            reason = ""
            if occupied and not self.save_mode:
                try:
                    calibration = self.profile_service.load_profile(data["slot"])["calibration"]
                    if calibration.get("requires_recalibration") or not calibration.get("ready"):
                        reason = "다시 설정 필요"
                except (OSError, ValueError, KeyError, TypeError):
                    reason = "설정 확인 필요"
            text = data.get("name", "내 설정") if occupied else ("새 설정 저장" if self.save_mode else "저장된 설정 없음")
            item.setText("%02d   %s%s" % (data["slot"], text, ("  ·  " + reason) if reason else ""))
            item.setToolTip("장치 기준이 바뀌었거나 저장값을 읽을 수 없어요. 새 기준 자세를 설정해 주세요." if reason else "")
            item.setChecked(data["slot"] == self.selected_slot)
            item.setEnabled(bool((occupied and not reason) or self.save_mode))
            delete.setEnabled(occupied)
        if hasattr(self, "confirm_button"):
            self.confirm_button.setEnabled(self.selected_slot is not None)

    def _select(self, slot):
        super()._select(slot)
        self.confirm_button.setEnabled(True)
