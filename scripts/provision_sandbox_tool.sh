#!/usr/bin/env bash
# scripts/provision_sandbox_tool.sh
#
# 一次性脚本：通过 `agr` CLI 在腾讯云 Agent Runtime 上创建一个
# code-interpreter 类型的沙箱工具（Sandbox Tool），用于本仓库的
# 「智能数据分析助手」showcase。
#
# 创建出的工具名称即应用运行时所需的 SANDBOX_TEMPLATE（E2B SDK 的
# template 参数）。脚本结束时会打印该名称，便于复制到 .env / Terraform。
#
# 前置条件：
#   1. 已安装 agr CLI：
#        curl -fsSL https://dl.tencentags.com/agr-cli/latest/install.sh | sh
#   2. 已配置云账号凭证（与运行时的 E2B_API_KEY 是不同体系！）：
#        agr init --secret-id "$TENCENTCLOUD_SECRET_ID" \
#                 --secret-key "$TENCENTCLOUD_SECRET_KEY" --non-interactive
#      或直接 export TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY。
#
# 使用：
#   bash scripts/provision_sandbox_tool.sh                          # 默认名 data-analyst-py
#   TOOL_NAME=my-tool bash scripts/provision_sandbox_tool.sh
#   bash scripts/provision_sandbox_tool.sh --dry-run                # 仅打印将执行的命令
#
# 安全：脚本不接受 / 不打印任何 SecretKey、API Key。
# 重入：tool name 重复时 agr 会报错；改名或先 `agr tool delete` 老的。

set -euo pipefail

TOOL_NAME="${TOOL_NAME:-data-analyst-py}"
TOOL_TYPE="code-interpreter"
NETWORK_MODE="SANDBOX"
DEFAULT_TIMEOUT="${TOOL_TIMEOUT:-10m}"

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

