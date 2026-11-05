#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_DIR="$PROJECT_DIR/WorkSpace/tasks"
mkdir -p "$TASK_DIR"

download_model() {
    local name="$1" url="$2" checksum="$3" temp_path
    if [[ -f "$TASK_DIR/$name" ]] && printf '%s  %s\n' "$checksum" "$TASK_DIR/$name" | sha256sum --check --status; then
        printf '%s already verified\n' "$name"
        return
    fi
    temp_path="$(mktemp "$TASK_DIR/.download.XXXXXX")"
    if ! curl --fail --location --retry 3 "$url" --output "$temp_path"; then
        rm -f "$temp_path"
        return 1
    fi
    if ! printf '%s  %s\n' "$checksum" "$temp_path" | sha256sum --check --status; then
        rm -f "$temp_path"
        printf 'Checksum mismatch: %s\n' "$name" >&2
        return 1
    fi
    mv "$temp_path" "$TASK_DIR/$name"
    printf '%s downloaded and verified\n' "$name"
}

download_model face_landmarker.task \
    https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task \
    64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff
download_model pose_landmarker_heavy.task \
    https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task \
    64437af838a65d18e5ba7a0d39b465540069bc8aae8308de3e318aad31fcbc7b
