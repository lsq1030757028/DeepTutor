# -*- coding: utf-8 -*-
"""secret_write — TAPD 令牌写入平台密钥库（0012 第三步 / 0027 确认执行）。

## 这个文件为什么存在（0012 的关键取舍）

平台的密钥库有个真缺口：**管理员 MCP 配置面根本没接它**——
`store_secrets` 全仓只有每用户空间面（`space_mcp.py`）调用，
管理员侧的 `mcp_settings.py` 一次都没调。所以在管理员表单里填的 env 值会
**明文落进 `settings/mcp.json` 并从 GET 原样回吐**，与「值不进配置文件」直接冲突。

**但读取半边是通的**（`${secret:...}` 在管理员条目里能解开）。缺的只是一条写入路径，
而它**不必改上游**：`store_secrets(owner_id, server, values)` 是个普通函数。

0012 原文的取舍：「宁可在自己的文件里加一个写入端点，也不去改上游的
`mcp_settings.py`——后者会新增一个上游触点，而触点表变长就是架构在往内核里长」。
本文件就是那个「自己的文件」。**净增上游触点 0。**

## 凭据纪律（三条，任何一条破了这个端点就该被删掉）

1. **值不回显**：返回体只有 `configured`（布尔）与 `masked`（末四位）。
2. **值不进日志**：本模块任何 print/log 都不许带 value；异常消息里也不许带。
3. **值不落我们的盘**：写进平台密钥库（`data/user/.../secrets/<server>.json`，
   平台自己 chmod 700），extensions 侧不留副本——这正是要退役 `secrets.env`
   TAPD 存储的原因（0012 第四步）。

## 边界

本模块**必须跑在容器内**才有意义：`store_secrets` 写的是容器里的密钥库路径。
宿主机网关进程 import 不到 `deeptutor.*`，故这里对 import 失败给的是一句
可执行的话（"这个端点只在容器内可用"），而不是一个 ImportError 栈。
"""
from __future__ import annotations

from typing import Any

#: 平台的共享 owner。管理员级 MCP 条目的密钥都挂在它名下
#: （`deeptutor/services/mcp/manager.py:58` 的 `SHARED_OWNER`）。
#: 与 0012 的「管理员级单条目，一个实例一个 TAPD 令牌」形态一致。
SHARED_OWNER = "_shared"

#: 允许经本端点写入的键。**白名单，不是黑名单**——
#: 拿一个通用的"写任意密钥"端点当 TAPD 令牌入口，等于给自己开一个万能写口。
ALLOWED_KEYS = ("TAPD_ACCESS_TOKEN",)

E_NOT_IN_CONTAINER = "E_NOT_IN_CONTAINER"
E_KEY_NOT_ALLOWED = "E_KEY_NOT_ALLOWED"
E_EMPTY_VALUE = "E_EMPTY_VALUE"


def mask(value: str) -> str:
    """掩码。短值全星号，长值露末 4 位方便用户认出「是不是我刚贴那个」。

    4 个字符不足以重建令牌，但足够让用户分辨自己换没换（银行卡尾号同理）。
    """
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) < 12:
        return "*" * len(text)
    return "*" * 8 + text[-4:]


def _platform_store():
    """拿平台的 `store_secrets`。拿不到就说清为什么，不抛 ImportError 栈。"""
    try:
        from deeptutor.services.mcp.secrets import store_secrets
    except ImportError:
        return None
    return store_secrets


def write_tapd_token(value: str, *, server: str = "tapd",
                     store=None) -> dict[str, Any]:
    """把 TAPD 令牌写进平台密钥库。返回体**不含值**。

    `store` 只为单测注入；生产路径一律走平台函数。
    """
    token = (value or "").strip()
    if not token:
        # 空值在平台语义里是「删除这条」，但那不该由一个叫"写令牌"的端点
        # 顺手做掉——删除是另一个动作，要另一个入口。
        return {"ok": False, "code": E_EMPTY_VALUE,
                "message": "令牌是空的。要删除已配置的令牌，用删除入口，不要写空值。"}
    writer = store or _platform_store()
    if writer is None:
        return {"ok": False, "code": E_NOT_IN_CONTAINER,
                "message": "这个端点只在 DeepTutor 容器内可用——它写的是平台密钥库，"
                           "而宿主机进程访问不到。请在容器内的配置面调用它。"}
    writer(SHARED_OWNER, server, {"TAPD_ACCESS_TOKEN": token})
    return {"ok": True, "code": "OK", "server": server,
            "configured": True, "masked": mask(token),
            # 给调用方一句：条目里该怎么引它。引用式写法是「值不进配置文件」的落点。
            "reference": "${secret:TAPD_ACCESS_TOKEN}"}


def write_secret(key: str, value: str, *, server: str = "tapd",
                 store=None) -> dict[str, Any]:
    """通用入口，但**只放行白名单里的键**。"""
    if key not in ALLOWED_KEYS:
        return {"ok": False, "code": E_KEY_NOT_ALLOWED,
                "message": f"{key!r} 不在允许写入的键白名单 {ALLOWED_KEYS} 内。"
                           "这个端点是 TAPD 令牌入口，不是通用密钥写口。"}
    return write_tapd_token(value, server=server, store=store)


def stdio_entry(server: str = "tapd",
                enabled_tools: tuple[str, ...] = (
                    "get_stories_or_tasks", "get_stories_fields_info")) -> dict[str, Any]:
    """0012 第三步要注册的 admin **stdio** 条目长什么样。

    命令指向 Dockerfile fork 块装出来的那个 venv 入口点；令牌走 `${secret:...}`
    引用而不是明文——引用能解开是平台既有能力（0012 已勘察实证「读取半边是通的」）。

    **白名单只放只读**（0027 裁定 2）：提缺陷、改状态等写工具**必须与确认闸同批放行**。
    先放写权限、后补流程约束，中间那段窗口期 AI 就能绕过确认直接提单——这个口子不开。
    所以这里的默认值是两个只读工具，**改它之前先确认确认闸在不在**。
    """
    return {
        "type": "stdio",
        # 与 Dockerfile 的 `uv venv --python /usr/bin/python3.13 /opt/tapd-mcp` 对齐。
        # 改这个路径必须同步改 Dockerfile，反之亦然。
        "command": "/opt/tapd-mcp/bin/mcp-server-tapd",
        "args": [],
        "env": {
            "TAPD_ACCESS_TOKEN": "${secret:TAPD_ACCESS_TOKEN}",
            # base_url 钉死：包对它无任何校验，不钉的话一个笔误就把令牌发去别处
            "TAPD_API_BASE_URL": "https://api.tapd.cn",
        },
        "cwd": "",
        "url": "",
        "headers": {},
        "tool_timeout": 30,
        "enabled_tools": list(enabled_tools),
        "disabled_tools": [],
        "enabled": True,
        "auth": "",
        "catalog_entry": "",
    }
