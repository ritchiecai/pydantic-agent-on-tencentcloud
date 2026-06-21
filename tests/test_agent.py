"""无网单元测试：用 pydantic-ai 的 TestModel 验证 agent 可构造、工具可被触发。

全程不触网、不需要任何 API key、不需要 MODEL_STRING。TestModel 默认
``call_tools='all'``，会调用所有注册的工具，因此 ``server_time`` 会被真实执行。
"""

import os
import re

import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

from app.agent import agent, build_model, server_time

# 合法的 ISO 8601 时间戳（含时区），用于断言工具返回值。
_ISO8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def test_agent_is_constructed() -> None:
    """Agent 可被构造、且不带 output_type（默认文本）。"""
    assert agent is not None


def test_server_time_returns_iso8601() -> None:
    """server_time 工具直接返回合法 ISO 8601 字符串。"""
    ts = server_time()
    assert isinstance(ts, str)
    assert _ISO8601.match(ts), f"not a valid ISO 8601 timestamp: {ts!r}"


def test_agent_run_invokes_server_time_tool_without_network() -> None:
    """用 TestModel 跑一次 agent，证明 server_time 工具会被调用。

    TestModel 默认 call_tools='all'：它会调用所有注册的 function tool，
    并把工具返回值作为模型输出。断言输出里包含 server_time 返回的 ISO 时间。
    """
    test_model = TestModel()
    with agent.override(model=test_model):
        result = agent.run_sync("现在几点？")

    # 工具被调用过：TestModel 记录了它发出的 tool call 列表。
    assert test_model.last_model_request_parameters is not None
    # output 是工具返回值序列化后的文本，应包含 ISO 时间戳。
    assert "server_time" in result.output


# ---------------------------------------------------------------------------
# build_model() provider 选择层（无网、无 key 单测）
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
