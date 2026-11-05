"""Presentation-only replacements; command and validation methods are inherited."""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget,
    QDoubleSpinBox, QSpinBox, QSlider, QProgressBar, QLineEdit, QDialog,
    QScrollArea, QMessageBox,
)
from monitor_arm_preparation_dialog import MonitorArmPreparationDialog, ArmPreparationCanvas
from user_profile_dialog import UserProfileDialog
from .theme import STYLE, label, button, card, role, close_button


class PreparationDialogV2(MonitorArmPreparationDialog):
    def __init__(self, *args, **kwargs):
        self._v2_ready = False
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
        self.setMinimumSize(760, 450)
        self.setMaximumSize(760, 450)
        self.resize(760, 450)
        self._v2_ready = True
        self.refresh_state()
        self._auto_connect_watchdog = QTimer(self)
        self._auto_connect_watchdog.setSingleShot(True)
        self._auto_connect_watchdog.timeout.connect(self._auto_connect_timed_out)
        self._auto_rest_watchdog = QTimer(self)
        self._auto_rest_watchdog.setSingleShot(True)
        self._auto_rest_watchdog.timeout.connect(self._auto_rest_timed_out)
        QTimer.singleShot(250, self._start_auto_connection_check)

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
        names = ("1  시작 준비", "2  화면 위치", "3  내 위치 기준")
        for idx, text in enumerate(names):
            item = button(text, lambda _=False, n=idx: self._go_step(n), "step", 30)
            item.setCheckable(True)
            self.step_buttons.append(item)
            if not self.manual_only or idx < 2:
                steps.addWidget(item)
            else:
                item.hide()
        outer.addLayout(steps)
        self.steps = QStackedWidget()
        self.steps.addWidget(self._build_connection())
        self.steps.addWidget(self._build_position())
        self.steps.addWidget(self._build_capture())
        outer.addWidget(self.steps, 1)
        self.status_label = label("모터 연결을 자동으로 확인하고 있어요.", "muted", True)
        # Original handler writes all diagnostics here; show them in Details instead
        # of letting fast telemetry overwrite the current instruction.
        self.status_label.hide()
        self.step_hint = label("", "muted")
        outer.addWidget(self.step_hint)
        bottom = QHBoxLayout()
        self.cancel_button = button("닫기" if self.manual_only else "준비 취소", self.cancel_preparation, "subtle", 36)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(button("장치 상세", self._show_details, "subtle", 36))
        bottom.addStretch(1)
        self.back_button = button("이전", lambda: self._go_step(self.steps.currentIndex()-1), "subtle", 36)
        bottom.addWidget(self.back_button)
        self.next_button = button("위치 조절로", lambda: self._go_step(self.steps.currentIndex()+1), "primary", 36)
        bottom.addWidget(self.next_button)
        self.finish_button = button("준비 완료 · 다음 단계로", lambda: self._send({"type": "FINISH_MONITOR_ARM_PREPARATION"}), "primary", 36)
        self.finish_button.setEnabled(False)
        bottom.addWidget(self.finish_button)
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
        if self._startup_error or self._auto_connect_acknowledged:
            return
        self._auto_connect_attempts += 1
        if self._send({"type": "MONITOR_ARM_CONNECT_ALL"}):
            self.connection_summary.setText("모터 1~4 연결 상태를 확인하고 있어요.")
            self.status_label.setText("모터 연결 상태를 자동으로 확인하고 있어요.")
            self._auto_connect_watchdog.start(10000)
            return
        if self._auto_connect_attempts < 12:
            QTimer.singleShot(300, self._start_auto_connection_check)
            return
        self._set_startup_error(
            "하드웨어 연결을 시작하지 못했어요. USB 어댑터와 모터 전원을 확인한 뒤 다시 실행해 주세요."
        )

    def _auto_connect_timed_out(self):
        if not self._auto_connect_acknowledged:
            self._set_startup_error(
                "모터 연결 응답이 없어요. USB 어댑터와 모터 전원을 확인한 뒤 다시 실행해 주세요."
            )

    def _request_auto_rest(self):
        if self._auto_rest_command_sent or self._startup_error:
            return
        self._auto_rest_send_attempts += 1
        if self._send({"type": "MONITOR_ARM_MOVE_REST", "confirmed": True}):
            self._auto_rest_command_sent = True
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
        if not self._auto_rest_complete:
            self._set_startup_error(
                "휴식 자세에 도착했는지 확인하지 못했어요. 화면과 팔이 안전한지 확인해 주세요."
            )

    def _set_startup_error(self, message):
        self._startup_error = str(message)
        self._auto_connect_watchdog.stop()
        self._auto_rest_watchdog.stop()
        self.connection_summary.setText("자동 준비를 완료하지 못했어요. 장치 상세에서 상태를 확인해 주세요.")
        self.status_label.setText(self._startup_error)
        self._sync_navigation()

    def _sync_auto_start_status(self):
        if self._startup_error:
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
        preparation = self._latest_preparation
        movement_status = str(preparation.get("movement_status", "idle"))
        if movement_status in {"timeout", "safety_error", "command_error", "telemetry_error"}:
            self._set_startup_error(
                str(preparation.get("last_error") or "휴식 자세 이동 상태를 확인하지 못했어요.")
            )
        elif self._auto_rest_acknowledged and preparation.get("movement_active"):
            self.connection_summary.setText("휴식 자세로 이동하고 있어요. 잠시 기다려 주세요.")
        elif self._auto_rest_acknowledged and movement_status == "completed":
            self._auto_rest_complete = True
            self._auto_rest_watchdog.stop()
            self.connection_summary.setText("모터 연결 완료 · 휴식 자세에 도착했어요.")

    def _sync_navigation(self):
        super()._sync_navigation()
        if not hasattr(self, "working_button"):
            return
        preparation = self._latest_preparation
        connected = bool(preparation.get("all_motors_ready", False))
        moving = bool(preparation.get("movement_active", False))
        capture_running = bool(self._latest_calibration.get("running", False))
        rest_ready = self.manual_only or self._auto_rest_complete
        self.working_button.setEnabled(
            connected and rest_ready and not moving and not capture_running and not self._startup_error
        )
        self.rest_button.setEnabled(
            connected and not moving and not capture_running and not self._startup_error
        )
        if self._startup_error:
            self.step_hint.setText("자동 준비를 완료하지 못했어요. 장치 상세에서 상태를 확인해 주세요.")
        elif not self._auto_connect_acknowledged:
            self.step_hint.setText("모터 1~4 연결을 자동으로 확인하고 있어요.")
        elif self.manual_only:
            self.step_hint.setText("연결이 확인됐어요. 작업 위치로 이동한 뒤 화면을 조절해 주세요.")
        elif not self._auto_rest_complete:
            self.step_hint.setText("저장된 휴식 자세로 이동 중이에요. 도착할 때까지 기다려 주세요.")
        else:
            self.step_hint.setText("휴식 자세에 도착했어요. 작업 시작 위치로 이동해 주세요.")

    def handle_hardware_event(self, event):
        super().handle_hardware_event(event)
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type", "")).upper()
        success = event.get("success") is not False
        message = str(event.get("message", ""))
        if event_type == "MONITOR_ARM_CONNECT_ALL_DONE":
            self._auto_connect_watchdog.stop()
            if not success:
                self._set_startup_error(message or "모터 연결 확인에 실패했어요.")
                return
            self._auto_connect_acknowledged = True
            if not self.manual_only:
                self._request_auto_rest()
            self._sync_auto_start_status()
            self._sync_navigation()
        elif event_type == "MONITOR_ARM_MOVE_REST_ACK":
            if not success:
                self._set_startup_error(message or "휴식 자세 이동에 실패했어요.")
                return
            self._auto_rest_acknowledged = True
            self._sync_auto_start_status()
            self._sync_navigation()

    def _build_position(self):
        panel = QWidget()
        row = QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        coordinates, col = card(14)
        col.setSpacing(7)
        col.addWidget(label("거리와 높이", "section"))
        grid = QGridLayout()
        self.user_slider = QSlider(Qt.Horizontal)
        self.user_slider.setRange(round(self.user_min_cm*10), round(self.user_max_cm*10))
        self.user_slider.setValue(round(self.user_min_cm*10))
        self.user_slider.setEnabled(False)
        self.user_slider.valueChanged.connect(self._on_ik_value_changed)
        self.user_value_label = label("", "muted")
        self.user_slider.setToolTip("장치에서 몸까지의 위치. 슬라이더 변경 시 화면도 이동합니다.")
        grid.addWidget(label("장치 → 몸 위치", "muted"), 0, 0)
        grid.addWidget(self.user_value_label, 0, 1)
        grid.addWidget(self.user_slider, 1, 0, 1, 2)
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(20, 100)
        self.distance_spin.setDecimals(1)
        self.distance_spin.setValue(self.distance_cm)
        self.distance_spin.setSuffix(" cm")
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(float(self.settings["manual_cartesian"]["monitor_z_min_m"])*100,
                                 float(self.settings["manual_cartesian"]["monitor_z_max_m"])*100)
        self.height_spin.setDecimals(1)
        self.height_spin.setValue(self.default_z_cm)
        self.height_spin.setSuffix(" cm")
        self.height_spin.setToolTip("장치 좌표 기준의 모니터 높이입니다.")
        self.distance_spin.valueChanged.connect(self._mark_constants_pending)
        self.height_spin.valueChanged.connect(self._mark_constants_pending)
        grid.addWidget(label("몸과 화면 사이", "muted"), 2, 0)
        grid.addWidget(self.distance_spin, 2, 1)
        grid.addWidget(label("화면 높이", "muted"), 3, 0)
        grid.addWidget(self.height_spin, 3, 1)
        col.addLayout(grid)
        self.apply_constants_button = button("거리·높이 적용하고 이동", self.apply_constants, "primary", 32)
        self.apply_constants_button.setEnabled(False)
        col.addWidget(self.apply_constants_button)
        row.addWidget(coordinates, 1)
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
        row.addWidget(tilt, 1)
        self.ik_label = label("")
        self.ik_label.hide()
        self.canvas = ArmPreparationCanvas(self.kinematics)
        self.canvas.hide()
        return panel

    def _build_capture(self):
        panel, col = card(20)
        col.addWidget(label("지금 앉은 위치를 기억할게요", "heading"))
        col.addWidget(label("화면 위치 조절을 마쳤다면 정면을 바라봐 주세요.\n얼굴이 보이는 상태로 5초 동안 자세를 유지해요.", "muted", True))
        self.sensor_summary = label("거리와 얼굴 인식을 확인하고 있어요", "notice", True)
        col.addWidget(self.sensor_summary)
        self.capture_button = button("5초 동안 위치 기준 저장", lambda: self._send({"type": "START_MONITOR_ARM_SENSOR_CAPTURE"}), "primary", 42)
        self.capture_button.setEnabled(False)
        col.addWidget(self.capture_button)
        self.capture_progress = QProgressBar()
        self.capture_progress.setRange(0, 50)
        self.capture_progress.setTextVisible(False)
        col.addWidget(self.capture_progress)
        self.capture_summary = label("저장을 마치면 기준 자세 설정으로 이어져요.", "muted")
        col.addWidget(self.capture_summary)
        self.live_sensor_label = label(""); self.live_sensor_label.hide()
        self.capture_result_label = label(""); self.capture_result_label.hide()
        col.addStretch(1)
        return panel

    def _go_step(self, index):
        maximum = 1 if self.manual_only else 2
        index = min(maximum, max(0, index))
        if index > 0 and not self._latest_preparation.get("all_motors_ready", False):
            return
        if index == 2 and not self._latest_preparation.get("working_start_completed", False):
            return
        if hasattr(self, "jog_timer"):
            self._stop_jog()
        self.steps.setCurrentIndex(index)
        if hasattr(self, "next_button"):
            self._sync_navigation()

    def _sync_navigation(self):
        idx = self.steps.currentIndex()
        connected = self._latest_preparation.get("all_motors_ready", False)
        working = self._latest_preparation.get("working_start_completed", False)
        moving = self._latest_preparation.get("movement_active", False)
        capture_running = self._latest_calibration.get("running", False)
        for n, item in enumerate(self.step_buttons):
            item.setChecked(n == idx)
            item.setEnabled(not capture_running and (n == 0 or (connected and (n == 1 or working))))
        self.back_button.setVisible(idx > 0)
        self.back_button.setEnabled(not capture_running)
        self.next_button.setVisible(idx < (1 if self.manual_only else 2))
        self.next_button.setText("위치 조절로" if idx == 0 else "위치 기준 저장으로")
        self.next_button.setEnabled(bool(connected and working and not moving and not capture_running))
        self.finish_button.setVisible(idx == 2 and not self.manual_only)
        self.step_hint.setText((
            "연결 확인 → 시작 위치 이동 순서로 진행해 주세요.",
            "위 슬라이더는 바로 이동해요. 거리·높이는 적용 버튼을 눌러 주세요.",
            "위치 기준을 저장한 뒤, 다음 화면에서 기준 자세를 측정해요.",
        )[idx])
        for item in self.jog_buttons:
            item.setEnabled(bool(connected and not capture_running))

    def refresh_state(self):
        super().refresh_state()
        if not self._v2_ready:
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
        arm = (self.get_hardware_state() or {}).get("monitor_arm", {})
        tof_ok = arm.get("live_tof_user_x_m") is not None
        eye_ok = arm.get("live_eye_gap_px") is not None
        self.sensor_summary.setText("거리 신호 %s    ·    얼굴 신호 %s" % (
            "확인됨" if tof_ok else "대기 중", "확인됨" if eye_ok else "대기 중"))
        if cal.get("running"):
            self.capture_summary.setText("자세를 유지해 주세요 · %.1f초 남았어요" % float(cal.get("remain_sec", 0) or 0))
        elif cal.get("session_ready"):
            self.capture_summary.setText("위치 기준을 저장했어요. ‘준비 완료’를 눌러 주세요.")
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
        super().handle_hardware_event(event)
        if isinstance(event, dict) and event.get("success") is False:
            self.step_hint.setText("요청을 완료하지 못했어요. 장치 상세에서 원인을 확인해 주세요.")

    def _stop_pending_ui(self):
        for name in ("state_timer", "ik_debounce"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        if hasattr(self, "jog_timer"):
            self._stop_jog()

    def cancel_preparation(self):
        self._stop_pending_ui()
        super().cancel_preparation()

    def done(self, result):
        self._stop_pending_ui()
        super().done(result)

    def closeEvent(self, event):
        self._stop_pending_ui()
        super().closeEvent(event)


class ProfileDialogV2(UserProfileDialog):
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
