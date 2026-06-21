"""应用包入口：在子模块 import 前加载仓库根目录的 `.env` 到 os.environ（不覆盖已存在变量）。

- `.env` 仅用于本地开发便利；部署侧（腾讯云 CVM）仍由 systemd EnvironmentFile 注入，二者不冲突。
- override=False：真实进程环境变量永远优先于 `.env`，避免污染单测的 monkeypatch 与部署环境。
"""
from dotenv import load_dotenv

load_dotenv()  # 默认查找 CWD 下的 .env；override=False
