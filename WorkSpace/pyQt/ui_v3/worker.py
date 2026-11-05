"""V3 camera worker extension for one combined baseline capture."""
from camera_worker_profile_all import CameraWorker, RunMode


class CameraWorkerV3(CameraWorker):
    """Start vision baselines beside ToF/eye and IMU capture in one action."""

    def finish_monitor_arm_preparation(self):
        self.pending_preparation_start = False
        self.vision_manager.finish_monitor_arm_preparation()
        self.mode = RunMode.PREVIEW
        self.status_changed.emit("위치와 기준 자세 준비를 마쳤어요.")

    def start_combined_preparation_capture(self):
        if not self.isRunning() or not self.result_worker.is_ready:
            self.status_changed.emit("카메라와 AI가 준비될 때까지 기다려 주세요.")
            return False
        if self.hardware_calibration_preparing or self.mode == RunMode.CALIBRATING:
            self.status_changed.emit("기준값을 이미 측정하고 있어요.")
            return False

        if not self.vision_manager.send_hardware_command({
            "type": "START_MONITOR_ARM_COMBINED_CAPTURE",
            "reuse_distance_reference": True
        }):
            self.status_changed.emit("통합 기준 저장을 시작하지 못했어요.")
            return False

        self.result_worker.reset_calibration()
        self.vision_manager.start_calibration()
        self.mode = RunMode.CALIBRATING
        self.status_changed.emit(
            "눈 간격, IMU 기준, 카메라 자세 기준을 함께 측정하고 있어요."
        )
        return True

    def cancel_combined_preparation_capture(self):
        self.hardware_calibration_preparing = False
        if self.mode == RunMode.CALIBRATING:
            self.vision_manager.stop_analysis()
            self.mode = RunMode.PREVIEW
            self.vision_manager.send_main_state("PREVIEW")
        return self.vision_manager.send_hardware_command({
            "type": "CANCEL_MONITOR_ARM_COMBINED_CAPTURE"
        })
