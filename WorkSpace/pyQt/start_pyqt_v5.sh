#!/usr/bin/env bash
set -eu
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$APP_DIR/../.." && pwd)"
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/poco"
mkdir -p "$LOG_DIR"
cd "$APP_DIR"
exec "$PROJECT_DIR/.venv/bin/python" -u -X faulthandler mainpyQt_v5.py "$@" >> "$LOG_DIR/ui-v5.log" 2>&1
