"""腾讯云 AI 产品集成适配层。

每个腾讯云 AI 产品（Agent Runtime / Agent Memory / 未来还会增加）一个独立模块，
对外只暴露**最小稳定接口**（如 ``SandboxExecutor``、``MemoryClient``），内部封装
SDK / HTTP / env 读取等细节。这样 ``app/agent.py`` 与 ``app/main.py`` 只依赖抽象，
后续新增产品 = 新增一个模块 + 在 agent 注册一个工具/上下文来源，符合开闭原则。

设计哲学（与现有 ``build_model()`` 一致）：
- **无副作用 import**：本包及子模块在 import 期**不读凭证、不构造客户端、不触网**。
- **请求期按需构造**：``SandboxExecutor`` / ``MemoryClient`` 在 ``main.py`` 编排时
  才实例化；构造时做 fail-fast 校验（缺 env 抛 ``RuntimeError``，明确指出缺失的变量）。
- **凭证 env-only**：与仓库既定原则一致，所有密钥只走环境变量，不入参、不入日志。
"""
from app.integrations.memory import MemoryClient
from app.integrations.sandbox import SandboxExecutor

__all__ = ["MemoryClient", "SandboxExecutor"]
