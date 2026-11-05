"""Offscreen contract checks: no hardware, camera, profiles or settings are written.

Run with POCO_UI_V2_BUILD pointing to this directory and POCO_PYQT_ROOT pointing
to the original app directory. All outgoing hardware messages are intercepted.
"""
import copy
import json
import os
from pathlib import Path
import sys
import types
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(os.environ.get('POCO_PYQT_ROOT', '/home/willtek/POCO/WorkSpace/pyQt'))
BUILD = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BUILD))
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QRect
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton

messages = []

class FakeWorker(QObject):
    frame_changed = pyqtSignal(object)
    status_changed = pyqtSignal(str)
    calibration_finished = pyqtSignal(bool, str)
    measurement_started = pyqtSignal(bool, str)
    result_changed = pyqtSignal(object)
    pose_state_changed = pyqtSignal(object)
    face_state_changed = pyqtSignal(object)
    hardware_changed = pyqtSignal(object)
    hardware_event_changed = pyqtSignal(object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        self._vision_resources_shutdown = False
        self.vision_manager = self
        self.shutdown_called = False
    def isRunning(self): return self.running
    def start(self): self.running = True
    def send_hardware_command(self, command): messages.append(copy.deepcopy(command)); return True
    def start_monitor_arm_preparation(self): messages.append({'type': 'UI_PREP_START'})
    def finish_monitor_arm_preparation(self): messages.append({'type': 'UI_PREP_FINISH'})
    def start_calibration(self): messages.append({'type': 'UI_BASELINE_START'})
    def start_measurement(self): messages.append({'type': 'UI_MEASUREMENT_START'})
    def stop_measurement(self): messages.append({'type': 'UI_MEASUREMENT_STOP'})
    def stop_camera_only(self): self.running = False
    def shutdown(self): self.shutdown_called = True; self.running = False

camera = types.ModuleType('camera_worker_profile_all')
camera.CameraWorker = FakeWorker
sys.modules['camera_worker_profile_all'] = camera
manager = types.ModuleType('managers.vision_process_manager_profile')
manager.PROFILE_MODE = 'BOTH'
sys.modules['managers.vision_process_manager_profile'] = manager

# Any accidental direct serial access fails this review instead of reaching hardware.
import serial
def no_serial(*args, **kwargs): raise AssertionError('Hardware access forbidden in UI review')
serial.Serial = no_serial

import mainpyQt as legacy
from ui_v2.theme import apply_theme
from ui_v2.window import MainWindowV2
from ui_v2.dialogs import PreparationDialogV2, ProfileDialogV2

class Profiles:
    def list_profiles(self):
        return [{'slot':n, 'occupied':n < 3, 'name': ('내 책상' if n == 1 else '이전 장치 설정')} for n in range(1, 5)]
    def load_profile(self, slot):
        return {'calibration': {'ready': slot == 1, 'requires_recalibration': slot == 2}}
    def delete_profile(self, slot): raise AssertionError('Test must not delete real data')

class Settings:
    def __init__(self, *args): pass
    def load(self): return legacy.AlarmSettings()
    def save(self, settings): messages.append({'type':'SETTINGS_SAVE', 'values':settings.to_dict()})

class Config:
    def load(self): return {}

legacy.SettingsManager = Settings
legacy.HardwareConfigService = Config
legacy.UserProfileService = Profiles
legacy.MonitorArmPreparationDialog = PreparationDialogV2
legacy.UserProfileDialog = ProfileDialogV2
QMessageBox.information = lambda *args, **kwargs: QMessageBox.Ok
QMessageBox.warning = lambda *args, **kwargs: QMessageBox.Ok
QMessageBox.question = lambda *args, **kwargs: QMessageBox.No

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
apply_theme(app)
output = BUILD / 'review'
output.mkdir(exist_ok=True)
checks = []
def ok(condition, message):
    assert condition, message
    checks.append(message)
def capture(widget, filename):
    widget.show(); app.processEvents()
    widget.grab().save(str(output / filename))
    if isinstance(widget, MainWindowV2) and widget.pages.currentIndex() == 0:
        for item in (widget.phase_title, widget.phase_description):
            required = item.fontMetrics().boundingRect(QRect(0,0,item.width(),1000), Qt.TextWordWrap, item.text()).height()
            ok(item.height() >= required, 'main instruction text fits: '+item.text().replace('\n',' '))
    for item in widget.findChildren(QPushButton):
        if item.isVisibleTo(widget) and item.isEnabled():
            top = item.mapTo(widget, item.rect().topLeft())
            bottom = item.mapTo(widget, item.rect().bottomRight())
            ok(top.x() >= 0 and top.y() >= 0 and bottom.x() < widget.width() and bottom.y() < widget.height(),
               'visible button inside window: '+item.text())

w = MainWindowV2()
w.has_baseline = lambda: True
capture(w, '01_home.png')
ok(w.size().width() == 800 and w.size().height() == 480, 'main fits 800x480')
ok(w.action_button.height() >= 44, 'primary action retains a 44px touch target')
ok(not messages and w.camera_worker is None, 'opening v2 sends no hardware or camera commands')
QTest.mouseClick(w.settings_button, Qt.LeftButton)
capture(w, '02_settings.png')
ok(w.pages.currentIndex() == 1, 'settings navigation works')
ok(not messages and w.camera_worker is None, 'opening settings does not start hardware')
QTest.mouseClick(w.settings_button, Qt.LeftButton)
QTest.mouseClick(w.action_button, Qt.LeftButton)
app.processEvents()
ok(messages[-1]['type'] == 'START_MONITOR_ARM_PREPARATION', 'prepare forwards original command')
d = w.monitor_arm_preparation_dialog
capture(d, '03_connect.png')
ok(not d.next_button.isEnabled(), 'cannot advance before connected/working')
QTest.mouseClick(d.connect_button, Qt.LeftButton)
ok(messages[-1] == {'type':'MONITOR_ARM_CONNECT_ALL'}, 'connect command unchanged')
state = {'monitor_arm': {'preparation': {'all_motors_ready':True, 'working_start_completed':False,
    'movement_active':False, 'motor_angles_deg':{}, 'current_pose':{}, 'target_pose':{}}, 'calibration':{}}}
w.hardware_state_store.update(state); d.refresh_state()
QTest.mouseClick(d.working_button, Qt.LeftButton)
ok(messages[-1] == {'type':'MONITOR_ARM_MOVE_WORKING_START'}, 'working position command unchanged')
state['monitor_arm']['preparation']['working_start_completed'] = True
w.hardware_state_store.update(state); d.refresh_state()
QTest.mouseClick(d.next_button, Qt.LeftButton)
capture(d, '04_position.png')
ok(d.steps.currentIndex() == 1, 'position is the second preparation page')
QTest.mouseClick(d.apply_constants_button, Qt.LeftButton)
ok(messages[-1]['type'] == 'MONITOR_ARM_MANUAL_IK_TARGET', 'position adjustment uses original IK handler')
QTest.mousePress(d.jog_buttons[0], Qt.LeftButton)
QTest.mouseRelease(d.jog_buttons[0], Qt.LeftButton)
ok(messages[-2]['type'] == 'MONITOR_ARM_GIMBAL_JOG' and messages[-1]['type'] == 'MONITOR_ARM_GIMBAL_JOG_STOP',
   'press/release preserves original jog and stop commands')
QTest.mouseClick(d.next_button, Qt.LeftButton)
capture(d, '05_capture.png')
QTest.mouseClick(d.capture_button, Qt.LeftButton)
ok(messages[-1]['type'] == 'START_MONITOR_ARM_SENSOR_CAPTURE', 'sensor capture command unchanged')
state['monitor_arm']['calibration'] = {'running':True,'remain_sec':3.2}
w.hardware_state_store.update(state); d.refresh_state()
ok(not any(x.isEnabled() for x in d.jog_buttons), 'capture locks jog controls')
state['monitor_arm']['calibration'] = {'running':False,'session_ready':True}
w.hardware_state_store.update(state); d.refresh_state()
capture(d, '06_capture_ready.png')
QTest.mouseClick(d.finish_button, Qt.LeftButton)
ok(messages[-1]['type'] == 'FINISH_MONITOR_ARM_PREPARATION', 'finish command unchanged')
w.on_hardware_event_changed({'type':'MONITOR_ARM_PREPARATION_FINISHED','success':True,'message':'준비 완료'})
app.processEvents()
ok(w._phase == 'baseline_ready' and w.action_button.isEnabled(), 'preparation success enables baseline step')
QTest.mouseClick(w.action_button, Qt.LeftButton)
ok(messages[-1]['type'] == 'UI_BASELINE_START', 'baseline uses original prechecks and worker')
w.on_calibration_finished(True, '기준 자세를 저장했어요')
ok(w._phase == 'ready', 'baseline success enables coaching')
capture(w, '07_ready.png')
QTest.mouseClick(w.action_button, Qt.LeftButton)
ok(messages[-1]['type'] == 'UI_MEASUREMENT_START', 'measurement forwards to original worker')
w.on_measurement_started(True, '자세 코칭 중입니다.')
w.on_result_changed({'posture_type':'정상 자세', 'confidence':.98,'elapsed_sec':42,'rank_text':'불안정 자세 TOP 3\n1위 고개 숙임'})
capture(w, '08_measuring.png')
ok('%' not in w.label_CurrentPose.text(), 'confidence is not presented as a posture score')
ok('휴식 자세' in w.motion_note.text(), 'end action explains existing rest movement')
QTest.mouseClick(w.action_button, Qt.LeftButton)
ok(messages[-1]['type'] == 'MEASUREMENT_STOP_AND_REST', 'end keeps original stop and rest behavior')
w._finish_camera_off()
ok(w._phase == 'finished', 'original stop completion reaches finished screen')
capture(w, '09_finished.png')
profile = ProfileDialogV2(Profiles(), parent=w)
capture(profile, '10_profiles.png')
ok(not profile.slot_buttons[1].isEnabled(), 'invalidated profile marked unavailable before applying')
ok(not profile.slot_buttons[2].isEnabled(), 'empty profile cannot be loaded')
QTest.mouseClick(profile.slot_buttons[0], Qt.LeftButton)
ok(profile.selected_slot == 1 and profile.confirm_button.isEnabled(), 'profile selection reaches original validation')
profile.reject()
save = ProfileDialogV2(Profiles(), save_mode=True, parent=w)
capture(save, '11_save_profile.png')
save.reject()
manual = PreparationDialogV2(lambda c: messages.append(c) or True, lambda: state, parent=w, manual_only=True)
capture(manual, '12_manual.png')
manual.close()
ok(messages[-1]['type'] == 'CANCEL_MONITOR_ARM_PREPARATION', 'dialog X keeps original cancel/stop behavior')
# Closing while a slider change is debounced must not issue a later move command.
manual = PreparationDialogV2(lambda c: messages.append(c) or True, lambda: state, parent=w, manual_only=True)
manual.show(); manual.refresh_state()
manual.user_slider.setValue(manual.user_slider.value()+1)
ok(manual.ik_debounce.isActive(), 'slider movement is initially pending')
manual.close()
count = len(messages)
QTest.qWait(160)
ok(len(messages) == count, 'closing cancels any pending delayed IK command')
# Failed preparation and calibration remain retryable instead of advancing.
w._camera_shutdown_in_progress = False
w.on_monitor_arm_preparation_finished(False, '연결을 확인해 주세요')
ok(w._phase == 'idle' and w.action_button.isEnabled(), 'cancelled preparation returns to first step')
w.on_monitor_arm_preparation_finished(True, '준비 완료')
w.on_calibration_finished(False, '얼굴을 찾을 수 없어요')
ok(w._phase == 'baseline_ready' and w.btnCalibrationStart.isEnabled(), 'failed calibration stays retryable')
capture(w, '13_retry.png')
worker = w.camera_worker
QTest.mouseClick(w.exit_button, Qt.LeftButton)
app.processEvents()
ok(worker.shutdown_called and not w.isVisible(), 'top-right X calls original orderly shutdown')
(output/'checks.json').write_text(json.dumps({'success':True, 'hardware_access':'blocked', 'checks':checks, 'messages':messages}, ensure_ascii=False, indent=2))
print('UI_CHECKS_PASSED', len(checks))
