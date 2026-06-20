# 实施计划：验证 Multica 多智能体工作流接入

- **关联 GitHub Issue**: [#1](https://github.com/ritchiecai/pydantic-agent-on-tencentcloud/issues/1)
- **Multica 工单**: BUB-11
- **父设计稿**: [docs/designs/MUL-BUB-10-multica-workflow-verification.md](../designs/MUL-BUB-10-multica-workflow-verification.md)
- **状态**: 草稿 (待评审)

## 概述

本需求是 Multica 多智能体 GitHub 事件驱动工作流的**端到端验证**。不涉及仓库内代码实现，核心是对现已存在于仓库的工作流配置（`.github/workflows/multica-dispatch.yml`）进行逐环节验证，确保从 GitHub issue 标签变更到 Multica agent 完成对应任务并回写 label 的整条链路畅通。

设计稿（BUB-10）中，`needs-design` → Designer-A 的环节已验证通过。剩余待验证的环节是规划 → 开发 → 测试 → 评审等后续角色链路。本计划将补齐全链路验证任务。

设计稿中指出的 **GitHub PAT 写权限不足** 是当前阻塞项——agent 无法直接在 GitHub issue 评论、开 PR、改标签。这影响所有需要 GitHub 回写的 agent 角色。

---

## 任务拆解

### T1: 环境准备——确认 GitHub PAT 写权限

- **文件**: 无文件变更。操作层面。
- **内容**: 
  - 检查当前 `GH_TOKEN` (fine-grained PAT) 的 scope 是否包含 issues/pull requests write
  - 如果不满足，切换到 keyring 中备用 token (`gho_...`，已有 `repo` scope) 或请求仓库 owner 配置正确的 PAT
- **依赖**: 无
- **验收标准**: `gh issue comment` / `gh pr create` 命令可成功执行

### T2: Planner-A 完成本轮规划并推进

- **文件**: 仅文档文件。
  - **新建** `docs/plans/MUL-BUB-11-multica-workflow-verification.md`（本文件）
- **内容**:
  - 完成本文档撰写
  - 创建 docs PR，标题 `MUL-BUB-11: plan for Multica workflow verification`
  - 在 Multica 工单 BUB-11 评论 @Planner-B 请求评审
  - 评审通过后 merge PR
- **依赖**: T1
- **验收标准**:
  - docs PR 已创建且包含本计划文件
  - Planner-B 评审已通过
  - docs PR 已 merge 到 main
  - GitHub issue #1 的 label 从 `needs-plan` 替换为 `ready-for-dev`

### T3: Developer 验证——接手 ready-for-dev 事件

- **文件**: 无代码文件变更。操作层面。
- **内容**:
  - 对 GitHub issue #1 打 `ready-for-dev` 标签
  - 验证 GitHub Actions 正确转发 `issues.labeled` 到 `AP_DEV` webhook
  - 验证 Multica 创建开发工单并指派给 Developer
  - Developer 在 GitHub issue 评论回链确认接手
- **依赖**: T2 (需要 label 已从 `needs-plan` 换成 `ready-for-dev`)
- **验收标准**:
  - GitHub Actions run log 显示 POST 返回 2xx
  - Multica 开发工单已创建并正确指派
  - Developer 已在 issue #1 评论回链

### T4: Tester 验证——接手 ready-for-test 事件

- **文件**: 无代码文件变更。操作层面。
- **内容**:
  - Developer 完成后，将 GitHub issue #1 的 label 换为 `ready-for-test`
  - 验证 GitHub Actions 正确转发到 `AP_TEST` webhook
  - 验证 Multica 创建测试工单并指派给 Tester
  - Tester 在 GitHub issue 评论回链确认接手
- **依赖**: T3
- **验收标准**:
  - GitHub Actions run log 显示 POST 返回 2xx
  - Multica 测试工单已创建并正确指派
  - Tester 已在 issue #1 评论回链

### T5: Reviewer 验证——接手 ready-for-acceptance 事件

- **文件**: 无代码文件变更。操作层面。
- **内容**:
  - Tester 完成后，将 GitHub issue #1 的 label 换为 `ready-for-acceptance`
  - 验证 GitHub Actions 正确转发到 `AP_ACCEPT` webhook
  - 验证 Multica 创建评审工单并指派给 Reviewer
  - Reviewer 在 GitHub issue 评论回链确认接手
- **依赖**: T4
- **验收标准**:
  - GitHub Actions run log 显示 POST 返回 2xx
  - Multica 评审工单已创建并正确指派
  - Reviewer 已在 issue #1 评论回链

### T6: 全链路总结——验证报告

- **文件**: 无新文件。操作层面。
- **内容**:
  - GitHub issue #1 评论中汇总所有 5 个角色的验证结果（Designer → Planner → Developer → Tester → Reviewer）
  - 确认所有角色的接手指派链路正常
  - 标注任何异常或阻塞项（如 GH_TOKEN 权限问题）
- **依赖**: T1, T2, T3, T4, T5
- **验收标准**: 验证报告已作为 issue #1 评论发布，汇总所有环节结果

---

## 依赖关系图

```
T1 (PAT 写权限)
 └─ T2 (Planner-A 规划 + docs PR)
     └─ T3 (Developer 验证)
         └─ T4 (Tester 验证)
             └─ T5 (Reviewer 验证)
                 └─ T6 (验证报告)
```

---

## 未纳入计划的内容

- **代码实现**：本需求是纯验证，不涉及代码编写。无新文件需要创建（除本文档本身）。
- **单元测试/集成测试**：设计稿中未要求。验证通过实际运行链路完成。
- **工作流配置文件修改**：`.github/workflows/multica-dispatch.yml` 在当前 `72487a8` 版本已是稳定状态，无需修改。Cloudflare 问题如有复现，作为独立 issue 处理。
- **GitHub Secrets 管理**：如 `MULTICA_AP_*_URL` 未配置，需仓库 owner 手动设置，不在本计划范围内。

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| GH_TOKEN 写权限不足 | T1 中切换备用 token 或请求 owner 配置；Plan B: 手动在 GitHub Web UI 完成回写操作 |
| Cloudflare 拦截 Multica webhook | 已在 git 历史中有调试记录（`72487a8` revert 到简单 curl 调用）。如复现，可能需要 Multica 侧配置防火墙白名单 |
| 后续 agent 角色未配置 | 本次验证主要覆盖 `needs-design` 和 `needs-plan` 链路；若后续角色（Developer/Tester/Reviewer）的 Multica webhook secret 未配置，则对应 T3-T5 降级为确认 secret 已就绪 |
| 并发标签导致路由异常 | 工作流对 `needs-plan` 和 `ready-for-dev` 等互斥标签分开处理无冲突；如后续 label 替换时先加后删导致瞬间多标签，GitHub Actions 会触发两次——按 label 名各自路由，预期行为正常 |

---

*Planner-A, 2026-06-20*
