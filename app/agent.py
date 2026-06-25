"""pydantic-ai Agent 定义：数据分析助手 + 腾讯云 AI 产品集成。

业务定位：「智能数据分析助手 / Data Analyst Copilot」。

接入两个腾讯云 AI 产品（可持续扩展）：
- **Agent Runtime（代码沙箱）**：通过 ``@agent.tool run_python`` 让模型生成的分析
  代码只在腾讯云沙箱内执行（不在本机 ``exec``），见 ``app.integrations.sandbox``。
- **Agent Memory（跨会话记忆）**：通过 ``@agent.instructions`` 把检索到的个性化
  上下文注入提示词；调用方（``app.main``）在 ``agent.run()`` 后写回结论。

关键约定（与现有 ``build_model()`` 一致）：
- **无副作用 import**：模块 import 期**不构造集成客户端、不读凭证、不触网**；
  ``defer_model_check=True`` 同样推迟模型 provider 实例化到 ``agent.run()``。
- **凭证 env-only**：所有密钥只走环境变量，错误时 fail-fast 抛 ``RuntimeError``。
- **deps 注入**：``user_id`` / ``session_id`` / 集成客户端经 ``RunContext[AgentDeps]``
  注入；运行期才装配，便于多用户多会话 + 测试以 fake 客户端替换。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:  # 仅类型，运行期不 import，保持无副作用 import
    from app.integrations.sandbox import SandboxExecutor

# 智谱 GLM 的 OpenAI 兼容端点（代码默认值；可用 ZHIPU_BASE_URL 覆盖）。
_ZHIPU_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"

# 走 OpenAI 兼容协议的 provider 配置表。各项差异：
#   key_env          ：MODEL_API_KEY 缺失时的回退 key 环境变量。
#   base_url_env     ：base_url 的来源环境变量。
#   default_base_url ：base_url 缺省值；None 表示必填（缺则 fail-fast）。
#   default_model    ：MODEL_STRING 未给模型名时的默认模型。
_OPENAI_COMPATIBLE = {
    "zhipu": {
        "key_env": "ZHIPU_API_KEY",
        "base_url_env": "ZHIPU_BASE_URL",
        "default_base_url": _ZHIPU_DEFAULT_BASE_URL,
        "default_model": "glm-4",
    },
    "tokenhub": {
        "key_env": "TOKENHUB_API_KEY",
        "base_url_env": "MODEL_BASE_URL",
        "default_base_url": None,  # 端点不固定，必填
        "default_model": "gpt-4o-mini",
    },
}


def _model_name_only(value: str) -> str:
    """去掉 ``provider:`` 前缀，只留模型名。"""
    return value.split(":", 1)[1] if ":" in value else value


def build_model():
    """按 ``MODEL_PROVIDER`` 选择层构造 pydantic-ai 模型对象。

    每次调用实时读 ``os.environ``（而非模块级常量），便于测试用 monkeypatch 切换分支。

    - ``openai``（默认，含拼写未知值）：直接返回 ``MODEL_STRING`` 字符串，向后兼容现状。
    - ``deepseek``：把 ``MODEL_API_KEY`` 映射到 ``DEEPSEEK_API_KEY``，返回
      ``deepseek:<model>`` 串，走 pydantic-ai 原生 provider。
    - ``zhipu`` / ``tokenhub``：走统一的 OpenAI 兼容路径（见 ``_OPENAI_COMPATIBLE``
      配置表），返回 ``OpenAIChatModel``；缺 key 时 fail-fast。其中 zhipu 的 base_url
      可省（走默认端点），tokenhub 端点不固定故 ``MODEL_BASE_URL`` 必填，缺则 fail-fast。
    """
    provider = os.environ.get("MODEL_PROVIDER", "openai").strip().lower()
    model_string = os.environ.get("MODEL_STRING", "openai:gpt-4o-mini")
    api_key = os.environ.get("MODEL_API_KEY")

    if provider == "deepseek":
        if api_key:
            os.environ.setdefault("DEEPSEEK_API_KEY", api_key)
        name = _model_name_only(model_string) or "deepseek-chat"
        return f"deepseek:{name}"

    if provider in _OPENAI_COMPATIBLE:
        cfg = _OPENAI_COMPATIBLE[provider]
        key = api_key or os.environ.get(cfg["key_env"])
        if not key:
            raise RuntimeError(
                f"MODEL_PROVIDER={provider} 需要提供 MODEL_API_KEY 或 {cfg['key_env']}"
            )
        base_url = os.environ.get(cfg["base_url_env"]) or cfg["default_base_url"]
        if not base_url:
            raise RuntimeError(
                f"MODEL_PROVIDER={provider} 需要提供 {cfg['base_url_env']}（端点不固定）"
            )
        name = _model_name_only(model_string) or cfg["default_model"]
        return OpenAIChatModel(
            name,
            provider=OpenAIProvider(base_url=base_url, api_key=key),
        )

    # 默认（含拼写未知的 provider 值）：保持现状，纯字符串。
    return model_string


# ---------------------------------------------------------------------------
# Deps：每次 /chat 请求实时装配的上下文（user_id/session_id + 集成客户端 + 记忆片段）
# ---------------------------------------------------------------------------


@dataclass
class AgentDeps:
    """注入到 ``RunContext`` 的依赖容器。

    设计要点：
    - ``sandbox`` 类型用前向字符串引用，避免 import 期触达 ``app.integrations``。
    - ``memory_context`` 由 ``app/main.py`` 在调用 ``agent.run()`` **前**完成检索并
      填入；这里不让 agent 工具直接调 Memory，是为了把「检索→执行→写回」编排集中在
      main，避免工具内嵌副作用让请求边界混乱。
    """

    user_id: str
    session_id: str
    sandbox: "SandboxExecutor | None" = None
    memory_context: str = ""
    # 沙箱执行的轨迹：每次工具调用追加一条简短摘要，便于 main 在请求结束后写回
    # Memory 作为「会话经验」沉淀。不放代码正文。
    run_log: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent 定义
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "你是一名严谨的中文数据分析助手（Data Analyst Copilot）。\n"
    "工作方式：\n"
    "1. 如果用户的问题需要计算、统计、绘图或处理数据，调用 `run_python` 工具，"
    "在腾讯云 Agent Runtime 沙箱中执行 Python 代码（pandas / numpy / matplotlib 等可用）。\n"
    "   - 代码必须自给自足：所需数据在代码里构造或读取，输出用 print。\n"
    "   - 工具会返回 stdout / stderr / error 三段文本；若 error 非空，请阅读并自我修正后重试。\n"
    "2. 如果是关于时间/服务器状态的浅问题，可调用 `server_time` 工具。\n"
    "3. 给出简洁结论（中文），必要时列出关键数字与下一步建议；避免冗长解释。\n"
    "4. 不要在本机执行任何代码，唯一执行路径是 `run_python` 工具。\n"
)


# defer_model_check=True：构造 Agent 时不立即实例化模型 provider，
# 因此 import 本模块不需要任何 API key（满足「无副作用 import」与无网单测）。
# 真正的 provider 实例化与凭证校验推迟到 agent.run() 时。
agent = Agent(
    build_model(),
    deps_type=AgentDeps,
    instructions=_INSTRUCTIONS,
    defer_model_check=True,
)


@agent.instructions
def _inject_memory_context(ctx: RunContext[AgentDeps]) -> str:
    """把 ``deps.memory_context``（已由 main 检索好）作为动态 instructions 注入。

    返回空串时 pydantic-ai 会把该段视为无内容，等同于无此段 instructions。
    """
    return ctx.deps.memory_context or ""


@agent.tool_plain
def server_time() -> str:
    """返回服务器当前时间（ISO 8601，UTC）。"""
    return datetime.now(timezone.utc).isoformat()


@agent.tool
def run_python(ctx: RunContext[AgentDeps], code: str) -> str:
    """在腾讯云 Agent Runtime 代码沙箱中执行一段 Python 代码并返回输出。

    Args:
        code: 要执行的 Python 代码。代码必须自给自足（数据在代码内构造/读取），
            用 ``print`` 输出关键结果；不要使用 ``input()`` 等交互式调用。

    返回值：包含 ``[stdout] / [stderr] / [error]`` 三段拼接文本；为空时为 ``[empty]``。

    安全：代码**只在腾讯云沙箱内执行**，本进程绝不调用 ``exec``/``subprocess``。
    """
    sandbox = ctx.deps.sandbox
    if sandbox is None:
        # 编排层未注入沙箱（如本地调试模型而未配凭证）：把错误回传给模型而非崩溃。
        return (
            "[error]\n沙箱未配置：请在部署/本地环境注入 E2B_API_KEY 与 SANDBOX_TEMPLATE"
            "（腾讯云 Agent Runtime 控制台创建）"
        )
    result = sandbox.run_python(code)
    # 记一条简短轨迹到 deps，供 main 写回 Memory（不包含代码正文）。
    ctx.deps.run_log.append(
        f"run_python: code_chars={len(code)} "
        f"stdout_chars={len(result.stdout)} "
        f"stderr_chars={len(result.stderr)} "
        f"error={'yes' if result.error else 'no'}"
    )
    return result.text
