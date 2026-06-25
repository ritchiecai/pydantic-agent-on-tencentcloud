# 设计：腾讯云 AI 产品集成 showcase（智能数据分析助手）

- **关联 GitHub Issue**: *待补*
- **Multica 工单**: BUB-25
- **状态**: 初稿（已落地实现）

---

## 背景与目标

### 背景

仓库当前是一个基础的 pydantic-ai agent MVP（FastAPI `/chat` + `/healthz`，单轮、`server_time`
工具）。客户希望把腾讯云的 AI 相关产品组合接入进来作为 showcase。本期接入两个：

- **腾讯云 Agent Runtime**（代码沙箱，E2B 兼容）—— 给 agent 提供安全代码执行能力。
- **腾讯云数据库 Agent Memory**（短期压缩 + 长期四层金字塔记忆）—— 给 agent 提供跨会话个性化。

后续会持续接入更多腾讯云 AI 产品（浏览器沙箱、知识库、Skills 等），因此**架构必须可扩展**。

### 目标

1. 用一个清晰的业务故事——**「智能数据分析助手 / Data Analyst Copilot」**——把两个产品组合起来
   讲清楚：用户自然语言 → 检索 Memory 个性化 → Agent 生成分析代码 → 沙箱安全执行 → 写回 Memory 沉淀。
2. 真实调用腾讯云服务（不做 mock 仿真），方便向客户演示真实接入路径。
3. 引入 `user_id` + `session_id` 维度，正面体现 Memory 「跨会话个性化」价值。
4. 引入**可插拔集成适配层**（`app/integrations/`），后续每接入一个新产品 = 加一个模块 + 在
   agent 注册一个工具/上下文来源，避免散落耦合。
5. 保持现有「无副作用 import、无网单测、env-only 凭证、fail-fast、Terraform 一键拉起」哲学不变。

### 非目标（明确排除，避免镀金）

- ❌ 流式响应（仍 `agent.run`，非流式；与 BUB-13/18/23 取舍一致）。
- ❌ 多沙箱并发 / 沙箱池化 / 跨请求复用沙箱（单请求单沙箱、用完即 kill）。
- ❌ 接入官方 Python SDK 的所有方法签名（自研 Agent SDK 接口未完全公开；本期用 HTTP
  端点兜底，SDK 上线后只需替换 `_Backend` 实现）。
- ❌ 多 agent / 多角色编排（保持单 agent，工具丰富即可）。

---

## 业务场景设计

**智能数据分析助手 / Data Analyst Copilot**：面向企业业务/运营人员，单次 `/chat` 流程：

1. 用户带 `user_id` + `session_id` 提问，例如「分析上月销售数据并画趋势图」。
2. `app/main.py` 用 `MemoryClient` 按 `(user_id, session_id, query)` 检索个性化上下文
   （常用数据源、图表偏好、历史结论、团队规范）。
3. 检索结果以「Bullet 列表」格式注入 agent 的动态 instructions（`@agent.instructions`）。
4. 用户问题交给 agent；agent 按需调用 `run_python` 工具，把生成的 Python 代码提交
   **腾讯云 Agent Runtime 代码沙箱**执行，回收 stdout/stderr/error。
5. 模型整合结果给出中文结论。
6. `app/main.py` 把本轮 user 与 assistant 消息写回 Memory（`role` 区分），失败不阻断。

后续扩展点（在架构上预留）：
- 接入 **Runtime 浏览器沙箱** → 联网调研工具。
- 接入 **知识库 / 向量数据库** → 企业问答上下文。
- 接入 **Skills Registry** → 复用「分析骨架」技能。

---

## 关键调研结论

### 1. Agent Runtime：兼容 E2B 协议，SDK 即 `e2b-code-interpreter`

