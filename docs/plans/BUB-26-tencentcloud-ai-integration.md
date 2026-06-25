# 计划：腾讯云 AI 产品集成 showcase 实施

- **关联设计**: [BUB-25 设计文档](../designs/BUB-25-tencentcloud-ai-integration.md)
- **Multica 工单**: BUB-26
- **状态**: 已实施

---

## 范围

把腾讯云 Agent Runtime（代码沙箱，E2B 兼容）与 Agent Memory（跨会话记忆）整合进现有
pydantic-ai agent，落地「智能数据分析助手」showcase。可插拔集成层为后续接入更多腾讯云
AI 产品预留扩展点。

## 实施任务（按依赖顺序）

### 1. 可插拔集成适配层 `app/integrations/`

- 新增 `__init__.py`：导出门面 `SandboxExecutor` / `MemoryClient`。
- 新增 `config.py`：env 读取与 fail-fast 助手（`runtime_api_key` / `runtime_sandbox_template`
  / `memory_endpoint` / `memory_api_key` 等）。
- 新增 `sandbox.py`：`SandboxExecutor` 封装 `e2b-code-interpreter`，单次 `run_python` 完成
  `create → run_code → kill` 全生命周期；`ExecutionResult` 字段截断 8000 字符。
- 新增 `memory.py`：`MemoryClient` + `_Backend` 协议；当前实现 `_HttpBackend`（Bearer 鉴权，
  `POST /v1/memory/recall`、`POST /v1/memory/capture`）；预留 SDK 替换位。

### 2. 改造 `app/agent.py`

- `persona` 改为「数据分析助手」。
- 新增 `AgentDeps`（`user_id` / `session_id` / `sandbox` / `memory_context` / `run_log`）。
- 给 `Agent` 加 `deps_type=AgentDeps`。
- 新增 `@agent.instructions` 注入 `ctx.deps.memory_context`。
- 新增 `@agent.tool run_python(ctx, code)`：调用 `ctx.deps.sandbox.run_python(code)`；deps
  未注入沙箱时返回错误文本而非崩溃。
- 保留 `build_model()` 与 `@agent.tool_plain server_time()`，无副作用 import 不变。

### 3. 改造 `app/main.py`

- `ChatRequest` 新增 `user_id` / `session_id`（带默认值，兼容现有调用方）。
- 请求期按需构造：`_build_sandbox()`（缺凭证 → HTTP 500）/ `_try_build_memory()`（缺凭证 →
  返回 None 降级）。
- 编排：`memory.retrieve_as_context` → `agent.run(deps=...)` → `memory.write_turn` × 2
  （user / assistant）。
- `/healthz` 扩展 `integrations` 字段（`configured` / `missing`）；`status` 始终 `ok`，
  避免 CLB 健康检查在凭证未配齐时把实例踢出。

### 4. 依赖与配置

- `pyproject.toml`：新增 `e2b-code-interpreter`、`httpx`。
- `.env.example`：补充 Agent Runtime 与 Agent Memory 的 env 模板与说明。
- `infra/variables.tf`：新增 `runtime_api_key`（sensitive）、`sandbox_template`、
  `runtime_domain`、`memory_endpoint`、`memory_api_key`（sensitive）。
- `infra/main.tf`：`templatefile()` 实参追加上述 5 个变量。
- `infra/terraform.tfvars.example`：补充非敏感占位与 `TF_VAR_*` 说明。
- `scripts/deploy_app.sh.tftpl`：`/etc/agent/env` 写入 `E2B_API_KEY` / `SANDBOX_TEMPLATE`
  / `E2B_DOMAIN` / `AGENT_MEMORY_ENDPOINT` / `AGENT_MEMORY_API_KEY`。

### 5. 测试

- 更新 `tests/test_agent.py`：
  - 沿用 TestModel，限定 `call_tools=['server_time']` 避免触发空参 `run_python`；
  - 用 `FakeSandbox`（实现 `run_python` 协议）验证 `run_python` 工具转发；
  - 验证 deps 未注入沙箱时 `run_python` 返回错误文本而非崩溃。
- 新增 `tests/test_integrations.py`：
  - `config.*` 缺 env fail-fast；
  - `SandboxExecutor` 用 fake SDK monkeypatch，验证 `template` / `timeout` / `kill()`
    生命周期，以及异常时也 kill；
  - `MemoryClient` 用 fake `_Backend` 验证检索格式化、空结果、检索失败降级为空串、
    写回失败静默告警、构造时缺 env fail-fast。
- 全程无网、无凭证，CI 兼容现状。

### 6. 文档

- `docs/designs/BUB-25-tencentcloud-ai-integration.md`：本期设计（含架构图引用、调研结论、
  关键决策、风险）。
- `docs/plans/BUB-26-tencentcloud-ai-integration.md`：本文件。
- `docs/assets/architecture.svg`：架构图（pydantic-ai Agent + Agent Runtime + Agent Memory，
  含 retrieve / sandbox / write 三条线与可插拔集成层）。
- `README.md`：新增「智能数据分析助手 showcase」一节，含运行流程、新 env、Terraform 注入、
  扩展指引。

## 验收标准

1. `uv sync` 成功；`uv run pytest -q` 全部通过且无网（包括新增的 `test_integrations.py`）。
2. `app/integrations/*`、`app/agent.py`、`app/main.py` 在缺凭证场景下 import 零副作用、
   零网络（沿用 `defer_model_check` 与延迟构造哲学）。
3. 配齐 env 后本地：
   ```bash
   curl -X POST localhost:8000/chat \
     -H 'Content-Type: application/json' \
     -d '{"message":"用 python 算 1..10 之和","user_id":"u1","session_id":"s1"}'
   ```
   能拿到含沙箱 stdout 的回复。
4. Terraform `plan` / `apply` 无错；部署后 `curl $service_url/healthz` 返回
   `{"status":"ok", "integrations": {"sandbox":"configured","memory":"configured"}}`。

---

*Planner（BUB-26），2026-06-25*
