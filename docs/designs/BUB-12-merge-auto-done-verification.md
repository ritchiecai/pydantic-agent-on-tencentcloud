# 设计：验证 docs PR merge 自动 Done

- **关联 GitHub Issue**: [#5](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/5)
- **Multica 工单**: BUB-12
- **状态**: 草稿 (待评审)

## 背景与目标

Multica 平台内置 PR merge 事件集成：当代码仓库中的 PR 被合并时，Multica 根据 PR 标题中携带的工单标识符（如 `BUB-xx`）自动将对应执行单状态迁移至 Done。

本需求是一个**验证测试**：通过 Designer-A 创建一个 docs PR（标题格式为纯净的 `BUB-xx: design...`），merge 后观察 Multica 是否自动将 BUB-12（本工单）迁移至 Done。

### 历史背景

之前两轮 PR 标题使用了 `MUL-BUB-xx:` 前缀格式：
- PR #3: `MUL-BUB-10: design for Multica workflow verification` (merged)
- PR #4: `MUL-BUB-11: plan for Multica workflow verification` (merged)

对应的 BUB-10 和 BUB-11 工单状态是否自动变为 Done 未知。本测试使用修正后的纯净格式 `BUB-xx:`，验证自动迁移是否生效。

## 方案概述

### 验证流程

```
Designer-A 创建 docs PR (标题: "BUB-12: design for merge auto-done verification")
  → Designer-A 自行评审通过 (本阶段简化，无 Designer-B)
  → Merge PR 到 main
  → 观察 Multica BUB-12 工单是否自动变为 Done
```

### 涉及文件

| 文件 | 操作 | 用途 |
|---|---|---|
| `docs/designs/BUB-12-merge-auto-done-verification.md` | 新建 | 本设计文档 |

仅涉及文档文件，无代码变更。

### PR 格式要求

按照修正后的 instructions，PR 标题应为纯净格式：
```
BUB-12: design for merge auto-done verification
```

不含 `MUL-` 前缀，以便 Multica 正确解析工单标识符。

### 验证判定标准

| 环节 | 通过条件 | 失败表现 |
|---|---|---|
| PR 创建 | PR 标题匹配 `BUB-12:` 格式 | N/A (手动确保) |
| PR Merge | 合并成功，文件进入 main | 合并冲突或其他错误 |
| 自动 Done | BUB-12 工单状态自动变为 `done` | 工单停留在 merge 前状态，需手动排查 Multica 集成配置 |

### 失败处理

如果 merge 后 BUB-12 未自动 Done：
1. 在 GitHub issue #5 记录失败，说明 PR 标题格式和 merge 时间
2. 手动将 BUB-12 收尾为 Done
3. 排查可能原因：Multica GitHub App 权限、webhook 配置、PR 标题解析规则

## 取舍与备选

| 决策 | 选择 | 备选 |
|---|---|---|
| PR 标题格式 | `BUB-12: ...` (纯净格式) | `MUL-BUB-12: ...` — 历史格式，可能导致解析失败 |
| 设计文档命名 | `BUB-12-merge-auto-done-verification.md` | `MUL-BUB-12-...` — 与标题格式保持一致 |
| 评审流程 | 本阶段跳过 Designer-B 评审，自审自合 | 正常双人评审流程 — 但本需求是验证 merge 自动 Done，简化以加速 |

## 风险点

1. **Multica PR merge 事件集成状态未知**：如果 Multica 侧尚未配置 PR merge → Done 的自动迁移逻辑，本测试将失败。此时需确认 Multica 平台集成是否已就绪。
2. **PR 标题解析规则不确定**：`BUB-xx:` 格式是否为 Multica 期望的确切格式待验证。如果不匹配，可能需要调整标题格式。
3. **GitHub PAT 权限**：当前使用 keyring token 有 `repo` scope，可正常创建 PR。如果权限失效，需切换备用方案。

---

*Designer-A, 2026-06-20*