来源：[Agent Runtime 快速入门](https://cloud.tencent.com/document/product/1814/123816)。

- 控制台「API Keys」创建 API Key（形如 `ark_xxxx`）；「沙箱工具」创建 template。
- 客户端只需设置：
  ```python
  os.environ["E2B_DOMAIN"]  = "ap-guangzhou.tencentags.com"
  os.environ["E2B_API_KEY"] = "ark_xxxx"
  ```
- 用法：
  ```python
  sb = Sandbox.create(template="<工具名>", timeout=3600)
  sb.run_code(code, on_stdout=cb, timeout=600)
  sb.kill()
  ```
- 影响：**不引入腾讯云独占 SDK**，直接用 `e2b-code-interpreter`。

### 2. Agent Memory：官方 Python SDK 优先 + HTTP 兜底

来源：[Memory Python SDK 简介](https://cloud.tencent.com/document/product/1813/132134)、
[新建客户端](https://cloud.tencent.com/document/product/1813/132136)、
[`add_conversation`](https://cloud.tencent.com/document/product/1813/132157)、
[`search_conversation`](https://cloud.tencent.com/document/product/1813/132159)。

- **包名**：`tencentdb-agent-memory-sdk-python`（import `tencentdb_agent_memory`）。
  - **未上 PyPI**，由腾讯云提供本地 wheel。本仓库通过 `vendor/` 目录 + `pyproject.toml`
    的 `[project.optional-dependencies] memory-sdk` + `[tool.uv.sources]` 文件路径来整合：
    仓库默认携带占位 wheel 让 `uv lock` 满意，用户替换为真 wheel 后 `uv sync --extra
    memory-sdk` 即生效。详见 `vendor/README.md`。
- **必填三件套**：`endpoint` / `api_key` / `service_id`。
  - `Authorization: Bearer <api_key>` + `x-tdai-service-id: <service_id>`。
- **接口分四层**（按"四层金字塔"）：原始会话 / 原子记忆 / 场景记忆 / 核心记忆。
  本场景核心用到「原始会话层」两个方法：
  - 写：`add_conversation(session_id, messages=[{role, content}, ...])`，role ∈ user/assistant/system；
    单次 1~100 条；返回 `{accepted_ids, total_count, trace_id}`。
  - 检索：`search_conversation(query, *, limit=5, session_id=None, time_start, time_end)`，
    语义检索（向量）；返回 `{data:{messages:[{id, role, content, timestamp, score}]}, trace_id}`，
    已按 score 倒序。
- **多用户隔离**：SDK 仅按 `session_id` 维度组织、无独立 `user_id` 字段。本服务对外保留
  `(user_id, session_id)` 双标识，内部合成 `effective_session_id = "{user_id}:{session_id}"`
  作为 SDK 的 session_id。后续真正多租户场景可改为按租户拆 `service_id`。
- **决策**：用**薄封装 + 适配器**模式（`MemoryClient` + `_Backend` 协议）：
  - 主用 `_SdkBackend`：包装 `tencentdb_agent_memory.MemoryClient`，方法 1:1 透传。
  - 兜底 `_HttpBackend`：未拿到 wheel 时仍可走 `POST /v2/conversation/{add,search}`
    HTTP 路径，保证 demo 可跑。两个后端实现同一 `_Backend` 协议、对外门面 `MemoryClient`
    接口完全相同，**调用方零感知**。
  - 默认顺序：`_default_backend()` 先试 `_SdkBackend()`，捕 `ImportError` 回退 `_HttpBackend()`。

### 3. 出向网络：现有安全组已覆盖

`infra/main.tf` 已放行 TCP 443 出向，Agent Runtime（`ap-guangzhou.tencentags.com`）与
Agent Memory（HTTPS 实例端点）均为 443，**无需改安全组**。

---

## 架构

![architecture](../assets/architecture.svg)

### 一次 `/chat` 请求的时序

```
Client ──POST /chat (user_id,session_id,message)──▶ main.py
                                                    │
                                                    ├─► MemoryClient.retrieve_as_context
                                                    │    └─► Agent Memory (HTTPS)  ──回填── memory_context
                                                    │
                                                    ├─► agent.run(message, deps=AgentDeps{...})
                                                    │    ├─► @agent.instructions 注入 memory_context
                                                    │    └─► @agent.tool run_python(code)
                                                    │         └─► SandboxExecutor.run_python
                                                    │              └─► Agent Runtime 代码沙箱
                                                    │                   create → run_code → kill
                                                    │
                                                    └─► MemoryClient.write_turn  (user / assistant)
                                                         └─► Agent Memory (HTTPS)
```

---

## 关键代码结构

### `app/integrations/`：可插拔集成层

| 文件 | 角色 | 关键 symbol |
|---|---|---|
| `config.py` | env 读取与 fail-fast 助手 | `runtime_api_key()` `runtime_sandbox_template()` `memory_endpoint()` `memory_api_key()` `memory_service_id()` 等 |
| `sandbox.py` | Agent Runtime 代码沙箱封装 | `SandboxExecutor.run_python(code) -> ExecutionResult` |
| `memory.py` | Agent Memory 客户端 | `MemoryClient.retrieve_as_context(...)` / `write_turn(...)`；内部 `_Backend` 协议 + `_SdkBackend`（首选）/ `_HttpBackend`（兜底）+ `_effective_session_id` 隔离 |

**设计约束**（与 `build_model()` 一致）：
- 模块 import **零副作用**：不读凭证、不构造客户端、不触网。SDK 在 `_sdk_sandbox_cls()` /
  `_post()` 内部延迟 import，本地无 e2b 包也能 import 集成层做单测。
- 构造期 fail-fast：缺凭证立即抛 `RuntimeError`，错误信息**只含变量名**、不打印任何凭证值。
- 安全：HTTP 端点固定来源于 `AGENT_MEMORY_ENDPOINT` env，**不接受用户可控 URL**（规避 SSRF）。

### `app/agent.py`：注入 deps + 沙箱工具

```python
@dataclass
class AgentDeps:
    user_id: str
    session_id: str
    sandbox: "SandboxExecutor | None" = None
    memory_context: str = ""
    run_log: list[str] = field(default_factory=list)

agent = Agent(build_model(), deps_type=AgentDeps,
              instructions=_INSTRUCTIONS, defer_model_check=True)

@agent.instructions
def _inject_memory_context(ctx: RunContext[AgentDeps]) -> str:
    return ctx.deps.memory_context or ""

@agent.tool
def run_python(ctx: RunContext[AgentDeps], code: str) -> str:
    """模型生成的分析代码只在腾讯云 Agent Runtime 沙箱内执行。"""
```

`server_time` 工具保留，作为「最小可演示」回退路径。

### `app/main.py`：编排「检索 → run → 写回」

- `ChatRequest` 新增 `user_id` / `session_id`，**带默认值**（`demo-user` / `demo-session`）以
  兼容现有调用方。
- `_build_sandbox()`：缺凭证 → HTTP 500（沙箱是核心能力，绝不退化到本机 exec）。
- `_try_build_memory()`：缺凭证 → 返回 `None`，对话不被阻断（记忆是助力非必经）。
- `/healthz` 增加 `integrations` 字段（`configured` / `missing`），但 `status` 始终 `ok`，
  避免 CLB 健康检查在凭证未配齐时误把实例踢出。

---

## 配置面

### 新增环境变量（env-only，部署侧由 Terraform 注入）

| 变量 | 必填 | 说明 |
|---|---|---|
| `E2B_API_KEY` | ✅ | Agent Runtime 控制台 API Key（形如 `ark_xxxx`） |
| `SANDBOX_TEMPLATE` | ✅ | 控制台「沙箱工具」名称（E2B SDK 的 template） |
| `E2B_DOMAIN` | ⛔ | 默认 `ap-guangzhou.tencentags.com` |
| `SANDBOX_TIMEOUT` | ⛔ | 沙箱存活上限秒数，默认 600 |
| `SANDBOX_RUN_TIMEOUT` | ⛔ | 单次 `run_code` 超时秒数，默认 120 |
| `AGENT_MEMORY_ENDPOINT` | ✅ | Memory 实例访问地址 |
| `AGENT_MEMORY_API_KEY` | ✅ | Memory 实例 API Key |
| `AGENT_MEMORY_TOP_K` | ⛔ | 召回条数上限，默认 5 |
| `AGENT_MEMORY_TIMEOUT` | ⛔ | HTTP 超时秒数，默认 10 |

### Terraform 变量与脚本

- `infra/variables.tf` 新增 `runtime_api_key`（sensitive）、`sandbox_template`、`runtime_domain`、
  `memory_endpoint`、`memory_api_key`（sensitive）。
- `infra/main.tf` 在 `templatefile(...)` 实参中追加上述变量。
- `scripts/deploy_app.sh.tftpl` 在 `/etc/agent/env` 写入 `E2B_*`、`SANDBOX_TEMPLATE`、
  `AGENT_MEMORY_*`。
- `infra/terraform.tfvars.example` 补充非敏感占位，敏感值仍走 `TF_VAR_*`：
  ```
  export TF_VAR_runtime_api_key=...
  export TF_VAR_memory_api_key=...
  ```

### 依赖

`pyproject.toml` 增加：
- `e2b-code-interpreter`（腾讯云 Agent Runtime 兼容 E2B 协议）。
- `httpx`（Memory HTTP 兜底客户端）。

---

## 测试策略

| 层次 | 方法 | 验证点 |
|---|---|---|
| 单测（无网） | `tests/test_integrations.py` | (a) env fail-fast：缺 `E2B_API_KEY`/`SANDBOX_TEMPLATE`/`AGENT_MEMORY_*` 抛 `RuntimeError` 且信息含变量名；(b) SandboxExecutor 通过 monkeypatch 替换 fake SDK，验证 `create → run_code → kill` 生命周期；(c) MemoryClient 用 fake `_Backend` 验证格式化、降级（检索失败返回空串、写失败静默） |
| 单测（无网） | `tests/test_agent.py`（扩展） | (a) 沿用 TestModel 验证 `server_time` 可触发；(b) 注入 `FakeSandbox` 验证 `run_python` 把代码转发给沙箱；(c) deps 未注入沙箱时 `run_python` 返回错误文本而非崩溃 |
| 真实联调 | 本地或部署后 `curl /chat` | 真实触达腾讯云 Agent Runtime + Agent Memory，验证演示路径 |

`pytest` 全程不触网、不需要任何 API key（包括腾讯云凭证）。

---

## 安全

- **代码执行隔离**：模型生成的代码**只在腾讯云 Agent Runtime 沙箱内执行**，本进程绝不
  `exec` / `subprocess`。RCE 风险全部转嫁给腾讯云沙箱隔离。
- **沙箱回收**：`try/finally` 保证异常时也 `kill()`，避免空跑产生费用。
- **凭证 env-only**：所有 key 只走环境变量；错误信息只点变量名、不打印任何值。
- **SSRF 规避**：HTTP 客户端固定指向 `AGENT_MEMORY_ENDPOINT`（来自 env），不接受用户可控 URL。
- **日志**：`/chat` 仅记录 `user_id` / `session_id` / 字符数 / 工具调用次数；不落消息正文、
  不落代码、不落记忆原文。沙箱 stdout/stderr 截断到 8000 字符防上下文撑爆。

---

## 取舍与备选

| 决策点 | 选择 | 备选 & 为何不选 |
|---|---|---|
| 沙箱接入 | `e2b-code-interpreter`（腾讯云 E2B 兼容） | **官方独占 SDK**：腾讯云本身就用 E2B 协议，无需另造 |
| Memory 接入 | HTTP 兜底（`_HttpBackend`） + 适配器预留 SDK 路径 | **直接绑定官方 SDK**：接口尚未完全公开，绑定风险高；薄封装 + 适配器让 SDK 上线后替换零感知 |
| 记忆调用位置 | 在 `main.py` 编排里检索 + 写回；agent 内只通过 `@agent.instructions` 注入 | **把 Memory 也做成 tool 让模型自己调**：自由度高但放大不确定性；编排式更适合「真实可演示」 |
| 用户/会话维度 | `user_id` + `session_id` 都引入 | **只引入 `session_id`**：会丢失 Memory 跨会话个性化最大卖点 |
| 沙箱失败处理 | `try/finally` 保证 `kill()`，异常上抛 | **吞掉异常返回空结果**：会让模型误以为执行成功 |
| 记忆失败处理 | 检索失败返回空串、写失败静默告警 | **失败即 500**：与「记忆是助力非必经」的产品语义不符 |
| 缺沙箱凭证处理 | 拒绝请求（HTTP 500） | **退化到模拟执行**：与「真实调用 showcase」语义冲突，可能误导客户 |
| 代码执行隔离 | 唯一执行路径 = `run_python` 沙箱工具 | **本地 fallback exec**：RCE 风险极高，红线 |

---

## 风险与缓解

1. **腾讯云 Memory Python SDK 接口尚未稳定**：当前用 HTTP 兜底；若实际控制台展示的路径/字段
   与 `/v1/memory/recall` `/v1/memory/capture` 不同，仅需调整 `_HttpBackend` 的 path/payload。
2. **沙箱启动延迟**：每次 `run_python` 创建独立沙箱，预计延迟数百 ms 到秒级。可接受（单次
   demo 体感不明显）；如需常驻沙箱再做沙箱池。
3. **token 成本**：检索注入的 bullet 列表设了 2000 字符预算；沙箱 stdout/stderr 截断到 8000
   字符，避免长输出把上下文撑爆。
4. **国内地域可达性**：Agent Runtime 默认走广州地域；Memory 实例端点由控制台展示。两者均
   HTTPS（443），现有 NAT + 安全组已覆盖。

---

### 修订记录

- 2026-06-25：初稿（沿用 BUB-13/18/23 风格），随实现一并落地。

*Designer（BUB-25），2026-06-25*
