#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
require_install
printf '%s\n' '请先在网页选择可用节点。在电脑端建立 SSH 本地转发：ssh -L 1455:127.0.0.1:1455 ubuntu@服务器公网IP'
printf '%s\n' '保持转发窗口打开，使用服务器生成的授权链接在电脑浏览器完成登录。不要把授权链接或令牌发到聊天中。'
run_with_hub_proxy env WRITABLE_PATH=/dev/null "$AI_HUB_ROOT/bin/cli-proxy-api" --config "$AI_HUB_ROOT/data/cliproxy/config.yaml" --codex-login --no-browser
