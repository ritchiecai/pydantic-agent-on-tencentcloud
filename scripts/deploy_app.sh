#!/usr/bin/env bash
# scripts/deploy_app.sh
#
# 在 CVM 上把 pydantic-ai agent 应用装成 systemd 服务（手工 SSH 部署入口）。
# 也可由 Terraform 的 user-data 模板（deploy_app.sh.tftpl）驱动同样的步骤。
#
# 用法（在 CVM 上以 root 执行）：
#   MODEL_STRING=openai:gpt-4o-mini MODEL_API_KEY=sk-xxx ./scripts/deploy_app.sh
# 或把这两个变量放进 /etc/agent/env（mode 0600），脚本会读它。
#
# 幂等可重入。不把密钥 echo 到 stdout/journal。
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/agent}"
ENV_FILE="${ENV_FILE:-/etc/agent/env}"
UNIT_FILE="/etc/systemd/system/agent.service"

# 1) 写密钥环境文件（若调用方通过 env 提供，则落地；否则保持既有 /etc/agent/env）。
if [ -n "${MODEL_API_KEY:-}" ]; then
  install -d -m 0750 "$(dirname "$ENV_FILE")"
  {
    echo "MODEL_STRING=${MODEL_STRING:-openai:gpt-4o-mini}"
    echo "MODEL_API_KEY=${MODEL_API_KEY}"
  } >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

# 2) 安装 uv。
export HOME="${HOME:-/root}"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# 3) 拉取应用代码 + 装依赖。
if [ ! -d "$APP_DIR/.git" ]; then
  git clone https://github.com/ritchiecai/pydantic-agent-on-tencentcloud.git "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi
cd "$APP_DIR"
uv sync

# 4) systemd unit。
cat >"$UNIT_FILE" <<'UNITEOF'
[Unit]
Description=pydantic-ai agent MVP (FastAPI + uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/agent
EnvironmentFile=/etc/agent/env
ExecStart=/root/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl enable --now agent.service

echo "deployed. check: systemctl status agent.service"
