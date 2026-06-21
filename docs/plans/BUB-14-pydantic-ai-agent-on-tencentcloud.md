# 实施计划:基于 pydantic-ai 的 agent MVP(腾讯云 + Terraform)

- **关联 GitHub Issue**: [#7](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/7)
- **设计稿**: [`docs/designs/BUB-13-pydantic-ai-agent-on-tencentcloud.md`](../designs/BUB-13-pydantic-ai-agent-on-tencentcloud.md)(已定稿,合并于 PR #8 / commit `2f1c749`)
- **Multica 工单**: BUB-14(本计划工单)
- **评审**: 待 Planner-B 评审

> 本计划是**文件级**任务拆解,严格对齐设计稿与需求,不增加需求外任务。每条任务给出:改/新建哪个文件、做什么、依赖哪一步、验收标准。
> 单元测试(`tests/test_agent.py`)因设计稿「交付与验收标准」明确要求 `pytest` 通过,故纳入计划(非额外镀金)。

---

## 总体落点(高层次探索结论)

当前 `main` 是全新功能仓库,既有文件仅 Multica 工作流相关:

- `.github/workflows/multica-dispatch.yml` —— **不触碰**(已稳定的标签路由,误伤代价高)。
- `README.md` —— 需追加「应用与部署」章节。
- 仓库根**没有 `.gitignore`** —— 需新建(防密钥/state 泄露)。
- 无 `pyproject.toml`、无 `app/`、无 `infra/`、无 `tests/`、无 `scripts/` —— 全部新建。

最终目录结构(与设计稿「目录结构(拟)」一致):

```
.
├── README.md                  # 改:追加应用与部署章节
├── .gitignore                 # 新建
├── pyproject.toml             # 新建:uv 管理
├── app/
│   ├── __init__.py            # 新建(空)
│   ├── agent.py               # 新建:Agent + server_time 工具
│   └── main.py                # 新建:FastAPI app
├── tests/
│   └── test_agent.py          # 新建:TestModel 无网测试
├── infra/
│   ├── main.tf                # 新建
│   ├── variables.tf           # 新建
│   ├── output.tf              # 新建(单数,与模块命名一致)
│   └── terraform.tfvars.example
└── scripts/
    └── deploy_app.sh          # 新建:CVM 上安装 systemd unit
```

**命名/类型约定**(源自设计稿,dev 需严格遵守):

- pydantic-ai `Agent(model, instructions=...)` —— **不是** `system_prompt=`。
- 仅用 `@agent.tool_plain`(无 deps),**不导入** `RunContext`。
- `/chat` 为**单轮**:每次请求全新对话,不传 `message_history`。
- Terraform 输出文件名用单数 `output.tf`;`module.clb.clb_vips[0]`(复数 list 取首)。

---

## 阶段 1:Python 应用层(任务 A1–A4)

> 依赖关系:A1 → A2;A3 依赖 A1/A2;A4 依赖 A2/A3。
> 全部本地可跑,不触网、不依赖云。

### 任务 A1 — 新建 `pyproject.toml`(依赖管理)

- **文件**: `pyproject.toml`(新建)
- **做什么**: 用 `uv` 初始化;声明运行时依赖 `pydantic-ai`、`fastapi`、`uvicorn`;开发依赖 `pytest`。指定 `requires-python = ">=3.11"`。锁定依赖版本由 `uv.lock` 生成并提交。
- **依赖**: 无(第一步)。
- **验收标准**:
  - `uv sync` 成功,生成 `uv.lock`。
  - `uv run python -c "import pydantic_ai, fastapi, uvicorn"` 不报错。
  - 仓库根**绝不**硬编码任何 API key(本文件不含密钥)。

### 任务 A2 — 新建 `app/__init__.py` + `app/agent.py`(Agent 定义)

- **文件**: `app/__init__.py`(新建,空文件,使 `app` 成为包)、`app/agent.py`(新建)
- **做什么**: 严格按设计稿「`app/agent.py` - Agent 定义」实现:
  - `MODEL_STRING = os.environ.get("MODEL_STRING", "openai:gpt-4o-mini")`。
  - `agent = Agent(MODEL_STRING, instructions="你是一个简洁的中文助手。需要时调用工具获取服务器本地时间。")`。
  - 用 `@agent.tool_plain` 定义 `server_time() -> str`,返回 `datetime.now(timezone.utc).isoformat()`。
  - **不**导入 `RunContext`;**不**设 `output_type`(默认文本)。
- **依赖**: A1(需 pydantic-ai 可导入)。
- **验收标准**:
  - `uv run python -c "from app.agent import agent, server_time; print(server_time())"` 输出合法 ISO8601 字符串。
  - 代码可 import 无副作用(不在此阶段发起任何模型调用)。

### 任务 A3 — 新建 `app/main.py`(FastAPI HTTP 接口)

- **文件**: `app/main.py`(新建)
- **做什么**: 严格按设计稿「`app/main.py` - HTTP 接口」实现:
  - `app = FastAPI(title="pydantic-ai agent MVP")`。
  - `ChatRequest(BaseModel)` 含 `message: str`;`ChatResponse(BaseModel)` 含 `reply: str`。
  - `@app.post("/chat", response_model=ChatResponse)` 内 `await agent.run(req.message)`,返回 `ChatResponse(reply=result.output)`。
  - `@app.get("/healthz")` 返回 `{"status": "ok"}`(浅检查,不调模型)。
- **依赖**: A1、A2。
- **验收标准**:
  - `uv run uvicorn app.main:app --port 8000` 启动无错。
  - `curl localhost:8000/healthz` 返回 `{"status":"ok"}`。

### 任务 A4 — 新建 `tests/test_agent.py`(单元测试,设计稿验收项)

- **文件**: `tests/test_agent.py`(新建);可能需要 `tests/__init__.py`(按 pytest 约定,若 rootdir 配置为 `app` 则可省,dev 自行判断)。
- **做什么**: 用 pydantic-ai `TestModel` 做无网测试:
  - `from pydantic_ai.models.test import TestModel`。
  - 用 `with agent.override(model=TestModel()):` 包裹,调用 `agent.run_sync(...)`(测试用同步入口,避免 async 测试样板)。
  - 断言:agent 可构造;`server_time` 工具可被触发(TestModel 默认会调用注册的 function tool);**全程不触网**。
- **依赖**: A2、A3。
- **验收标准**:
  - `uv run pytest` 全绿。
  - 测试在**无 `MODEL_STRING`/无 API key/断网**环境下仍通过(证明不触网)。

---

## 阶段 2:Terraform 基础设施层(任务 B1–B4)

> 依赖关系:B1 → B2/B3/B4。B 系列不依赖 A 系列代码本身,但端到端验收(D)需 A 完成。
> 所有敏感变量(`tencentcloud_secret_id/key`、`model_api_key`)**绝不**写进 `.tf`/`.tfvars`,只经 `TF_VAR_*` 环境变量或 user-data 注入。

### 任务 B1 — 新建 `infra/main.tf`(资源编排)

- **文件**: `infra/main.tf`(新建)
- **做什么**: 复用 [tencentcloud-landing-zone-booster](https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster) 模块,声明:
  - `terraform` 块:锁定 provider 版本;模块 `source` 用 git tag/commit 显式锁定(对应设计稿风险点 #2)。
  - `modules/vpc`、`modules/vpc-subnet`(CVM 所在子网)。
  - `modules/vpc-security-group`:入向仅 CLB→CVM:8000,出向仅 CVM:443(调模型 API)。
  - `modules/cvm-instance`:单实例,镜像腾讯云公共 Ubuntu 22.04;`user-data` 注入应用启动逻辑(见任务 D)。
  - `modules/nat-gateway` + 一个 EIP:子网默认路由指向它,让无公网 IP 的 CVM 能出向访问模型 API。
  - `modules/clb-instance` + `modules/clb-listener`:HTTPS(443) 终止 → 转发 CVM:8000。
  - SSL 证书经 `ssl_certificate_id` 引用(MVP 无证书时按设计稿风险点 #5 允许先用 HTTP 80 监听器跑通)。
- **依赖**: 无(B 系列第一步)。
- **验收标准**:
  - `terraform init` 成功(模块可解析、provider 可下载)。
  - **入参必须对齐所选模块版本的 `variables.tf`**(dev 需读对应模块入参,不可臆造参数名)。
  - 资源组合覆盖设计稿「基础设施层」资源表全部 7 行(VPC、子网、安全组、CVM、NAT+EIP、CLB、SSL 证书)。

### 任务 B2 — 新建 `infra/variables.tf`(变量声明)

- **文件**: `infra/variables.tf`(新建)
- **做什么**: 按设计稿「关键 `variables.tf`(摘要)」声明:
  - `tencentcloud_secret_id` / `tencentcloud_secret_key`(string,敏感)。
  - `region`(string,默认 `ap-guangzhou`)。
  - `cvm_instance_type`(string,默认 `SA2.MEDIUM4`)。
  - `model_api_key`(string,`sensitive = true`)。
  - `model_string`(string,默认 `openai:gpt-4o-mini`)。
  - `ssl_certificate_id`(string,可选——MVP 允许为空走 HTTP)。
- **依赖**: B1(需与 main.tf 引用一致)。
- **验收标准**:
  - `terraform validate` 通过。
  - 敏感变量均 `sensitive = true` 或仅经环境变量注入;**无任何明文密钥**。

### 任务 B3 — 新建 `infra/output.tf`(输出)

- **文件**: `infra/output.tf`(新建;**单数文件名**,与 landing-zone-booster 模块命名一致)
- **做什么**: 按设计稿:
  - `output "service_url"`:`value = "https://${module.clb.clb_vips[0]}"`。
  - `output "cvm_private_ip"`:`value = module.cvm.private_ip`。
  - **注意**:`module.clb.clb_vips` 是复数 list,必须取 `[0]`(设计稿 R1 修订点 #3)。
- **依赖**: B1。
- **验收标准**: `terraform plan` 阶段能正确计算这两个 output。

### 任务 B4 — 新建 `infra/terraform.tfvars.example`(占位示例)

- **文件**: `infra/terraform.tfvars.example`(新建)
- **做什么**: 给所有非密钥变量填占位示例值;密钥变量只写注释 `<set via TF_VAR_xxx, do NOT commit>`,**不**放真实值。
- **依赖**: B2。
- **验收标准**: 文件可被 `terraform plan -var-file=` 引用且不含任何真实密钥。

---

## 阶段 3:部署脚本与仓库卫生(任务 C1–C4)

### 任务 C1 — 新建 `.gitignore`(防泄露)

- **文件**: `.gitignore`(新建,仓库根当前**无**此文件)
- **做什么**: 至少排除:
  - `__pycache__/`、`*.pyc`、`.venv/`、`.pytest_cache/`。
  - `.terraform/`、`*.tfstate`、`*.tfstate.*`。
  - `*.tfvars`(但保留 `*.tfvars.example`)。
  - 任何含密钥的本地文件(如 `infra/terraform.tfvars`)。
- **依赖**: 无。
- **验收标准**: `git status` 不再追踪上述文件类型;真实密钥文件永不被 `git add`。

### 任务 C2 — 新建 `scripts/deploy_app.sh`(CVM 上 systemd 部署)

- **文件**: `scripts/deploy_app.sh`(新建)
- **做什么**: 在 CVM 上把应用装成 systemd 服务:
  - 安装 `uv`(若未装)、`uv sync` 装依赖。
  - 生成 systemd unit(如 `/etc/systemd/system/agent.service`),`ExecStart` 指向 `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`。
  - `EnvironmentFile=/etc/agent/env`(由 user-data 写入,mode 0600),读取 `MODEL_STRING` 与模型 API key。
  - 写完 env 文件后 `chmod 600` 并删除写入脚本(对应设计稿风险点 #4 缓解)。
  - `systemctl enable --now agent`。
- **依赖**: A 系列代码存在(脚本会 `uv sync` 它们)。
- **验收标准**: 脚本幂等可重入;不把密钥 echo 到 stdout/journal。

### 任务 C3 — 更新 `README.md`(应用与部署章节)

- **文件**: `README.md`(改,在现有 Multica 章节后追加)
- **做什么**: 追加章节:
  - **本地运行**:`uv sync` → `uv run uvicorn app.main:app --port 8000` → `curl localhost:8000/healthz`;测试 `uv run pytest`。
  - **部署到腾讯云**:经 `TF_VAR_*` 注入凭证与 `model_api_key` → `cd infra && terraform init/plan/apply` → 读 `service_url`。
  - **端到端验证**:`curl -X POST https://<service_url>/chat -d '{"message":"现在几点?"}'`。
  - **最小权限**:列出 Terraform 凭证所需最小权限策略(对应设计稿风险点 #1)。
  - **回收**:`terraform destroy` 一键回收(对应风险点 #6 成本缓解)。
  - **HTTPS 证书**:无证书时先用 HTTP 80 监听器跑通,补证书步骤见风险点 #5。
  - **模型网络可达性**:国内地域访问 OpenAI 可能不稳定,可经 `MODEL_STRING` 切换国内 provider(风险点 #3)。
- **依赖**: A/B 完成后命令行才真实可用(文档先行也可)。
- **验收标准**: 章节命令与实际代码/资源一致;不含明文密钥。

### 任务 C4 — `.github/workflows/multica-dispatch.yml` 保持不动(显式确认)

- **文件**: `.github/workflows/multica-dispatch.yml`(**不改动**)
- **做什么**: 无。仅在此显式记录:**不动 Multica 路由配置**,避免误伤已稳定链路(设计稿「涉及的现有 symbol」明确要求)。
- **依赖**: 无。
- **验收标准**: 该文件 diff 为空。

---

## 阶段 4:验收(任务 D,端到端,不做 CI 自动化)

> 设计稿非目标明确排除 CI/CD 流水线,故 D 为**手工验收**,不写 workflow 文件。

### 任务 D — 端到端手工验收

- **做什么**: 依次验证设计稿「交付与验收标准」表四行:
  1. `uv run pytest` 全绿(对应 A4)。
  2. 本地 `uv run uvicorn app.main:app --port 8000`,`curl localhost:8000/healthz` 返回 `{"status":"ok"}`(对应 A3)。
  3. `cd infra && terraform plan` 无错;`apply` 后 `service_url` 可解析(对应 B1–B4)。
  4. `curl -X POST https://<service_url>/chat -d '{"message":"现在几点?"}'` 返回带时间字符串的 JSON,证明 `server_time` 工具被调用(对应全栈)。
- **依赖**: A、B、C 全部完成。
- **验收标准**: 四项全过。失败则回到对应任务修订。

---

## 风险与边界(给 dev 的提醒,直接引用设计稿)

1. **provider 鉴权**:凭证需有创建 CVM/CLB/VPC/NAT 的权限,dev 前用 `terraform plan` 验证(README 列最小权限)。
2. **模块版本兼容**:`source` 锁 git tag/commit;dev 必须读所选模块 `variables.tf` 对齐入参,不可臆造参数。
3. **模型 API 网络可达**:国内地域访问 OpenAI 可能不稳,默认 `ap-guangzhou` + 可经 `MODEL_STRING` 切国内 provider。
4. **密钥泄露面**:user-data 写 env 文件后立即 `chmod 600` 并删脚本;生产级改用腾讯云 SSM(后续 issue)。
5. **HTTPS 证书来源**:MVP 允许先用 HTTP 80 监听器跑通,再补 HTTPS。
6. **成本**:CVM + CLB + NAT + EIP 持续计费;用最小规格,提供 `terraform destroy` 回收说明。

---

## 范围红线(明确不做,防镀金)

- ❌ 多 agent 编排、RAG、前端 UI、鉴权/多租户、高可用多副本、CI/CD、监控告警体系(均见设计稿「非目标」)。
- ❌ 多轮对话(`/chat` 单轮 only,不带 `message_history`)。
- ❌ 流式输出(用 `agent.run`,非 `run_stream`)。
- ❌ COS 对象存储。
- ❌ 任何对 Multica 工作流配置的改动。

---

*Planner-A, 2026-06-21*
