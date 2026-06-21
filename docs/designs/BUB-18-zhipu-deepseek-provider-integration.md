# 设计：支持智谱（Zhipu/GLM）与 DeepSeek 接入

- **关联 GitHub Issue**: [#11](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/11) —「支持智谱和deepseek接入」
- **Multica 工单**: BUB-18
- **状态**: 初稿（待 Design Reviewer 评审）

---

## 背景与目标

### 需求原文（issue #11）

> 支持智谱和deepseek接入

### 背景

本仓库当前的 MVP（由 BUB-13 落地）用一个 `pydantic-ai` `Agent` 暴露 `/chat` 接口。模型由环境变量
`MODEL_STRING`（默认 `openai:gpt-4o-mini`）决定，API key 由 `MODEL_API_KEY` 注入。应用层关键代码：

```python
# app/agent.py
MODEL_STRING = os.environ.get("MODEL_STRING", "openai:gpt-4o-mini")
agent = Agent(MODEL_STRING, instructions="...", defer_model_check=True)
```

`MODEL_STRING` 直接传给 `Agent(model=...)`，pydantic-ai 据其前缀（`provider:model`）解析出对应的
`Model` 实例。当前形态只对 OpenAI 开箱即用（pydantic-ai 内置 `openai` provider）。

issue #11 要求**额外支持智谱（Zhipu / GLM 系列）与 DeepSeek**。这两家均为国内主流、国内网络可达、
且都提供 **OpenAI 兼容**的 Chat Completions 接口。本设计的核心问题就是：如何在不破坏现有 MVP、
不引入需求外复杂度的前提下，让部署侧能选定智谱或 DeepSeek 作为后端模型。

### 目标

1. **部署时可切换**到智谱或 DeepSeek 作为 agent 的后端模型（经环境变量 / Terraform 变量）。
2. **保持现有接口与行为不变**：`/chat`、`/healthz`、单轮、`server_time` 工具、`TestModel` 无网单测均不受影响。
3. **凭证注入方式不变**：API key 仍只经环境变量 / `TF_VAR_*` 注入，绝不写进代码或 `.tfvars`。
4. **不绑定单一厂商**：与现有「provider 由部署侧决定」的哲学一致，新增能力是「多一种 provider 选项」，
   而非「硬编码某一家」。

### 非目标（明确排除，避免镀金）

- ❌ 多 provider 同时在线 / 运行时动态路由（一次部署只跑一个 provider，由环境变量选定）。
- ❌ provider 自动 fallback / 重试切换（pydantic-ai 的 `FallbackModel` 留待后续）。
- ❌ 流式输出（仍 `agent.run`，非流式，沿用 BUB-13 的 MVP 取舍）。
- ❌ 多轮对话（仍单轮 `/chat`）。
- ❌ 新增业务工具（仍只有 `server_time`）。
- ❌ 为智谱/DeepSeek 引入它们各自的官方 SDK（如 `zhipuai` SDK）；两者都用 pydantic-ai 已有的
  OpenAI 兼容路径，**不引入新运行时依赖**。

---

## 关键调研结论（决定了方案形态）

> 以下结论均来自对仓库已安装的 `pydantic_ai`（随 `pydantic-ai` 主依赖引入）源码的核对，不是假设。

### 1. DeepSeek：pydantic-ai 有**一等公民** provider

`pydantic_ai/providers/deepseek.py` 提供了 `DeepSeekProvider`：

- 模型串 `deepseek:<model>`（如 `deepseek:deepseek-chat`、`deepseek:deepseek-reasoner`）在
  `KnownModelName` 里已注册，pydantic-ai 会自动解析。
- `base_url` 硬编码为 `https://api.deepseek.com`。
- API key 读环境变量 **`DEEPSEEK_API_KEY`**（未设置且未传 `api_key=` 时抛 `UserError`）。
- 底层就是 `AsyncOpenAI(base_url=..., api_key=...)`——OpenAI 兼容协议。
- 依赖：只需 `openai` 包（已是 `pydantic-ai-slim[openai]` extra，仓库 uv.lock 里已存在），**无新依赖**。

结论：**DeepSeek 零代码改动即可用**，只需在部署侧设 `MODEL_STRING=deepseek:deepseek-chat` +
`DEEPSEEK_API_KEY=...`。但当前 `app/agent.py` 用统一 `MODEL_API_KEY` 注入，而 DeepSeek provider
读的是 `DEEPSEEK_API_KEY`——存在「key 环境变量名不匹配」的问题，需要设计侧解决（见下文）。

### 2. 智谱（Zhipu / GLM）：pydantic-ai **没有**一等公民 provider

`pydantic_ai/providers/` 与 `pydantic_ai/profiles/` 里没有 `zhipu` / `glm`。也没有
`zhipu:<model>` 的已知模型串。但智谱的开放平台（`https://open.bigmodel.cn/api/paas/v4/`）提供
**OpenAI 兼容**的 Chat Completions 接口，模型名形如 `glm-4`、`glm-4-flash`、`glm-4-plus` 等。

pydantic-ai 的 `OpenAIProvider`（`pydantic_ai/providers/openai.py`）支持自定义 `base_url`：

```python
OpenAIProvider(base_url="https://open.bigmodel.cn/api/paas/v4/", api_key=os.getenv("ZHIPU_API_KEY"))
```

配上 `OpenAIChatModel("<model_name>", provider=...)` 即可指向智谱。该路径同样**不引入新依赖**
（用的还是 `openai` 包）。

### 3. 现状代码的局限

`app/agent.py` 直接把 `MODEL_STRING` 字符串塞给 `Agent(model=...)`。这对 `openai:*`、`deepseek:*`
都能工作，但**对智谱无法用纯字符串表达**——因为「`OpenAIProvider` + 自定义 `base_url`」必须显式
构造对象，没有 `zhipu:glm-4` 这种字符串快捷方式。

### 4. 出向网络：安全组放行 443 已覆盖两家

`infra/main.tf` 的安全组出向规则是 `TCP 443 0.0.0.0/0`。智谱（`open.bigmodel.cn`）与 DeepSeek
（`api.deepseek.com`）均为 HTTPS（443），**无需改动安全组**。

---

## 方案概述

引入一个**极薄的 provider 选择层**：根据环境变量 `MODEL_PROVIDER`（新增，可选）与既有
`MODEL_STRING` / API key，在 `app/agent.py` 里构造出对应的 pydantic-ai `Model` 对象，再交给 `Agent`。

- **DeepSeek**：走 pydantic-ai 原生 `deepseek:*` 字符串路径。
- **智谱**：走「`OpenAIChatModel` + `OpenAIProvider(base_url=智谱端点)`」路径。
- **OpenAI（默认，兼容现状）**：保持 `MODEL_STRING` 直传不变，向后兼容。

核心是：**把「provider」从隐式的字符串前缀，变成一个可显式指定的维度**，且只新增一个可选环境变量、
不引入新依赖、不改动任何对外接口。

### 总体流程

```
        部署侧注入（环境变量 / TF_VAR_*）
   ┌────────────────────────────────────────────┐
   │  MODEL_PROVIDER=openai | deepseek | zhipu   │   ← 新增（可选；默认 openai）
   │  MODEL_STRING=<模型串>                       │   ← 既有
   │  MODEL_API_KEY=<key>                         │   ← 既有（见下方 key 映射）
   └─────────────────────┬──────────────────────┘
                         │
                         ▼
        app/agent.py: build_model() 选择层
                         │
   ┌─────────────────────┼──────────────────────────────┐
   │ openai              │ deepseek                     │ zhipu
   │ Agent(MODEL_STRING) │ Agent("deepseek:<m>")        │ Agent(OpenAIChatModel(
   │                     │ key 读 DEEPSEEK_API_KEY      │   "<m>", provider=OpenAIProvider(
   │                     │ 或 MODEL_API_KEY（见下）     │     base_url=智谱, api_key=...)))
   └─────────────────────┴──────────────────────────────┘
                         │
                         ▼
            pydantic-ai Agent（接口/行为不变）
```

---

## 关键接口 / 数据结构

### 应用层：`app/agent.py`（改写）

```python
import os

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# ── 配置（全部来自环境变量，部署时注入）──────────────────────────────
# provider 维度：openai（默认，向后兼容）| deepseek | zhipu
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "openai").strip().lower()
# 模型维度：含义随 provider 而变
#   openai    -> "openai:gpt-4o-mini" 之类，或纯模型名
#   deepseek  -> "deepseek-chat" / "deepseek-reasoner"（不带 deepseek: 前缀也接受）
#   zhipu     -> "glm-4" / "glm-4-flash" / "glm-4-plus" ...
MODEL_STRING = os.environ.get("MODEL_STRING", "openai:gpt-4o-mini")
# 统一的 key 入口；选择层会按 provider 映射到各家期望的环境变量名
MODEL_API_KEY = os.environ.get("MODEL_API_KEY")


# ── provider 选择层 ──────────────────────────────────────────────────
def _model_name_only(value: str) -> str:
    """去掉 'provider:' 前缀，只留模型名。对 zhipu/deepseek 用得到。"""
    return value.split(":", 1)[1] if ":" in value else value


def build_model():
    """根据 MODEL_PROVIDER 构造 pydantic-ai Model 对象。

    - openai   : 直接把 MODEL_STRING 塞回 Agent（保持现状行为）
    - deepseek : 走 pydantic-ai 原生 deepseek 路径；把 MODEL_API_KEY
                 映射为 DeepSeekProvider 期望的 DEEPSEEK_API_KEY
    - zhipu    : OpenAIChatModel + OpenAIProvider(base_url=智谱端点)
    """
    provider = MODEL_PROVIDER

    if provider == "deepseek":
        # 让原生 DeepSeekProvider 能读到 key：优先用 MODEL_API_KEY，
        # 没有则回退到调用方自己设的 DEEPSEEK_API_KEY。
        if MODEL_API_KEY:
            os.environ.setdefault("DEEPSEEK_API_KEY", MODEL_API_KEY)
        name = _model_name_only(MODEL_STRING) if MODEL_STRING else "deepseek-chat"
        return f"deepseek:{name}"

    if provider == "zhipu":
        # 智谱无原生 provider：用 OpenAI 兼容路径。
        api_key = MODEL_API_KEY or os.environ.get("ZHIPU_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MODEL_PROVIDER=zhipu 需要设置 MODEL_API_KEY（或 ZHIPU_API_KEY）"
            )
        base_url = os.environ.get(
            "ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"
        )
        model_name = _model_name_only(MODEL_STRING) if MODEL_STRING else "glm-4"
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )

    # 默认 openai：保持现状——MODEL_STRING 直传（含 openai: 前缀）。
    return MODEL_STRING


agent = Agent(
    build_model(),
    instructions="你是一个简洁的中文助手。需要时调用工具获取服务器本地时间。",
    # 与现状一致：构造期不实例化 provider，import 本模块不需要任何 key。
    defer_model_check=True,
)


@agent.tool_plain
def server_time() -> str:
    """返回服务器当前时间（ISO 8601，UTC）。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

要点说明：

- **向后兼容**：不设 `MODEL_PROVIDER` 时，`build_model()` 返回原始 `MODEL_STRING`，行为与 BUB-13
  现状完全一致。现有部署与现有测试零影响。
- **`defer_model_check=True` 仍然有效**：`build_model()` 在模块 import 时执行，但**只构造对象**，
  不会触发任何网络请求或 key 校验（`OpenAIProvider(...)` 与字符串都只是构造）；真正的鉴权推迟到
  `agent.run()`。因此 import 本模块仍不需要任何 key——满足「无副作用 import」与无网单测。
  - 唯一例外：`zhipu` 分支在 key 缺失时**显式 `raise`**，这是设计选择（fail-fast，避免运行时才报
    模糊错误）。这一 raise 只在 `MODEL_PROVIDER=zhipu` 且 key 缺失时触发；不设 provider 或走
    openai/deepseek 分支时不触发，**不影响现有 import 行为**。
- **key 命名策略**：保持对外只有一个 `MODEL_API_KEY` 入口（部署侧只认一个变量），由选择层在内部
  映射到各家期望的名字（`DEEPSEEK_API_KEY` / 智谱的构造参数）。这样 Terraform 侧也只需暴露一个
  `model_api_key` 变量，**不新增 Terraform 变量**。
  - 同时保留 `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` 作为「高级用户直设」的回退，方便本地实验。

### 基础设施层：`infra/`（小改）

无需新增 Terraform 变量，**只新增一个非敏感变量** `model_provider`，并把它经 user-data 注入 CVM
的环境文件，使部署侧能选 provider。

#### `infra/variables.tf` 新增

```hcl
variable "model_provider" {
  description = "模型后端 provider：openai（默认）| deepseek | zhipu。决定 MODEL_PROVIDER 环境变量。"
  type        = string
  default     = "openai"
}
```

> 不引入 `zhipu_base_url`、`model_name` 等变量——智谱端点用代码默认值，模型名仍复用既有
> `model_string` 变量（部署侧把 `model_string` 设成 `glm-4` 即可）。遵循「不引入需求外的配置面」。

#### `scripts/deploy_app.sh.tftpl` 改动（仅一行 env）

在 env 文件里追加 `MODEL_PROVIDER`：

```bash
cat >/etc/agent/env <<'ENVEOF'
MODEL_PROVIDER=${model_provider}
MODEL_STRING=${model_string}
MODEL_API_KEY=${model_api_key}
ENVEOF
```

`infra/main.tf` 里 `templatefile` 的实参相应加一项 `model_provider = var.model_provider`。

#### 安全组：**不改**

`infra/main.tf` 出向规则 `TCP 443 0.0.0.0/0` 已覆盖智谱、DeepSeek 两家（均 HTTPS:443）。

#### `infra/terraform.tfvars.example` 更新示例

```hcl
model_provider = "zhipu"            # openai | deepseek | zhipu
model_string   = "glm-4"            # 随 provider 而定
# model_api_key 仍经 TF_VAR_model_api_key 注入，不写进本文件
```

### 配置矩阵（部署侧速查）

| `MODEL_PROVIDER` | `MODEL_STRING` 示例 | key 注入 | 实际后端 |
|---|---|---|---|
| `openai`（默认） | `openai:gpt-4o-mini` | `MODEL_API_KEY` | OpenAI（现状） |
| `deepseek` | `deepseek-chat` | `MODEL_API_KEY`（内部映射为 `DEEPSEEK_API_KEY`） | `https://api.deepseek.com` |
| `zhipu` | `glm-4` | `MODEL_API_KEY`（内部用作智谱 key） | `https://open.bigmodel.cn/api/paas/v4/` |

---

## 涉及的现有 symbol

| 现有文件 / symbol | 关系 |
|---|---|
| `app/agent.py` — `MODEL_STRING`、`agent`、`server_time` | **改写**：新增 `MODEL_PROVIDER`、`build_model()`；`agent` 改用 `build_model()`；`server_time` 不变 |
| `app/main.py` — `chat`、`healthz` | **不改**：仍 `await agent.run(...)`，对 provider 切换无感 |
| `tests/test_agent.py` | **不破坏**：现有 3 个用例不设 `MODEL_PROVIDER`，走 openai 分支，`build_model()` 返回字符串，与现状一致 |
| `infra/variables.tf` | **新增** `model_provider` 变量（非敏感） |
| `infra/main.tf` — `module "cvm"` 的 `templatefile` 实参 | **加一行** `model_provider = var.model_provider` |
| `scripts/deploy_app.sh.tftpl` — env 文件 | **加一行** `MODEL_PROVIDER=${model_provider}` |
| `infra/terraform.tfvars.example` | **更新示例**（追加 `model_provider` 行） |
| `infra/main.tf` 安全组出向 | **不改**（443 已覆盖） |
| `pyproject.toml` 依赖 | **不改**（DeepSeek/智谱均复用 `openai` 包，已在 uv.lock） |
| `README.md` | **更新**：在「应用与部署」补一节「切换到智谱 / DeepSeek」 |

---

## 测试策略

| 层次 | 方法 | 验证点 |
|---|---|---|
| 单测（无网） | 扩展 `tests/test_agent.py` | (a) 不设 `MODEL_PROVIDER` 时 `build_model()` 返回 `MODEL_STRING` 字符串（向后兼容）；(b) `MODEL_PROVIDER=zhipu` 且设 key 时 `build_model()` 返回 `OpenAIChatModel` 实例、其 provider base_url 指向智谱端点；(c) `MODEL_PROVIDER=deepseek` 时返回 `deepseek:<name>` 字符串；(d) `zhipu` 缺 key 时抛 `RuntimeError`。全程 `monkeypatch` 环境变量、用 `TestModel` 跑 `agent`，不触网 |
| 单测（无网，既有） | 现有 3 个用例 | 全部仍通过（回归） |
| 本地起服务（需 key） | `MODEL_PROVIDER=deepseek MODEL_API_KEY=... uv run uvicorn ...` 后 `curl /chat` | 返回带 `server_time` ISO 时间的回复（证明 agent 真的调通了 DeepSeek） |
| 本地起服务（需 key） | 同上，切 `MODEL_PROVIDER=zhipu MODEL_STRING=glm-4` | 返回正常回复 |
| Terraform | `terraform plan` 无错；`apply` 后 `service_url` 可解析 | 仅多一个非敏感变量，无 resource 结构变化 |

> 单测不调真实模型：`OpenAIChatModel` / provider 对象只构造不发请求；`agent` 用 `TestModel` override。
> 因此 CI / `pytest` 仍**不触网、不需 key**，延续 BUB-13 的测试哲学。

---

## 取舍与备选

| 决策点 | 选择 | 备选 & 为何不选 |
|---|---|---|
| 智谱接入方式 | `OpenAIChatModel` + `OpenAIProvider(base_url=智谱)`（OpenAI 兼容协议） | **引入 `zhipuai` 官方 SDK / 写自定义 `Model` 子类**：增加新依赖与维护面；pydantic-ai 官方对「OpenAI 兼容端点」就是推荐这条路，无需自造轮子 |
| DeepSeek 接入方式 | 用 pydantic-ai 原生 `deepseek:*` 字符串（一等公民 provider） | **也走 `OpenAIChatModel`+自定义 base_url**：能用，但原生 provider 自带正确的 model profile（如 `deepseek-reasoner` 的 thinking 处理），少踩坑，故优先原生 |
| provider 维度如何表达 | 新增独立环境变量 `MODEL_PROVIDER` | **仅靠 `MODEL_STRING` 前缀推断**（`zhipu:` → 智谱）：智谱无原生前缀，强行约定一个自定义前缀会让 `Agent(model=...)` 的字符串解析歧义更大；独立变量更显式、更易在 Terraform 侧配置 |
| key 注入 | 对外只暴露 `MODEL_API_KEY`，内部映射到各家期望名 | **新增 `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` 两个 Terraform 变量**：配置面变大，且部署侧每换一家就要改 tfvars；统一入口更简洁。保留两个名字作为「内部回退 / 本地实验」高级用法，权衡后可接受 |
| 智谱端点是否做成变量 | 硬编码默认值，仅在代码里留 `ZHIPU_BASE_URL` env 覆盖口 | **新增 `zhipu_base_url` Terraform 变量**：MVP 用不到，属于需求外配置面；env 覆盖口已足够应对未来端点变更 |
| 是否支持 fallback / 多 provider 同时在线 | 不支持 | **用 `FallbackModel`**：需求里没提，属于镀金；留给后续 issue |
| `zhipu` 缺 key 时 fail 时机 | import 期（`build_model()` 执行时）即 raise | **推迟到 `agent.run()` 让 openai SDK 报错**：报错更晚、信息更模糊（SDK 抛的是通用 401），显式 raise 更利于排障 |
| 默认 provider | `openai`（向后兼容现状） | **默认改成 `deepseek` 或 `zhipu`**：会改变现有部署的默认行为，破坏 BUB-13 的部署兼容性 |

---

## 风险点

1. **智谱端点的 OpenAI 兼容度**：智谱 v4 接口对绝大多数 Chat Completions 字段兼容，但个别高级特性
   （如某些 `tool_choice` 取值、部分 response 字段）可能与 OpenAI 有差异。pydantic-ai 的工具调用
   走标准 function calling，`server_time` 这类简单工具风险低。**缓解**：MVP 只验证文本对话 +
   `server_time` 工具能跑通；若发现兼容问题，后续可在选择层为智谱挂自定义 `ModelProfile`
   （参考 pydantic-ai `openai_model_profile` 写法）。
2. **DeepSeek `deepseek-reasoner` 的工具调用限制**：`deepseek-reasoner`（R1 系列）对
   `tool_choice=required` 等有限制（pydantic-ai 的 `DeepSeekProvider` 已在 profile 里处理）。
   MVP 用 `deepseek-chat` 即可避开；如需 reasoner，README 注明其工具调用约束。
3. **环境变量覆盖的副作用**：`build_model()` 里 `os.environ.setdefault("DEEPSEEK_API_KEY", ...)`
   会写进程环境。属可接受（同进程内、key 来自受信环境），但**注意**：这使 `DEEPSEEK_API_KEY` 在
   import 后存在于 `os.environ`，若有别的代码读它会拿到该值。MVP 无此风险，记入风险以便 dev 知情。
4. **`MODEL_PROVIDER` 取值校验**：拼错的 provider（如 `zhupu`）会落到 openai 默认分支，行为「静默
   不符合预期」。**缓解**：在 `build_model()` 末尾对未知取值记一条 warning 日志（不 raise，避免破坏
   向后兼容）；dev 可选实现。
5. **key 命名约定的认知成本**：部署侧可能误以为 DeepSeek 要设 `DEEPSEEK_API_KEY`。**缓解**：README
   明确「统一用 `MODEL_API_KEY`，选择层自动映射」；terraform.tfvars.example 给出配置矩阵。
6. **国内网络可达性**：智谱、DeepSeek 均为国内厂商，国内地域（`ap-guangzhou`）直连稳定——这正是
   本需求的动机。反而是 OpenAI 在国内可能不稳。本设计让「切国内 provider」成为一等公民选项，
   正向缓解了 BUB-13 遗留的「模型网络可达性」风险。

---

### 修订记录

- *（初稿，待 Design Reviewer 评审）*

---

*Designer（BUB-18），2026-06-21*
