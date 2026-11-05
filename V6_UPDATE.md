# POCO v6 update

This repository now includes the v6 source snapshot installed and verified on 2026-11-05. The repository name remains POCO-v3.

- v5 uses the v3 motor control variant; v6 uses the v4 motor control variant.
- GPIO18 uses Raspberry Pi 5 RP1 PWM0 channel 2 at 2000 Hz, 50% duty.
- A dedicated buzzer process controls beep timing independently of sensor/motor I/O.
- Missing-person detection cancels active and queued posture warnings.
- Existing v4 motor control behavior is retained; this release does not resolve its separately investigated oscillation issues.

Launch: `WorkSpace/pyQt/start_pyqt_v6.sh` in the existing POCO environment.
Hardware PWM requires `dtoverlay=pwm` under `[all]` in `/boot/firmware/config.txt`, followed by reboot, and PWM sysfs write permissions for the gpio group. Discover the RP1 PWM0 controller by its device-tree node; do not select the cooling fan PWM1 controller by numeric pwmchip index.

Validation: 41 simulated-device tests passed, actual GPIO18 hardware PWM was verified silently, and v5/v6 UI/control-variant mapping was checked without starting cameras or motors. Audible quality comparison was not performed.

The upload uses the retained verified installation snapshot because the Raspberry Pi hostname was unavailable at upload time. Runtime logs, user profiles, calibration captures, backups, and machine-specific installation manifests are not part of this update.
