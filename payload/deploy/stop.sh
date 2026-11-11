#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
check_user_manager
check_owned_units
systemctl --user stop ai-hub-web.service ai-hub-cliproxy.service ai-hub-mihomo.service
printf '%s\n' '已停止本项目的三个服务；未操作出货报告服务。'
