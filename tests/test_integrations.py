"""无网单测：腾讯云集成适配层。

覆盖：
- env 读取与 fail-fast（缺凭证抛 RuntimeError，错误信息含变量名）。
- SandboxExecutor 通过 monkeypatch 用 fake SDK 替换，验证参数装配与生命周期。
- MemoryClient 通过 fake _Backend 替换，验证格式化、降级、字符预算。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.integrations import config, memory as memory_mod
from app.integrations.memory import MemoryClient, MemoryItem, _Backend
from app.integrations.sandbox import ExecutionResult, SandboxExecutor


# ----- config: env 读取与 fail-fast --------------------------------------


def test_runtime_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        config.runtime_api_key()
    assert "E2B_API_KEY" in str(exc.value)


def test_runtime_sandbox_template_accepts_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_TEMPLATE", raising=False)
    monkeypatch.setenv("E2B_TEMPLATE", "tpl-from-alias")
    assert config.runtime_sandbox_template() == "tpl-from-alias"
    monkeypatch.setenv("SANDBOX_TEMPLATE", "tpl-primary")
    assert config.runtime_sandbox_template() == "tpl-primary"


def test_runtime_sandbox_template_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_TEMPLATE", raising=False)
    monkeypatch.delenv("E2B_TEMPLATE", raising=False)
    with pytest.raises(RuntimeError) as exc:
        config.runtime_sandbox_template()
    assert "SANDBOX_TEMPLATE" in str(exc.value)


def test_runtime_domain_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2B_DOMAIN", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_DOMAIN", raising=False)
    assert config.runtime_domain() == config.DEFAULT_RUNTIME_DOMAIN


def test_memory_endpoint_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_MEMORY_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError) as exc:
        config.memory_endpoint()
    assert "AGENT_MEMORY_ENDPOINT" in str(exc.value)


def test_memory_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_MEMORY_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        config.memory_api_key()
    assert "AGENT_MEMORY_API_KEY" in str(exc.value)


def test_memory_service_id_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_MEMORY_SERVICE_ID", raising=False)
    with pytest.raises(RuntimeError) as exc:
        config.memory_service_id()
    assert "AGENT_MEMORY_SERVICE_ID" in str(exc.value)


# ----- SandboxExecutor: fake SDK -----------------------------------------


class _FakeExecution:
    def __init__(self, error: Any = None) -> None:
        self.error = error


class _FakeSdkSandbox:
    """模拟 e2b_code_interpreter.Sandbox 的最小协议。"""

    last: "_FakeSdkSandbox | None" = None

    def __init__(self, template: str, timeout: int) -> None:
        self.template = template
        self.timeout = timeout
        self.code: str | None = None
        self.run_timeout: int | None = None
        self.killed = False
        type(self).last = self

    @classmethod
    def create(cls, template: str, timeout: int) -> "_FakeSdkSandbox":
        return cls(template=template, timeout=timeout)

    def run_code(
        self,
        code: str,
        on_stdout: Any = None,
        on_stderr: Any = None,
        timeout: int | None = None,
    ) -> _FakeExecution:
        self.code = code
        self.run_timeout = timeout
        if on_stdout:
            on_stdout("hello\n")
        if on_stderr:
            on_stderr("warn\n")
        return _FakeExecution(error=None)

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "ark_fake")
    monkeypatch.setenv("SANDBOX_TEMPLATE", "tpl-test")
    monkeypatch.setenv("E2B_DOMAIN", "fake.tencentags.com")
    monkeypatch.setenv("SANDBOX_TIMEOUT", "30")
    monkeypatch.setenv("SANDBOX_RUN_TIMEOUT", "5")


def test_sandbox_init_fails_fast_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setenv("SANDBOX_TEMPLATE", "x")
    with pytest.raises(RuntimeError) as exc:
        SandboxExecutor()
    assert "E2B_API_KEY" in str(exc.value)


def test_sandbox_init_fails_fast_without_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "x")
    monkeypatch.delenv("SANDBOX_TEMPLATE", raising=False)
    monkeypatch.delenv("E2B_TEMPLATE", raising=False)
    with pytest.raises(RuntimeError) as exc:
        SandboxExecutor()
    assert "SANDBOX_TEMPLATE" in str(exc.value)


def test_sandbox_run_python_lifecycle(
    runtime_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = SandboxExecutor()
    monkeypatch.setattr(sandbox, "_sdk_sandbox_cls", lambda: _FakeSdkSandbox)

    result = sandbox.run_python("print('hi')")

    fake = _FakeSdkSandbox.last
    assert fake is not None
    assert fake.template == "tpl-test"
    assert fake.timeout == 30
    assert fake.run_timeout == 5
    assert fake.code == "print('hi')"
    assert fake.killed is True

    assert isinstance(result, ExecutionResult)
    assert "hello" in result.stdout
    assert "warn" in result.stderr
    assert result.error is None


def test_sandbox_kills_even_on_run_failure(
    runtime_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _ExplodingSandbox(_FakeSdkSandbox):
        def run_code(self, *args: Any, **kwargs: Any) -> _FakeExecution:  # type: ignore[override]
            raise RuntimeError("boom")

    sandbox = SandboxExecutor()
    monkeypatch.setattr(sandbox, "_sdk_sandbox_cls", lambda: _ExplodingSandbox)

    with pytest.raises(RuntimeError, match="boom"):
        sandbox.run_python("print('x')")

    assert _FakeSdkSandbox.last is not None
    assert _FakeSdkSandbox.last.killed is True


# ----- MemoryClient: fake backend ----------------------------------------


class _FakeBackend(_Backend):
    def __init__(self, items: list[MemoryItem] | None = None, fail: bool = False) -> None:
        self._items = items or []
        self._fail = fail
        self.writes: list[tuple[str, str, str, str]] = []

    def retrieve(
        self, user_id: str, session_id: str, query: str, top_k: int
    ) -> list[MemoryItem]:
        if self._fail:
            raise RuntimeError("network down")
        return list(self._items[:top_k])

    def write(self, user_id: str, session_id: str, content: str, role: str) -> None:
        if self._fail:
            raise RuntimeError("network down")
        self.writes.append((user_id, session_id, content, role))


@pytest.fixture
def memory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_ENDPOINT", "https://fake-memory.example")
    monkeypatch.setenv("AGENT_MEMORY_API_KEY", "key-fake")
    monkeypatch.setenv("AGENT_MEMORY_SERVICE_ID", "mem-fake")


def test_memory_client_retrieve_formats_bullets(memory_env: None) -> None:
    backend = _FakeBackend(items=[
        MemoryItem(content="用户偏好折线图", score=0.9),
        MemoryItem(content="常用数据源 sales_2025.csv", score=0.85),
    ])
    client = MemoryClient(backend=backend)
    ctx = client.retrieve_as_context("u1", "s1", "上月销售如何？")
    assert "用户偏好折线图" in ctx
    assert "常用数据源 sales_2025.csv" in ctx
    # 以 bullet 形式展示
    assert "- 用户偏好折线图" in ctx


def test_memory_client_retrieve_empty_returns_empty(memory_env: None) -> None:
    client = MemoryClient(backend=_FakeBackend(items=[]))
    assert client.retrieve_as_context("u1", "s1", "Q") == ""


def test_memory_client_retrieve_degrades_on_failure(memory_env: None) -> None:
    """检索失败时**降级为空串**，不抛异常，确保对话不被记忆故障阻断。"""
    client = MemoryClient(backend=_FakeBackend(fail=True))
    assert client.retrieve_as_context("u1", "s1", "Q") == ""


def test_memory_client_write_records_calls(memory_env: None) -> None:
    backend = _FakeBackend()
    client = MemoryClient(backend=backend)
    client.write_turn("u1", "s1", "本次结论", role="assistant")
    assert backend.writes == [("u1", "s1", "本次结论", "assistant")]


def test_memory_client_write_degrades_silently(memory_env: None) -> None:
    """写回失败时**静默告警**，不抛异常。"""
    client = MemoryClient(backend=_FakeBackend(fail=True))
    # 不应该抛
    client.write_turn("u1", "s1", "x", role="user")


def test_memory_client_init_fails_fast_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """不传 backend 且无 env 时构造立即抛 RuntimeError。

    默认后端选择顺序：SDK 优先 → ImportError 回退 HTTP；HTTP 构造期 require_env 抛错。
    """
    monkeypatch.delenv("AGENT_MEMORY_ENDPOINT", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_SERVICE_ID", raising=False)
    with pytest.raises(RuntimeError):
        MemoryClient()


# ----- _SdkBackend / 默认后端选择 -----------------------------------------


class _FakeSdkMemoryClient:
    """模拟官方 ``tencentdb_agent_memory.MemoryClient`` 的最小协议。"""

    last: "_FakeSdkMemoryClient | None" = None

    def __init__(self, *, endpoint: str, api_key: str, service_id: str, timeout: float) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.service_id = service_id
        self.timeout = timeout
        self.add_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        type(self).last = self

    def add_conversation(self, *, session_id: str, messages: list[dict[str, str]]) -> dict:
        self.add_calls.append({"session_id": session_id, "messages": messages})
        return {"accepted_ids": ["msg-1"], "total_count": len(messages)}

    def search_conversation(self, *, query: str, limit: int, session_id: str) -> dict:
        self.search_calls.append(
            {"query": query, "limit": limit, "session_id": session_id}
        )
        return {
            "data": {
                "messages": [
                    {
                        "id": "msg-1",
                        "role": "user",
                        "content": f"hit for {query}",
                        "timestamp": "2026-06-01T00:00:00Z",
                        "score": 0.91,
                    }
                ]
            },
            "trace_id": "tr-fake",
        }


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSdkMemoryClient]:
    """把 fake SDK 注入 sys.modules，让 ``from tencentdb_agent_memory import MemoryClient`` 拿到 fake。"""
    import sys
    import types

    fake_module = types.ModuleType("tencentdb_agent_memory")
    fake_module.MemoryClient = _FakeSdkMemoryClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tencentdb_agent_memory", fake_module)
    _FakeSdkMemoryClient.last = None
    return _FakeSdkMemoryClient


def test_sdk_backend_uses_effective_session_id_for_write(
    memory_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cls = _install_fake_sdk(monkeypatch)
    client = MemoryClient()  # 走默认 backend → SDK
    client.write_turn("alice", "s1", "今天买了机票", role="user")

    sdk = fake_cls.last
    assert sdk is not None
    # 三必填项透传
    assert sdk.endpoint == "https://fake-memory.example"
    assert sdk.api_key == "key-fake"
    assert sdk.service_id == "mem-fake"
    # 多用户隔离：effective session_id 为 "{user_id}:{session_id}"
    assert sdk.add_calls == [
        {
            "session_id": "alice:s1",
            "messages": [{"role": "user", "content": "今天买了机票"}],
        }
    ]


def test_sdk_backend_search_returns_memory_items(
    memory_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cls = _install_fake_sdk(monkeypatch)
    client = MemoryClient()
    ctx = client.retrieve_as_context("alice", "s1", "买什么")
    sdk = fake_cls.last
    assert sdk is not None
    assert sdk.search_calls == [
        {"query": "买什么", "limit": 5, "session_id": "alice:s1"}
    ]
    # search_conversation 返回的 messages 应被解析为 bullet 文本
    assert "hit for 买什么" in ctx


def test_sdk_backend_normalizes_unknown_role(
    memory_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 user/assistant/system 的 role 归一化为 user，避免 SDK 返回 400。"""
    fake_cls = _install_fake_sdk(monkeypatch)
    client = MemoryClient()
    client.write_turn("alice", "s1", "x", role="tool")

    sdk = fake_cls.last
    assert sdk is not None
    assert sdk.add_calls[0]["messages"][0]["role"] == "user"


def test_default_backend_falls_back_to_http_when_sdk_missing(
    memory_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SDK 未安装时默认后端回退 _HttpBackend。"""
    import sys

    # 确保 SDK 不可 import
    monkeypatch.setitem(sys.modules, "tencentdb_agent_memory", None)  # type: ignore[arg-type]

    backend = memory_mod._default_backend()
    assert isinstance(backend, memory_mod._HttpBackend)
