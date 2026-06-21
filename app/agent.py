import os
from datetime import datetime, timezone

from pydantic_ai import Agent

# 模型串来自环境变量，部署时注入；本地默认值仅用于开发。
MODEL_STRING = os.environ.get("MODEL_STRING", "openai:gpt-4o-mini")

# defer_model_check=True：构造 Agent 时不立即实例化模型 provider，
# 因此 import 本模块不需要任何 API key（满足“无副作用 import”与无网单测）。
# 真正的 provider 实例化与凭证校验推迟到 agent.run() 时。
agent = Agent(
    MODEL_STRING,
    instructions="你是一个简洁的中文助手。需要时调用工具获取服务器本地时间。",
    defer_model_check=True,
)


@agent.tool_plain
def server_time() -> str:
    """返回服务器当前时间（ISO 8601，UTC）。"""
    return datetime.now(timezone.utc).isoformat()
