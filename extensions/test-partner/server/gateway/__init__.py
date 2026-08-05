"""能力网关：宿主机唯一常驻进程里的「配置面」。

同一个 Python 进程（`server/main.py` 启动）里跑两个面：

- **MCP 面**：`0.0.0.0:3790`，streamable-http，供 DeepTutor 容器挂载（现状不动）。
- **配置面**：`127.0.0.1:3789`，本模块，浏览器打开填凭据、看状态、点重启。

配置面在独立线程里跑 uvicorn，MCP 面继续占主线程。这么分是因为 `MCPServer.run()`
自己要 `asyncio.run()` 一个事件循环并占住主线程，硬把两个 ASGI 应用拼进一个循环
需要动 SDK 的启动路径——那是"改内核"，不做。两个线程各跑各的事件循环，
共享的只有配置文件与子进程管家（都带锁）。

模块分工（决策 0006 的落地）：

| 模块 | 职责 |
| --- | --- |
| `config.py` | 配置中心：gateway.json（非敏感）+ secrets.env（凭据），原子读写、掩码投影 |
| `supervisor.py` | 子服务管家：mcp-server-tapd 子进程的启停/状态/失败诊断 |
| `deeptutor_client.py` | DeepTutor 部署注册表客户端（收编 register_tapd.py 的逻辑） |
| `webapp.py` | 配置页与 API（Starlette，内嵌单页 HTML） |
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from server.gateway.config import GatewayConfig, default_config
from server.gateway.webapp import DEFAULT_GATEWAY_PORT, GATEWAY_HOST, create_app

log = logging.getLogger("test-partner.gateway")

__all__ = [
    "GatewayConfig",
    "default_config",
    "create_app",
    "gateway_port",
    "gateway_url",
    "serve_in_thread",
    "GATEWAY_HOST",
    "DEFAULT_GATEWAY_PORT",
]


def gateway_port() -> int:
    """配置面端口。开发期想另起一份用 `TEST_PARTNER_GATEWAY_PORT`。"""
    try:
        return int(os.environ.get("TEST_PARTNER_GATEWAY_PORT",
                                  str(DEFAULT_GATEWAY_PORT)))
    except ValueError:
        return DEFAULT_GATEWAY_PORT


def gateway_url(port: int | None = None) -> str:
    return f"http://localhost:{port or gateway_port()}"


def serve_in_thread(*, port: int | None = None, **app_kwargs: Any) -> threading.Thread:
    """在守护线程里起配置面，返回线程句柄（已 start）。

    uvicorn 在非主线程里不会去装信号处理器（`capture_signals` 自己判断了主线程），
    所以这里不需要额外的 `install_signal_handlers` 补丁。线程是 daemon：
    主线程（MCP 面）退出时它跟着走，不会把进程吊住。
    """
    import uvicorn  # 局部导入：只有真起服务时才需要它

    resolved_port = port or gateway_port()
    app = create_app(gateway_port=resolved_port, **app_kwargs)
    config = uvicorn.Config(
        app,
        host=GATEWAY_HOST,   # 只绑回环，不给参数（见 webapp 模块 docstring 第 1 道）
        port=resolved_port,
        log_level="warning",  # 访问日志太吵，且没有可看的信息量
        access_log=False,
    )
    server = uvicorn.Server(config)

    def run() -> None:
        try:
            server.run()
        except Exception:  # noqa: BLE001 - 配置面挂了不该把 MCP 面带走
            log.exception("配置页服务异常退出（MCP 面不受影响）")

    thread = threading.Thread(target=run, name="gateway-webapp", daemon=True)
    thread.start()
    return thread
