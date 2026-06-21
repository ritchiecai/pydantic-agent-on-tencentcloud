# 实施计划：支持智谱（Zhipu/GLM）与 DeepSeek 接入

- **关联 GitHub Issue**: [#11](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/11) —「支持智谱和deepseek接入」
- **Multica 工单**: BUB-19
- **依据设计稿**: [docs/designs/BUB-18-zhipu-deepseek-provider-integration.md](../designs/BUB-18-zhipu-deepseek-provider-integration.md)（docs PR #12，已合并、定稿）
- **状态**: 初稿（待 Plan Reviewer 评审）

---

## 0. 计划总览

按定稿设计稿，引入一个**极薄的 provider 选择层**：新增环境变量 `MODEL_PROVIDER`
（`openai` 默认 | `deepseek` | `zhipu`），在 `app/agent.py` 里用 `build_model()` 构造对应的
pydantic-ai `Model` 对象，再交给既有 `Agent`。DeepSeek 走 pydantic-ai 原生 `deepseek:*` 路径，
智谱走 `OpenAIChatModel` + `OpenAIProvider(base_url=智谱端点)` 的 OpenAI 兼容路径，**零新运行时依赖**
（两者都复用已存在于 `uv.lock` 的 `openai` 包）。对外 API key 仍统一为 `MODEL_API_KEY`，由选择层
内部映射到各家期望的环境变量名。

- **不改**：`/chat`、`/healthz` 接口；单轮对话；`server_time` 工具；安全组出向规则；`pyproject.toml` 依赖；现有 3 个无网单测的行为（向后兼容）。
- **不新增**：多 provider 并存 / fallback / 流式 / 多轮 / 新业务工具 / 厂商官方 SDK / 新 Terraform 敏感变量。

涉及改动的文件 **6 个**（应用层 1 + 测试 1 + 基础设施 4），全部为既有文件的小改，无新建模块。
推荐按下方步骤顺序实施（每步均有依赖关系与验收标准）。

---

## 1. 步骤拆解

### 步骤 1：应用层 — 改写 `app/agent.py`

- **文件**：`app/agent.py`（既有，**改写**）
- **做什么**：
  1. 新增模块级常量 `MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "openai").strip().lower()`。
  2. 保留既有 `MODEL_STRING`、`MODEL_API_KEY`；其中 `MODEL_API_KEY` 改为读取
     `os.environ.get("MODEL_API_KEY")`（当前代码未读它，本步新增读取）。
  3. 新增辅助函数 `_model_name_only(value: str) -> str`：去掉 `provider:` 前缀，只留模型名。
  4. 新增 `build_model()` 选择层函数，按 `MODEL_PROVIDER` 分支：
     - `deepseek`：若 `MODEL_API_KEY` 非空则 `os.environ.setdefault("DEEPSEEK_API_KEY", MODEL_API_KEY)`
       （让原生 `DeepSeekProvider` 能读到 key）；模型名取 `_model_name_only(MODEL_STRING)`
       （缺省回退 `"deepseek-chat"`）；返回字符串 `f"deepseek:{name}"`。
     - `zhipu`：`api_key = MODEL_API_KEY or os.environ.get("ZHIPU_API_KEY")`，缺失则
       `raise RuntimeError(...)`（fail-fast）；`base_url = os.environ.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")`；
       `model_name = _model_name_only(MODEL_STRING)`（缺省回退 `"glm-4"`）；
       返回 `OpenAIChatModel(model_name, provider=OpenAIProvider(base_url=base_url, api_key=api_key))`。
     - 默认 `openai`（含拼写未知的值）：直接返回 `MODEL_STRING`（向后兼容现状）。
       > 设计稿「风险点 4」建议对未知 provider 取值记一条 warning 日志，**列为可选增强**，
       > 若实现成本极低可顺手加；否则保持纯默认分支，不加新依赖（如 `logging` 配置）。
  5. 把 `Agent(MODEL_STRING, ...)` 改为 `Agent(build_model(), ...)`；其余（`instructions`、
     `defer_model_check=True`、`server_time` 工具）**完全保持不变**。
  6. import：`from pydantic_ai.models.openai import OpenAIChatModel` 与
     `from pydantic_ai.providers.openai import OpenAIProvider` 放在文件顶部。**注意**：这两个
     import 是模块级，即使走 openai/deepseek 分支也会被执行——已确认这两个符号在仓库当前
     `pydantic-ai==1.107.0` 中存在（见 §3 调研），import 本身不触网、不需 key，符合「无副作用 import」。
- **依赖**：无前置依赖（本步是后续所有步骤的根）。
- **验收标准**：
  - 不设 `MODEL_PROVIDER` 时 `build_model() == MODEL_STRING`（字符串相等），`agent` 行为与现状一致。
  - `MODEL_PROVIDER=deepseek`、`MODEL_STRING=deepseek-chat`、`MODEL_API_KEY=x` 时，`build_model()`
    返回 `"deepseek:deepseek-chat"`，且 `os.environ["DEEPSEEK_API_KEY"] == "x"`。
  - `MODEL_PROVIDER=zhipu`、`MODEL_API_KEY=x` 时，`build_model()` 返回 `OpenAIChatModel` 实例，
    其 provider 的 `base_url` 指向 `https://open.bigmodel.cn/api/paas/v4/`。
  - `MODEL_PROVIDER=zhipu` 且 `MODEL_API_KEY` 与 `ZHIPU_API_KEY` 均未设时，`build_model()` 抛 `RuntimeError`。
  - `import app.agent` 在无任何 key、无网环境下成功（无副作用 import）。

### 步骤 2：测试层 — 扩展 `tests/test_agent.py`

- **文件**：`tests/test_agent.py`（既有，**追加用例**；不改写现有 3 个用例）
- **做什么**：新增针对 `build_model()` 的无网单测（设计稿「测试策略」第 1 行 a–d）：
  1. `test_build_model_default_returns_model_string`：不设 `MODEL_PROVIDER` 时，
     `build_model()` 返回 `MODEL_STRING` 字符串（用 `monkeypatch.delenv("MODEL_PROVIDER", raising=False)`
     清环境，`monkeypatch.setenv("MODEL_STRING", "openai:gpt-4o-mini")`）。
  2. `test_build_model_deepseek_returns_prefixed_string`：`monkeypatch.setenv("MODEL_PROVIDER","deepseek")`、
     `MODEL_STRING="deepseek-chat"`、`MODEL_API_KEY="x"`，断言 `build_model() == "deepseek:deepseek-chat"`，
     且 `os.environ["DEEPSEEK_API_KEY"] == "x"`。
  3. `test_build_model_zhipu_returns_openai_chat_model`：`MODEL_PROVIDER="zhipu"`、`MODEL_API_KEY="x"`，
     断言 `isinstance(build_model(), OpenAIChatModel)`；进一步断言其底层 client/provider 的 `base_url`
     指向智谱端点（可取 `OpenAIProvider` 实例的 `_base_url` 或经 `provider.client` 取 `base_url`，
     按实际可访问属性实现；若不易稳定断言，至少断言对象类型与不抛异常）。
  4. `test_build_model_zhipu_without_key_raises`：`MODEL_PROVIDER="zhipu"` 且无 `MODEL_API_KEY`/`ZHIPU_API_KEY`，
     断言 `pytest.raises(RuntimeError)`。
- **注意**：`build_model()` 在模块 import 时已被 `Agent(...)` 调用过一次（当时环境决定）。
  测试里**直接 import 并调用 `build_model()`** 即可重新求值（它是纯函数，每次调用读 `os.environ`/模块常量）。
  若模块常量在 import 时已被「冻结」为字符串而绕过 `monkeypatch`，则**改为让 `build_model()` 内部实时
  读 `os.environ`**（而非读模块级常量）——这是一个实现细节微调，留给实现者按可测性决定，**不偏离设计语义**。
  实现者须保证测试真的能用 `monkeypatch` 切换分支，而非只测了 import 时的固定分支。
- **依赖**：步骤 1（需 `build_model`、`MODEL_PROVIDER` 存在）。
- **验收标准**：
  - 4 个新用例 + 既有 3 个用例全部通过：`uv run pytest`（无网、无 key、无 `MODEL_STRING`）。
  - 现有 3 个用例（`test_agent_is_constructed`、`test_server_time_returns_iso8601`、
    `test_agent_run_invokes_server_time_tool_without_network`）**不受影响**——不设 `MODEL_PROVIDER`
    时 `build_model()` 返回字符串，走 openai 默认分支，TestModel override 仍正常。

### 步骤 3：基础设施 — 新增 `model_provider` 变量

- **文件**：`infra/variables.tf`（既有，**新增一个变量块**）
- **做什么**：在 `variable "model_string"` 块之后追加：
  ```hcl
  variable "model_provider" {
    description = "模型后端 provider：openai（默认）| deepseek | zhipu。决定 MODEL_PROVIDER 环境变量。"
    type        = string
    default     = "openai"
  }
  ```
  仅新增这一个**非敏感**变量（`sensitive` 不设，默认 `false`）。**不引入** `zhipu_base_url`、
  `model_name`、`deepseek_api_key`、`zhipu_api_key` 等变量（智谱端点用代码默认值，模型名复用既有
  `model_string`，key 仍走既有 `model_api_key`）。
- **依赖**：无（可与步骤 1 并行）。
- **验收标准**：`terraform -chdir=infra validate` 通过；`terraform -chdir=infra plan`（提供必要 TF_VAR 凭证后）
  无错，且 diff 仅显示新增一个非敏感变量、无 resource 结构变化、无 in-place 替换风险。

### 步骤 4：基础设施 — user-data 模板注入 `MODEL_PROVIDER`

- **文件**：`scripts/deploy_app.sh.tftpl`（既有，**改 env 文件 + 一行模板实参**）
- **做什么**：
  1. 在 env 文件写入段追加一行，使渲染后 env 文件含 `MODEL_PROVIDER`：
     ```bash
     cat >/etc/agent/env <<'ENVEOF'
     MODEL_PROVIDER=${model_provider}
     MODEL_STRING=${model_string}
     MODEL_API_KEY=${model_api_key}
     ENVEOF
     ```
  2. （实参由 `infra/main.tf` 的 `templatefile` 提供，见步骤 5。）
- **依赖**：步骤 3（`model_provider` 变量必须先存在）+ 步骤 5（`templatefile` 须传入同名实参）。
  **步骤 4 与步骤 5 必须同一次提交完成**，否则 `templatefile` 会因缺实参报错。
- **验收标准**：渲染后的 env 文件含 `MODEL_PROVIDER=openai`（默认）一行，且 `model_api_key` 仍只
  存在 env 文件（mode 0600）中，未泄露到脚本正文。

### 步骤 5：基础设施 — `templatefile` 传参

- **文件**：`infra/main.tf`（既有，**改 `module "cvm"` 的 `user_data_raw`**）
- **做什么**：在 `templatefile(...)` 的实参 map 里加一行：
  ```hcl
  user_data_raw = templatefile("${path.module}/../scripts/deploy_app.sh.tftpl", {
    model_provider = var.model_provider
    model_string   = var.model_string
    model_api_key  = var.model_api_key
  })
  ```
  其余（CVM 规格、网络、安全组等）**不动**。
- **依赖**：步骤 3（变量存在）+ 步骤 4（模板引用了 `${model_provider}`）。**与步骤 4 同次提交**。
- **验收标准**：`terraform -chdir=infra validate` 与 `plan` 均无错；`templatefile` 实参与模板占位符一一对应。

### 步骤 6：基础设施 — 更新 tfvars 示例

- **文件**：`infra/terraform.tfvars.example`（既有，**追加一行示例**）
- **做什么**：在 `model_string` 行附近追加，并给出配置矩阵注释：
  ```hcl
  model_provider = "openai"        # openai（默认）| deepseek | zhipu
  model_string   = "openai:gpt-4o-mini"
  # 切 DeepSeek：model_provider="deepseek", model_string="deepseek-chat"
  # 切 智谱：    model_provider="zhipu",    model_string="glm-4"
  ```
  密钥行（`model_api_key`）注释**保持不变**（仍经 `TF_VAR_model_api_key` 注入，绝不写进示例）。
- **依赖**：步骤 3（变量名要存在才有意义）。
- **验收标准**：示例文件语法正确；不出现任何真实/占位密钥值。

### 步骤 7：文档 — 更新 `README.md`

- **文件**：`README.md`（既有，**追加一节**）
- **做什么**：在「应用与部署」相关章节后新增「切换到智谱 / DeepSeek」小节，内容覆盖：
  - `MODEL_PROVIDER` 环境变量 / `model_provider` Terraform 变量的取值（`openai` | `deepseek` | `zhipu`）。
  - 配置矩阵表（照搬设计稿「配置矩阵」小节，3 行）。
  - key 统一走 `MODEL_API_KEY`（强调：DeepSeek/智谱都只用这一个，**无需**单独设
    `DEEPSEEK_API_KEY`/`ZHIPU_API_KEY`，后者仅作高级用户本地实验回退）。
  - 各 provider 的 `MODEL_STRING` 示例值。
  - （可选）注明 `deepseek-reasoner` 工具调用受限、智谱个别高级字段可能不兼容——一句话提示即可，
    详见设计稿「风险点 1/2」。
- **依赖**：步骤 1–6 完成（文档要反映最终落点）。
- **验收标准**：读者照 README 能独立完成「切到智谱」或「切到 DeepSeek」的部署；不与 tfvars 示例矛盾。

---

## 2. 验收（端到端，交付时由实现者或 PlanReviewer 抽验）

| 验收项 | 方法 | 通过标准 |
|---|---|---|
| 无网单测 | `uv run pytest` | 7 个用例（3 旧 + 4 新）全过，无网无 key |
| 向后兼容 | 不设任何新环境变量跑现有部署/测试 | 行为与 BUB-13 现状一致 |
| Terraform | `terraform -chdir=infra validate && plan`（提供 TF_VAR 凭证） | 无错；diff 仅新增非敏感变量 |
| 本地起服务（DeepSeek，需 key） | `MODEL_PROVIDER=deepseek MODEL_STRING=deepseek-chat MODEL_API_KEY=... uv run uvicorn app.main:app`，`curl /chat` | 返回包含 `server_time` 的正常回复（实现者本地验，CI 不做） |
| 本地起服务（智谱，需 key） | `MODEL_PROVIDER=zhipu MODEL_STRING=glm-4 MODEL_API_KEY=... uv run uvicorn app.main:app` | 同上 |

> CI / pytest 路径**始终不触网、不需 key**（沿用 BUB-13 测试哲学）。带 key 的本地起服务验证不在 CI 内，
> 由实现者在合并前自行抽验一次即可。

---

## 3. 实现前已确认的事实（供实现者免重复调研）

1. **pydantic-ai 版本**：仓库当前 `pydantic-ai==1.107.0`（`uv.lock`），且其 extras 已含
   `openai==2.43.0`。设计稿引用的以下符号**均已在本环境实测存在**（`uv run python -c ...` 通过）：
   - `pydantic_ai.models.openai.OpenAIChatModel`
   - `pydantic_ai.providers.openai.OpenAIProvider`，`__init__(self, base_url, api_key, openai_client, http_client)`
   - `pydantic_ai.providers.deepseek.DeepSeekProvider`（支持 `deepseek:*` 模型串，读 `DEEPSEEK_API_KEY`）
   - `pydantic_ai.models.test.TestModel`
2. **不引入新依赖**：DeepSeek 与智谱都复用 `openai` 包，`pyproject.toml` / `uv.lock` **不改**。
3. **安全组无需改**：`infra/main.tf` 出向 `TCP 443 0.0.0.0/0` 已覆盖智谱（`open.bigmodel.cn`）与
   DeepSeek（`api.deepseek.com`），均为 HTTPS:443。
4. **现有 `app/agent.py` 与设计稿示例有微小出入**：现状把 `from datetime import datetime, timezone`
   放在文件顶部（设计稿示例放在 `server_time` 函数内）。实现时**保留现状顶部 import 风格**，不照搬
   设计稿的函数内 import——这不影响设计语义。

---

## 4. 范围之外（明确不做，避免镀金）

照设计稿「非目标」与「取舍」一节，本计划**不包含**：多 provider 并存、运行时路由、`FallbackModel`、
流式输出、多轮对话、新业务工具、`zhipuai` 官方 SDK、自定义 `Model` 子类、`zhipu_base_url` /
`deepseek_api_key` / `zhipu_api_key` 等 Terraform 变量、安全组改动、依赖变更。

---

## 修订记录

- *（初稿，待 Plan Reviewer 评审）*

---

*Planner（BUB-19），2026-06-21*
