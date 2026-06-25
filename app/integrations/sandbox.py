"""腾讯云 Agent Runtime 代码沙箱执行器（兼容 E2B 协议）。

腾讯云 Agent Runtime 沿用 ``e2b-code-interpreter`` SDK，只需把 ``E2B_DOMAIN`` 指向
腾讯云域名（如 ``ap-guangzhou.tencentags.com``）、配置 ``E2B_API_KEY``，并把控制台
创建的「沙箱工具名称」作为 ``template`` 传入即可。

封装目标：
- 对 ``app/agent.py`` 暴露一个最小稳定接口：``run_python(code) -> ExecutionResult``。
- 单次 ``run_python`` 在一个独立沙箱内执行：``create → run_code → kill``，避免长期
  驻留沙箱产生费用。
- 缺凭证 / 缺模板时 fail-fast，错误信息指出缺失的 env。
- import 期不构造客户端、不读凭证、不触网（与 ``build_model()`` 哲学一致）。

⚠️ 安全：模型生成的代码**仅在腾讯云沙箱内执行**，不在本机 ``exec``/``subprocess``。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from app.integrations import config

logger = logging.getLogger("agent.sandbox")


@dataclass
class ExecutionResult:
    """沙箱代码执行结果（提供给 agent 工具的精简返回值）。

    - ``stdout`` / ``stderr``：执行过程的标准输出/错误（已截断防止把上下文撑爆）。
    - ``error``：执行报错的简要文本（None 表示成功）。
    - ``text``：默认序列化文本，用作工具返回值；包含三段拼接以方便模型读取。
    """

    stdout: str
    stderr: str
    error: str | None

    @property
    def text(self) -> str:
        parts: list[str] = []
        if self.stdout:
            parts.append(f"[stdout]\n{self.stdout}")
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        if self.error:
            parts.append(f"[error]\n{self.error}")
        return "\n".join(parts) if parts else "[empty]"


# stdout/stderr 的硬上限（字符数），防止极长输出污染模型上下文与日志。
_MAX_CAPTURE_CHARS = 8000


def _truncate(text: str, limit: int = _MAX_CAPTURE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


class SandboxExecutor:
    """Agent Runtime 代码沙箱执行器。

    构造时做 fail-fast 校验（缺 env 立即 raise），并把端点/Key 注入 ``os.environ``
    供 ``e2b_code_interpreter`` SDK 读取（SDK 通过 ``E2B_DOMAIN`` / ``E2B_API_KEY``
    环境变量识别后端）。
    """

    def __init__(self) -> None:
        # 必填项校验（fail-fast）
        self._api_key = config.runtime_api_key()
        self._template = config.runtime_sandbox_template()
        self._domain = config.runtime_domain()
        self._sandbox_timeout = config.runtime_sandbox_timeout()
        self._run_timeout = config.runtime_run_timeout()

        # SDK 通过 env 读取后端配置；显式设进 os.environ 以确保同进程内一致。
        # 用 setdefault 是为了不覆盖外层已显式设置的值（例如部署侧统一注入）。
        os.environ.setdefault("E2B_DOMAIN", self._domain)
        os.environ.setdefault("E2B_API_KEY", self._api_key)

    # ------------------------------------------------------------------
    # 内部：延迟 import SDK（保持本模块的无副作用 import）
    # ------------------------------------------------------------------
    def _sdk_sandbox_cls(self) -> Any:
        try:
            from e2b_code_interpreter import Sandbox  # type: ignore
        except ImportError as exc:  # pragma: no cover - 仅在 deps 缺失时触发
            raise RuntimeError(
                "缺少依赖 e2b-code-interpreter，请 `uv sync` 后再运行（腾讯云 "
                "Agent Runtime 兼容 E2B 协议，复用该 SDK）"
            ) from exc
        return Sandbox

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def run_python(self, code: str) -> ExecutionResult:
        """在腾讯云 Agent Runtime 代码沙箱中同步执行一段 Python 代码。

        语义：每次调用启动一个独立沙箱、执行完即 ``kill()`` 回收，避免长期驻留
        造成费用与状态泄漏。流式 ``stdout`` 透传到本地日志（不打代码正文）。
        """
        Sandbox = self._sdk_sandbox_cls()

        logger.info(
            "sandbox run start: template=%s domain=%s code_chars=%d",
            self._template,
            self._domain,
            len(code),
        )

        stdout_buf: list[str] = []
        stderr_buf: list[str] = []

        def _on_stdout(data: Any) -> None:
            # SDK 会把每行/每块输出回调到这里；统一转 str。
            try:
                stdout_buf.append(str(data))
            except Exception:  # noqa: BLE001 - 任何异常都不能影响主流程
                pass

        def _on_stderr(data: Any) -> None:
            try:
                stderr_buf.append(str(data))
            except Exception:  # noqa: BLE001
                pass

        sandbox = Sandbox.create(template=self._template, timeout=self._sandbox_timeout)
        try:
            execution = sandbox.run_code(
                code,
                on_stdout=_on_stdout,
                on_stderr=_on_stderr,
                timeout=self._run_timeout,
            )
        finally:
            # 一定要 kill：超时 / 异常都要回收沙箱，避免空跑产生费用。
            try:
                sandbox.kill()
            except Exception:  # noqa: BLE001 - kill 失败不影响主流程
                logger.exception("sandbox kill failed")

        stdout = _truncate("".join(stdout_buf))
        stderr = _truncate("".join(stderr_buf))
        # SDK 的 Execution 对象一般有 .error 字段（Exception/None）；做容错读取。
        error_obj = getattr(execution, "error", None)
        error_text: str | None = None
        if error_obj is not None:
            # 只取 traceback / name，不把任意对象 repr 打印出来。
            name = getattr(error_obj, "name", type(error_obj).__name__)
            value = getattr(error_obj, "value", str(error_obj))
            error_text = _truncate(f"{name}: {value}")

        logger.info(
            "sandbox run done: stdout_chars=%d stderr_chars=%d error=%s",
            len(stdout),
            len(stderr),
            bool(error_text),
        )
        return ExecutionResult(stdout=stdout, stderr=stderr, error=error_text)
