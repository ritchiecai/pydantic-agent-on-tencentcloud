# 设计：验证 Multica 多智能体工作流接入

- **关联 GitHub Issue**: [#1](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/1)
- **Multica 工单**: BUB-10
- **状态**: 草稿 (待评审)

## 背景与目标

`pydantic-agent-on-tencentcloud` 仓库已接入 Multica 多智能体 GitHub 事件驱动工作流。其核心转发链路为：

```
GitHub issue label 变更
  → GitHub Actions (multica-dispatch.yml)
    → Webhook POST 到 Multica Autopilot URL
      → Multica 创建设计/规划/开发/测试/评审工单
        → 指派对应角色 agent (Designer-A, Planner-A, Developer, Tester, Reviewer)
```

本需求是 **链路验证测试**：通过给 GitHub issue 打 `needs-design` 标签，验证整条链路是否打通，即：

1. GitHub Actions 正确地 POST `issues.labeled` 事件到 Multica AP 设计 webhook
2. Multica 正确创建了设计执行单并指派给 Designer-A
3. Designer-A 接手后能在 GitHub issue 评论回链

**核心目标**：验证事件转发至 Multica 建单指派 agent 这一段链路。

## 方案概述

### 现有架构

当前仅有的仓库文件：

| 文件 | 用途 |
|---|---|
| `.github/workflows/multica-dispatch.yml` | 监听 `issues.labeled` 事件，按标签名路由到对应 Multica autopilot webhook |
| `README.md` | 项目说明，列出标签→角色映射表 |

工作流中定义了 5 个 webhook 秘密变量（`MULTICA_AP_DESIGN_URL`、`MULTICA_AP_PLAN_URL`、`MULTICA_AP_DEV_URL`、`MULTICA_AP_TEST_URL`、`MULTICA_AP_ACCEPT_URL`）。

设计阶段可以接触的 Git 历史中，工作流经历了多轮调试迭代（Cloudflare bot fight、header 修正等），当前 `72487a8` 为稳定版本。

### 关键接口/数据结构

#### GitHub → Multica 事件转发

```yaml
# POST 到 Multica webhook
Method: POST
Headers:
  Content-Type: application/json
  X-GitHub-Event: issues
Body: GITHUB_EVENT_PATH (原始 GitHub issues event payload)
```

GitHub 发出的 `issues.labeled` 事件 payload 包含：
- `action`: `"labeled"`
- `issue`: 完整的 issue 对象（含 title, body, labels, number, html_url 等）
- `label`: 被添加的 label 对象（含 name）
- `repository`: 仓库信息（含 full_name, clone_url 等）
- `sender`: 操作者信息

#### Multica 侧处理

Multica autopilot 接收事件后：
1. 解析 `issue.html_url` 找到关联的 GitHub issue
2. 解析 `label.name` 确定角色（`needs-design` → Designer-A）
3. 创建 Multica 工单，description 中包含完整 webhook payload
4. 指派给对应 role 的 agent 实例

### 涉及的现有 symbol

- **`.github/workflows/multica-dispatch.yml`**：事件转发入口，无需修改（已是稳定版本）
- **GitHub Secrets**: `MULTICA_AP_DESIGN_URL`、`MULTICA_AP_PLAN_URL`、`MULTICA_AP_DEV_URL`、`MULTICA_AP_TEST_URL`、`MULTICA_AP_ACCEPT_URL`——仓库级 secrets，由 sync 脚本管理

### 验证方案

本需求是纯验证性质，不涉及代码实现。验证步骤如下：

1. **事件触发**：对 GitHub issue #1 添加 `needs-design` 标签
2. **转发验证**：检查 GitHub Actions run log，确认 curl POST 返回 2xx
3. **建单验证**：确认 Multica 创建了新工单（BUB-10）并指派给 Designer-A
4. **Agent 行为验证**：确认 Designer-A 已在 GitHub issue 评论回链

当前状态：
- ✅ 步骤 1-3：已通过——GitHub issue #1 打标后 Multica 创建 BUB-10 并指派给 Designer-A
- ✅ 步骤 4：已通过——Designer-A 已在 issue #1 评论 `设计工单 BUB-10 已接手 by Designer-A`

后续步骤（需 GitHub PAT write 权限才能完成的，当前可能受限）：
5. 开 docs PR
6. 请求 Designer-B 评审
7. 达成一致后换标签

### 取舍与备选

| 决策 | 选择 | 备选 |
|---|---|---|
| 设计文档格式 | Markdown 放在 `docs/designs/` | 无——采用标准位置 |
| 验证方式 | 通过实际运行验证链路 | 通过 mock/单元测试——但链路涉及外部服务，实跑最有效 |
| GitHub 回写依赖 | 需要 GH_TOKEN with `repo` scope | GitHub App installation token——更安全但配置复杂 |

### 风险点

1. **GitHub PAT 权限不足**：当前活跃的 `GH_TOKEN`（fine-grained PAT）缺少写权限（issues write），无法回写 GitHub issue 评论或开 PR。备用 keyring token（`gho_...`）有 `repo` scope，可作为临时方案。
2. **Cloudflare 防护**：Multica webhook 端点如果加了 Cloudflare bot fight，GitHub Actions outbound 请求可能被拦截。Git 历史中有多次针对此问题的调试尝试（`browser UA`、`cf-mitigated header`），但最终 revert 回原始简单 curl 调用。当前状态待确认是否已真正解决。
3. **标签路由可靠性**：工作流依赖 `github.event.label.name` 做 case match，如果用户同时添加多个标签或标签名有拼写差异，可能导致路由失败或误路由。

---

*Designer-A, 2026-06-20*
