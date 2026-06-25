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
APP_GIT_REPO="${APP_GIT_REPO:-https://github.com/ritchiecai/pydantic-agent-on-tencentcloud.git}"
APP_GIT_REF="${APP_GIT_REF:-main}"   # 灰度部署：APP_GIT_REF=feat/xxx ./scripts/deploy_app.sh
echo "[deploy] app source: $APP_GIT_REPO @ $APP_GIT_REF"

# 确保 git 可用（精简镜像可能未预装），否则后续 clone 会因 set -e 中断。
if ! command -v git >/dev/null 2>&1; then
  yum install -y git
fi
# 切换 repo URL 时把现有仓库视作脏目录，重新 clone（避免远端不一致冲突）。
if [ -d "$APP_DIR/.git" ]; then
  current_origin=$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)
  if [ -n "$current_origin" ] && [ "$current_origin" != "$APP_GIT_REPO" ]; then
    echo "[deploy] origin changed ($current_origin -> $APP_GIT_REPO); re-cloning"
    rm -rf "$APP_DIR"
  fi
fi
if [ -d "$APP_DIR" ] && [ ! -d "$APP_DIR/.git" ]; then
  rm -rf "$APP_DIR"
fi
install -d -m 0755 "$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
  # 首次 clone：--branch 支持分支/tag；不支持 commit sha → 失败时回退到默认 clone + detach checkout。
  if git clone --branch "$APP_GIT_REF" --single-branch "$APP_GIT_REPO" "$APP_DIR" 2>/dev/null; then
    echo "[deploy] cloned $APP_GIT_REF directly"
  else
    echo "[deploy] $APP_GIT_REF is not a branch/tag; clone default then checkout"
    git clone "$APP_GIT_REPO" "$APP_DIR"
    git -C "$APP_DIR" checkout --detach "$APP_GIT_REF"
  fi
else
  git -C "$APP_DIR" fetch --tags --prune origin
  git -C "$APP_DIR" checkout --force "$APP_GIT_REF"
  if git -C "$APP_DIR" show-ref --verify --quiet "refs/heads/$APP_GIT_REF"; then
    git -C "$APP_DIR" reset --hard "origin/$APP_GIT_REF"
  fi
fi
cd "$APP_DIR"
echo "[deploy] checked out: $(git rev-parse --short HEAD) ($APP_GIT_REF)"

# PyPI 镜像加速：默认走腾讯云软件源（CVM 内网直连免流）。
# 本地或非腾讯云环境跑此脚本时，可改 PIP_INDEX_URL 或设为空走默认 pypi.org。
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.cloud.tencent.com/pypi/simple}"
if [ -n "$PIP_INDEX_URL" ]; then
  export UV_DEFAULT_INDEX="$PIP_INDEX_URL"
  echo "[deploy] uv index: $PIP_INDEX_URL"
fi
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"

# 与 tftpl 一致的 memory-sdk extra 探测兜底。
if grep -q '^memory-sdk' pyproject.toml 2>/dev/null \
   || grep -qE '^\s*memory-sdk\s*=' pyproject.toml 2>/dev/null; then
  uv sync --extra memory-sdk
else
  uv sync
fi

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
