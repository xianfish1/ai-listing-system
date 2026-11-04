#!/usr/bin/env bash
# AI Listing System API 启动脚本（macOS / Linux）。
# Windows 请继续使用 start-api.bat / start-api-silent.vbs（行为不变）。
#
# 用法：
#   bash start-api.sh
#   API_PORT=8790 bash start-api.sh
#   PYTHON=/opt/homebrew/bin/python3.12 bash start-api.sh

set -euo pipefail
cd "$(dirname "$0")"

export API_PORT="${API_PORT:-8788}"
exec "${PYTHON:-python3}" -m services.api.main
