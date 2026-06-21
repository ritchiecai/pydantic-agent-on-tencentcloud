# 设计:基于 pydantic-ai 的 agent MVP(腾讯云 + Terraform)

- **关联 GitHub Issue**: [#7](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/7)
- **Multica 工单**: BUB-13
- **状态**: 修订 R1（已据 Designer-B 评审修订，待复评）

---

## 背景与目标

### 需求原文(issue #7)

> 开发一个基于 pydantic ai 的 agent,完成一个 MVP。
> 需要部署在腾讯云上,使用 terraform 管理,这里有参考:https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster。

### 目标

交付一个**最小可用**的 pydantic-ai agent 服务:

1. **一个能跑的 agent**:用 pydantic-ai 定义一个 Agent,暴露一个 HTTP 接口,接受用户输入并返回 agent 回复。
2. **可部署到腾讯云**:用 Terraform 把运行该服务的云资源一键拉起来,基础设施即代码。
3. **MVP 级别**:不做多 agent 编排、不做 RAG、不做鉴权/多租户、不做高可用集群--这些是后续 issue 的事。本 issue 只把"代码 + 基础设施"这条最小闭环跑通。

### 非目标(明确排除,避免镀金)

- ❌ 复杂的业务工具集(MVP 只内置 1 个示范工具,证明 tool 机制可用即可)
- ❌ 前端 UI(仅 HTTP API)
- ❌ 用户鉴权 / 多租户 / 配额
- ❌ 高可用、多副本、自动伸缩(单实例即可)
- ❌ CI/CD 流水线(手工 `terraform apply` + SSH 部署即可;自动化留给后续 issue)
- ❌ 监控告警体系(仅结构化日志)

---

## 方案概述

### 总体架构

```
┌──────────────┐   HTTP POST /chat    ┌──────────────────────────┐
│  调用方(curl)│ ───────────────────► │  腾讯云 CVM (单实例)      │
└──────────────┘                      │  ┌────────────────────┐  │
                                      │  │ FastAPI + uvicorn  │  │
                                      │  │  (app.py)          │  │
                                      │  │   POST /chat       │  │
                                      │  │        │           │  │
                                      │  │        ▼           │  │
                                      │  │ pydantic-ai Agent  │  │
                                      │  │   (agent.py)       │  │
                                      │  │     │  ┌────────┐  │  │
                                      │  │     └─►│ 1 tool │  │  │
                                      │  │        └────────┘  │  │
                                      │  └────────────────────┘  │
                                      │           │              │
                                      │   CLB (443) ── 公网入口   │
                                      └──────────────────────────┘
                                                 ▲
                                                 │ HTTPS
                                       (模型 API key 经环境变量注入)
```

**部署形态**：单台 CVM 跑 FastAPI 应用，前置一个 CLB 做 HTTPS 终止与公网入口。CVM 本身不挂公网 IP，经 **NAT 网关** 出向访问模型 provider API。所有云资源由 Terraform 管理。

### 技术选型(每项都有理由,不堆砌)

| 维度 | 选择 | 理由 |
|---|---|---|
| Agent 框架 | **pydantic-ai** | 需求明确指定 |
| Web 框架 | **FastAPI + uvicorn** | pydantic-ai 官方文档与 skill 示例均以 FastAPI 为集成对象;异步原生,与 pydantic-ai 的 `agent.run()` (async) 契合 |
| 运行时 | Python 3.11 + `uv` (依赖管理) | pydantic-ai 要求 3.10+;`uv` 比 pip/poetry 快,锁文件简单。**不引入未在生态里验证过的依赖** |
| 模型 provider | 由环境变量 `MODEL_STRING` 决定,默认 `openai:gpt-4o-mini` | pydantic-ai 用 `provider:model` 模型串;MVP 不绑定单一厂商,部署时再定 |
| 云主机 | 腾讯云 **CVM**(单实例) | 最简单可控的计算单元;Serverless(SCF)虽省心但冷启动与长连接对 LLM 流式不友好,MVP 不选 |
| 网络 | 自建 **VPC + 子网 + 安全组 + NAT 网关** | 不用默认 VPC，符合 landing-zone 最佳实践；安全组仅放行 CLB→CVM:8000 入向、CVM 出向 443（调模型 API）；NAT 网关让无公网 IP 的 CVM 可出公网 |
| 公网入口 | **CLB**（应用型）做 HTTPS 终止 | 把 TLS 证书、公网暴露收敛在 CLB，CVM 不直接挂公网 IP 更安全 |
| 出向公网 | **NAT 网关 + EIP** | CVM 调模型 API 需出公网；NAT 比 CVM 直挂 EIP 更安全（CVM 仍不可被公网直连），代价是网关小时费 + 出向流量费（见成本风险） |
| 对象存储 | **暂不用 COS** | MVP 无文件/持久化需求;列在备选,留待后续 |
| IaC | **Terraform** + landing-zone-booster 模块 | 需求指定参考仓库;优先复用其 `cvm-instance`/`vpc`/`clb-*` 等模块而非裸写 resource |

---

## 关键接口 / 数据结构

### 应用层(Python)

#### `app/agent.py` - Agent 定义

```python
import os
from pydantic_ai import Agent

# 模型串来自环境变量，部署时注入；本地默认值仅用于开发
MODEL_STRING = os.environ.get("MODEL_STRING", "openai:gpt-4o-mini")

agent = Agent(
    MODEL_STRING,
    instructions="你是一个简洁的中文助手。需要时调用工具获取服务器本地时间。",
)

@agent.tool_plain
def server_time() -> str:
    """返回服务器当前本地时间（ISO 8601）。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

> 说明：
> - 构造参数是 `instructions=`（**不是** `system_prompt=`，后者在 pydantic-ai 会抛 `TypeError`）。
> - 只放 1 个 `server_time` 工具，目的是**证明 tool 机制与部署环境打通**（调用方能验证“agent 真的跑在腾讯云那台机器上”）。用 `@agent.tool_plain`（无 deps），故不导入 `RunContext`。
> - `/chat` 为**单轮**：每次请求都是全新对话，不传 `message_history`。多轮留给后续 issue（见取舍表）。

#### `app/main.py` - HTTP 接口

```python
from fastapi import FastAPI
from pydantic import BaseModel
from .agent import agent

app = FastAPI(title="pydantic-ai agent MVP")

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    result = await agent.run(req.message)      # pydantic-ai 异步入口
    return ChatResponse(reply=result.output)

@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
```

> 非流式即可(MVP)。`result.output` 为 `str`(Agent 未设 `output_type`,默认文本输出)。

#### 目录结构(拟)

```
.
├── README.md
├── pyproject.toml              # uv 管理;依赖:pydantic-ai, fastapi, uvicorn
├── app/
│   ├── __init__.py
│   ├── agent.py                # Agent + 1 个示范工具
│   └── main.py                 # FastAPI app
├── tests/
│   └── test_agent.py           # 用 pydantic-ai 的 TestModel 做无网测试
├── infra/                      # Terraform（输出文件用 output.tf 单数，与 landing-zone-booster 模块一致）
│   ├── main.tf
│   ├── variables.tf
│   ├── output.tf
│   └── terraform.tfvars.example
└── scripts/
    └── deploy_app.sh           # CVM 上启动 uvicorn 的 systemd unit 安装脚本
```

### 基础设施层(Terraform,`infra/`)

复用 [tencentcloud-landing-zone-booster](https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster) 模块。关键 resource 组合:

| 资源 | 来源模块 | 作用 |
|---|---|---|
| VPC | `modules/vpc` | 专属 VPC(不污染默认网络) |
| 子网 | `modules/vpc-subnet` | CVM 所在子网 |
| 安全组 | `modules/vpc-security-group` | 仅放行入向 CLB→CVM:8000、出向 443（调模型 API） |
| CVM | `modules/cvm-instance` | 单实例，跑 FastAPI；镜像用腾讯云公共 Ubuntu 22.04 |
| NAT 网关 | `modules/nat-gateway`（+ 一个 EIP） | 子网默认路由指向它，让无公网 IP 的 CVM 能出向访问模型 API |
| CLB | `modules/clb-instance` + `modules/clb-listener` | HTTPS(443) 终止 → 转发到 CVM:8000 |
| SSL 证书 | `modules/ssl-certificate`（或上传已有证书 ID） | CLB 443 监听器引用 |

#### 关键 `variables.tf`(摘要)

```hcl
variable "tencentcloud_secret_id"  { type = string }   # 敏感,经环境变量/secret 注入
variable "tencentcloud_secret_key" { type = string }   # 敏感
variable "region" { type = string default = "ap-guangzhou" }
variable "cvm_instance_type" { type = string default = "SA2.MEDIUM4" }  # 2C4G,足够 MVP
variable "model_api_key" { type = string sensitive = true }  # 模型 provider key,经 user-data 注入 CVM
variable "model_string"  { type = string default = "openai:gpt-4o-mini" }
```

#### `output.tf`

> 文件名用单数 `output.tf`，与 landing-zone-booster 各模块的输出文件命名一致（`clb-instance`、`cvm-instance` 模块均为 `output.tf`）。

```hcl
output "service_url" {
  # clb-instance 模块输出 clb_vips（复数，是 list），取第一个 VIP
  value = "https://${module.clb.clb_vips[0]}"
}
output "cvm_private_ip" { value = module.cvm.private_ip }
```

### 配置与密钥

- **模型 API key**:通过 Terraform `user-data` 写入 CVM 的 `/etc/agent/env`(mode 0600),systemd 单元 `EnvironmentFile=` 引用。**不进 Git**。
- **腾讯云凭证**:只在本机/CI 的 `TF_VAR_*` 环境变量里,**绝不**写进 `.tf` / `.tfvars`。`terraform.tfvars.example` 只放占位符。
- `.gitignore` 必须排除 `*.tfvars`(除 `.example`)、`.terraform/`、`*.tfstate*`。

---

## 涉及的现有 symbol

本仓库目前是**全新的功能代码**(既有文件仅 Multica 工作流相关):

| 现有文件 | 关系 |
|---|---|
| `.github/workflows/multica-dispatch.yml` | **不改动**。它是 Multica 标签→agent 路由,与本 agent 服务无关 |
| `README.md` | **需更新**:追加"应用与部署"章节,说明如何本地跑 + `terraform apply` |
| `docs/designs/MUL-BUB-10-*.md`、`BUB-12-*.md` | 仅供参考风格,不改动 |

本设计**新增**全部 `app/`、`infra/`、`tests/`、`scripts/`,**不触碰** Multica 工作流配置(避免误伤已稳定的链路)。

---

## 交付与验收标准(给后续 plan/dev 用)

| 环节 | 通过条件 |
|---|---|
| 单元测试 | `pytest` 通过;`test_agent.py` 用 pydantic-ai `TestModel` 验证 agent 可构造、tool 可触发,**不触网** |
| 本地起服务 | `uv run uvicorn app.main:app --port 8000`,`curl localhost:8000/healthz` 返回 `{"status":"ok"}` |
| Terraform | `terraform plan` 无错;`apply` 后 `service_url` 可解析 |
| 端到端 | `curl -X POST https://<service_url>/chat -d '{"message":"现在几点?"}'` 返回带时间字符串的 JSON(证明 tool 被调用) |

---

## 取舍与备选

| 决策点 | 选择 | 备选 & 为何不选 |
|---|---|---|
| 部署形态 | CVM 单实例 + CLB | **SCF(云函数)**:冷启动 + 流式难做,MVP 不选;**TKE(K8s)**:对单服务过重 |
| 公网暴露 | CLB HTTPS 终止 | **CVM 直接挂 EIP + nginx 自管证书**:证书轮换与安全收敛不如 CLB;MVP 也可接受,但既然有 landing-zone 模块,CLB 更省心 |
| 依赖管理 | `uv` | **poetry/pip**:均可;选 `uv` 因快、锁文件简单。dev 阶段如团队不熟可降级为 pip + venv |
| 默认 provider | openai(可经 env 覆盖) | **固定某厂商**:MVP 应保持 provider 无关,避免锁死;国内可经 `MODEL_STRING` 换成腾讯混元等 |
| 流式输出 | 不做（`agent.run`） | **SSE 流式（`run_stream`）**：体验更好但增加接口复杂度，留给后续 issue |
| 对话轮次 | **单轮 only**（`/chat` 不带 `message_history`） | **多轮**（传 `message_history`）：MVP 不做；显式声明，避免 plan 阶段误以为要支持多轮 |
| 是否用 COS | 不用 | 用 COS 存对话历史 → MVP 无持久化需求 |
| 健康检查 | `/healthz` 浅检查 | **深度检查(真实调一次模型)**:会消耗 token 且依赖外部可用性,MVP 用浅检查 |

---

## 风险点

1. **腾讯云 Terraform provider 鉴权**:`tencentcloud_secret_id/key` 需有创建 CVM/CLB/VPC 的权限。若用子账号权限不足,`apply` 会在资源创建处失败。**缓解**:在 README 列出所需最小权限策略,dev 前用 `terraform plan` 验证。
2. **landing-zone-booster 模块版本兼容**:参考仓库的模块会迭代,具体入参可能随版本变化。**缓解**:在 `infra/main.tf` 用 `source` + 显式 `version`(git tag 或 commit)锁定,避免漂移;dev 阶段需读对应模块 `variables.tf` 对齐入参。
3. **模型 API 网络可达性**:CVM 需能出向访问所选 provider(如 OpenAI)。国内地域访问 OpenAI 可能不稳定。**缓解**:默认 `ap-guangzhou` + 可经 `MODEL_STRING` 切换到国内可达 provider;README 注明此点。
4. **密钥泄露面**:`model_api_key` 经 user-data 注入存在被同一 CVM 其它进程读取的窗口。**缓解**:user-data 写入后立即 `chmod 600` 并删脚本;MVP 可接受,生产级改用腾讯云 SSM/凭据管家(后续 issue)。
5. **HTTPS 证书来源**:CLB 443 需要证书。MVP 可用自签或 Let's Encrypt;正式证书需用户自行上传并提供 ID。**缓解**:`variables.tf` 支持 `ssl_certificate_id` 传入;无证书时 README 给出申请步骤;MVP 允许先用 HTTP(80) 监听器跑通再补 HTTPS。
6. **成本**：CVM + CLB + **NAT 网关 + EIP** 持续计费。NAT 网关小时费 + 出向流量按 GB 计费。**缓解**：用最小规格实例；README 提供 `terraform destroy` 一键回收说明；模型调用流量本就不大，NAT 成本可控。

---

### 修订记录

- **R1（据 Designer-B 评审）**：
  1. `Agent(system_prompt=...)` → `Agent(instructions=...)`（否则 pydantic-ai 抛 TypeError）。
  2. 删除未使用的 `RunContext` 导入（仅用 `@agent.tool_plain`）。
  3. `output.tf` 中 `tencentcloud_clb_instance.main.clb_vip` → `module.clb.clb_vips[0]`（模块输出为复数 list）。
  4. 新增 **NAT 网关 + EIP**，解决 CVM 无公网 IP 时无法出向调模型 API 的问题。
  5. `outputs.tf` → `output.tf`（与 landing-zone-booster 模块命名一致）。
  6. 取舍表显式声明 `/chat` 为单轮 only。

---

*Designer-A, 2026-06-21*
