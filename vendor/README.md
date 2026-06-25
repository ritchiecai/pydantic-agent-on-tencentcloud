# vendor/ — 本地 wheel 依赖目录

本目录承载**未上 PyPI 但本项目可选启用**的第三方包 wheel。

## 当前承载的 wheel

| 包 | 文件名 | 用途 |
|---|---|---|
| 腾讯云数据库 Agent Memory Python SDK | `tencentdb_agent_memory_sdk_python-0.1.0-py3-none-any.whl` | 走官方 SDK 调用 Memory（启用 `_SdkBackend`）。未启用时 `MemoryClient` 自动回退到 `_HttpBackend`（HTTP 调用 Memory 网关，功能等价） |

> 仓库默认携带一个**占位空 wheel**（metadata 合法、不导出任何模块，~1KB）。占位 wheel
> 用于让 `uv sync` 在没有真 wheel 时也能解析锁文件；运行时由于无可 import 模块，会
> 自动触发回退到 HTTP 后端，**不影响功能**。

## 如何换上真 wheel

腾讯云数据库 Agent Memory 官方文档：
<https://cloud.tencent.com/document/product/1813/132134>

按官方指引（控制台 / 邮件等）获取真 wheel 后，**保持文件名完全不变**，覆盖本目录
下的占位 wheel：

```
vendor/
└─ tencentdb_agent_memory_sdk_python-0.1.0-py3-none-any.whl   ← 直接覆盖
```

随后重新 sync：

```bash
uv sync --extra memory-sdk
```

应用代码无需任何改动；`MemoryClient` 会自动检测到 `tencentdb_agent_memory` 可
import 并切到 `_SdkBackend`。

## pyproject 关联

`pyproject.toml` 中的相关声明：

```toml
[project.optional-dependencies]
memory-sdk = ["tencentdb-agent-memory-sdk-python"]

[tool.uv.sources]
tencentdb-agent-memory-sdk-python = { path = "vendor/tencentdb_agent_memory_sdk_python-0.1.0-py3-none-any.whl" }
```

`uv sync`（默认）不会装 memory-sdk extra；`uv sync --extra memory-sdk` 才装。

## 版本升级

官方发布新版后，需要同步：

1. 替换 `vendor/` 下的 wheel 文件（如版本号变动则改文件名）。
2. 同步更新：
   - `pyproject.toml` 的 `tool.uv.sources` 路径
   - 本 README 表格的「文件名」
   - `vendor/_make_placeholder_wheel.py` 的 `VERSION` 常量
3. `uv lock` 重新生成锁文件。

## 兼容性

`tencentdb_agent_memory_sdk_python-0.1.0-py3-none-any.whl` 是 **pure Python wheel**
（`py3-none-any`），跨平台、跨架构通用：macOS / Linux x86_64 / ARM64 均可直接装。

## 安全

- wheel 由腾讯云官方下发，安装前请按官方指引校验来源。
- 本目录**不放凭证 / API Key / `.env` 等敏感文件**，仅放分发包。

## 占位 wheel 重新生成（一般用不到）

如果误删了占位 wheel 或需要重新生成（修改了 VERSION 等），运行：

```bash
python3 vendor/_make_placeholder_wheel.py
```

CI / 普通开发流程不需要运行此脚本。
