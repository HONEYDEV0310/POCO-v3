"""New shell around the original MainWindow and its existing action handlers."""
import re
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QStackedWidget,
    QSizePolicy, QMessageBox, QDialog, QScrollArea,
)
import mainpyQt as legacy
from .theme import STYLE, role, label, button, card, close_button


class MainWindowV2(legacy.MainWindow):
    def __init__(self, parent=None):
        self._ui_ready = False
        self._phase = "idle"
        self._raw_status = ""
        self._last_result = {}
        super().__init__(parent)
        self.setWindowTitle("POCO · UI v2")
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
        """Open the normal setup flow so its automatic connect/rest sequence runs."""
        if self._app_closing or self._phase != "idle":
            return
        if self.monitor_arm_preparation_dialog is None:
            self.on_calibration_clicked()

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
        if self._phase == "preparing":
            dialog = self.monitor_arm_preparation_dialog
            if dialog is not None:
                dialog.show(); dialog.raise_(); dialog.activateWindow()
            return
        target = self._action()
        if target is not None and target.isEnabled():
            target.click()

    def _run_secondary(self):
        target = self.btnCalibration if self._phase in ("ready", "finished") else self.btnCamOff
        if target.isEnabled():
            target.click()

    def _sync_view(self):
        if not self._ui_ready:
            return
        states = {
            "idle": (0, "나에게 맞추고,\n편하게 시작해요", "화면 위치를 맞춘 뒤\n내 기준 자세를 기록해요.", "화면 준비 시작"),
            "preparing": (0, "시작을\n준비하고 있어요", "모터 연결을 확인하고 휴식 자세로 이동해요.", "준비 상태 보기"),
            "baseline_ready": (1, "내 기준 자세를\n기억할게요", "정면을 바라보고 기준 자세를 유지해 주세요. 센서 준비 후 측정해요.", "기준 자세 측정"),
            "calibrating": (1, "지금 자세를\n기록하고 있어요", "화면의 안내를 따라 기준 자세를 유지해 주세요.", "기준 자세 측정 중…"),
            "ready": (2, "준비됐어요!\n코칭을 시작해요", "내 기준 자세로 움직임을 살펴보고 자세 변화를 알려드려요.", "자세 코칭 시작"),
            "starting": (2, "코칭을\n준비하고 있어요", "저장한 기준값과 장치 상태를 확인하고 있어요.", "코칭 준비 중…"),
            "measuring": (2, "조금씩 더\n편안한 자세로", "현재 자세와 모니터 움직임을 확인해 주세요.", "측정 끝내기"),
            "stopping": (2, "측정을 마치고\n화면을 정리해요", "모니터암이 휴식 자세로 이동하고 있어요.", "종료 처리 중…"),
            "finished": (2, "수고했어요!\n기록을 확인해요", "상세 기록에서 자세 변화를 살펴볼 수 있어요.", "측정 기록 보기"),
            "profile_loading": (0, "내 설정으로\n화면을 맞춰요", "연결 확인 후 저장된 작업 위치로 모니터암이 이동해요.", "내 설정 적용 중…"),
        }
        step, title, description, action = states.get(self._phase, states["idle"])
        self.phase_title.setText(title)
        self.phase_description.setText(description)
        self.action_button.setText(action)
        target = self._action()
        self.action_button.setEnabled(bool(target and target.isEnabled()) or self._phase == "preparing")
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
        for idx, item in enumerate(self.step_labels):
            item.setStyleSheet("color:%s;font-size:11px;font-weight:%s;" % (
                "#3182F6" if idx == step else "#8B95A1", "700" if idx == step else "400"))
        running = self.camera_worker is not None and self.camera_worker.isRunning()
        self.camera_badge.setText("코칭 중" if self._phase == "measuring" else ("카메라 켜짐" if running else "준비 전"))
        status = self._raw_status.replace("\n", " ")
        for before, after in (("초기값 측정시작", "기준 자세 측정"),
                              ("초기값 준비", "화면 준비"),
                              ("ToF/눈 간격 평균", "위치 기준"),
                              ("초기값 설정", "기준 자세 설정"),
                              ("모터 자세를 조정", "화면 위치를 조절")):
            status = status.replace(before, after)
        # The full original diagnostic stays available via '상태 보기'.
        font = self.status_hint.fontMetrics()
        self.status_hint.setText(font.elidedText(status, Qt.ElideRight, max(160, self.status_hint.width())))
        self.status_hint.setToolTip(self._raw_status)

    def on_calibration_clicked(self):
        super().on_calibration_clicked()
        self._phase = "preparing"
        self._sync_view()

    def on_monitor_arm_preparation_finished(self, success, message):
        super().on_monitor_arm_preparation_finished(success, message)
        self._phase = "baseline_ready" if success else "idle"
        self._sync_view()

    def on_calibration_start_clicked(self):
        # Original preconditions and sensor/vision ordering remain authoritative.
        super().on_calibration_start_clicked()
        if not self.btnCalibrationStart.isEnabled():
            self._phase = "calibrating"
        self._sync_view()

    def on_calibration_finished(self, success, message):
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
        if isinstance(event, dict) and event.get("type") == "USER_PROFILE_APPLIED":
            self._phase = "ready" if event.get("success") else "idle"
        self._sync_view()

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
        if hasattr(self, "_view_timer"):
            self._view_timer.stop()
        # This is exactly the original orderly CameraWorker/process shutdown.
        super().closeEvent(event)
