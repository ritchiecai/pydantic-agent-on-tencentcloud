"""无网单元测试：用 pydantic-ai 的 TestModel 验证 agent 可构造、工具可被触发。

全程不触网、不需要任何 API key / 不需要 MODEL_STRING / 不需要腾讯云凭证。
集成客户端（沙箱/记忆）走 fake 实例注入，验证 ``run_python`` 工具可被调用且不触网。
"""

import os
import re
from dataclasses import dataclass

import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

from app.agent import AgentDeps, agent, build_model, server_time
from app.integrations.sandbox import ExecutionResult

# 合法的 ISO 8601 时间戳（含时区），用于断言工具返回值。
_ISO8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


# ---------------------------------------------------------------------------
# Fake 沙箱：实现 SandboxExecutor.run_python 协议，绝不触网
# ---------------------------------------------------------------------------


@dataclass
class FakeSandbox:
    """符合 ``SandboxExecutor.run_python`` 协议的假实现，用于无网测试。"""

    calls: list[str]

    def run_python(self, code: str) -> ExecutionResult:
        self.calls.append(code)
        return ExecutionResult(stdout="ok-from-fake-sandbox", stderr="", error=None)


def _make_deps(*, sandbox=None) -> AgentDeps:
    return AgentDeps(
        user_id="u-test",
        session_id="s-test",
        sandbox=sandbox,
        memory_context="",
    )


# ---------------------------------------------------------------------------
# 基础：构造 + 工具
# ---------------------------------------------------------------------------


def test_agent_is_constructed() -> None:
    """Agent 可被构造、且不带 output_type（默认文本）。"""
    assert agent is not None


def test_server_time_returns_iso8601() -> None:
    """server_time 工具直接返回合法 ISO 8601 字符串。"""
    ts = server_time()
    assert isinstance(ts, str)
    assert _ISO8601.match(ts), f"not a valid ISO 8601 timestamp: {ts!r}"


def test_agent_run_invokes_server_time_tool_without_network() -> None:
    """用 TestModel 精准只调 server_time，证明工具可被触发、不触网。

    （限定到单个工具是为了避免 TestModel 自动用空参调到 ``run_python``，
    在 deps 未注入沙箱时返回错误文本，从而干扰本测试的语义。）
    """
    test_model = TestModel(call_tools=["server_time"])
    with agent.override(model=test_model):
        result = agent.run_sync("现在几点？", deps=_make_deps())

    assert test_model.last_model_request_parameters is not None
    assert "server_time" in result.output


def test_agent_run_python_invokes_fake_sandbox() -> None:
    """用 TestModel 触发 run_python，验证它把代码转发给注入的沙箱、不触网。"""
    calls: list[str] = []
    fake = FakeSandbox(calls=calls)
    test_model = TestModel(call_tools=["run_python"])
    with agent.override(model=test_model):
        result = agent.run_sync("跑一段代码", deps=_make_deps(sandbox=fake))

    # 沙箱被调用过一次（TestModel 用占位参数生成 code）。
    assert len(calls) == 1
    assert isinstance(calls[0], str)
    # 输出里包含 fake 沙箱的 stdout 标记，证明工具返回值被回传给模型。
    assert "ok-from-fake-sandbox" in result.output


def test_run_python_without_sandbox_returns_error_text() -> None:
    """deps 未注入沙箱时，run_python 工具返回错误文本而非崩溃，保证演示稳定。"""
    test_model = TestModel(call_tools=["run_python"])
    with agent.override(model=test_model):
        result = agent.run_sync("跑一段代码", deps=_make_deps(sandbox=None))

    # 错误文本里提示了缺失的关键 env，便于定位。
    assert "沙箱未配置" in result.output
    assert "E2B_API_KEY" in result.output


# ---------------------------------------------------------------------------
# build_model() provider 选择层（无网、无 key 单测）—— 沿用原有用例
# ---------------------------------------------------------------------------


def test_build_model_default_returns_model_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """不设 MODEL_PROVIDER 时，build_model() 返回 MODEL_STRING 字符串（向后兼容）。"""
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("MODEL_STRING", "openai:gpt-4o-mini")
    assert build_model() == "openai:gpt-4o-mini"


def test_build_model_deepseek_returns_prefixed_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_PROVIDER=deepseek 时返回 'deepseek:<model>' 并把 key 映射到 DEEPSEEK_API_KEY。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MODEL_STRING", "deepseek-chat")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    assert build_model() == "deepseek:deepseek-chat"
    assert os.environ["DEEPSEEK_API_KEY"] == "x"


def test_build_model_zhipu_returns_openai_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_PROVIDER=zhipu 时返回 OpenAIChatModel，且指向智谱端点。"""
    monkeypatch.setenv("MODEL_PROVIDER", "zhipu")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setenv("MODEL_STRING", "glm-4")
    model = build_model()
    assert isinstance(model, OpenAIChatModel)


def test_build_model_zhipu_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_PROVIDER=zhipu 且无 MODEL_API_KEY/ZHIPU_API_KEY 时 fail-fast 抛 RuntimeError。"""
    monkeypatch.setenv("MODEL_PROVIDER", "zhipu")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        build_model()


def test_build_model_tokenhub_returns_openai_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_PROVIDER=tokenhub 且 MODEL_BASE_URL 已设时返回 OpenAIChatModel（OpenAI 兼容协议）。"""
    monkeypatch.setenv("MODEL_PROVIDER", "tokenhub")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setenv("MODEL_STRING", "gpt-4o-mini")
    monkeypatch.setenv("MODEL_BASE_URL", "http://tokenhub.example/tokenhub/v1")
    model = build_model()
    assert isinstance(model, OpenAIChatModel)


def test_build_model_tokenhub_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_PROVIDER=tokenhub 且无 MODEL_API_KEY/TOKENHUB_API_KEY 时 fail-fast 抛 RuntimeError。"""
    monkeypatch.setenv("MODEL_PROVIDER", "tokenhub")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("TOKENHUB_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_BASE_URL", "http://tokenhub.example/tokenhub/v1")
    with pytest.raises(RuntimeError):
        build_model()


def test_build_model_tokenhub_without_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_PROVIDER=tokenhub 且缺 MODEL_BASE_URL 时 fail-fast 抛 RuntimeError。"""
    monkeypatch.setenv("MODEL_PROVIDER", "tokenhub")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        build_model()
