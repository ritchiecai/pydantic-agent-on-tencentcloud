# pydantic-agent-on-tencentcloud

terraform + pydantic agent on Tencent Cloud.

## Multica 多智能体工作流

本仓库已接入 Multica 多智能体 GitHub 事件驱动工作流。
打 issue 标签会触发对应角色 agent：

| 标签 | 触发角色 |
|---|---|
| `needs-design` | Designer-A |
| `needs-plan` | Planner-A |
| `ready-for-dev` / `changes-requested` | Developer |
| `ready-for-test` | Tester |
| `ready-for-acceptance` | Reviewer |

转发逻辑见 `.github/workflows/multica-dispatch.yml`。
