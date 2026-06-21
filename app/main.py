from fastapi import FastAPI
from pydantic import BaseModel

from .agent import agent

app = FastAPI(title="pydantic-ai agent MVP")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # 单轮：每次请求都是全新对话，不传 message_history。
    result = await agent.run(req.message)
    return ChatResponse(reply=result.output)


@app.get("/healthz")
async def healthz() -> dict:
    # 浅检查，不调用模型。
    return {"status": "ok"}
