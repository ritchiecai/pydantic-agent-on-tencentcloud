# BUB-6: 验证 Multica 多智能体工作流接入

## 背景与目标

pydantic-agent-on-tencentcloud 仓库已接入 Multica 多智能体 GitHub 事件驱动工作流。当 GitHub issue 打上特定标签时，GitHub Actions（`.github/workflows/multica-dispatch.yml`）路由事件到 Multica autopilot webhook URL，Multica 平台创建工单并指派给对应角色 agent。

本需求是一个**端到端集成验证**，验证的核心链路是：

> GitHub Issue 打 `needs-design` → Actions 转发 `issues.labeled` → Multica webhook 接收 → 创建设计工单 → 指派 Designer-A → Designer-A 按流程执行

**目标**：验证该链路从事件触发到 agent 接单并产出设计稿的完整闭环。

## 架构概述

### 事件转发层（GitHub Actions）

文件：`.github/workflows/multica-dispatch.yml`

- 监听 `issues.labeled` 事件
- 根据标签名路由到对应 Multica webhook URL（通过仓库 Secrets 注入）
- 使用 `POST` 原始 GitHub event JSON 到 autopilot endpoint

标签 → 角色映射：

| 标签 | 触发角色 | Multica Secret |
|------|---------|----------------|
| `needs-design` | Designer-A | `MULTICA_AP_DESIGN_URL` |
| `needs-plan` | Planner-A | `MULTICA_AP_PLAN_URL` |
| `ready-for-dev` / `changes-requested` | Developer | `MULTICA_AP_DEV_URL` |
| `ready-for-test` | Tester | `MULTICA_AP_TEST_URL` |
| `ready-for-acceptance` | Reviewer | `MULTICA_AP_ACCEPT_URL` |

### Multica 平台（autopilot 接收 & 建单）

webhook 接收层解析 GitHub event payload：
- 提取：repository 信息、issue 号、标签名、issue 标题与正文
- 创建 Multica 工单，描述中嵌入 GitHub event payload
- 根据标签 → 角色映射指派对应 agent

### Agent 执行层

各角色 agent 的职责与工作流在 AGENTS.md 中定义。本次验证聚焦 Designer-A：

1. 从 GitHub issue 解析需求
2. 在 GitHub issue 评论"已接手"并贴 Multica 工单链接
3. 将工单归入 `pydantic-agent-on-tencentcloud` project、打标签、改标题
4. 探索仓库代码结构
5. 产出设计稿到 `docs/designs/`
6. 开 docs PR
7. @Designer-B 请求评审
8. 评审通过后换标签 `needs-design` → `needs-plan`

## 涉及的现有符号

### 代码仓库

| 路径 | 描述 |
|------|------|
| `.github/workflows/multica-dispatch.yml` | GitHub Actions 转发层 |
| `README.md` | 仓库说明，含工作流文档 |
| `docs/designs/` | 设计文档目录 |

### Multica 平台

| 资源 | 标识 |
|------|------|
| 项目 | `pydantic-agent-on-tencentcloud` (UUID: `077930c8-102f-45c3-bdf3-49911cdbe38c`) |
| Agent | Designer-A (`cb8cfd12-7ad0-4e44-aa7c-102c8ae988c4`) |
| 标签 | `req-1`（本 issue 专属标签） |
| 工单 | BUB-6 (`8909d417-c086-4c6f-a7ed-c141d7488ff7`) |
| GitHub repository | `ritchiecai/pydantic-agent-on-tencentcloud` |

## 验证策略

本需求的"设计"本质上是对工作流架构的文档化描述，而验证策略是使本次测试闭环的关键。

### 验证点

1. **转发层**：GitHub Actions 监听到 `issues.labeled` 事件后正确向 Multica webhook POST
2. **建单层**：Multica 接收到 payload 后创建工单并指派 Designer-A
3. **Agent 层**：Designer-A 正确执行工作流（GitHub comment → issue 管理 → 设计稿 → docs PR → 请求评审 → 换标签）
4. **闭环**：`needs-design` 标签最终被替换为 `needs-plan`

### 成功标准

- GitHub issue #1 下有 Designer-A 的"已接手"评论
- Multica 工单 BUB-6 标题更新为 `[#1] 设计：...`，已打标签 `req-1`，已归 project
- `docs/designs/BUB-6-*.md` 存在且内容完整
- GitHub 上有对应的 docs PR
- 最终 `needs-design` 标签被 `needs-plan` 替换（需评审通过后）

## 前置依赖

- GitHub Actions workflow 已在仓库中配置（✅ 已存在）
- GH_TOKEN / PAT 已配置（⚠️ 按需求描述，可能尚未配置，Designer-A 的部分 GitHub 回写可能失败）
- Multica webhook URL secrets 已配置（需验证）

## 取舍与备选

### 当前方案：GitHub Actions → Multica webhook

- **优**：解耦，Actions 只做转发，Multica 负责工单管理与 agent 调度
- **缺**：多一跳，需要两个系统都正常

### 备选：Multica GitHub App 直接订阅

- 如果 Multica 以 GitHub App 形式订阅 `issues.labeled` webhook，可减少配置。但当前仓库已选择 Secrets + curl 方式。

**保留当前方案**，因为它已经在仓库中实现完毕。

## 风险点

1. **GH_TOKEN 未配置**：按需求描述，PAT 可能尚未配置。如果 `gh` CLI 无法认证，Designer-A 将无法评论 GitHub issue、开 PR、或更新标签。在此情况下，Designer-A 应尽力完成不需要 GH_TOKEN 的操作（如 Multica 工单内部操作、本地设计文档产出），并在工单评论中明确报告哪些步骤因缺少 GH_TOKEN 而跳过。

2. **Secret 未配置**：如果 `MULTICA_AP_DESIGN_URL` 等 webhook URL 未设，转发层将静默失败。验证需要确认 webhook payload 是否真正被转发。

3. **Agent 运行时环境**：Designer-A 需要 `gh` CLI、`multica` CLI 等工具可用，这在 Multica 平台托管的 runtime 中应该有保证。
