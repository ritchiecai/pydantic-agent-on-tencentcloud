# 实施计划：支持 `.env` 配置文件

- **关联 GitHub Issue**: [#15](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/15) —「支持 `.env` 配置文件」
- **Multica 工单**: BUB-24
- **依据设计稿**: [docs/designs/BUB-23-dotenv-config-support.md](../designs/BUB-23-dotenv-config-support.md)（docs PR #16，已合并、定稿，评审结论 Approve）
- **状态**: 初稿（待 Plan Reviewer 评审）

---

## 0. 计划总览

按定稿设计稿，引入**极薄的 `.env` 加载层**：在应用包入口 `app/__init__.py` 调用一次
`python-dotenv` 的 `load_dotenv()`（默认 `override=False`），把仓库根目录的 `.env` 读进
`os.environ`（不覆盖已存在变量）。`app/agent.py` 里既有的 `os.environ.get(...)` 读取代码
**完全不动**，`.env` 注入后自动生效。加载点选在 `app/__init__.py` 而非 `app/main.py`，是为了
同时覆盖 `from app.main import app` 与 `from app.agent import agent`（含单测）两条 import 路径。

显式声明 `python-dotenv` 为运行时依赖（虽已作为 `pydantic-ai` 的间接依赖存在于 `uv.lock`，
但未声明在 `pyproject.toml` 的 `dependencies`——按 Designer 准则「绝不假设某库可用」必须显式声明）。
另提供一个入库的 `.env.example` 模板，并在 `.gitignore` 补防御性反向规则，README 增补 `.env` 用法说明。

优先级：**真实进程环境变量 > `.env` 文件 > 代码内置默认值**（`override=False`）。

- **不改**：`app/agent.py`、`app/main.py`、`tests/test_agent.py`、`scripts/deploy_app.sh.tftpl`、`infra/*`、`/chat`、`/healthz` 接口、`server_time` 工具、3 个现有无网单测的行为。
- **不新增**：`pydantic-settings` 的 `BaseSettings` 配置类体系；多环境 `.env.*` 分层加载；配置热加载；加密 `.env`；改动 Terraform / CVM 部署链路。（均为设计稿明确排除的非目标。）
- **单元测试**：设计稿未要求新增单测，现有 `tests/test_agent.py` 保持不动且继续全绿（`override=False` 保证其 monkeypatch 不被本地 `.env` 干扰）。是否在实现期补一个 `.env` 加载相关的单测，**列为可选增强**（见步骤 1 的可选项），由 Developer 在实现时按成本自行取舍，不强制。

涉及改动的文件 **5 个**（应用入口 1 + 依赖声明 1 + gitignore 1 + 新增模板 1 + 文档 1），全部为既有文件的小改或纯新增文件，无新建模块、无业务逻辑改动。推荐按下方步骤顺序实施（每步均有依赖关系与验收标准）。

---

## 1. 步骤拆解

### 步骤 1：应用层 — 在 `app/__init__.py` 加载 `.env`

- **文件**：`app/__init__.py`（既有，当前为空文件，**新增加载逻辑**）
- **做什么**：
  1. 在文件顶部新增模块 docstring，说明加载意图、`override=False` 语义、部署侧不受影响。
  2. `from dotenv import load_dotenv`（`python-dotenv` 包的导入名为 `dotenv`）。
  3. 调用 `load_dotenv()`（**不带参数**，默认从 CWD 向上查找 `.env`；使用默认 `override=False`，
     即真实进程环境变量永远优先于 `.env`，不覆盖 monkeypatch 或 systemd 已注入的变量）。
     > 落地形态参考设计稿「关键接口 / 数据结构 §1」。**注意**：调用必须放在模块顶层
     > （import `app/__init__.py` 时立即执行），且**早于**任何 `from app.agent import ...` /
     > `from app.main import ...` 触发的子模块 import——`app/__init__.py` 是 `app` 包 import 时最先
     > 执行的代码，天然满足此顺序，无需额外排序。文件不存在时 `load_dotenv()` 静默返回，无副作用。
  4. **不改** `app/agent.py`、`app/main.py` 的任何代码——`build_model()` 里的 `os.environ.get(...)`
     会在 `.env` 注入后自动拿到值。
- **依赖**：无前置依赖（本步是后续所有步骤的根）。
- **验收标准**：
  - `import app`（或 `from app.agent import agent` / `from app.main import app`）时，若 CWD 下存在
    `.env`（如 `MODEL_API_KEY=test-xxx`），`os.environ` 中出现该键值。
  - 若 CWD 下**无** `.env`，import 正常成功、不报错（`load_dotenv()` 为 no-op），现有 3 个无网单测全绿。
  - 已存在的进程环境变量不被 `.env` 覆盖（即设 `MODEL_API_KEY=override` 跑应用时，`os.environ` 中
    该键仍是 `override`，而非 `.env` 里的值）。
  - 加载点在 `app/__init__.py` 而非 `app/main.py`：经 `from app.agent import agent`（不经 main）的
    路径也能拿到 `.env` 值（这是把加载点放在 `__init__.py` 的核心理由，由设计稿 §「加载点选址」明确）。
- **可选增强（不强制）**：若实现成本极低，可在 `tests/test_agent.py` 追加一个用
  `tmp_path` + `monkeypatch.chdir` 的 `.env` 加载冒烟单测（验证 `load_dotenv` 调用确实把文件值注入
  `os.environ` 且不覆盖已存在键）。设计稿未要求，Developer 视成本取舍；不做也满足验收。

### 步骤 2：依赖声明 — `pyproject.toml` 显式加 `python-dotenv`

- **文件**：`pyproject.toml`（既有，**追加一行依赖**）
- **做什么**：
  1. 在 `[project]` 的 `dependencies` 列表末尾追加 `"python-dotenv"`（紧跟 `uvicorn` 之后）。
     > 设计稿「关键接口 / 数据结构 §4」给的形态。**不写死版本上限**（保持与现有 `pydantic-ai`/
     > `fastapi`/`uvicorn` 一致的无约束写法）；`uv.lock` 中已解析出 `python-dotenv==1.2.2`，
     > 加约束无额外收益。
  2. 运行 `uv lock` 让 `uv.lock` 与 `pyproject.toml` 重新对齐（`python-dotenv` 会从「间接依赖」
     提升为「直接依赖」，lock 内容可能微调但其版本不变，仍为 1.2.2）。**务必提交更新后的 `uv.lock`**。
- **依赖**：步骤 1（`app/__init__.py` 已 `import dotenv`，需要依赖先就位才能 import 通过；实际实现可
  并行，但本地验证 import 必须在本步完成后）。
- **验收标准**：
  - `pyproject.toml` 的 `dependencies` 显式含 `python-dotenv`。
  - `uv lock` 成功，`uv.lock` 与 `pyproject.toml` 一致（无 drift）。
  - `uv sync` 后，全新环境下 `from dotenv import load_dotenv` 可成功 import（即依赖确实被安装，
    `app/__init__.py` 的 import 副作用不会因缺包让整个 `app` 包不可 import——设计稿风险点 2 的缓解）。

### 步骤 3：`.gitignore` 补防御性反向规则

- **文件**：`.gitignore`（既有，**追加一行**）
- **做什么**：
  1. 在「本地密钥 / 环境文件」区块（含 `*.env` 与 `infra/terraform.tfvars` 的那段），于 `*.env`
     下方追加 `!*.env.example`。
     > 设计稿「关键接口 / 数据结构 §3」。当前规则 `*.env` 的 glob 已**不**匹配 `.env.example`
     > （即模板文件现状本可被追踪），这里补 `!*.env.example` 是**防御性声明**——避免后续有人把
     > 规则改成更宽的 `.env*` 通配时误伤模板，并让意图自解释。不改动其他规则。
- **依赖**：无（独立小改，可与步骤 1/2 并行）。
- **验收标准**：
  - `git check-ignore .env` 仍报告 `.env` 被忽略（密钥文件绝不入库）。
  - `git check-ignore .env.example` 报告**未被忽略**（模板可入库）。
  - 现有 `infra/terraform.tfvars.example` 的忽略例外（`!*.tfvars.example`）行为不受影响。

### 步骤 4：新增 `.env.example` 入库模板

- **文件**：`.env.example`（**新建**，仓库根目录）
- **做什么**：
  1. 在仓库根目录新建 `.env.example`，内容**严格照搬**设计稿「关键接口 / 数据结构 §2」给出的
     dotenv 模板：包含 `MODEL_PROVIDER`、`MODEL_STRING`、`MODEL_API_KEY` 三个主配置项（含注释说明
     各 provider 取值），以及注释掉的 `ZHIPU_BASE_URL` / `ZHIPU_API_KEY` 高级回退项。
  2. 模板顶部保留「复制为 `.env` 后填入真实值（`.env` 已被 `.gitignore` 忽略，勿提交真实密钥）」的
     提示行。模板里的 key 用占位值（如 `MODEL_API_KEY=sk-xxx`），**绝不填真实密钥**。
- **依赖**：步骤 3（`.gitignore` 的 `!*.env.example` 例外确保该文件能被追踪）。
- **验收标准**：
  - `.env.example` 在仓库根目录，`git status` 显示其被追踪（非 ignored）。
  - 内容含 `MODEL_PROVIDER` / `MODEL_STRING` / `MODEL_API_KEY` 三键及注释。
  - 文件中无任何真实密钥（仅占位符）。

### 步骤 5：文档 — `README.md` 增补 `.env` 用法

- **文件**：`README.md`（既有，**改写「本地运行」小节**）
- **做什么**：
  1. 在「本地运行」小节（当前展示 `MODEL_API_KEY=sk-xxx uv run uvicorn ...` 命令前缀注入的位置），
     增补一段 `.env` 用法说明：
     - 说明可 `cp .env.example .env` 复制模板、填入真实 key 后，直接 `uv run uvicorn app.main:app --port 8000`
       而无需命令前缀；
     - 强调优先级（真实环境变量 > `.env` > 代码默认值）；
     - 强调 `.env` 不入库（已被 `.gitignore` 忽略），只提交 `.env.example`；
     - **明确部署侧（腾讯云 CVM）不受影响**——仍由 systemd `EnvironmentFile=/etc/agent/env` 注入，
       与本地 `.env` 互不干扰。
  2. 「切换到智谱（GLM）/ DeepSeek」小节可保留命令前缀示例，或顺手补一句「也可改用 `.env`」，
     视实现成本取舍（非强制）。**不改** Terraform 部署、目录结构等其他章节。
- **依赖**：步骤 1、4（`.env` 加载已实现、模板已入库，文档才有实物可指）。
- **验收标准**：
  - README「本地运行」小节出现 `.env` 用法（复制模板 → 填 key → 直接 uvicorn）。
  - 文档明确写出优先级与「部署侧不受影响」两点。
  - 不引入与设计稿冲突的描述（如不要写「`.env` 会覆盖环境变量」之类与 `override=False` 相悖的话）。

---

## 2. 验收清单（对应设计稿「验收要点」）

实现完成后逐项核验（全部应通过）：

- [ ] 仓库根放 `.env`（`MODEL_API_KEY=test-xxx`），`uv run uvicorn app.main:app` 起来后配置确实来自
      `.env`（可用 `TestModel` 或断言 `os.environ` 验证）。
- [ ] `MODEL_API_KEY=override uv run uvicorn ...`（或 pytest）时，进程环境变量优先于 `.env`
      （`override=False` 生效）。
- [ ] `uv run pytest` 全绿，且本地无 `.env` 时也能跑（`load_dotenv()` 为 no-op）。
- [ ] `git status` 确认 `.env` 不被追踪、`.env.example` 被追踪。
- [ ] `pyproject.toml` 显式含 `python-dotenv`；`uv lock` 后 `uv.lock` 一致。
- [ ] 部署链路（Terraform + CVM systemd）零改动，端到端 `/chat` 仍通。

---

## 3. 调研备忘（已确认，供 Developer 直接复用）

- **`python-dotenv` 已在 `uv.lock`**：版本 `1.2.2`，来源 pypi。当前仅作为 `pydantic-ai` 的间接依赖
  存在，未声明在 `pyproject.toml` 的 `dependencies`——本计划步骤 2 把它提升为直接依赖，符合
  Designer「绝不假设某库可用」准则。
- **导入名**：包名 `python-dotenv`，import 时用 `from dotenv import load_dotenv`（不是 `python_dotenv`）。
- **`app/__init__.py` 现状**：空文件（仅隐式包标记），适合放加载副作用，无需担心破坏既有内容。
- **`app/agent.py` 的 `defer_model_check=True`**：`Agent(build_model(), ...)` 在 import 期不触网、不需要
  key；但 `build_model()` 本身会读一次 `os.environ`。把 `load_dotenv()` 放在 `app/__init__.py`
  （早于 `agent.py` 的 import）能保证 `.env` 在这次读取前已注入。
- **`tests/test_agent.py` 不受影响**：单测用 `monkeypatch.setenv` / `delenv`；`override=False` 意味着
  import 期 `load_dotenv()` 不覆盖后续 monkeypatch 的值，且 `monkeypatch.delenv(rasing=False)` 在
  import 之后执行、`load_dotenv()` 不会再补回。CI 上无 `.env` 时 `load_dotenv()` 是 no-op。详见
  设计稿「风险点 4 / 5」。
- **`.gitignore` 现状**：`*.env` 通配已忽略 `.env` 但**不**忽略 `.env.example`；同区块已有
  `!*.tfvars.example` 的先例，补 `!*.env.example` 风格一致。
- **部署侧零改动**：`scripts/deploy_app.sh.tftpl` 写 `/etc/agent/env` + systemd `EnvironmentFile`，
  与本地 `.env`（CWD 查找）路径不同、互不干扰；`infra/*` 不动。
