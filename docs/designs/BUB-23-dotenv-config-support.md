# 设计：支持 `.env` 配置文件

- **关联 GitHub Issue**: [#15](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/15) —「支持 `.env` 配置文件」
- **Multica 工单**: BUB-23
- **状态**: 初稿（待 Design Reviewer 评审）

---

## 背景与目标

### 需求原文（issue #15）

> 支持 `.env` 配置文件，可以将各个配置项及环境变量放入

### 背景

当前 MVP 的配置全部来自**进程环境变量**，由 `app/agent.py` 用 `os.environ.get(...)` 逐项读取：

- `MODEL_PROVIDER`（默认 `openai`）
- `MODEL_STRING`（默认 `openai:gpt-4o-mini`）
- `MODEL_API_KEY`（无默认）
- 智谱分支额外读 `ZHIPU_API_KEY` / `ZHIPU_BASE_URL`（高级回退）

部署侧已有「类 env-file」机制：Terraform 把这三个变量渲染进 CVM 上的 `/etc/agent/env`，
systemd 单元用 `EnvironmentFile=/etc/agent/env` 加载（见 `scripts/deploy_app.sh.tftpl`）。
这部分是**部署期**的事，本次设计不改动它。

痛点在**本地开发 / 手工运行**场景：README 当前要求把 key 拼在命令前缀上——

```bash
MODEL_PROVIDER=zhipu MODEL_STRING=glm-4 MODEL_API_KEY=sk-xxx uv run uvicorn app.main:app --port 8000
```

变量一多就容易写错、丢失、漏配置；也没法把「非敏感的默认配置」与「敏感 key」分离管理。
issue #15 希望支持一个仓库根目录下的 `.env` 文件，把这些配置项集中放进去，本地拉起时自动加载。

### 目标

1. **本地开发体验**：仓库根目录放一个 `.env`，`uv run uvicorn app.main:app` 时应用自动读取其中的
   配置项（`MODEL_*` 等），不必再拼命令前缀。
2. **环境变量优先级明确**：真实进程环境变量 > `.env` 文件 > 代码内置默认值；已存在的环境变量
   不被 `.env` 覆盖（符合 12-factor 与 dotenv 惯例）。
3. **不破坏现有部署链路**：腾讯云 CVM 上 `/etc/agent/env` + systemd `EnvironmentFile` 的部署形态
   原样保留，不受影响。
4. **不外泄密钥**：`.env`（含真实密钥）绝不入库；只提交一个 `.env.example` 模板。

### 非目标（明确排除，避免镀金）

- ❌ 引入 `pydantic-settings` 的 `BaseSettings` 配置类体系（会把 `app/agent.py` 里散落的
  `os.environ.get` 重写成强类型 settings 对象，改动面大且超出「支持 .env 文件」的需求边界）。
  本设计只做「读取 `.env` 并注入 `os.environ`」这一件事。
- ❌ 改动 Terraform / CVM 部署链路（部署侧已用 `EnvironmentFile`，无需 `.env`）。
- ❌ 多环境 `.env.production` / `.env.local` 分层加载（MVP 一个 `.env` 足够）。
- ❌ 配置热加载 / 运行时改配置不重启（不在需求内）。
- ❌ 加密 `.env`（如 `sops`/`doppler`）——超出 MVP 范围。

---

## 方案概述

在应用启动入口（`app/__init__.py`，早于 `agent.py` 的 import 副作用）调用一次
`python-dotenv` 的 `load_dotenv()`，把仓库根目录的 `.env` 读进 `os.environ`（不覆盖已存在变量）。
之后 `app/agent.py` 里既有的 `os.environ.get(...)` 代码**完全不动**即可自动拿到 `.env` 里的值。

新增 `python-dotenv` 为**显式**运行时依赖（虽然它已作为 pydantic-ai 的间接依赖出现在 `uv.lock`，
但未声明在 `pyproject.toml` 的 `dependencies` 里——按 Designer 准则「绝不假设某库可用」，必须显式声明）。

提供一个 `.env.example` 模板（入库），并在 `.gitignore` 里给 `.env` 留出入库例外（`.gitignore`
当前用 `*.env` 通配忽略，会连带忽略 `.env.example`，需要 `!.env.example` 反向规则）。

加载策略上选「**不覆盖**」（`override=False`，dotenv 默认）：真实环境变量永远优先于 `.env`，
这样部署环境（systemd 已注入环境变量）与单测（monkeypatch 环境变量）都不受 `.env` 干扰。

### 加载点选址：为什么是 `app/__init__.py` 而不是 `app/main.py`

- `app/main.py`（FastAPI app）只在 `uvicorn app.main:app` 入口被加载；但 `tests/test_agent.py`
  是直接 `from app.agent import ...`，**不经 `app.main`**。若加载点放 `main.py`，单测路径就拿不到
  `.env`，行为不一致。
- `app/agent.py` 在模块顶层就 `agent = Agent(build_model(), ...)`（虽有 `defer_model_check=True`
  不触网，但 `build_model()` 会读一次 `os.environ`）。要让 `.env` 在这之前生效，加载必须早于
  `agent.py` 的 import。
- `app/__init__.py` 是 `app` 包被 import 时最先执行的代码，且 `from app.agent import agent` 与
  `from app.main import app` 两条路径都会先触发它——是覆盖面最广、副作用最小的加载点。

`app/__init__.py` 当前为空，正好放加载逻辑。

---

## 关键接口 / 数据结构

无新增公共 API。改动是模块加载时的副作用 + 一个 `.env.example` 文件 + `.gitignore`/`pyproject.toml`
的小修。下面给出**落地形态**（实现细节留给后续 Planner / Developer）。

### 1. `app/__init__.py`（新增加载逻辑）

```python
"""应用包入口：在子模块 import 前加载仓库根目录的 `.env` 到 os.environ（不覆盖已存在变量）。

- `.env` 仅用于本地开发便利；部署侧（腾讯云 CVM）仍由 systemd EnvironmentFile 注入，二者不冲突。
- override=False：真实进程环境变量永远优先于 `.env`，避免污染单测的 monkeypatch 与部署环境。
"""
from dotenv import load_dotenv

load_dotenv()  # 默认查找 CWD 下的 .env；override=False
```

> 说明：`load_dotenv()` 不带参数时按「从 CWD 向上查找 `.env`」定位文件，本地 `uv run uvicorn`
> 的 CWD 即仓库根，能命中。开发侧若换工作目录可传 `dotenv_path=...`，但 MVP 不暴露此参数。

### 2. `.env.example`（新增，入库模板）

```dotenv
# 复制为 .env 后填入真实值（.env 已被 .gitignore 忽略，勿提交真实密钥）。

# 模型后端 provider：openai（默认）| deepseek | zhipu
MODEL_PROVIDER=openai

# pydantic-ai 模型串；常见取值：
#   openai   -> openai:gpt-4o-mini
#   deepseek -> deepseek-chat
#   zhipu    -> glm-4
MODEL_STRING=openai:gpt-4o-mini

# 模型 provider 的 API key（必填）
MODEL_API_KEY=sk-xxx

# —— 以下为高级回退，一般无需设置 ——
# 智谱 OpenAI 兼容端点（仅在自定义端点时覆盖）
# ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
# 智谱专用 key 回退（优先级低于 MODEL_API_KEY）
# ZHIPU_API_KEY=
```

### 3. `.gitignore`（小修）

当前规则 `*.env` 的 glob 是「任意前缀 + `.env`」，匹配 `.env`、`prod.env` 等，但**不**匹配 `.env.example`。
也就是说 `.env.example` 现在其实能被追踪。这里**补一行 `!.env.example` 反向例外**是防御性声明——
避免后续有人把规则改成更宽的 `.env*` 通配时误伤模板文件，也让意图自解释：

```gitignore
############################################################
# 本地密钥 / 环境文件（绝不要提交）
############################################################
*.env
!*.env.example
infra/terraform.tfvars
/etc/agent/
```

### 4. `pyproject.toml`（显式声明依赖）

```toml
dependencies = [
    "pydantic-ai",
    "fastapi",
    "uvicorn",
    "python-dotenv",   # 新增：本地 .env 加载
]
```

### 5. `README.md`（文档：本地运行小节增补 `.env` 用法）

在「本地运行」小节补一段：复制 `.env.example` 为 `.env`、填 key、直接 `uv run uvicorn ...` 即可，
无需命令前缀；并强调部署侧（CVM）不受影响。

---

## 涉及的现有 symbol / 文件

| 文件 | 现状 | 本设计改动 |
|---|---|---|
| `app/__init__.py` | 空文件 | 新增 `load_dotenv()` 调用 |
| `app/agent.py` | `os.environ.get(...)` 读配置 | **不动**（`.env` 已注入 `os.environ`，自动生效） |
| `app/main.py` | FastAPI app | **不动** |
| `tests/test_agent.py` | monkeypatch 环境变量做无网单测 | **不动**（`override=False` 保证 monkeypatch 优先；本地无 `.env` 时 `load_dotenv()` 是 no-op） |
| `scripts/deploy_app.sh.tftpl` | 写 `/etc/agent/env` + systemd `EnvironmentFile` | **不动**（部署链路保留） |
| `infra/*` | Terraform 注入 `MODEL_*` | **不动** |
| `pyproject.toml` | 无 `python-dotenv` | 显式加入 `dependencies` |
| `.gitignore` | `*.env` 通配忽略 | 加 `!*.env.example` 例外（防御性） |
| `.env.example` | 不存在 | 新增（入库模板） |
| `README.md` | 本地运行用命令前缀注入 | 增补 `.env` 用法说明 |

---

## 取舍与备选

### 备选 A：用 `pydantic-settings` 的 `BaseSettings`（`model_config = SettingsConfigDict(env_file=".env")`）

- **优点**：强类型配置对象，自动从 `.env` + 环境变量读取，校验/默认值/类型一应俱全。
- **缺点**：
  - 要把 `app/agent.py` 里散落的 `os.environ.get` 重写为一个 `Settings` 类并替换所有读取点，
    改动面远超「支持 `.env` 文件」这一需求；
  - `Settings` 是模块级单例，会破坏 `build_model()` 当前「每次调用实时读 `os.environ`、便于
    monkeypatch 切分支」的设计（单测里反复 `monkeypatch.setenv` + 重新实例化 settings 较别扭）；
  - 引入「配置体系」属于需求外复杂度（镀金）。
- **结论**：**不采用**。可作为后续「配置治理」单独立项时的演进方向，记录在此供日后参考。

### 备选 B：手写解析 `.env`（不引依赖）

- **优点**：零新依赖。
- **缺点**：要自己处理引号、转义、`#` 注释、跨行、空行等边界，重复造轮子且易出 bug。
- **结论**：**不采用**。`python-dotenv` 已是事实标准且体积小。

### 备选 C：`override=True`（`.env` 覆盖真实环境变量）

- **优点**：本地 `.env` 永远生效，所见即所得。
- **缺点**：会污染单测（`tests/test_agent.py` 里 monkeypatch 设的环境变量被 `.env` 覆盖）、
  也会让部署环境行为依赖一个不该存在的 `.env` 文件。
- **结论**：**不采用**，坚持 `override=False`（dotenv 默认）。

### 最终选定：`python-dotenv` + `load_dotenv(override=False)`，加载点 `app/__init__.py`

最小改动、零侵入业务代码、与现有 `os.environ` 读取方式与单测完全兼容。

---

## 风险点

1. **`.env` 被误提交泄露密钥**（最高风险）。
   - 缓解：`.gitignore` 已有 `*.env`；`.env.example` 明确标注「勿提交真实密钥」；README 强调。
   - 残余：依赖开发者纪律。MVP 可接受；生产敏感场景应走 secret manager（非本设计范围）。

2. **加载点在 `app/__init__.py` 顶层有 import 副作用**，若 `python-dotenv` 未安装会让整个 `app`
   包不可 import（包括单测）。
   - 缓解：显式声明 `python-dotenv` 为运行时依赖（本设计已要求）；`uv sync` 后必然存在。
   - 残余：极低。`load_dotenv()` 本身是轻量调用，文件不存在时静默返回，无副作用。

3. **`load_dotenv()` 默认按 CWD 查找 `.env`**：若从仓库外目录运行（CWD 不是仓库根）则不加载。
   - 影响：仅在非常规启动方式下发生；此时退化为「用环境变量」，与现状一致，不回退。
   - 缓解：README 说明从仓库根运行；如确需，可在 `__init__.py` 里用
     `Path(__file__).resolve().parent.parent / ".env"` 显式定位——**MVP 暂不做**，留作后续微调。

4. **对 `tests/test_agent.py` 的潜在干扰**：本地若存在一个真实 `.env`（含 `MODEL_API_KEY` 等），
   `load_dotenv()` 会把它读进 `os.environ`。
   - 分析：单测用 `monkeypatch.setenv` / `delenv`，而 `override=False` 意味着 `load_dotenv()` **不会**
     覆盖已被 monkeypatch 的值；`monkeypatch.delenv(..., raising=False)` 会真正删除该键，
     `load_dotenv()` 此时已执行完毕（在 import 期），不会再补回。因此单测行为不受本地 `.env` 影响。
   - 结论：可接受，无需特殊处理。

5. **CI 环境**（`.github/workflows/`）若未来加 pytest CI：CI 上无 `.env`，`load_dotenv()` 是 no-op，
   单测照常绿——无新增风险。

---

## 验收要点（供后续 Planner / Developer 参考）

- [ ] 仓库根放一个 `.env`（`MODEL_API_KEY=test-xxx`），`uv run uvicorn app.main:app` 起来后 `/chat`
      能调到模型（或用 `TestModel` 验证 `.env` 值确实进了 `os.environ`）。
- [ ] `MODEL_API_KEY=override uv run pytest` 时，进程环境变量优先于 `.env`（`override=False` 生效）。
- [ ] `tests/test_agent.py` 全绿，且无本地 `.env` 时也能跑。
- [ ] `git status` 确认 `.env` 不被追踪、`.env.example` 被追踪。
- [ ] `pyproject.toml` 显式含 `python-dotenv`；`uv lock` 后 `uv.lock` 一致。
- [ ] 部署链路（Terraform + CVM systemd）零改动，端到端 `/chat` 仍通。
