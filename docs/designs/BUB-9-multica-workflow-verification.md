# BUB-9: 验证 Multica 多智能体工作流接入

## 背景与目标

本仓库 (`pydantic-agent-on-tencentcloud`) 已接入 Multica 多智能体 GitHub 事件驱动工作流。通过 `.github/workflows/multica-dispatch.yml`，当 issue 被打上特定标签时，GitHub Actions 将 `issues.labeled` 事件 POST 到对应角色的 Multica autopilot webhook URL，触发 Multica 平台创建工单并指派给对应 agent。

本次需求（GitHub issue [#1](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/1)）的目标是**端到端验证这条链路是否已打通**，尤其聚焦：

> 【事件转发 → Multica 建单 → 指派 agent】

## 方案概述

### 角色-标签映射

| 标签 | 触发角色 | Webhook Secret |
|---|---|---|
| `needs-design` | Designer-A | `MULTICA_AP_DESIGN_URL` |
| `needs-plan` | Planner-A | `MULTICA_AP_PLAN_URL` |
| `ready-for-dev` / `changes-requested` | Developer | `MULTICA_AP_DEV_URL` |
| `ready-for-test` | Tester | `MULTICA_AP_TEST_URL` |
| `ready-for-acceptance` | Reviewer | `MULTICA_AP_ACCEPT_URL` |

### 事件流

```
GitHub issue labeled
       │
       ▼
.github/workflows/multica-dispatch.yml
       │  (case 匹配标签 → curl POST 到对应 webhook)
       ▼
Multica autopilot webhook
       │  (POST body = GitHub event payload)
       ▼
Multica 创建执行工单 + 指派 agent
       │
       ▼
Agent 接手执行 → GitHub issue 评论回链
```

## 关键接口/数据结构

### GitHub Actions 转发层

已实现于 `.github/workflows/multica-dispatch.yml`：

- **触发条件**: `issues.labeled`
- **输入**: `github.event.label.name` (标签名) + `github.event` JSON payload
- **输出**: `curl -sS -X POST <webhook_url> -H "Content-Type: application/json" -H "X-GitHub-Event: issues" --data @$GITHUB_EVENT_PATH`
- **权限**: `contents: read`, `issues: read`
- **Secrets**: 5 个仓库级 Secret (`MULTICA_AP_DESIGN_URL` 等)

### Agent 需完成的关键回链动作

1. 在 GitHub issue 评论"已接手"并贴 Multica 工单链接
2. 将 Multica 工单归入对应 project、打标签、改标题
3. (设计阶段) 产出设计文档、开 docs PR
4. 请求同行评审
5. 达成一致后更换 GitHub issue 标签

## 涉及的现有 symbol

- `.github/workflows/multica-dispatch.yml` — 事件转发 workflow（已存在）
- `README.md` — 工作流文档（已存在）

## 已验证 vs 待验证

### ✅ 已验证（本轮）

| 环节 | 结果 |
|---|---|
| GitHub issue #1 打 `needs-design` 标签 | 成功触发 workflow |
| workflow curl POST 到 `AP_DESIGN` webhook | Multica 工单 BUB-9 创建成功 |
| Multica 将工单指派给 Designer-A | 本 agent 收到并开始执行 |
| Designer-A 在 GitHub issue 回链 | 已评论 [#1](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/1#issuecomment-4758334377) |

### ⏳ 待验证（后续角色）

| 角色 | 标签 | 状态 |
|---|---|---|
| Planner-A | `needs-plan` | 待 Designer-A 完成设计后将 issue #1 标签从 `needs-design` 换为 `needs-plan` |
| Developer | `ready-for-dev` / `changes-requested` | 待 Planner-A 完成后触发 |
| Tester | `ready-for-test` | 待 Developer 完成后触发 |
| Reviewer | `ready-for-acceptance` | 待 Tester 完成后触发 |

### ⚠️ 已知限制

- **GH_TOKEN 未配置**: Designer-A 当前仅用 `gh` CLI 操作 GitHub（需要已认证的 `gh`），如果能正常执行，说明环境已有认证；若后续角色遇到权限问题，可能需要配置 `GH_TOKEN` 或 PAT。

## 取舍与备选

- **选择**: 采用 GitHub Actions `issues.labeled` + `curl` 转发方式，职责清晰，无需引入额外中间件。
- **备选**: GitHub App webhook 直连 Multica，但需要 App 安装授权，复杂度更高。
- **未选择**: 在 Multica 侧轮询 GitHub API — 延迟高且浪费 API 配额。

## 风险点

1. **Secret 泄露风险**: 5 个 webhook URL 存储在仓库级 Secrets，任何有 `workflow_dispatch` 或 push 权限的人可通过修改 workflow 打印 secret。需确保仓库权限管控严格。
2. **单点依赖**: 所有角色依赖同一个 GitHub Actions workflow 文件，若该文件被破坏，整条链路中断。
3. **GH_TOKEN 依赖**: Agent 需要写 GitHub（评论、PR、改标签），依赖 `gh` CLI 的认证状态或 `GH_TOKEN` 环境变量。若未配置，agent 可执行本地操作但无法回写 GitHub。
4. **幂等性**: GitHub `labeled` 事件可被重复触发（移除再添加标签），需 Multica webhook 侧做去重，否则会创建重复工单。
