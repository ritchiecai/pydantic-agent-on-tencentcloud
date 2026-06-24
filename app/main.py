from fastapi import FastAPI
from pydantic import BaseModel

from .agent import agent
from .logging_config import setup_logging

logger = setup_logging()

app = FastAPI(title="pydantic-ai agent MVP")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # 单轮：每次请求都是全新对话，不传 message_history。
    # 仅记录长度，不落盘消息正文，避免敏感信息写入日志。
    logger.info("chat request received (chars=%d)", len(req.message))
    try:
        result = await agent.run(req.message)
    except Exception:
        logger.exception("chat request failed")
        raise
    logger.info("chat request done (reply_chars=%d)", len(result.output))
    return ChatResponse(reply=result.output)


@app.get("/healthz")
async def healthz() -> dict:
    # 浅检查，不调用模型。
    return {"status": "ok"}
