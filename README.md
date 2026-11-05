# POCO v3

라즈베리파이에서 실행하는 자세 코칭 및 스마트 모니터암 제어 프로젝트입니다.
PyQt5 UI, MediaPipe 기반 Pose/Face 분석, ToF 거리 센서, IMU 및 서보 모터 제어를 통합합니다.

2026-11-05 실제 설치본을 기준으로 만든 v3 소스 저장소입니다. v3 시작 흐름과 통합 기준 저장 수정,
공통 하드웨어·비전 서비스, 모터 SDK, 학습된 TFLite 모델과 scaler를 포함합니다.

## v3 주요 흐름

- 저장 프로필 선택 또는 일반 준비로 시작합니다.
- **5초 위치 기준 저장**으로 ToF·눈 간격·IMU·Pose/Face 기준을 함께 저장합니다.
- 기준값이 준비되면 메인 화면으로 진행합니다.
- `--autostart`는 프로필 선택 전에 장치를 연결하고 저장된 휴식 위치를 준비합니다.
- 기존 UI와 v2 소스도 공통 기능 및 호환성 확인을 위해 보존했습니다.

## 실행 준비

대상 환경은 Raspberry Pi OS/Linux, Python 3.11, PyQt5, 카메라 및 프로젝트 센서·모터 구성입니다.

```bash
git clone https://github.com/HONEYDEV0310/POCO-v3.git
cd POCO-v3
```

기존 설치 환경에서는 프로젝트 루트의 `.venv`와 하드웨어 설정을 준비한 뒤 실행합니다.
새 장치의 환경 구성은 기존 설치 스크립트 `scripts/setup_pi.sh`를 검토하여 적용하세요.
이 스크립트는 시스템 패키지와 Python 환경을 변경합니다. `requirements.txt`는 기존 환경의
패키지 목록이며 운영체제·아키텍처에 따른 별도 의존성 조정이 필요할 수 있습니다.
새 장치에서의 전체 설치 및 실물 동작은 이 업로드 작업에서 재검증하지 않았습니다.

MediaPipe의 외부 모델 파일은 다음 명령으로 받습니다. 다운로드 후 설치본과 동일한 SHA-256인지 확인합니다.

```bash
bash scripts/download_mediapipe_models.sh
```

출처: [Face Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker),
[Pose Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker).

## 실행

```bash
bash WorkSpace/pyQt/start_pyqt_v3.sh
# 창 모드
bash WorkSpace/pyQt/start_pyqt_v3.sh --windowed
```

로그는 `${XDG_STATE_HOME:-$HOME/.local/state}/poco/ui-v3.log`에 기록됩니다.
실물 모터를 사용하기 전에 `WorkSpace/config/monitor_arm_settings.json`과
`WorkSpace/hardware/servo_calibration_result.json`을 해당 장치에 맞게 확인하세요.
포함된 모터 보정값은 원래 장치의 값이며 다른 장치에 그대로 적용하면 안 됩니다.

## 코드 구성

| 경로 | 역할 |
| --- | --- |
| `WorkSpace/pyQt/mainpyQt_v3.py` | v3 실행 진입점 |
| `WorkSpace/pyQt/ui_v3/` | v3 창·대화상자·테마·카메라 작업자 |
| `WorkSpace/pyQt/processes/` | 비전·하드웨어 프로세스 |
| `WorkSpace/pyQt/services/` | 센서·프로필·보정·모니터암 제어 서비스 |
| `WorkSpace/hardware/` | 모터 드라이버, SDK 및 장치 보정 |
| `WorkSpace/config/` | 모니터암 설정 |
| `WorkSpace/saved_model/` | 학습 모델, scaler 및 임계값 |
| `WorkSpace/streamlit/` | 기록 분석·리포트 도구 |

## 공개 범위

사용자 프로필, 개인 Pose/Face 기준값, 촬영·측정 기록, 실행 로그, 백업 및 가상환경은 포함하지 않습니다.
기준값은 앱에서 새로 측정합니다. MediaPipe `.task` 파일은 위 스크립트로 별도 다운로드합니다.
`stservo-env/scservo_sdk`에는 실행에 필요한 SDK 소스만 포함되어 있습니다.

기존 구조와 상세 동작 설명은 [기존 README](docs/README_LEGACY.md)와
[v3 사용 안내](WorkSpace/pyQt/UI_V3_README.txt)를 참고하세요.
이 저장소에 새로운 프로젝트 라이선스는 추가하지 않았습니다. 외부 라이브러리·모델의 이용 조건은 각 배포처를 따릅니다.