log() { printf '\033[1;36m[provision]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[provision]\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 0. 先决检查
# ---------------------------------------------------------------------------
if ! command -v agr >/dev/null 2>&1; then
  err "未找到 agr CLI；请先安装："
  err "  curl -fsSL https://dl.tencentags.com/agr-cli/latest/install.sh | sh"
  exit 127
fi

log "agr 版本与凭证状态："
agr version -o json --non-interactive | sed 's/^/  /'
status_json=$(agr status -o json --non-interactive)
echo "$status_json" | sed 's/^/  /'
if ! echo "$status_json" | grep -q '"Present":[[:space:]]*true'; then
  err "agr 未检测到云凭证。先运行："
  err "  agr init --secret-id <SECRET_ID> --secret-key <SECRET_KEY> --non-interactive"
  err "或导出 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY。"
  exit 4
fi

# ---------------------------------------------------------------------------
# 1. 幂等检查：同名工具若已存在则跳过创建（agr 同名创建会直接报错）
# ---------------------------------------------------------------------------
existing_id=$(agr tool list --limit 100 -o json --non-interactive 2>/dev/null \
  | python3 -c '
import json, sys, os
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
name = os.environ.get("TOOL_NAME", "")
for item in (d.get("Data") or {}).get("Items", []) or []:
    if item.get("ToolName") == name:
        print(item.get("ToolId", ""))
        break
' || true)
if [ -n "${existing_id}" ]; then
  log "已存在同名沙箱工具：ToolName=${TOOL_NAME} ToolId=${existing_id}，跳过创建。"
  tool_id="${existing_id}"
  SKIP_CREATE=1
else
  SKIP_CREATE=0
fi

# ---------------------------------------------------------------------------
# 2. 探测 agr 当前版本接受的参数风格
#
# - GitHub v0.6.2 README：--tool-name / --tool-type / --network-configuration JSON
# - 官方文档（1814/132210，稍滞后）：--name / --type / --network
# 用 `agr tool create --help` 探测一次，按命中的参数名构造命令，避免 unknown flag。
# ---------------------------------------------------------------------------
if [ "${SKIP_CREATE}" -eq 0 ]; then
help_text=$(agr tool create --help 2>&1 || true)
if echo "${help_text}" | grep -q -- '--tool-name'; then
  STYLE="github"
elif echo "${help_text}" | grep -q -- '--name'; then
  STYLE="docs"
else
  err "无法识别 agr tool create 的参数风格，请手工执行 agr tool create --help 后参照其参数列表创建。"
  exit 1
fi
log "检测到 agr tool create 参数风格：${STYLE}"

# ---------------------------------------------------------------------------
# 3. 构造命令
# ---------------------------------------------------------------------------
if [ "${STYLE}" = "github" ]; then
  set -- agr tool create \
    --tool-name "${TOOL_NAME}" \
    --tool-type "${TOOL_TYPE}" \
    --network-configuration "{\"NetworkMode\":\"${NETWORK_MODE}\"}" \
    -o json --non-interactive
else
  set -- agr tool create \
    --name "${TOOL_NAME}" \
    --type "${TOOL_TYPE}" \
    --network "${NETWORK_MODE}" \
    --timeout "${DEFAULT_TIMEOUT}" \
    --tag "Key=app,Value=pydantic-agent-on-tencentcloud" \
    --tag "Key=scene,Value=data-analyst" \
    -o json --non-interactive
fi

log "即将执行："
printf '  '; printf '%q ' "$@"; printf '\n'
if [ "${DRY_RUN}" -eq 1 ]; then
  log "--dry-run，已退出。"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. 创建
# ---------------------------------------------------------------------------
create_json=$("$@")
echo "${create_json}" | sed 's/^/  /'

# 提取 ToolId
tool_id=$(printf '%s' "${create_json}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print((d.get("Data") or {}).get("ToolId", ""))
')
if [ -z "${tool_id}" ]; then
  err "未能从返回结果中解析出 ToolId，请检查上方 JSON。"
  exit 1
fi
# 注意：变量插入字符串时**必须**用 ${...} 显式定界。bash 在 `set -u` 下若紧跟中文等
# 非 ASCII 字符（如「（」），可能把变量名连同后续字节一起当作未定义变量名解析。
log "创建已受理：ToolId=${tool_id}（异步生效，下面轮询等待 ACTIVE）"

fi   # end of: if [ "${SKIP_CREATE}" -eq 0 ]

# ---------------------------------------------------------------------------
# 5. 轮询等待 ACTIVE（最多 60s）
# ---------------------------------------------------------------------------
status=""
for i in $(seq 1 30); do
  sleep 2
  detail=$(agr tool get "${tool_id}" -o json --non-interactive 2>/dev/null || true)
  status=$(printf '%s' "${detail}" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
print((d.get("Data") or {}).get("Status", ""))
')
  log "  attempt ${i}/30: Status=${status:-unknown}"
  case "${status}" in
    ACTIVE)   break ;;
    FAILED)
      err "工具创建失败。详情："
      echo "${detail}" | sed 's/^/  /' >&2
      exit 1
      ;;
  esac
done

if [ "${status:-}" != "ACTIVE" ]; then
  err "60s 内未达到 ACTIVE，请稍后再用 \`agr tool get ${tool_id}\` 检查。"
  exit 1
fi

# ---------------------------------------------------------------------------
# 6. 完成提示
#
# 用 $'...' 提前算好 ANSI 转义，heredoc 内变量展开正常（heredoc 不解释 \033）。
# ---------------------------------------------------------------------------
GREEN=$'\033[1;32m'
YELLOW=$'\033[1;33m'
CYAN=$'\033[1;36m'
RESET=$'\033[0m'

cat <<EOF

${GREEN}========================================================================${RESET}
${GREEN} 沙箱工具就绪 ✓${RESET}
${GREEN}========================================================================${RESET}
  ToolId    : ${tool_id}
  ToolName  : ${TOOL_NAME}   <- 这是应用运行时所需的 SANDBOX_TEMPLATE
  Type      : ${TOOL_TYPE}
  Network   : ${NETWORK_MODE}

把它接到本仓库：

  - 本地 .env:
      SANDBOX_TEMPLATE=${TOOL_NAME}

  - Terraform (infra/terraform.tfvars):
      sandbox_template = "${TOOL_NAME}"

  - 运行时 API Key (与 agr 凭证是${YELLOW}不同体系${RESET}, 需另行准备):
      控制台「API Keys」生成 ark_xxx -> E2B_API_KEY=ark_xxx
      Terraform 注入: export TF_VAR_runtime_api_key=ark_xxx

${CYAN}Tip:${RESET} 删除该工具: agr tool delete ${tool_id}
EOF
