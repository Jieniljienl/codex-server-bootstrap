#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
require_install
check_user_manager
check_owned_units
"$AI_HUB_ROOT/.venv/bin/python" "$AI_HUB_ROOT/deploy/preflight.py" --root "$AI_HUB_ROOT"
systemctl --user restart "${AI_HUB_UNITS[@]}"
sleep 2
systemctl --user is-active --quiet "${AI_HUB_UNITS[@]}" || die '有本项目服务启动失败。使用 deploy/status.sh 检查；原有出货报告服务未改动。'
bash "$AI_HUB_ROOT/deploy/status.sh"
