"""FastAPI 入口：编排腾讯云 AI 产品集成的数据分析助手 showcase。

单次 ``/chat`` 流程：
    1. 取 ``user_id`` / ``session_id`` / ``message``（user_id/session_id 带默认值，向后兼容）。
    2. 按需构造 ``MemoryClient`` / ``SandboxExecutor``（请求期实例化，fail-fast）。
    3. 用 ``MemoryClient`` 检索个性化上下文并填入 ``AgentDeps.memory_context``。
    4. 调 ``agent.run(message, deps=...)``；模型按需调用 ``run_python`` 沙箱工具。
    5. 把本轮 user / assistant 消息写回 Memory 沉淀。
    6. 返回 ``reply``（沿用单字段响应，对外接口兼容）。

安全/降级：
- 沙箱：构造失败（缺凭证）时**拒绝请求**（HTTP 500，错误信息只点出缺失的 env），
  避免误退化到本机执行；这是 showcase 「真实调用」的明确语义。
- 记忆：构造或读写失败时**降级为不可用、不阻断对话**（记忆是助力、不是必经）。
- 日志：仅记录字符数与元信息，不落消息正文 / 代码 / 记忆原文。
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import AgentDeps, agent
from .integrations.memory import MemoryClient
from .integrations.sandbox import SandboxExecutor
from .logging_config import setup_logging

logger = setup_logging()

app = FastAPI(title="pydantic-ai data analyst copilot (on Tencent Cloud)")


# ---------------------------------------------------------------------------
# Schemas（user_id / session_id 带默认，兼容现有调用方）
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户提问内容")
    user_id: str = Field(
        default="demo-user",
        description="用户标识，用于 Agent Memory 按用户维度组织个性化记忆。",
    )
    session_id: str = Field(
        default="demo-session",
        description="会话标识，用于跨多轮聚合上下文。",
    )


class ChatResponse(BaseModel):
    reply: str


class HealthResponse(BaseModel):
    status: str
    integrations: dict


# ---------------------------------------------------------------------------
# 集成客户端构造：请求期按需，封装错误处理
# ---------------------------------------------------------------------------


def _build_sandbox() -> SandboxExecutor:
    """构造沙箱执行器；缺凭证抛 HTTP 500，错误信息只点出 env 名。"""
    try:
        return SandboxExecutor()
    except RuntimeError as exc:
        # 沙箱是数据分析助手的核心能力，缺凭证直接拒绝请求，不退化到本机 exec。
        logger.exception("sandbox init failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _try_build_memory() -> Optional[MemoryClient]:
    """构造记忆客户端；缺凭证时**返回 None 并告警**，不阻断对话。"""
    try:
        return MemoryClient()
    except RuntimeError:
        logger.exception("memory init failed - degrade to no-memory mode")
        return None


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # 仅记录长度与会话元信息，绝不落消息正文。
    logger.info(
        "chat request received: user_id=%s session_id=%s chars=%d",
        req.user_id,
        req.session_id,
        len(req.message),
    )

    # ── 1) 构造集成客户端（请求期按需） ──────────────────────────────
    sandbox = _build_sandbox()
    memory = _try_build_memory()

    # ── 2) 检索记忆并装配 deps ─────────────────────────────────────
    memory_context = ""
    if memory is not None:
        memory_context = memory.retrieve_as_context(
            req.user_id, req.session_id, req.message
        )

    deps = AgentDeps(
        user_id=req.user_id,
        session_id=req.session_id,
        sandbox=sandbox,
        memory_context=memory_context,
    )

    # ── 3) 跑 agent（模型可能多次调用 run_python） ─────────────────
    try:
        result = await agent.run(req.message, deps=deps)
    except Exception:
        logger.exception(
            "chat request failed (user_id=%s session_id=%s)",
            req.user_id,
            req.session_id,
        )
        raise

    reply = result.output
    logger.info(
        "chat request done: user_id=%s session_id=%s reply_chars=%d tool_runs=%d",
        req.user_id,
        req.session_id,
        len(reply),
        len(deps.run_log),
    )

    # ── 4) 写回记忆：user message + assistant reply 各一条（降级容错） ─
    if memory is not None:
        memory.write_turn(req.user_id, req.session_id, req.message, role="user")
        memory.write_turn(req.user_id, req.session_id, reply, role="assistant")

    return ChatResponse(reply=reply)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """浅探活：不调模型 / 不触网，仅检查关键 env 是否已配置。

    每个集成项的状态为 ``configured``（必需 env 全在）/ ``missing``（缺至少一个）。
    CLB 健康检查只读取 ``status`` 字段，故无论集成项缺失与否，``status`` 都返回
    ``ok``，避免无 Memory/Runtime 凭证时实例被踢出。
    """
    sandbox_ok = bool(
        os.environ.get("E2B_API_KEY")
        and (os.environ.get("SANDBOX_TEMPLATE") or os.environ.get("E2B_TEMPLATE"))
    )
    memory_ok = bool(
        os.environ.get("AGENT_MEMORY_ENDPOINT")
        and os.environ.get("AGENT_MEMORY_API_KEY")
        and os.environ.get("AGENT_MEMORY_SERVICE_ID")
    )
    return HealthResponse(
        status="ok",
        integrations={
            "sandbox": "configured" if sandbox_ok else "missing",
            "memory": "configured" if memory_ok else "missing",
        },
    )
