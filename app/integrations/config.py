"""集成层环境变量读取与 fail-fast 助手。

复用 ``app/agent.py`` 的 env-only + fail-fast 风格：缺失必填项时抛 ``RuntimeError``，
错误信息明确指出缺失的变量名，便于运维排障；非必填项给出代码内默认值。

所有读取都**实时走 ``os.environ``**（而非模块级常量），这样：
- 测试可用 ``monkeypatch.setenv/delenv`` 切换分支。
- ``.env``（由 ``app/__init__.py`` 的 ``load_dotenv()`` 加载）与进程 env 都能生效。
"""
from __future__ import annotations

import os

# ---- Agent Runtime（兼容 E2B 协议）---------------------------------------
# 控制台域名（按地域选择）；广州地域为默认值，可被 env 覆盖。
DEFAULT_RUNTIME_DOMAIN = "ap-guangzhou.tencentags.com"


def require_env(name: str, hint: str = "") -> str:
    """读取必填 env，缺失则 fail-fast 抛 ``RuntimeError``。

    错误信息只提变量名与可选提示，**不打印任何值**，避免凭证误入日志/异常栈。
    """
    value = os.environ.get(name)
    if not value:
        suffix = f"（{hint}）" if hint else ""
        raise RuntimeError(f"缺少必需的环境变量 {name}{suffix}")
    return value


def runtime_domain() -> str:
    """Agent Runtime 接入域名。

    腾讯云 Agent Runtime 兼容 E2B 协议，使用 ``E2B_DOMAIN`` 指定端点；为方便
    Terraform / 部署侧覆盖，同时接受 ``AGENT_RUNTIME_DOMAIN`` 别名。
    """
    return (
        os.environ.get("E2B_DOMAIN")
        or os.environ.get("AGENT_RUNTIME_DOMAIN")
        or DEFAULT_RUNTIME_DOMAIN
    )


def runtime_api_key() -> str:
    """Agent Runtime API Key（必填，控制台「API Keys」创建，形如 ``ark_xxxx``）。"""
    return require_env(
        "E2B_API_KEY",
        "腾讯云 Agent Runtime 控制台「API Keys」页面创建后注入",
    )


def runtime_sandbox_template() -> str:
    """沙箱工具名称（必填，控制台「沙箱工具」创建）。

    对应 E2B SDK 的 ``template`` 参数；同时接受 ``SANDBOX_TEMPLATE`` 别名以方便配置。
    """
    value = os.environ.get("SANDBOX_TEMPLATE") or os.environ.get("E2B_TEMPLATE")
    if not value:
        raise RuntimeError(
            "缺少必需的环境变量 SANDBOX_TEMPLATE（腾讯云 Agent Runtime 控制台"
            "「沙箱工具」页面创建后注入；对应 E2B SDK 的 template 参数）"
        )
    return value


def runtime_sandbox_timeout() -> int:
    """单个沙箱实例存活上限（秒），默认 600s（10min）。"""
    raw = os.environ.get("SANDBOX_TIMEOUT", "600")
    try:
        return max(1, int(raw))
    except ValueError:
        return 600


def runtime_run_timeout() -> int:
    """单次 ``run_code`` 调用超时（秒），默认 120s，避免长任务把请求拖死。"""
    raw = os.environ.get("SANDBOX_RUN_TIMEOUT", "120")
    try:
        return max(1, int(raw))
    except ValueError:
        return 120


# ---- Agent Memory ---------------------------------------------------------


def memory_endpoint() -> str:
    """Memory 实例「API 接入」展示的访问地址（必填）。

    ``MemoryClient`` 仅向此**固定**端点发请求；端点来源于 env，**不由用户控制**，
    规避 SSRF。
    """
    return require_env(
        "AGENT_MEMORY_ENDPOINT",
        "腾讯云 Agent Memory 控制台实例详情页「API 接入」区域展示",
    )


def memory_api_key() -> str:
    """Memory 实例「获取密钥」生成的 API Key（必填）。"""
    return require_env(
        "AGENT_MEMORY_API_KEY",
        "腾讯云 Agent Memory 控制台实例详情页「获取密钥」生成",
    )


def memory_service_id() -> str:
    """Memory 实例 ID（必填，形如 ``mem-xxxxxxxx``）。

    腾讯云 Memory 官方 Python SDK 的 ``MemoryClient`` 三必填项之一，注入 HTTP 头
    ``x-tdai-service-id``，用于指定要操作的 Memory 实例。控制台「实例列表」可见。
    """
    return require_env(
        "AGENT_MEMORY_SERVICE_ID",
        "腾讯云 Agent Memory 实例 ID，控制台「实例列表」可见，形如 mem-xxxxxxxx",
    )


def memory_timeout() -> float:
    """Memory HTTP 调用超时（秒），默认 10s。"""
    raw = os.environ.get("AGENT_MEMORY_TIMEOUT", "10")
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 10.0


def memory_top_k() -> int:
    """检索召回条数上限，默认 5。"""
    raw = os.environ.get("AGENT_MEMORY_TOP_K", "5")
    try:
        return max(1, int(raw))
    except ValueError:
        return 5
