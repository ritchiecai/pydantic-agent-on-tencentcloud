# pydantic-agent-on-tencentcloud

terraform + pydantic agent on Tencent Cloud.

## Multica 多智能体工作流

本仓库已接入 Multica 多智能体 GitHub 事件驱动工作流。
打 issue 标签会触发对应角色 agent：

| 标签 | 触发角色 |
|---|---|
| `needs-design` | Designer-A |
| `needs-plan` | Planner-A |
| `ready-for-dev` / `changes-requested` | Developer |
| `ready-for-test` | Tester |
| `ready-for-acceptance` | Reviewer |

转发逻辑见 `.github/workflows/multica-dispatch.yml`。

---

## 应用与部署

一个基于 [pydantic-ai](https://ai.pydantic.dev/) 的 agent MVP：FastAPI + uvicorn 暴露
`/chat`（单轮）与 `/healthz`，内置一个 `server_time` 示范工具；用 Terraform 一键拉起
腾讯云资源（VPC / 子网 / 安全组 / CVM / NAT 网关 / CLB）。

### 本地运行

需要 Python ≥ 3.11 与 [`uv`](https://docs.astral.sh/uv/)。

```bash
uv sync
uv run uvicorn app.main:app --port 8000
# 另一个终端：
curl localhost:8000/healthz          # -> {"status":"ok"}
```

调 `/chat` 需要模型 provider 的 API key（默认 OpenAI）：

```bash
MODEL_API_KEY=sk-xxx uv run uvicorn app.main:app --port 8000
curl -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"现在几点？"}'
# -> {"reply":"...含 ISO 时间..."}（证明 server_time 工具被调用）
```

模型串可经 `MODEL_STRING` 切换 provider，例如 `openai:gpt-4o-mini`、
国内可达 provider 等。

### 切换到智谱（GLM）/ DeepSeek

通过 `MODEL_PROVIDER` 选择模型后端，零新依赖（DeepSeek 走 pydantic-ai 原生 provider，
智谱走 OpenAI 兼容接口）。**API key 仍统一用 `MODEL_API_KEY` 一个变量**——DeepSeek
和智谱都只用它，无需单独设 `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY`（后者仅作高级用户
本地实验回退）。

| `MODEL_PROVIDER` | `MODEL_STRING` 示例 | 说明 |
|---|---|---|
| `openai`（默认） | `openai:gpt-4o-mini` | 向后兼容现状 |
| `deepseek` | `deepseek-chat` | pydantic-ai 原生 provider |
| `zhipu` | `glm-4` | OpenAI 兼容端点（`open.bigmodel.cn`） |

本地：

```bash
# DeepSeek
MODEL_PROVIDER=deepseek MODEL_STRING=deepseek-chat MODEL_API_KEY=sk-xxx uv run uvicorn app.main:app --port 8000
# 智谱
MODEL_PROVIDER=zhipu MODEL_STRING=glm-4 MODEL_API_KEY=sk-xxx uv run uvicorn app.main:app --port 8000
```

部署时把 `MODEL_PROVIDER` 也写进 Terraform（对应 `infra/` 的 `model_provider` 变量，
默认 `openai`，见 `infra/terraform.tfvars.example`）。

> 注意：`deepseek-reasoner` 等模型对工具调用支持有限；智谱个别高级字段可能不完全
> 兼容 OpenAI 语义——遇到异常优先换 `deepseek-chat` / `glm-4` 这类主流模型。

### 测试

```bash
uv run pytest
```

`tests/test_agent.py` 用 pydantic-ai 的 `TestModel` 做无网测试，验证 agent 可构造、
`server_time` 工具可被触发，全程不需要任何 API key / 不触网。

### 部署到腾讯云（Terraform）

基础设施全部由 `infra/` 下的 Terraform 管理，复用
[tencentcloud-landing-zone-booster](https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster)
模块（锁定到 git tag `v0.1.0`）。

> ⚠️ 所有敏感凭证（腾讯云 `secret_id`/`secret_key`、`model_api_key`）**只**经
> `TF_VAR_*` 环境变量注入，绝不写进 `.tf` / `.tfvars`。`*.tfvars` 已被
> `.gitignore` 忽略（保留 `*.tfvars.example`）。

```bash
export TF_VAR_tencentcloud_secret_id=AKID...
export TF_VAR_tencentcloud_secret_key=...
export TF_VAR_model_api_key=sk-xxx

cd infra
terraform init
terraform plan
terraform apply

# 应用起来后：
terraform output -raw service_url  # -> http(s)://<CLB VIP>
```

CVM 首次启动时由 user-data 自动 `git clone` 本仓库 → `uv sync` → 安装 systemd
服务 `agent`（监听 8000）。CLB 终止 HTTPS（无证书时退化为 HTTP:80）并转发到 CVM:8000。

### 端到端验证

```bash
URL=$(terraform -chdir=infra output -raw service_url)
curl -X POST "$URL/chat" -H 'Content-Type: application/json' \
  -d '{"message":"现在几点？"}'
# -> {"reply":"...含时间字符串..."}，证明 server_time 工具在腾讯云 CVM 上被调用
```

### HTTPS 证书

MVP 默认只起 HTTP:80 监听器。需要 HTTPS 时，在腾讯云 [SSL 证书](https://console.cloud.tencent.com/ssl)
上传/申请证书，把证书 ID 经 `ssl_certificate_id`（或 `TF_VAR_ssl_certificate_id`）传入，
`terraform apply` 后会自动启用 HTTPS:443 监听器，`service_url` 也会切到 `https://`。

### 最小权限（腾讯云凭证）

`terraform apply` 所用子账号需具备创建以下资源的权限：

- `VPC`：`vpc`、`subnet`、`route table`、`route entry`
- `CVM`：`instance`、`security group`、`eip`
- `NAT 网关`：`nat gateway`
- `CLB`：`clb instance`、`clb listener`、`clb target`（绑定后端）

建议用自定义策略只授予上述 `QcloudCVM*/QcloudVPC*/QcloudCLB*` 的 `Create*/Describe*` 等
必需动作，避免使用超级管理员账号。

### 模型网络可达性

CVM 经 NAT 网关出向访问模型 provider API。国内地域（如 `ap-guangzhou`）访问
OpenAI 可能不稳定，可经 `MODEL_STRING` 切换到国内可达 provider（如腾讯混元等）。

### 回收（成本控制）

CVM + CLB + NAT 网关 + EIP 持续计费，验证完后一键回收：

```bash
cd infra
terraform destroy
```

### 目录结构

```
.
├── app/
│   ├── agent.py        # pydantic-ai Agent + server_time 工具
│   └── main.py         # FastAPI: /chat, /healthz
├── tests/
│   └── test_agent.py   # TestModel 无网单测
├── infra/
│   ├── main.tf
│   ├── variables.tf
│   ├── output.tf
│   └── terraform.tfvars.example
└── scripts/
    ├── deploy_app.sh        # 手工 SSH 部署 / systemd 安装
    └── deploy_app.sh.tftpl  # Terraform user-data 模板
```
