import os
from datetime import datetime, timezone

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

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


# defer_model_check=True：构造 Agent 时不立即实例化模型 provider，
# 因此 import 本模块不需要任何 API key（满足“无副作用 import”与无网单测）。
# 真正的 provider 实例化与凭证校验推迟到 agent.run() 时。
agent = Agent(
    build_model(),
    instructions="你是一个简洁的中文助手。需要时调用工具获取服务器本地时间。",
    defer_model_check=True,
)


@agent.tool_plain
def server_time() -> str:
    """返回服务器当前时间（ISO 8601，UTC）。"""
    return datetime.now(timezone.utc).isoformat()
