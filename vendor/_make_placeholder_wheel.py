"""生成一个占位空 wheel：``tencentdb_agent_memory_sdk_python-0.1.0-py3-none-any.whl``。

为什么需要占位 wheel
--------------------
``pyproject.toml`` 通过 ``tool.uv.sources`` 把 ``tencentdb-agent-memory-sdk`` 解析到
``vendor/tencentdb_agent_memory_sdk_python-0.1.0-py3-none-any.whl``。即便该依赖只在
``[project.optional-dependencies] memory-sdk`` 中声明，``uv lock`` 仍会读取该 source
以解析元数据。**如果文件不存在，``uv sync`` / ``uv lock`` 会直接报错**，开发者就连
不带 extra 的默认场景也无法正常 sync。

为此，仓库中放一个**最小占位 wheel**：元数据合法但**不包含任何代码 / 不导出
``tencentdb_agent_memory`` 模块**。效果：
- ``uv sync`` 正常完成（即便用户没装真 SDK）。
- ``uv sync --extra memory-sdk`` 也能装上占位包，但运行时
  ``from tencentdb_agent_memory import MemoryClient`` 仍抛 ``ImportError``，
  ``MemoryClient`` 自动回退到 ``_HttpBackend``。
- 用户拿到真 wheel 后**直接覆盖**同名文件，重跑 ``uv sync --extra memory-sdk`` 即
  装上真 SDK，``_SdkBackend`` 自动启用。

何时运行
--------
- 在工程初始化阶段运行**一次**，生成 wheel 后提交进 git。
- 真 wheel 到位后**无需再运行此脚本**，覆盖文件即可。
- CI 不需要运行此脚本。

执行
----
    python3 vendor/_make_placeholder_wheel.py
"""
from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from pathlib import Path

# wheel 文件元信息，需与 pyproject.toml `tool.uv.sources` 中指定的文件名匹配。
DIST = "tencentdb_agent_memory_sdk_python"
VERSION = "0.1.0"
WHEEL_NAME = f"{DIST}-{VERSION}-py3-none-any.whl"

METADATA = f"""Metadata-Version: 2.1
Name: tencentdb-agent-memory-sdk-python
Version: {VERSION}
Summary: Placeholder wheel for Tencent Cloud Agent Memory Python SDK. Replace with the official wheel from Tencent Cloud to enable the SDK backend.
Home-page: https://cloud.tencent.com/document/product/1813/132134
License: Proprietary
"""

WHEEL_META = """Wheel-Version: 1.0
Generator: pydantic-agent-on-tencentcloud placeholder generator
Root-Is-Purelib: true
Tag: py3-none-any
"""


def _record_line(arcname: str, data: bytes) -> str:
    """生成 RECORD 文件中的一行：路径,sha256-hash,size。"""
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"{arcname},sha256={encoded},{len(data)}\n"


def main() -> None:
    dist_info = f"{DIST}-{VERSION}.dist-info"
    metadata_arc = f"{dist_info}/METADATA"
    wheel_arc = f"{dist_info}/WHEEL"
    record_arc = f"{dist_info}/RECORD"

    metadata_bytes = METADATA.encode()
    wheel_bytes = WHEEL_META.encode()

    record = ""
    record += _record_line(metadata_arc, metadata_bytes)
    record += _record_line(wheel_arc, wheel_bytes)
    # RECORD 自身条目按规范留 hash/size 字段为空。
    record += f"{record_arc},,\n"

    out_path = Path(__file__).resolve().parent / WHEEL_NAME
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(metadata_arc, metadata_bytes)
        zf.writestr(wheel_arc, wheel_bytes)
        zf.writestr(record_arc, record.encode())
    out_path.write_bytes(buf.getvalue())
    print(f"wrote placeholder wheel: {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
