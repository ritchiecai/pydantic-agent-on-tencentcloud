"""腾讯云数据库 Agent Memory 客户端（薄封装）。

腾讯云 Agent Memory 提供「短期压缩 + 长期四层金字塔」记忆能力。本模块封装两种接入：

1. **官方 Python SDK** ``tencentdb-agent-memory-sdk``
   （pip 安装名，import 名 ``tencentdb_agent_memory``）。需走腾讯云提供的本地 wheel
   安装（暂未上 PyPI），详见 README。SDK 必填三件套：``endpoint`` / ``api_key`` /
   ``service_id``。
   - 写入：``add_conversation(session_id, messages=[{role, content}, ...])``
   - 检索：``search_conversation(query, *, limit, session_id)``
2. **HTTP 兜底**：当 SDK 未安装或导入失败时，自动走 Memory 网关的等价 HTTP 路径
   （``POST /v2/conversation/add``、``POST /v2/conversation/search``，鉴权头
   ``Authorization: Bearer`` + ``x-tdai-service-id``）。保证 showcase 在仅装好基础
   依赖、未拿到本地 wheel 时也能跑。

多用户隔离策略
--------------
官方 SDK 的「原始对话」层按 ``session_id`` 维度组织、**无独立 user_id 字段**。本模块
对外仍暴露 ``(user_id, session_id)`` 双标识；内部合成 ``effective_session_id =
"{user_id}:{session_id}"`` 作为 SDK 的 session_id，实现跨用户隔离。后续若需更强
的租户隔离，可按租户为每个用户分配独立 Memory 实例（``service_id``）替换本约定。

安全
----
- 端点/实例 ID 来自环境变量，**不接受用户可控 URL**（规避 SSRF）。
- API Key 仅出现在请求头，不入日志、不入异常正文。
- 日志只打元信息（user_id / session_id / 命中条数 / 耗时），不落记忆原文。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.integrations import config

logger = logging.getLogger("agent.memory")


@dataclass(frozen=True)
class MemoryItem:
    """单条召回记忆的轻量表示。"""

    content: str
    score: float | None = None


def _effective_session_id(user_id: str, session_id: str) -> str:
    """合成 SDK 用的 session_id（``{user_id}:{session_id}``）。

    冒号在 Memory session_id 中不具特殊语义，仅作命名分隔；user_id 与 session_id
    都由本服务生成/校验，外部输入不影响其它用户的命名空间。
    """
    return f"{user_id}:{session_id}"


class _Backend:
    """记忆后端抽象。两个实现：SDK / HTTP。"""

    def retrieve(
        self, user_id: str, session_id: str, query: str, top_k: int
    ) -> list[MemoryItem]:
        raise NotImplementedError

    def write(
        self, user_id: str, session_id: str, content: str, role: str
    ) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SDK 后端（首选）
# ---------------------------------------------------------------------------


class _SdkBackend(_Backend):
    """走官方 ``tencentdb_agent_memory.MemoryClient`` 的 SDK 后端。

    构造期完成 fail-fast：缺少 ``endpoint`` / ``api_key`` / ``service_id`` 任一项
    会立即抛 ``RuntimeError``。SDK 未安装时由 ``MemoryClient`` 门面捕捉 ImportError
    并回退到 HTTP 后端。
    """

    def __init__(self) -> None:
        # 仅在真正使用 SDK 时导入，保持本模块 import 无副作用。
        from tencentdb_agent_memory import MemoryClient as _SdkClient  # type: ignore

        self._client = _SdkClient(
            endpoint=config.memory_endpoint(),
            api_key=config.memory_api_key(),
            service_id=config.memory_service_id(),
            timeout=config.memory_timeout(),
        )

    def retrieve(
        self, user_id: str, session_id: str, query: str, top_k: int
    ) -> list[MemoryItem]:
        sid = _effective_session_id(user_id, session_id)
        # 官方签名：search_conversation(query, *, limit=None, session_id=None, ...)
        # limit 取值范围 [1, 100]，默认 5。
        data = self._client.search_conversation(
            query=query, limit=top_k, session_id=sid
        )
        return _parse_search_result(data)

    def write(
        self, user_id: str, session_id: str, content: str, role: str
    ) -> None:
        sid = _effective_session_id(user_id, session_id)
        # 官方签名：add_conversation(session_id, messages=[{role, content, timestamp?}])
        # role 取值仅 user / assistant / system；其它角色名归一化为 user 以避免 400。
        safe_role = role if role in ("user", "assistant", "system") else "user"
        self._client.add_conversation(
            session_id=sid,
            messages=[{"role": safe_role, "content": content}],
        )


# ---------------------------------------------------------------------------
# HTTP 兜底实现（始终可用：只依赖 httpx）
# ---------------------------------------------------------------------------


class _HttpBackend(_Backend):
    """直接走 Memory 网关 HTTP API 的兜底实现。

    路径与字段与官方 SDK 完全一一对应（参考腾讯云文档 1813/132157、1813/132159）：
    - ``POST /v2/conversation/add``：写入；payload ``{session_id, messages:[{role,content}]}``
    - ``POST /v2/conversation/search``：语义检索；payload ``{query, limit, session_id}``
    鉴权：``Authorization: Bearer <api_key>`` + ``x-tdai-service-id: <service_id>``。
    """

    def __init__(self) -> None:
        self._endpoint = config.memory_endpoint().rstrip("/")
        self._api_key = config.memory_api_key()
        self._service_id = config.memory_service_id()
        self._timeout = config.memory_timeout()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx  # 延迟 import，保持本模块的无副作用 import
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "缺少依赖 httpx，请 `uv sync` 后再运行"
            ) from exc

        url = f"{self._endpoint}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "x-tdai-service-id": self._service_id,
            "Content-Type": "application/json",
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {}

    def retrieve(
        self, user_id: str, session_id: str, query: str, top_k: int
    ) -> list[MemoryItem]:
        sid = _effective_session_id(user_id, session_id)
        data = self._post(
            "/v2/conversation/search",
            {"query": query, "limit": top_k, "session_id": sid},
        )
        return _parse_search_result(data)

    def write(
        self, user_id: str, session_id: str, content: str, role: str
    ) -> None:
        sid = _effective_session_id(user_id, session_id)
        safe_role = role if role in ("user", "assistant", "system") else "user"
        self._post(
            "/v2/conversation/add",
            {
                "session_id": sid,
                "messages": [{"role": safe_role, "content": content}],
            },
        )


def _parse_search_result(data: Any) -> list[MemoryItem]:
    """把 search_conversation 返回结构解析为 MemoryItem 列表。

    官方返回结构：``{"data": {"messages": [{id, role, content, timestamp, score}]}}``；
    SDK 与 HTTP 接口一致。容错对历史/异常结构兼容（``data`` 直接是 list、或顶层
    就是 ``messages``）。
    """
    if not isinstance(data, dict):
        return []
    inner = data.get("data")
    messages: list[Any]
    if isinstance(inner, dict):
        messages = inner.get("messages") or []
    elif isinstance(inner, list):
        messages = inner
    else:
        messages = data.get("messages") or []  # type: ignore[assignment]

    out: list[MemoryItem] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content") or msg.get("text") or ""
        if not content:
            continue
        score_raw = msg.get("score")
        score = float(score_raw) if isinstance(score_raw, (int, float)) else None
        out.append(MemoryItem(content=str(content), score=score))
    return out


# ---------------------------------------------------------------------------
# 对外门面：默认 SDK 优先、SDK 不可用回退 HTTP
# ---------------------------------------------------------------------------


def _default_backend() -> _Backend:
    """选择默认后端：优先 SDK，导入失败时回退 HTTP。

    SDK 走腾讯云本地 wheel 安装、未上 PyPI；未装时不强制依赖，自动回退 HTTP
    继续可用。其他构造错误（缺 env）直接抛给调用方。
    """
    try:
        return _SdkBackend()
    except ImportError:
        logger.info(
            "tencentdb_agent_memory SDK not installed; "
            "falling back to HTTP backend"
        )
        return _HttpBackend()


class MemoryClient:
    """对 ``app/agent.py`` 与 ``app/main.py`` 暴露的稳定门面。

    - 构造期 fail-fast：缺 env 立即抛 ``RuntimeError``，由调用方决定是否降级。
    - 默认按「SDK 优先 → HTTP 回退」自动选后端；测试可显式注入 fake backend。
    """

    # 注入到 agent 上下文的个性化文本块的最大字符数；防止把模型上下文撑爆。
    _CONTEXT_CHAR_BUDGET = 2000

    def __init__(self, backend: _Backend | None = None) -> None:
        self._backend: _Backend = backend or _default_backend()
        self._top_k = config.memory_top_k()

    def retrieve_as_context(
        self, user_id: str, session_id: str, query: str
    ) -> str:
        """检索并格式化为可直接注入 instructions 的文本片段。

        失败时**降级为空串**并打告警，记忆不可用绝不阻断对话。日志只打元信息。
        """
        started = time.monotonic()
        try:
            items = self._backend.retrieve(user_id, session_id, query, self._top_k)
        except Exception:  # noqa: BLE001
            logger.exception(
                "memory retrieve failed (user_id=%s session_id=%s)",
                user_id,
                session_id,
            )
            return ""
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "memory retrieve done: user_id=%s session_id=%s hits=%d elapsed_ms=%d",
            user_id,
            session_id,
            len(items),
            elapsed_ms,
        )
        if not items:
            return ""

        # 拼成有限长度的 bullet list；超出预算时截断。
        lines: list[str] = []
        used = 0
        for it in items:
            line = f"- {it.content.strip()}"
            if used + len(line) + 1 > self._CONTEXT_CHAR_BUDGET:
                break
            lines.append(line)
            used += len(line) + 1
        if not lines:
            return ""
        return (
            "以下是该用户/会话的相关历史记忆（来自腾讯云 Agent Memory），"
            "如与本次问题相关请在回答时参考：\n" + "\n".join(lines)
        )

    def write_turn(
        self,
        user_id: str,
        session_id: str,
        content: str,
        role: str = "assistant",
    ) -> None:
        """把一轮对话的结论/原子事实写回 Memory 沉淀。

        失败时**降级为静默告警**，写回不可用绝不阻断对话；不打印 content 正文。
        """
        started = time.monotonic()
        try:
            self._backend.write(user_id, session_id, content, role)
        except Exception:  # noqa: BLE001
            logger.exception(
                "memory write failed (user_id=%s session_id=%s role=%s chars=%d)",
                user_id,
                session_id,
                role,
                len(content),
            )
            return
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "memory write done: user_id=%s session_id=%s role=%s chars=%d elapsed_ms=%d",
            user_id,
            session_id,
            role,
            len(content),
            elapsed_ms,
        )
