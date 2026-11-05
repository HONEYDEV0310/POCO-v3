import os
"""New shell around the original MainWindow and its existing action handlers."""
import math
import re
import time
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QStackedWidget,
    QSizePolicy, QMessageBox, QDialog, QScrollArea, QProgressBar,
)
import mainpyQt as legacy
from .theme import STYLE, role, label, button, card, close_button

COMBINED_CAPTURE_TIMEOUT_SEC = 20.0


class MainWindowV3(legacy.MainWindow):
    def __init__(self, parent=None, *, autostart=False):
        self._boot_autostart = bool(autostart)
        self._ui_ready = False
        self._phase = "idle"
        self._raw_status = ""
        self._last_result = {}
        self._capture_on_main = False
        self._capture_disabled_widgets = []
        self._combined_capture = {
            "active": False,
            "attempted": False,
            "sensor_done": False,
            "imu_done": False,
            "imu_success": False,
            "pose_done": False,
            "pose_success": False,
            "started_at": None,
            "failure": "",
        }
        super().__init__(parent)
        self.setWindowTitle("POCO · UI v3")
        self.setMinimumSize(800, 480)
        self.resize(800, 480)
        self.setStyleSheet(STYLE)
        # Keep original widgets alive: the original handlers still own all state,
        # validation, signals, IPC and shutdown. Only selected widgets are reparented.
        self._legacy_central = self.takeCentralWidget()
        self._legacy_central.setParent(self)
        self._legacy_central.hide()
        self._build_shell()
        # Reparenting designer widgets can retain their old selector/font cache.
        # Invalidate it after the whole new tree has been assembled.
        self.setStyleSheet("")
        self.setStyleSheet(STYLE)
        self._ui_ready = True
        self._view_timer = QTimer(self)
        self._view_timer.timeout.connect(self._sync_view)
        self._view_timer.start(200)
        self._sync_view()
        QTimer.singleShot(300, self._start_startup_preparation)

    def _start_startup_preparation(self):
        """Boot goes straight to connect/rest; manual launch offers profiles."""
        if self._app_closing or self._phase != "idle":
            return
        if self.monitor_arm_preparation_dialog is None:
            if not self._boot_autostart and self._offer_saved_profile():
                return
            self.on_calibration_clicked()

    def send_hardware_command(self, message):
        # Qt may close child dialogs after the worker's IPC queues are closed.
        if self._app_closing or self._camera_shutdown_in_progress:
            return False
        try:
            if (message.get('type') == 'START_MONITOR_ARM_PREPARATION'
                    and getattr(self, '_new_distance_session', False)):
                message = {**message, 'reset_distance_reference': True}
            return super().send_hardware_command(message)
        except (ValueError, EOFError, BrokenPipeError) as error:
            print('[POCO IPC] Command unavailable: %s' % error, flush=True)
            return False

    def _offer_saved_profile(self):
        """Return True when a saved profile was selected and applied."""
        try:
            usable = []
            mode = str(getattr(legacy, "PROFILE_MODE", "BOTH")).upper()
            require_pose = mode in ("POSE_ONLY", "BOTH")
            require_face = mode in ("FACE_ONLY", "BOTH")
            for item in self.user_profile_service.list_profiles():
                if not item.get("occupied"):
                    continue
                try:
                    bundle = self.user_profile_service.load_profile(item["slot"])
                    calibration = bundle["calibration"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if calibration.get("ready") and not calibration.get("requires_recalibration"):
                    metadata = bundle.get("metadata", {})
                    profile_dir = self.user_profile_service.slot_dir(item["slot"])
                    pose_ok = not require_pose or (
                        metadata.get("has_pose") and (profile_dir / "pose_baseline.pkl").is_file()
                    )
                    face_ok = not require_face or (
                        metadata.get("has_face") and (profile_dir / "face_baseline.pkl").is_file()
                    )
                    if pose_ok and face_ok:
                        usable.append(item)
        except Exception:
            return False
        if not usable:
            return False

        answer = QMessageBox.question(
            self,
            "저장된 설정으로 시작할까요?",
            "사용할 수 있는 저장 프로필이 있어요.\n"
            "불러오면 저장된 화면 위치와 기준값으로 바로 시작합니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False

        self.on_user_profile_clicked()
        return self._pending_profile_slot is not None

    def start_combined_preparation_capture(self):
        if self._combined_capture.get("active"):
            return False
        worker = self.camera_worker
        if worker is None or not hasattr(worker, "start_combined_preparation_capture"):
            return False
        if not worker.isRunning() or not worker.result_worker.is_ready:
            return False
        self._combined_capture = {
            "active": True,
            "attempted": True,
            "sensor_done": False,
            "imu_done": False,
            "imu_success": False,
            "pose_done": False,
            "pose_success": False,
            "started_at": time.monotonic(),
            "failure": "",
        }
        if not worker.start_combined_preparation_capture():
            self._combined_capture.update(active=False, failure="통합 기준 저장을 시작하지 못했어요.")
            return False
        self.set_status("거리 기준을 맞춘 뒤 자세를 저장하고 있어요. 정면을 유지해 주세요.")
        print('[POCO capture] Started: position + IMU + camera.', flush=True)
        return True

    def _set_main_capture_mode(self, active):
        if bool(active) == self._capture_on_main:
            return
        self._capture_on_main = bool(active)
        if active:
            widgets = (self.btnUserProfile, self.btnManualArm, self.settings_button, self.btnReport)
            self._capture_disabled_widgets = [(widget, widget.isEnabled()) for widget in widgets]
            for widget, _ in self._capture_disabled_widgets:
                widget.setEnabled(False)
        else:
            for widget, enabled in self._capture_disabled_widgets:
                widget.setEnabled(enabled)
            self._capture_disabled_widgets = []

    def show_main_calibration(self, dialog):
        if dialog is not self.monitor_arm_preparation_dialog:
            return
        dialog.hide()
        self.pages.setCurrentIndex(0)
        self._set_main_capture_mode(True)
        self._phase = "capture_ready"
        self.set_status("카메라에서 얼굴과 어깨를 확인한 뒤 5초 동안 자세 기준을 함께 저장해 주세요.")
        self._sync_view()

    def _return_to_position(self):
        dialog = self.monitor_arm_preparation_dialog
        if (not self._capture_on_main or dialog is None
                or self._combined_capture.get("active") or dialog._finish_requested):
            return
        # A new position needs a new baseline, but keep the camera/preparation
        # session alive. Do not rerun connection or automatically move to rest.
        if self._combined_capture.get("attempted"):
            self.cancel_combined_preparation_capture()
            self._combined_capture["failure"] = ""
            self.camera_worker.start_monitor_arm_preparation()
        self._set_main_capture_mode(False)
        self._phase = "preparing"
        dialog._go_step(1)
        dialog.show()
        self._sync_view()

    def _sync_main_capture(self):
        self.coach_state_pages.setCurrentIndex(1 if self._capture_on_main else 0)
        if not self._capture_on_main:
            return
        dialog = self.monitor_arm_preparation_dialog
        if dialog is None:
            return
        dialog.refresh_state()
        combined = self.combined_preparation_capture_state()
        if dialog._finish_requested:
            self._phase = "capture_finishing"
        elif combined.get("active"):
            self._phase = "capturing"
        elif combined.get("ready"):
            self._phase = "capture_done"
        elif combined.get("failure"):
            self._phase = "capture_failed"
        else:
            self._phase = "capture_ready"
        self.main_capture_progress.setValue(dialog.capture_progress.value())
        self.main_capture_summary.setText(dialog.capture_summary.text())
        if self._phase == "capture_ready":
            self.main_capture_summary.setText(
                "얼굴과 어깨가 보이게 앉아 주세요.\n버튼을 누르면 자세 기준을 함께 저장해요."
                if combined.get("camera_ready") else "카메라와 AI를 준비하고 있어요. 잠시 기다려 주세요."
            )
        elif self._phase == "capturing":
            remaining = max(0, math.ceil(5.0 - (time.monotonic() - combined["started_at"])))
            posture_ready = combined.get("imu_success") and combined.get("pose_success")
            self.main_capture_summary.setText(
                "눈 간격 %s  ·  자세 %s\n%s" % (
                    "완료" if combined.get("sensor_ready") else "측정 중",
                    "완료" if posture_ready else "측정 중",
                    "약 %d초 동안 지금 자세를 유지해 주세요." % remaining
                    if remaining else "측정 결과를 확인하고 있어요. 잠시 기다려 주세요.",
                )
            )
        elif self._phase == "capture_done":
            self.main_capture_summary.setText(
                "위치와 자세 기준을 모두 저장했어요.\n‘저장 완료 · 계속’을 누르면 코칭을 준비해요."
            )

    def cancel_combined_preparation_capture(self):
        worker = self.camera_worker
        if self._combined_capture.get("attempted") and worker is not None:
            cancel = getattr(worker, "cancel_combined_preparation_capture", None)
            if callable(cancel):
                cancel()
        self._combined_capture.update(active=False, attempted=False)

    def combined_preparation_capture_state(self):
        hardware = self.get_latest_hardware_state() or {}
        arm = hardware.get("monitor_arm", {}) if isinstance(hardware, dict) else {}
        calibration = arm.get("calibration", {}) if isinstance(arm, dict) else {}
        worker = self.camera_worker
        camera_ready = bool(
            worker is not None
            and worker.isRunning()
            and getattr(worker.result_worker, "is_ready", False)
        )
        sensor_ready = bool(
            calibration.get("session_ready", False)
            and self._combined_capture.get("sensor_done", False)
        )
        ready = bool(
            self._combined_capture.get("attempted")
            and not self._combined_capture.get("failure")
            and sensor_ready
            and self._combined_capture.get("imu_done")
            and self._combined_capture.get("imu_success")
            and self._combined_capture.get("pose_done")
            and self._combined_capture.get("pose_success")
        )
        return {
            **self._combined_capture,
            "camera_ready": camera_ready,
            "sensor_running": bool(calibration.get("running", False)),
            "sensor_ready": sensor_ready,
            "ready": ready,
        }

    def _update_combined_capture_completion(self):
        if not self._combined_capture.get("active"):
            return
        state = self.combined_preparation_capture_state()
        if state["ready"]:
            self._combined_capture["active"] = False
            self.set_status("눈 간격·IMU·카메라 기준을 모두 저장했어요. ‘기준 저장 완료 · 계속’을 눌러 주세요.")
            print('[POCO capture] All baselines ready; Continue enabled.', flush=True)
            return
        started = self._combined_capture.get("started_at")
        if started is not None and time.monotonic() - started >= COMBINED_CAPTURE_TIMEOUT_SEC:
            pending = []
            if not state["sensor_ready"]:
                pending.append("눈 간격")
            if not state.get("imu_success"):
                pending.append("IMU")
            if not state.get("pose_success"):
                pending.append("카메라 자세")
            self._fail_combined_capture(
                "기준 저장 응답이 늦어지고 있어요: " + ", ".join(pending)
                + ". 장치 상태와 자세를 확인한 뒤 다시 저장해 주세요."
            )

    def _fail_combined_capture(self, message):
        if not self._combined_capture.get("active"):
            return
        self._combined_capture.update(active=False, failure=str(message or "통합 기준 저장에 실패했어요."))
        worker = self.camera_worker
        cancel = getattr(worker, "cancel_combined_preparation_capture", None)
        if callable(cancel):
            cancel()
        self.btnCamOn.setEnabled(False)
        self.btnCalibrationStart.setEnabled(False)
        self.set_status(self._combined_capture["failure"])
        print('[POCO capture] Failed: %s' % self._combined_capture["failure"], flush=True)
        dialog = self.monitor_arm_preparation_dialog
        if dialog is not None and dialog.isVisible():
            dialog.refresh_state()

    def _build_shell(self):
        shell = QWidget()
        shell.setObjectName("shell")
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(20, 14, 20, 12)
        outer.setSpacing(12)
        header = QHBoxLayout()
        mark = label("p", "title")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(36, 36)
        mark.setStyleSheet("background:#3182F6;color:white;border-radius:12px;font-size:27px;font-weight:700;")
        header.addWidget(mark)
        header.addWidget(label("POCO", "title"))
        header.addWidget(label("v4 · 관절 처짐 보정" if os.environ.get("POCO_CONTROL_VARIANT") == "v4" else "v3", "muted"))
        header.addSpacing(5)
        header.addWidget(label("자세 코칭", "muted"))
        header.addStretch(1)
        for item, text, hint in ((self.btnUserProfile, "내 설정", "저장한 내 설정 불러오기"),
                                 (self.btnManualArm, "화면 조절", "모니터 위치와 기울기 조절")):
            item.setText(text)
            item.setToolTip(hint)
            item.setProperty("controlHeight", 36)
            role(item, "subtle")
            item.setFixedHeight(36)
            header.addWidget(item)
        self.settings_button = button("설정", self._toggle_settings, "subtle", 36)
        header.addWidget(self.settings_button)
        self.exit_button = close_button(self.close)
        self.exit_button.setToolTip("POCO 프로그램 종료")
        self.exit_button.setAccessibleName("POCO 프로그램 종료")
        header.addWidget(self.exit_button)
        outer.addLayout(header)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_coaching_page())
        self.pages.addWidget(self._build_settings_page())
        outer.addWidget(self.pages, 1)
        footer = QHBoxLayout()
        self.tof_monitor_badge = label("ToF  ·  측정 대기", "badge")
        self.tof_monitor_badge.setMinimumWidth(142)
        self.tof_monitor_badge.setToolTip("ToF 거리 센서의 실시간 측정값")
        footer.addWidget(self.tof_monitor_badge)
        footer.addSpacing(8)
        self.status_hint = label("화면 준비부터 차근차근 시작해요.", "muted")
        self.status_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        footer.addWidget(self.status_hint, 1)
        self.status_details_button = button("상태 보기", self._show_status, "subtle", 24)
        self.status_details_button.setFixedHeight(28)
        footer.addWidget(self.status_details_button)
        outer.addLayout(footer)
        self.setCentralWidget(shell)

    def _build_coaching_page(self):
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        left, col = card(18)
        col.setSpacing(8)
        left.setFixedWidth(246)
        steps = QHBoxLayout()
        self.step_labels = []
        for text in ("1 준비", "2 기준", "3 코칭"):
            item = label(text, "muted")
            steps.addWidget(item)
            self.step_labels.append(item)
        col.addLayout(steps)
        col.addSpacing(7)
        self.phase_title = label("", "heading", True)
        self.phase_title.setMinimumHeight(56)
        col.addWidget(self.phase_title)
        self.phase_description = label("", "muted", True)
        self.phase_description.setMinimumHeight(44)
        col.addWidget(self.phase_description)
        self.action_button = button("화면 준비 시작", self._run_action, "primary", 44)
        col.addWidget(self.action_button)
        self.secondary_action = button("", self._run_secondary, "subtle", 28)
        col.addWidget(self.secondary_action)
        self.motion_note = label("", "muted", True)
        col.addWidget(self.motion_note)
        col.addStretch(1)
        self.btnReport.setText("측정 기록 보기  →")
        self.btnReport.setProperty("controlHeight", 30)
        role(self.btnReport, "subtle")
        self.btnReport.setMinimumHeight(30)
        self.btnReport.setMaximumHeight(34)
        col.addWidget(self.btnReport)
        row.addWidget(left)
        right = QVBoxLayout()
        right.setSpacing(12)
        preview, preview_layout = card(12)
        preview_header = QHBoxLayout()
        preview_header.addWidget(label("내 자세 보기", "section"))
        preview_header.addStretch(1)
        self.camera_badge = label("준비 전", "badge")
        preview_header.addWidget(self.camera_badge)
        preview_layout.addLayout(preview_header)
        self.label.setStyleSheet("QLabel{background:#F2F6FB;color:#8B95A1;border:none;border-radius:12px;font-size:13px;}")
        self.label.setMinimumSize(0, 120)
        self.label.setMaximumSize(16777215, 16777215)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setText("카메라 미리보기\n\n준비를 시작하면 내 모습이 보여요")
        preview_layout.addWidget(self.label, 1)
        right.addWidget(preview, 1)
        state, state_layout = card(14)
        self.coach_state_pages = QStackedWidget()
        state_layout.addWidget(self.coach_state_pages)
        normal_state = QWidget()
        self.coach_state_pages.addWidget(normal_state)
        state_layout = QVBoxLayout(normal_state)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(4)
        top = QHBoxLayout()
        self.coach_caption = label("오늘도 편안한 자세로", "muted")
        top.addWidget(self.coach_caption)
        top.addStretch(1)
        self.label_totalsec.setMinimumSize(0, 0)
        self.label_totalsec.setMaximumSize(16777215, 16777215)
        role(self.label_totalsec, "muted")
        top.addWidget(self.label_totalsec)
        state_layout.addLayout(top)
        self.label_CurrentPose.setMinimumSize(0, 0)
        role(self.label_CurrentPose, "section")
        self.label_CurrentPose.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_CurrentPose.setWordWrap(True)
        self.label_CurrentPose.setText("먼저 나에게 맞는 화면 위치를 설정해요")
        state_layout.addWidget(self.label_CurrentPose)
        self.coach_detail = label("얼굴과 어깨가 카메라 안에 보이도록 앉아 주세요.", "muted", True)
        state_layout.addWidget(self.coach_detail)
        capture_state = QWidget()
        capture_layout = QVBoxLayout(capture_state)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        capture_layout.setSpacing(7)
        capture_layout.addWidget(label("자세 기준 저장", "section"))
        self.main_capture_progress = QProgressBar()
        self.main_capture_progress.setRange(0, 100)
        self.main_capture_progress.setTextVisible(False)
        self.main_capture_progress.setFixedHeight(8)
        self.main_capture_progress.setStyleSheet(
            "QProgressBar{border:0;border-radius:4px;background:#E8EDF3;min-height:8px;max-height:8px;}"
            "QProgressBar::chunk{background:#3182F6;border-radius:4px;}"
        )
        self.main_capture_progress.setValue(0)
        capture_layout.addWidget(self.main_capture_progress)
        self.main_capture_summary = label("카메라에서 내 모습을 확인해 주세요.", "muted", True)
        self.main_capture_summary.setMinimumHeight(36)
        capture_layout.addWidget(self.main_capture_summary)
        self.coach_state_pages.addWidget(capture_state)
        right.addWidget(state)
        row.addLayout(right, 1)
        return page

    def _build_settings_page(self):
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        alarm, col = card(18)
        col.addWidget(label("알림을 내게 맞게", "heading"))
        col.addWidget(label("자세가 흐트러졌을 때 알려드려요.", "muted"))
        self.checkAlarmEnabled.setText("자세 알림 사용")
        self.checkAlarmEnabled.setMinimumHeight(28)
        col.addWidget(self.checkAlarmEnabled)
        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        for idx, (text, item) in enumerate((
            ("알림까지 기다리는 시간", self.spinBadPostureDurationSec),
            ("일반 알림 반복 횟수", self.spinPostureAlertCount),
            ("강한 알림 횟수 기준", self.spinPostureStrongLimit),
            ("강한 알림 후 쉬는 시간", self.spinStrongAlertCooldownMin),
        )):
            grid.addWidget(label(text, "muted"), idx, 0)
            item.setMaximumWidth(105)
            item.setMinimumHeight(32)
            grid.addWidget(item, idx, 1)
        col.addLayout(grid)
        col.addStretch(1)
        self.btnSaveSettings.setText("알림 설정 저장")
        self.btnSaveSettings.setProperty("controlHeight", 40)
        role(self.btnSaveSettings, "primary")
        col.addWidget(self.btnSaveSettings)
        row.addWidget(alarm, 1)
        details, detail_col = card(18)
        detail_col.addWidget(label("장치와 기록", "heading"))
        detail_col.addWidget(label("연결 상태와 자세한 수치는 여기서 확인해요.", "muted", True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setStyleSheet("background:white;")
        content_col = QVBoxLayout(content)
        content_col.setContentsMargins(0, 0, 0, 0)
        self.labelSensorMonitor.setMinimumSize(0, 0)
        self.labelSensorMonitor.setWordWrap(True)
        content_col.addWidget(self.labelSensorMonitor)
        role(self.label_Rank, "muted")
        self.label_Rank.setMinimumSize(0, 80)
        self.label_Rank.setWordWrap(True)
        self.label_Rank.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        content_col.addWidget(self.label_Rank)
        content_col.addStretch(1)
        scroll.setWidget(content)
        detail_col.addWidget(scroll, 1)
        detail_col.addWidget(button("현재 상태 자세히 보기", self._show_status, "subtle"))
        row.addWidget(details, 1)
        return page

    def _toggle_settings(self):
        target = 1 - self.pages.currentIndex()
        self.pages.setCurrentIndex(target)
        self.settings_button.setText("코칭 화면" if target else "설정")

    def _show_status(self):
        QMessageBox.information(self, "현재 상태", self._raw_status or "화면 준비를 시작해 주세요.")

    def set_status(self, message):
        self._raw_status = str(message)
        super().set_status(message)
        if self._ui_ready:
            remaining = re.search(r"남은 시간\s*([0-9.]+)초", self._raw_status)
            if self._phase == "calibrating" and remaining:
                self.coach_caption.setText("내 기준 자세를 기록하고 있어요")
                self.label_CurrentPose.setText("%s초 남았어요" % remaining.group(1))
                self.coach_detail.setText("정면을 바라보고 지금 자세를 유지해 주세요.")
            self._sync_view()

    def _action(self):
        return {
            "idle": self.btnCalibration, "baseline_ready": self.btnCalibrationStart,
            "ready": self.btnCamOn, "measuring": self.btnCamOff,
            "finished": self.btnReport,
        }.get(self._phase)

    def _run_action(self):
        if self._capture_on_main:
            dialog = self.monitor_arm_preparation_dialog
            if dialog is None:
                return
            if self._phase in ("capture_ready", "capture_failed") and dialog.capture_button.isEnabled():
                dialog.capture_button.click()
            elif self._phase == "capture_done" and dialog.finish_button.isEnabled():
                dialog.finish_button.click()
            self._sync_view()
            return
        if self._phase == "preparing":
            dialog = self.monitor_arm_preparation_dialog
            if dialog is not None:
                dialog.show(); dialog.raise_(); dialog.activateWindow()
            return
        target = self._action()
        if target is not None and target.isEnabled():
            target.click()

    def _run_secondary(self):
        if self._capture_on_main:
            self._return_to_position()
            return
        target = self.btnCalibration if self._phase in ("ready", "finished") else self.btnCamOff
        if target.isEnabled():
            target.click()

    def _sync_view(self):
        if not self._ui_ready:
            return
        self._update_combined_capture_completion()
        self._sync_main_capture()
        states = {
            "idle": (0, "나에게 맞추고,\n편하게 시작해요", "화면 위치와 기준 자세를\n한 번에 저장해요.", "화면 준비 시작"),
            "preparing": (0, "시작을\n준비하고 있어요", "연결 확인 후 거리 기준을 맞춘 뒤 자세를 저장해요.", "준비 상태 보기"),
            "baseline_ready": (1, "내 기준 자세를\n기억할게요", "위치와 기준 자세를 모두 저장했어요.", "자세 코칭 시작"),
            "calibrating": (1, "자세 기준을\n기록하고 있어요", "정면을 바라보고 저장이 끝날 때까지 자세를 유지해 주세요.", "기준 저장 중…"),
            "ready": (2, "준비됐어요!\n코칭을 시작해요", "내 기준 자세로 움직임을 살펴보고 자세 변화를 알려드려요.", "자세 코칭 시작"),
            "starting": (2, "코칭을\n준비하고 있어요", "저장한 기준값과 장치 상태를 확인하고 있어요.", "코칭 준비 중…"),
            "measuring": (2, "조금씩 더\n편안한 자세로", "현재 자세와 모니터 움직임을 확인해 주세요.", "측정 끝내기"),
            "stopping": (2, "측정을 마치고\n화면을 정리해요", "모니터암이 휴식 자세로 이동하고 있어요.", "종료 처리 중…"),
            "finished": (2, "수고했어요!\n기록을 확인해요", "상세 기록에서 자세 변화를 살펴볼 수 있어요.", "측정 기록 보기"),
            "profile_loading": (0, "내 설정으로\n화면을 맞춰요", "연결 확인 후 저장된 작업 위치로 모니터암이 이동해요.", "내 설정 적용 중…"),
            "capture_ready": (1, "화면을 보며\n기준을 맞춰요", "정면을 바라보고 편하게 앉아 주세요. 5초 동안 지금 자세를 기억해요.", "5초 자세 기준 저장"),
            "capturing": (1, "지금 자세를\n유지해 주세요", "카메라를 보며 저장이 끝날 때까지 지금 자세를 유지해 주세요.", "자세 기준 저장 중…"),
            "capture_done": (1, "기준을 모두\n저장했어요", "위치와 자세 기준이 준비됐어요. 계속 버튼을 눌러 코칭으로 넘어가세요.", "저장 완료 · 계속"),
            "capture_failed": (1, "기준 저장을\n다시 시도해요", "아래 안내를 확인하고 정면을 바라본 뒤 다시 저장해 주세요.", "다시 5초 기준 저장"),
            "capture_finishing": (1, "저장을 마치고\n코칭을 준비해요", "저장 결과를 확인하고 있어요. 잠시 기다려 주세요.", "완료 확인 중…"),
        }
        step, title, description, action = states.get(self._phase, states["idle"])
        self.phase_title.setText(title)
        self.phase_description.setText(description)
        self.action_button.setText(action)
        target = self._action()
        self.action_button.setEnabled(bool(target and target.isEnabled()) or self._phase == "preparing")
        if self._capture_on_main:
            dialog = self.monitor_arm_preparation_dialog
            self.action_button.setEnabled(bool(dialog and (
                (self._phase in ("capture_ready", "capture_failed") and dialog.capture_button.isEnabled())
                or (self._phase == "capture_done" and dialog.finish_button.isEnabled())
            )))
        desired_role = "danger" if self._phase == "measuring" else "primary"
        if self.action_button.property("role") != desired_role:
            role(self.action_button, desired_role)
        self.motion_note.setText("종료하면 모니터암이 휴식 자세로 이동해요." if self._phase == "measuring" else "")
        self.motion_note.setVisible(self._phase == "measuring")
        restart = self._phase in ("ready", "finished")
        cancellable = self._phase in ("baseline_ready", "calibrating", "starting")
        self.secondary_action.setVisible(restart or cancellable)
        self.secondary_action.setText("다시 준비하기" if restart else "준비 종료 · 화면 정리")
        self.secondary_action.setEnabled((self.btnCalibration if restart else self.btnCamOff).isEnabled())
        if self._capture_on_main:
            self.secondary_action.setVisible(True)
            self.secondary_action.setText("화면 위치 다시 조절")
            self.secondary_action.setEnabled(self._phase not in ("capturing", "capture_finishing"))
        for idx, item in enumerate(self.step_labels):
            item.setStyleSheet("color:%s;font-size:11px;font-weight:%s;" % (
                "#3182F6" if idx == step else "#8B95A1", "700" if idx == step else "400"))
        running = self.camera_worker is not None and self.camera_worker.isRunning()
        self.camera_badge.setText("코칭 중" if self._phase == "measuring" else ("카메라 켜짐" if running else "준비 전"))
        status = self._raw_status.replace("\n", " ")
        for before, after in (("초기값 측정시작", "5초 기준 저장"),
                              ("초기값 준비", "화면 준비"),
                              ("ToF/눈 간격 평균", "위치 기준"),
                              ("초기값 설정", "기준 자세 설정"),
                              ("모터 자세를 조정", "화면 위치를 조절")):
            status = status.replace(before, after)
        # The full original diagnostic stays available via '상태 보기'.
        font = self.status_hint.fontMetrics()
        self.status_hint.setText(font.elidedText(status, Qt.ElideRight, max(160, self.status_hint.width())))
        self.status_hint.setToolTip(self._raw_status)
        self._sync_tof_monitor()

    def _sync_tof_monitor(self):
        state = self.get_latest_hardware_state()
        if not isinstance(state, dict):
            self.tof_monitor_badge.setText("ToF  ·  측정 대기")
            self.tof_monitor_badge.setStyleSheet(
                "QLabel{font-size:11px;font-weight:600;color:#6B7684;"
                "background:#E8EBEF;border-radius:9px;padding:4px 9px;}"
            )
            return
        tof = state.get("tof", {})
        if not isinstance(tof, dict):
            tof = {}
        value = tof.get("filtered_distance_m", tof.get("distance_m"))
        try:
            distance_cm = float(value) * 100.0
        except (TypeError, ValueError):
            distance_cm = None
        if distance_cm is not None and not math.isfinite(distance_cm):
            distance_cm = None
        valid = bool(tof.get("valid", False))
        if distance_cm is None:
            self.tof_monitor_badge.setText("ToF  ·  신호 대기")
            self.tof_monitor_badge.setStyleSheet(
                "QLabel{font-size:11px;font-weight:600;color:#6B7684;"
                "background:#E8EBEF;border-radius:9px;padding:4px 9px;}"
            )
            return
        self.tof_monitor_badge.setText("ToF  ·  %.1f cm" % distance_cm)
        background, color = ("#E8F7EF", "#1F7A4D") if valid else ("#FFF6E5", "#9A6700")
        self.tof_monitor_badge.setStyleSheet(
            "QLabel{font-size:11px;font-weight:600;color:%s;background:%s;"
            "border-radius:9px;padding:4px 9px;}" % (color, background)
        )

    def on_calibration_clicked(self):
        self._set_main_capture_mode(False)
        self._new_distance_session = True
        try:
            super().on_calibration_clicked()
        finally:
            self._new_distance_session = False
        self._phase = "preparing"
        self._sync_view()

    def on_calibration_start_clicked(self):
        # Original preconditions and sensor/vision ordering remain authoritative.
        super().on_calibration_start_clicked()
        if not self.btnCalibrationStart.isEnabled():
            self._phase = "calibrating"
        self._sync_view()

    def on_calibration_finished(self, success, message):
        if self._combined_capture.get("attempted"):
            if not self._combined_capture.get("active"):
                return
            self._combined_capture.update(
                pose_done=True,
                pose_success=bool(success),
            )
            print('[POCO capture] Camera result: %s' % bool(success), flush=True)
            self.btnCamOn.setEnabled(False)
            self.btnCalibrationStart.setEnabled(False)
            self.btnCamOff.setEnabled(True)
            if not success:
                self._fail_combined_capture(message)
            elif not self._combined_capture.get("failure"):
                self.set_status("카메라 자세 기준을 저장했어요. 다른 기준값도 확인하고 있어요.")
            self._phase = "preparing"
            self._sync_view()
            return
        super().on_calibration_finished(success, message)
        self._phase = "ready" if success else "baseline_ready"
        self.coach_caption.setText("기준 자세 설정 완료" if success else "기준 자세 설정을 확인해 주세요")
        self.label_CurrentPose.setText("이제 자세 코칭을 시작할 수 있어요" if success else "안내를 확인하고 다시 측정해 주세요")
        self.coach_detail.setText("내 기준 자세에 맞춰 자세 변화를 살펴봐요." if success else str(message))
        self._sync_view()

    def on_camera_on_clicked(self):
        super().on_camera_on_clicked()
        if not self.btnCamOn.isEnabled():
            self._phase = "starting"
        self._sync_view()

    def on_measurement_started(self, success, message):
        super().on_measurement_started(success, message)
        self._phase = "measuring" if success else ("ready" if self.btnCamOn.isEnabled() else "idle")
        self._sync_view()

    def on_camera_off_clicked(self):
        self._phase = "stopping"
        super().on_camera_off_clicked()
        self._sync_view()

    def _finish_camera_off(self):
        was_pending = self._measurement_stop_pending
        super()._finish_camera_off()
        if was_pending:
            self._phase = "finished"
            self.label.setText("이번 측정을 마쳤어요\n\n측정 기록에서 자세 변화를 확인해 보세요")
        self._sync_view()

    def on_user_profile_clicked(self):
        super().on_user_profile_clicked()
        if self._pending_profile_slot is not None:
            self._phase = "profile_loading"
        self._sync_view()

    def on_hardware_event_changed(self, event):
        super().on_hardware_event_changed(event)
        # The legacy window forwards only to visible dialogs. Our preparation
        # controller stays alive while its calibration UI is on the main page.
        dialog = self.monitor_arm_preparation_dialog
        if self._capture_on_main and dialog is not None and not dialog.isVisible():
            dialog.handle_hardware_event(event)
        if isinstance(event, dict):
            event_type = str(event.get("type", "")).upper()
            active = self._combined_capture.get("active", False)
            if event_type == "MONITOR_ARM_COMBINED_CAPTURE_STARTED" and active:
                if event.get("success") is False:
                    self._fail_combined_capture(event.get("message"))
            elif event_type == "MONITOR_ARM_SENSOR_CAPTURE_DONE" and active:
                self._combined_capture["sensor_done"] = event.get("success") is True
                print('[POCO capture] Position result: %s' % event.get("success"), flush=True)
                if event.get("success") is not True:
                    self._fail_combined_capture(event.get("message"))
            elif event_type == "HARDWARE_CALIBRATION_READY" and active:
                self._combined_capture.update(
                    imu_done=True,
                    imu_success=event.get("success") is True,
                )
                print('[POCO capture] IMU result: %s' % event.get("success"), flush=True)
                if event.get("success") is not True:
                    self._fail_combined_capture(event.get("message"))
            elif event_type == "IMU_OFFSET_SAVED" and active and event.get("success") is False:
                self._fail_combined_capture(event.get("message") or "IMU 기준값을 파일에 저장하지 못했어요.")
            elif event_type == "USER_PROFILE_APPLIED":
                self._phase = "ready" if event.get("success") else "idle"
        self._sync_view()

    def on_monitor_arm_preparation_finished(self, success, message):
        save_combined_profile = bool(
            success
            and self._combined_capture.get("attempted")
            and self.combined_preparation_capture_state().get("ready", False)
        )
        self._set_main_capture_mode(False)
        super().on_monitor_arm_preparation_finished(success, message)
        self._combined_capture.update(active=False, attempted=False)
        self.btnCalibrationStart.setEnabled(False)
        self.btnCamOn.setEnabled(bool(success))
        self.btnCamOff.setEnabled(False)
        self._phase = "ready" if success else "idle"
        if success:
            self.set_status("위치와 기준 자세를 저장했어요. 이제 자세 코칭을 시작할 수 있어요.")
            print('[POCO capture] Preparation finished; coaching ready.', flush=True)
            self.coach_caption.setText("위치와 기준 자세 저장 완료")
            self.label_CurrentPose.setText("이제 자세 코칭을 시작할 수 있어요")
            self.coach_detail.setText("저장한 기준으로 자세 변화를 살펴봐요.")
        self._sync_view()
        if save_combined_profile:
            answer = QMessageBox.question(
                self,
                "프로필 저장",
                "이번에 저장한 ToF·IMU·카메라 기준을 사용자 프로필로 저장할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self.save_current_profile()

    def on_result_changed(self, result):
        super().on_result_changed(result)
        if not self._ui_ready or self._camera_shutdown_in_progress or self._app_closing:
            return
        self._last_result = dict(result)
        posture = str(result.get("posture_type", "자세 확인 중"))
        self.coach_caption.setText("지금 내 자세")
        self.label_CurrentPose.setText(posture)
        self.label_CurrentPose.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.coach_detail.setText("화면 속 내 자세와 함께 확인해 주세요.")
        self.label_CurrentPose.setToolTip("모델 판단 신뢰도: %.1f%% (자세 점수가 아닙니다)" % (float(result.get("confidence", 0)) * 100))

    def closeEvent(self, event):
        if self._capture_on_main and self.monitor_arm_preparation_dialog is not None:
            self.monitor_arm_preparation_dialog.cancel_preparation()
        if hasattr(self, "_view_timer"):
            self._view_timer.stop()
        # This is exactly the original orderly CameraWorker/process shutdown.
        super().closeEvent(event)
