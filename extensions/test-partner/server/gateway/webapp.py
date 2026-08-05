"""本地配置页：只绑 127.0.0.1:3789 的单页 Web 应用（Starlette，无前端框架）。

体感目标就一句话：**浏览器开 http://localhost:3789 → 粘 TAPD 令牌 → 保存并生效**。
保存这一下背后是四步（写配置 → 重启子服务 → 注册进 DeepTutor → 回读状态），
页面逐条显示 √/×，失败给分类诊断，不让用户去猜。

## 依赖

`starlette` + `uvicorn` 都已经在 `mcp[cli]==2.0.0` 的依赖树里（实测 starlette 1.3.1 /
uvicorn 0.52.1），不新引框架。页面是内嵌的一份 HTML 字符串，没有构建步骤、没有 CDN。

## 安全（四道，逐条有测试）

1. **只绑 127.0.0.1**。局域网不可达——这是最硬的一道，其余三道都是它的补强。
2. **Host 头白名单**。挡 DNS rebinding：攻击者让 `evil.com` 解析到 127.0.0.1，
   浏览器就会带着 `Host: evil.com` 打到本服务。只放行 localhost / 127.0.0.1 / ::1
   （可带端口），别的一律 403。**所有请求都查**，不只是 POST——页面本身也不该被外域读走。
3. **CSRF 双提交**。GET / 时发一个随机 token，同时写进 `SameSite=Strict` 的 HttpOnly
   cookie 和页面内嵌变量；所有 POST 要求 `X-CSRF-Token` 头与 cookie 逐字节相等
   （`hmac.compare_digest`）。为什么不只靠 SameSite：SameSite 是浏览器行为，老浏览器和
   非浏览器客户端不受约束；双提交则是服务端能自己验的。两道叠着用。
4. **凭据不回显**。页面与所有 API 响应里凭据只以 `{configured, masked, length}` 出现；
   日志同理（本模块不打任何含凭据的行，子进程日志过 `log_tail` 的兜底替换）。

## 页面形态：五个 tab + 可内嵌

页面按**类型**分五个 tab（概览 / 连接与凭据 / 测试环境 / 测试伙伴 / 工作台），切换是纯前端的
（不发请求、不掉已填内容），当前 tab 记在 URL hash 里（`#environments`），
刷新与直达都保持。分 tab 不是为了好看：配置页的分区只会越加越多（决策 0006 的扩展
契约就是"新能力 = 加一个分区"），堆成一个长页面的话每加一个能力就多一屏滚动。

页面还要能被嵌进 DeepTutor 的界面里。内嵌方式是实测确认过的：聊天页点任意 http(s)
链接会在**右侧 Viewer 面板**用 iframe 打开，面板宽度 400–960px 可拖拽，iframe 带
`sandbox="allow-scripts allow-same-origin allow-forms allow-popups"`、
`referrerPolicy="no-referrer"`，宿主页自己没有 CSP、没有 X-Frame-Options。
这套参数直接决定了页面能做什么、不能做什么：

- **`?embed=1`**：不渲染大标题那一块"独立页面才需要的 chrome"，外框留白也收掉，
  只出内容主体。tab 条留着——它是内容不是 chrome。
- **布局按 400–960px 设计**：容器只给 `max-width`，栅格与 flex 全用相对单位，
  400px 下不塌、不横向溢出。
- **深浅色都认**：颜色全走 CSS 变量，`prefers-color-scheme: dark` 换一套值。
  DeepTutor 默认深色，写死浅色嵌进去会刺眼。
- **不用 `alert()` / `confirm()` / `prompt()`**：sandbox 没给 `allow-modals`，
  调用会被**静默阻止**——`confirm()` 直接返回 false，二次确认永远点不动，
  而且一点报错都不给。删除环境的确认改成页面内的行内确认条。
- **不依赖浏览器下载**：sandbox 没给 `allow-downloads`。要给用户文件就给**绝对路径
  + 一键复制**（`navigator.clipboard`，失败回退到选中文本让用户 Ctrl+C）。
- **响应头不加 `X-Frame-Options`**：starlette 默认不加，这里也不补。补 `DENY` 固然
  不行，补 `SAMEORIGIN` 一样会挡死——DeepTutor 在 `127.0.0.1:3782`、配置页在
  `localhost:3789`，端口不同即跨源。同理不加 `CSP: frame-ancestors`。
  挡 clickjacking 的活由下面第 2、3 道闸兜着：跨站 iframe 里 `SameSite=Strict` 的
  CSRF cookie 发不出去，写操作一律 403。
  **Host 白名单与 CSRF 校验一个都不放开**，内嵌场景是同源回环访问，本来就满足。
- **不做任何基于 `Referer` 的来源判断**：iframe 是 `no-referrer`，这个头压根不来。
  想加来源校验只能加在 Host 白名单那一层。

## 端点

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 配置页（内嵌初始状态，不含凭据）；`?embed=1` 去掉独立页面 chrome |
| GET | `/api/status` | 全量状态 JSON（含 DeepTutor 连接状态，页面轮询用） |
| POST | `/api/tapd/token` | 保存令牌 → 重启子服务 → 注册 DeepTutor → 回读，逐步返回 |
| POST | `/api/tapd/restart` | 只重启 TAPD 子服务 |
| POST | `/api/refresh-session` | 拨 test-partner 条目开关，刷新聊天侧会话 |
| GET | `/api/environments` | 已配测试环境（**去值投影**：变量只出键名与掩码） |
| POST | `/api/environments/save` | 新增/编辑一个测试环境 |
| POST | `/api/environments/delete` | 删除一个测试环境 |
| GET | `/api/partner/status` | 伙伴体检（人格对不对、挂了几个工具、绑了哪些技能） |
| POST | `/api/partner/ensure` | 一键创建/修复测试伙伴，逐步返回 |
| GET | `/api/deliveries` | 交付批次列表（时间倒序，元信息读 receipt.json） |
| GET | `/api/deliveries/{id}` | 一个批次的用例表（旧批次走降级投影） |
| POST | `/api/deliveries/{id}/execute` | 开一轮执行，立刻返回 run_id（后台线程跑） |
| GET | `/api/runs/{run_id}` | 轮询进度与结果 |

## 工作台分区：为什么执行是「开一轮 + 轮询」

执行一批用例是秒级到分钟级的事，占住一个 HTTP 请求会让页面看起来死了，
也让"第 N/M 条"无从显示。所以 POST 只负责开工与返回 `run_id`，
真正的执行在后台线程里跑（`server/gateway/workbench.py` 的 `RunRegistry`），
页面每秒 GET 一次 `/api/runs/{id}`。不上 websocket 的理由写在那个模块里。

**执行逻辑不在本模块**：端点只做入参闸与 JSON 封装，真发请求的是
`server/execute.py` 的 `execute_cases`（进程内直接调用，不绕 MCP）。
凭据同理——页面只传得出**环境名**，值由 `execute_cases` 在本进程内向配置中心解析。

**所有 POST 的请求体只收 JSON**（`Content-Type: application/json`），不收表单编码。
页面自己发的就是 JSON；手工 curl 调试反正要手写 cookie 与 `X-CSRF-Token`（第 3 道闸），
多带一个 Content-Type 不算负担。不收表单是为了不引入 `python-multipart`——
starlette 1.3.1 对任何 form 解析都硬性要求它，详见 `_read_json` 的 docstring。

## 测试伙伴分区的一条约定

这个分区**没有删除路径**，也不会改名。`/api/partner/ensure` 要么建一个新伙伴，
要么往用户指定的既有伙伴上补配置（工具取并集、技能追加、人格覆盖）。
用户手工建的伙伴不会因为点了这个按钮而消失或被重命名。

## 测试环境分区的一条约定

编辑既有环境时页面**不知道**变量的原值（投影里没有），所以值输入框是空的。
提交时留空的变量按「保留原值」处理，合并发生在本模块的 `_save_environment` 里——
页面拿不到值，用户也不用为了改一个 base_url 把所有令牌重贴一遍。
要清掉某个变量就把整行删掉：提交里没有这个键，落盘后就没有这个键。
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets as secrets_module
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from server.gateway.config import (EnvironmentConfigError, GatewayConfig,
                                   default_config, normalize_variables)
from server.gateway.deeptutor_client import DeepTutorClient, DeepTutorError
from server.gateway.partner_setup import (DEFAULT_PARTNER_NAME, ensure_partner,
                                          partner_overview)
from server.gateway.supervisor import TapdSupervisor
from server.gateway.workbench import (RunRegistry, WorkbenchError,
                                      read_delivery, scan_deliveries)

log = logging.getLogger("test-partner.gateway")

#: 配置面固定只绑回环。这不是默认值，是约束——不要给它加 host 参数。
GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 3789

CSRF_COOKIE = "tp_csrf"
CSRF_HEADER = "X-CSRF-Token"

#: 工作台执行的默认单请求超时（秒）。与 `server/execute.py` 的默认值同源。
DEFAULT_EXEC_TIMEOUT_S = 15

#: Host 头白名单（端口部分单独剥掉再比）
ALLOWED_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _hostname_of(host_header: str) -> str:
    """从 Host 头里剥出主机名（去端口、去 IPv6 方括号、小写）。"""
    host = (host_header or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):  # IPv6 字面量 [::1]:3789
        closing = host.find("]")
        if closing != -1:
            return host[: closing + 1]
        return host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header: str) -> bool:
    return _hostname_of(host_header) in ALLOWED_HOSTNAMES


class GatewayApp:
    """把配置中心、子服务管家、DeepTutor 客户端接成一个 Web 面。

    三个协作者都从构造函数注入，测试里换成假件即可——本类自己不 new 任何东西，
    也就没有"测试时不小心真起了子进程 / 真打了 DeepTutor"的可能。
    """

    def __init__(
        self,
        *,
        config: GatewayConfig | None = None,
        supervisor: Any | None = None,
        deeptutor: Any | None = None,
        version: str = "0.8.0",
        mcp_host: str = "0.0.0.0",
        mcp_port: int = 3790,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        deliveries_dir: str | None = None,
        executor: Any | None = None,
    ) -> None:
        self.config = config or default_config()
        self.supervisor = supervisor if supervisor is not None else TapdSupervisor(
            config=self.config)
        self.deeptutor = deeptutor if deeptutor is not None else DeepTutorClient(
            config=self.config)
        self.version = version
        self.mcp_host = mcp_host
        self.mcp_port = mcp_port
        self.gateway_port = gateway_port
        #: 工作台看的批次根目录（测试指到临时目录，真身是 <repo>/deliveries）
        self.deliveries_dir = deliveries_dir
        #: 执行台账。`executor` 是注入点：测试塞假执行器，一个真实请求都不发。
        self.runs = RunRegistry(executor=executor, deliveries_root_dir=deliveries_dir)

    # ── 状态组装 ────────────────────────────────────────────────────────────

    def gateway_info(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "mcp_url_for_container": f"http://host.docker.internal:{self.mcp_port}/mcp",
            "config_url": f"http://localhost:{self.gateway_port}",
            # 贴进聊天或 SOUL 提示词的那一行：DeepTutor 里点 http(s) 链接会在右侧
            # Viewer 面板打开，所以这一行就是"打开工作台"的入口。用 127.0.0.1 而不是
            # localhost：两者都在 Host 白名单里，但 IP 形式不吃 DNS 解析这一步。
            "entry_markdown":
                f"[打开测试伙伴工作台](http://127.0.0.1:{self.gateway_port})",
        }

    def local_state(self) -> dict[str, Any]:
        """不打网络的那部分状态（页面首屏内嵌用，秒开）。"""
        return {
            "gateway": self.gateway_info(),
            "config": self.config.public_state(),
            "tapd": self.supervisor.status(),
        }

    def deeptutor_state(self) -> dict[str, Any]:
        settings = self.config.load_settings()
        self_entry = str((settings.get("deeptutor") or {}).get("self_entry")
                         or "test-partner")
        try:
            overview = self.deeptutor.overview([self_entry, "tapd"])
        except DeepTutorError as exc:
            return {"ok": False, "code": exc.code, "error": str(exc),
                    "api_base": self.deeptutor.api_base, "self_entry": self_entry}
        overview["ok"] = True
        overview["self_entry"] = self_entry
        return overview

    def full_state(self) -> dict[str, Any]:
        state = self.local_state()
        state["deeptutor"] = self.deeptutor_state()
        return state

    # ── 端点 ────────────────────────────────────────────────────────────────

    async def index(self, request: Request) -> Response:
        token = secrets_module.token_urlsafe(32)
        state = await run_in_threadpool(self.local_state)
        html = render_page(state, token,
                           embed=embed_requested(request.query_params.get("embed")))
        response = HTMLResponse(html)
        response.set_cookie(
            CSRF_COOKIE, token,
            httponly=True,       # 页面用的是内嵌副本，JS 不需要读 cookie
            samesite="strict",   # 与双提交叠加，见模块 docstring 第 3 条
            path="/",
            # 不设 secure：配置页是 http://localhost，设了 cookie 直接发不出去。
        )
        # 配置页永远不该被缓存：状态是活的，缓存住就成了骗人的灯。
        response.headers["Cache-Control"] = "no-store"
        return response

    async def api_status(self, request: Request) -> Response:
        state = await run_in_threadpool(self.full_state)
        return JSONResponse(state, headers={"Cache-Control": "no-store"})

    async def api_tapd_token(self, request: Request) -> Response:
        payload = await _read_json(request)
        token = str(payload.get("token") or "").strip()
        if not token:
            return JSONResponse(
                {"ok": False, "code": "EMPTY_TOKEN",
                 "message": "没有输入令牌。去 TAPD 网页端右上角头像 → 个人设置 → "
                            "个人访问令牌 生成一个，只勾选你自己需要的项目。",
                 "steps": []},
                status_code=400)
        result = await run_in_threadpool(self._save_token_and_apply, token)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def api_tapd_restart(self, request: Request) -> Response:
        result = await run_in_threadpool(self.supervisor.restart)
        # 重启后条目 URL 没变，但连接是新的——顺手让 DeepTutor 重连一次更省事。
        return JSONResponse(
            {"ok": bool(result.get("ok")), "code": result.get("code"),
             "message": result.get("message"),
             "log_tail": result.get("log_tail") or [],
             "state": await run_in_threadpool(self.local_state)},
            headers={"Cache-Control": "no-store"})

    async def api_refresh_session(self, request: Request) -> Response:
        result = await run_in_threadpool(self._refresh_session)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    # ── 测试环境 ────────────────────────────────────────────────────────────

    async def api_environments(self, request: Request) -> Response:
        rows = await run_in_threadpool(self.config.environments_public)
        return JSONResponse({"ok": True, "environments": rows},
                            headers={"Cache-Control": "no-store"})

    async def api_environment_save(self, request: Request) -> Response:
        payload = await _read_json(request)
        result = await run_in_threadpool(self._save_environment, payload)
        return JSONResponse(result, status_code=200 if result["ok"] else 400,
                            headers={"Cache-Control": "no-store"})

    async def api_environment_delete(self, request: Request) -> Response:
        payload = await _read_json(request)
        result = await run_in_threadpool(
            self._delete_environment, str(payload.get("name") or ""))
        return JSONResponse(result, status_code=200 if result["ok"] else 404,
                            headers={"Cache-Control": "no-store"})

    # ── 测试伙伴 ────────────────────────────────────────────────────────────

    async def api_partner_status(self, request: Request) -> Response:
        # `?partner_id=` = 用户在下拉里选中的那个，无论它长什么样都要体检。
        result = await run_in_threadpool(
            self._partner_overview, str(request.query_params.get("partner_id") or ""))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def api_partner_ensure(self, request: Request) -> Response:
        payload = await _read_json(request)
        result = await run_in_threadpool(
            self._ensure_partner,
            str(payload.get("name") or "").strip(),
            str(payload.get("partner_id") or "").strip())
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    # ── 工作台 ──────────────────────────────────────────────────────────────

    async def api_deliveries(self, request: Request) -> Response:
        payload = await run_in_threadpool(scan_deliveries, self.deliveries_dir)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def api_delivery(self, request: Request) -> Response:
        delivery_id = request.path_params.get("delivery_id", "")
        try:
            payload = await run_in_threadpool(
                read_delivery, delivery_id, self.deliveries_dir)
        except WorkbenchError as exc:
            return JSONResponse(
                {"ok": False, "code": exc.code, "message": str(exc)},
                status_code=404 if exc.code == "DELIVERY_NOT_FOUND" else 400,
                headers={"Cache-Control": "no-store"})
        # 环境清单跟着一起发：执行面上的下拉就是它，页面不必再打一次
        payload["environments"] = await run_in_threadpool(
            self.config.environments_public)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def api_delivery_execute(self, request: Request) -> Response:
        """开一轮执行。**这是有副作用的动作**——真发请求、写操作会真改数据。

        页面在按之前有行内确认条，但端点不依赖页面：入参闸在 `RunRegistry.start`
        里全过一遍，过不了一条请求都不发。
        """
        body = await _read_json(request)
        try:
            run = await run_in_threadpool(
                lambda: self.runs.start(
                    delivery_id=request.path_params.get("delivery_id", ""),
                    env=str(body.get("env") or ""),
                    case_ids=body.get("case_ids") or [],
                    timeout_s=body.get("timeout_s") or DEFAULT_EXEC_TIMEOUT_S,
                    auth=body.get("auth") or "env"))
        except WorkbenchError as exc:
            return JSONResponse(
                {"ok": False, "code": exc.code, "message": str(exc)},
                status_code=404 if exc.code == "DELIVERY_NOT_FOUND" else 400,
                headers={"Cache-Control": "no-store"})
        return JSONResponse({"ok": True, "run": run},
                            headers={"Cache-Control": "no-store"})

    async def api_run(self, request: Request) -> Response:
        run = self.runs.get(request.path_params.get("run_id", ""))
        if run is None:
            return JSONResponse(
                {"ok": False, "code": "RUN_NOT_FOUND",
                 "message": "没有这一轮执行的记录（网关重启过？）。"
                            "执行报告落在批次目录里，去产物区看。"},
                status_code=404, headers={"Cache-Control": "no-store"})
        return JSONResponse({"ok": True, "run": run},
                            headers={"Cache-Control": "no-store"})

    # ── 业务动作（同步，跑在线程池里） ──────────────────────────────────────

    def _partner_overview(self, selected: str = "") -> dict[str, Any]:
        """伙伴体检。DeepTutor 连不上不算本页故障——回一个带 error 的投影，
        页面把这个分区标灰即可，别让整页 500。"""
        try:
            return partner_overview(self.deeptutor,
                                    include=[selected] if selected else [])
        except DeepTutorError as exc:
            return {"ok": False, "code": exc.code, "error": str(exc),
                    "default_name": DEFAULT_PARTNER_NAME,
                    "partners": [], "candidates": []}

    def _ensure_partner(self, name: str, partner_id: str) -> dict[str, Any]:
        """一键创建/修复。`partner_id` 非空 = 修复既有那一个（页面下拉选的）。"""
        result = ensure_partner(
            client=self.deeptutor,
            name=name or DEFAULT_PARTNER_NAME,
            partner_id=partner_id)
        log.info("gateway: 测试伙伴配置完成 partner_id=%s created=%s ok=%s",
                 result.get("partner_id"), result.get("created"), result.get("ok"))
        return result

    def _save_token_and_apply(self, token: str) -> dict[str, Any]:
        """保存令牌 → 重启子服务 → 注册进 DeepTutor → 回读状态。

        **`token` 只在本函数内流动**：写进 secrets.env、注入子进程环境。
        它不进返回值、不进日志、不进异常消息。
        """
        steps: list[dict[str, Any]] = []

        def step(name: str, ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
            row = {"name": name, "ok": ok, "detail": detail}
            row.update(extra)
            steps.append(row)
            return row

        # 1) 落盘
        try:
            self.config.set_secret("TAPD_ACCESS_TOKEN", token)
        except (OSError, ValueError) as exc:
            step("保存令牌到本机配置", False, f"写配置文件失败：{exc}")
            return {"ok": False, "code": "SAVE_FAILED", "steps": steps,
                    "state": self.local_state()}
        step("保存令牌到本机配置", True,
             f"已写入 {self.config.secrets_path}（不进版本库、不出本机）")
        log.info("gateway: TAPD 令牌已更新（值不记录）")

        # 2) 重启子服务并等就绪
        restart = self.supervisor.restart()
        if not restart.get("ok"):
            step("重启 TAPD 子服务", False, str(restart.get("message") or ""),
                 code=restart.get("code"), log_tail=restart.get("log_tail") or [])
            return {"ok": False, "code": restart.get("code"), "steps": steps,
                    "state": self.local_state()}
        step("重启 TAPD 子服务", True, str(restart.get("message") or ""))

        # 3) 注册进 DeepTutor
        tapd_settings = self.config.service_settings("tapd")
        try:
            status = self.deeptutor.register_tapd(
                mcp_url=str(tapd_settings.get("mcp_url")),
                enabled_tools=list(tapd_settings.get("enabled_tools") or []),
                tool_timeout=int(tapd_settings.get("tool_timeout") or 30),
            )
        except DeepTutorError as exc:
            step("注册进 DeepTutor 部署注册表", False, str(exc), code=exc.code)
            return {"ok": False, "code": exc.code, "steps": steps,
                    "state": self.local_state()}
        allowed = "、".join(status.get("enabled_tools") or [])
        step("注册进 DeepTutor 部署注册表", True,
             f"条目 tapd 已{'新建' if status.get('created') else '更新'}，"
             f"放行工具：{allowed or '（未设置）'}")

        # 4) 回读连接状态
        if status.get("state") == "connected":
            step("回读连接状态", True,
                 f"tapd 已连接，DeepTutor 侧可见 {status.get('tool_count', 0)} 个工具")
            return {"ok": True, "code": "OK", "steps": steps,
                    "state": self.local_state()}
        step("回读连接状态", False,
             f"条目在，但状态是 {status.get('state') or '(空)'}。"
             f"{status.get('error') or ''} "
             "最常见原因：容器打不到 host.docker.internal，或子服务刚起来还没被探到，"
             "稍等几秒点「重启 TAPD 服务」再看一次。")
        return {"ok": False, "code": "NOT_CONNECTED", "steps": steps,
                "state": self.local_state()}

    def _save_environment(self, payload: dict[str, Any]) -> dict[str, Any]:
        """新增/编辑一个测试环境。

        **变量的值只在本函数内流动**：从请求体到 `environments.json`，
        不进返回值、不进日志、不进异常消息。返回的 `environments` 是去值投影。

        留空即保留原值：编辑时页面不知道原值（投影里没有），值输入框必然是空的。
        """
        original_name = str(payload.get("original_name") or "").strip()
        try:
            variables = normalize_variables(payload.get("variables"))
        except EnvironmentConfigError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc),
                    "environments": self.config.environments_public()}

        name = str(payload.get("name") or "").strip()
        previous = self.config.get_environment(original_name or name)
        if previous:
            for key, value in list(variables.items()):
                if not value and key in previous["variables"]:
                    variables[key] = previous["variables"][key]

        try:
            saved = self.config.upsert_environment(
                {"name": name, "base_url": payload.get("base_url"),
                 "note": payload.get("note"), "variables": variables},
                original_name=original_name)
        except EnvironmentConfigError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc),
                    "environments": self.config.environments_public()}
        except OSError as exc:
            return {"ok": False, "code": "SAVE_FAILED",
                    "message": f"写 {self.config.environments_path} 失败：{exc}",
                    "environments": self.config.environments_public()}

        log.info("gateway: 测试环境「%s」已保存（%d 个变量，值不记录）",
                 saved["name"], len(saved["variables"]))
        return {
            "ok": True,
            "code": "SAVED",
            "message": (f"环境「{saved['name']}」已保存到本机 "
                        f"{self.config.environments_path}（不进版本库、不经聊天）。"
                        f"执行用例时说环境名即可，"
                        f"{len(saved['variables'])} 个变量由网关在本机解析。"),
            "environments": self.config.environments_public(),
        }

    def _delete_environment(self, name: str) -> dict[str, Any]:
        cleaned = str(name or "").strip()
        if not cleaned:
            return {"ok": False, "code": "ENV_NAME_REQUIRED",
                    "message": "没说要删哪个环境。",
                    "environments": self.config.environments_public()}
        try:
            removed = self.config.delete_environment(cleaned)
        except (OSError, EnvironmentConfigError) as exc:
            return {"ok": False, "code": "DELETE_FAILED",
                    "message": f"删除失败：{exc}",
                    "environments": self.config.environments_public()}
        if not removed:
            return {"ok": False, "code": "ENV_NOT_FOUND",
                    "message": f"没有名为「{cleaned}」的环境。",
                    "environments": self.config.environments_public()}
        return {"ok": True, "code": "DELETED",
                "message": f"环境「{cleaned}」及其变量已从本机删除。",
                "environments": self.config.environments_public()}

    def _refresh_session(self) -> dict[str, Any]:
        settings = self.config.load_settings()
        name = str((settings.get("deeptutor") or {}).get("self_entry") or "test-partner")
        try:
            status = self.deeptutor.toggle_entry(name)
        except DeepTutorError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc)}
        return {
            "ok": status.get("state") == "connected",
            "code": "OK" if status.get("state") == "connected" else "NOT_CONNECTED",
            "message": f"已把 {name} 条目的开关拨了一次（关→开），当前状态："
                       f"{status.get('state') or '(空)'}，"
                       f"{status.get('tool_count', 0)} 个工具。"
                       "回聊天窗口重发一次消息即可。",
            "entry": status,
        }


async def _read_json(request: Request) -> dict[str, Any]:
    """读请求体。**只认 JSON 对象**，读不出就返回空字典，缺什么由端点自己报。

    这里曾经退化到 `await request.form()` 兜底，为的是手工 curl 少打一个
    `Content-Type`。两个理由把它去掉了：

    1. 省不下什么。CSRF 双提交要求 curl 自己带 cookie 罐和 `X-CSRF-Token` 头，
       调试命令本来就是多行的，再多一个头不构成负担。
    2. 代价是一个运行时依赖。starlette 1.3.1 对**任何** form 解析都硬性要求
       `python-multipart`（不只是 multipart，`x-www-form-urlencoded` 同样要），
       没装就抛 AssertionError——被这里的 `except` 吞掉后表现为"字段全空"，
       比直接不支持还难查。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 空体/非 JSON 一律按"没传"处理
        return {}
    return body if isinstance(body, dict) else {}


def create_app(**kwargs: Any) -> Starlette:
    """造配置页的 ASGI 应用。kwargs 透传给 `GatewayApp`。"""
    gateway = GatewayApp(**kwargs)

    routes = [
        Route("/", gateway.index, methods=["GET"]),
        Route("/api/status", gateway.api_status, methods=["GET"]),
        Route("/api/tapd/token", gateway.api_tapd_token, methods=["POST"]),
        Route("/api/tapd/restart", gateway.api_tapd_restart, methods=["POST"]),
        Route("/api/refresh-session", gateway.api_refresh_session, methods=["POST"]),
        Route("/api/environments", gateway.api_environments, methods=["GET"]),
        Route("/api/environments/save", gateway.api_environment_save,
              methods=["POST"]),
        Route("/api/environments/delete", gateway.api_environment_delete,
              methods=["POST"]),
        Route("/api/partner/status", gateway.api_partner_status, methods=["GET"]),
        Route("/api/partner/ensure", gateway.api_partner_ensure, methods=["POST"]),
        # 工作台。`{delivery_id}` 是单段匹配（不是 `:path`）——批次 id 只能是
        # deliveries/ 下的一级目录名，带斜杠的东西压根不该匹配上这条路由。
        Route("/api/deliveries", gateway.api_deliveries, methods=["GET"]),
        Route("/api/deliveries/{delivery_id}", gateway.api_delivery, methods=["GET"]),
        Route("/api/deliveries/{delivery_id}/execute", gateway.api_delivery_execute,
              methods=["POST"]),
        Route("/api/runs/{run_id}", gateway.api_run, methods=["GET"]),
    ]

    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        # 第 2 道：Host 头白名单（挡 DNS rebinding），所有方法都查。
        if not host_allowed(request.headers.get("host", "")):
            return JSONResponse(
                {"ok": False, "code": "BAD_HOST",
                 "message": "配置页只服务本机访问。请用 http://localhost:"
                            f"{gateway.gateway_port} 打开。"},
                status_code=403)
        # 第 3 道：CSRF 双提交，只对写操作。
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            cookie_token = request.cookies.get(CSRF_COOKIE, "")
            header_token = request.headers.get(CSRF_HEADER, "")
            if not cookie_token or not header_token or not hmac.compare_digest(
                    cookie_token, header_token):
                return JSONResponse(
                    {"ok": False, "code": "BAD_CSRF",
                     "message": "会话校验没过。刷新一下配置页再试。"},
                    status_code=403)
        return await call_next(request)

    # starlette 1.x 去掉了 `@app.middleware("http")` 装饰器，改成构造期声明。
    app = Starlette(
        routes=routes,
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=guard)],
    )
    app.state.gateway = gateway
    return app


# ── 页面 ────────────────────────────────────────────────────────────────────

#: 「独立页面才需要」的那一块（大标题 + 副标题）在模板里用这对注释圈起来，
#: `?embed=1` 时整段删掉——不是 `display:none`，是真不发给浏览器。
_CHROME_RE = re.compile(r"<!--CHROME-->.*?<!--/CHROME-->", re.DOTALL)

#: `?embed=` 认哪些写法。`?embed`（无值）也算开——手打 URL 时最容易这么写。
_EMBED_TRUE = frozenset({"", "1", "true", "yes", "on"})


def embed_requested(raw: str | None) -> bool:
    """`?embed=` 的取值判定。参数缺席 = 独立页面（默认形态不变）。"""
    if raw is None:
        return False
    return str(raw).strip().lower() in _EMBED_TRUE


def render_page(state: dict[str, Any], csrf_token: str, embed: bool = False) -> str:
    """把初始状态与 CSRF token 塞进内嵌 HTML。

    用标记替换而不是 `str.format`：模板里 CSS 和 JS 全是花括号，format 会炸。
    `state` 来自 `local_state()`，**已经是去凭据的投影**——这里不做二次过滤，
    过滤责任在 `GatewayConfig.public_state()`（那里有测试盯着）。

    `embed=True` 摘掉 chrome 段，并把 `data-embed` 置 1 让 CSS 收掉外框留白。
    """
    payload = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    html = (_CHROME_RE.sub("", PAGE_HTML) if embed
            else PAGE_HTML.replace("<!--CHROME-->", "").replace("<!--/CHROME-->", ""))
    return (html
            .replace("__EMBED__", "1" if embed else "0")
            .replace("__INITIAL_STATE__", payload)
            .replace("__CSRF_TOKEN__", csrf_token))


PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>测试伙伴 · 能力网关</title>
<style>
  /* 颜色一律走变量：下面那组 prefers-color-scheme 只换值，不重写规则。
     页面要能嵌进 DeepTutor（默认深色），写死浅色在深色宿主里是一块刺眼的白。 */
  :root {
    color-scheme: light dark;
    --bg: #f5f6f8; --card: #ffffff; --line: #e3e6ea; --text: #1f2328;
    --muted: #656d76; --accent: #1f6feb; --ok: #1a7f37; --bad: #cf222e; --warn: #9a6700;
    --field: #ffffff; --field-off: #f0f1f3; --field-off-text: #9aa2ab;
    --soft: #fbfcfd; --idle: #b9c0c8; --on-accent: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1117; --card: #161b22; --line: #30363d; --text: #e6edf3;
      --muted: #9198a1; --accent: #4493f8; --ok: #3fb950; --bad: #f85149;
      --warn: #d29922;
      --field: #0d1117; --field-off: #21262d; --field-off-text: #6e7681;
      --soft: #0d1117; --idle: #484f58; --on-accent: #0d1117;
    }
  }
  * { box-sizing: border-box; }
  /* 留白用 clamp：600px 的窄 iframe 里收到 12px，独立页面上还是 32px。 */
  body { margin: 0; padding: clamp(14px, 3vw, 32px) clamp(12px, 3vw, 20px) 48px;
         background: var(--bg); color: var(--text);
         font: 15px/1.6 "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; }
  /* 内嵌形态：外框留白与最大宽度都交给宿主，页面自己不占地方。 */
  body[data-embed="1"] { padding: 0; background: transparent; }
  body[data-embed="1"] .wrap { max-width: none; }
  .wrap { max-width: 960px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; }

  /* ── tab 条 ─────────────────────────────────────────────────────────────
     原生 button + JS 切换，不引任何框架。窄容器里横向滚动，不换行不压扁。 */
  .tabs { display: flex; gap: 4px; overflow-x: auto; margin-bottom: 16px;
          border-bottom: 1px solid var(--line); }
  .tabs button.tab { flex: 0 0 auto; white-space: nowrap; cursor: pointer;
          padding: 8px 14px; font: inherit; color: var(--muted);
          background: transparent; border: 1px solid transparent;
          border-radius: 8px 8px 0 0; margin-bottom: -1px; }
  .tabs button.tab:hover { color: var(--text); }
  .tabs button.tab[aria-selected="true"] { color: var(--text); background: var(--card);
          border-color: var(--line); border-bottom-color: var(--card); font-weight: 600; }
  .panel { display: none; }
  .panel.active { display: block; }

  .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
          padding: clamp(14px, 2.5vw, 20px) clamp(14px, 2.5vw, 22px); margin-bottom: 16px; }
  .card h2 { font-size: 16px; margin: 0 0 14px; }
  .card h2 .hd { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .card h2 .hd .sp { margin-left: auto; }
  /* 长表单内部再分组：一组一个小标题，视觉上不连成一片。 */
  .group { border-top: 1px dashed var(--line); margin-top: 16px; padding-top: 12px; }
  .group > .gt { font-size: 13px; font-weight: 600; margin-bottom: 10px; }
  /* minmax 给 200px：600px 宽的 iframe 里还能排两列，400px 以下自动落成一列。 */
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  .item { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
          min-width: 0; }
  .item .k { color: var(--muted); font-size: 12px; }
  .item .v { font-size: 14px; margin-top: 4px; word-break: break-all; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
         margin-right: 7px; vertical-align: 1px; background: var(--idle); }
  .dot.ok { background: var(--ok); } .dot.bad { background: var(--bad); }
  .dot.warn { background: var(--warn); }
  label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
  input[type=password], input[type=text], select {
    width: 100%; padding: 9px 11px; border: 1px solid var(--line); border-radius: 7px;
    font: inherit; background: var(--field); color: var(--text); }
  input:disabled { background: var(--field-off); color: var(--field-off-text); }
  .row { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
  /* flex-basis 240 而不是 320：窄容器里两个输入框不会各占一行还留半截空白。
     min-width:0 是关键——不加的话长 URL 会把 flex 项撑破容器，页面横向溢出。 */
  .row > div { flex: 1 1 240px; min-width: 0; }
  button { padding: 9px 18px; border-radius: 7px; border: 1px solid var(--accent);
           background: var(--accent); color: var(--on-accent); font: inherit;
           cursor: pointer; }
  button.ghost { background: var(--card); color: var(--accent); }
  button:disabled { opacity: .55; cursor: default; }
  .hint { font-size: 12px; color: var(--muted); margin-top: 10px; }
  .hint code { word-break: break-all; }
  .steps { margin-top: 16px; }
  .step { display: flex; gap: 10px; padding: 8px 0; border-top: 1px dashed var(--line); }
  .step .mark { font-weight: 700; width: 18px; flex: none; }
  .step.ok .mark { color: var(--ok); } .step.bad .mark { color: var(--bad); }
  .step .txt { min-width: 0; }
  .step .txt .n { font-size: 14px; }
  .step .txt .d { font-size: 12px; color: var(--muted); white-space: pre-wrap;
                  overflow-wrap: anywhere; }
  pre.log { background: var(--soft); border: 1px solid var(--line); border-radius: 7px;
            padding: 10px; font-size: 12px; overflow-x: auto; margin: 8px 0 0; }
  .reserved { opacity: .68; }
  .env { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
         margin-bottom: 10px; }
  .env .head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .env .nm { font-size: 15px; font-weight: 600; }
  .env .url { color: var(--muted); font-size: 13px; word-break: break-all; }
  .env .note { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .env .acts { margin-left: auto; display: flex; gap: 8px; }
  .chips { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
  .chip { font-size: 12px; border: 1px solid var(--line); border-radius: 6px;
          padding: 2px 8px; background: var(--soft); }
  .chip code { color: var(--muted); }
  button.mini { padding: 4px 12px; font-size: 13px; }
  button.danger { border-color: var(--bad); color: var(--bad); background: var(--card); }
  button.danger.solid { background: var(--bad); color: var(--on-accent); }
  /* 行内确认条：sandbox 没给 allow-modals，confirm() 会被静默阻止，
     二次确认只能长在页面里。 */
  .confirm { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
             margin-top: 8px; padding: 8px 10px; border-radius: 7px;
             border: 1px solid var(--bad); font-size: 13px; }
  .confirm .q { flex: 1 1 200px; min-width: 0; }
  /* 路径/链接一律"给文本 + 一键复制"：sandbox 没给 allow-downloads。 */
  .copyrow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .copytext { flex: 1 1 220px; min-width: 0; background: var(--soft);
              border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px;
              font-size: 12px; word-break: break-all; }
  /* 变量行在窄容器里换行成两行，而不是把值输入框压成几十像素。 */
  .varrow { display: flex; gap: 8px; margin-bottom: 8px; align-items: center;
            flex-wrap: wrap; }
  .varrow input.k { flex: 1 1 140px; min-width: 0; }
  .varrow input.v { flex: 2 1 200px; min-width: 0; }
  /* 保留变量名的行内说明：占满一行长在变量行下面，不挤输入框。 */
  .varrow .why { flex: 1 1 100%; font-size: 12px; color: var(--muted);
                 margin: -2px 0 2px 2px; }
  .varrow .why.on { color: var(--text); }
  .empty { color: var(--muted); font-size: 13px; padding: 6px 0 10px; }
  .tag { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px;
         border: 1px solid var(--line); color: var(--muted); margin-left: 6px; }

  /* ── 工作台 ─────────────────────────────────────────────────────────────
     表格在 400px 的窄面板里横向滚动，不做卡片式降级：用例表是拿来"扫一眼比对"
     的，列对齐比不换行更值钱。滚动条只长在表格自己身上（.tablewrap），
     body 永远不横向滚。 */
  .tablewrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
  table.tbl { border-collapse: collapse; width: 100%; min-width: 540px;
              font-size: 13px; }
  table.tbl th, table.tbl td { padding: 7px 9px; text-align: left; vertical-align: top;
              border-bottom: 1px solid var(--line); }
  table.tbl th { color: var(--muted); font-weight: 600; white-space: nowrap;
              background: var(--soft); position: sticky; top: 0; }
  table.tbl tr:last-child td { border-bottom: none; }
  table.tbl td.nowrap { white-space: nowrap; }
  table.tbl tr.clickable { cursor: pointer; }
  table.tbl tr.clickable:hover td { background: var(--soft); }
  table.tbl td.detail { background: var(--soft); font-size: 12px; }
  table.tbl input[type=checkbox] { cursor: pointer; }
  .kv { margin: 2px 0; word-break: break-all; }
  .kv b { font-weight: 600; color: var(--muted); }
  .picks { display: flex; gap: 6px; flex-wrap: wrap; align-items: center;
           margin-bottom: 10px; }
  .picks select { width: auto; flex: 0 1 160px; padding: 4px 8px; font-size: 13px; }
  .badge { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px;
           border: 1px solid var(--line); white-space: nowrap; }
  .badge.ok { color: var(--ok); border-color: var(--ok); }
  .badge.bad { color: var(--bad); border-color: var(--bad); }
  .badge.skip { color: var(--warn); border-color: var(--warn); }
  .bar { height: 6px; border-radius: 3px; background: var(--field-off);
         overflow: hidden; margin: 8px 0; }
  .bar > i { display: block; height: 100%; background: var(--accent); width: 0; }
  .batch { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
           margin-bottom: 10px; cursor: pointer; }
  .batch:hover { background: var(--soft); }
  .batch .head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .batch .nm { font-size: 15px; font-weight: 600; min-width: 0; word-break: break-all; }
  .batch .meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
</style>
</head>
<body data-embed="__EMBED__">
<div class="wrap">
<!--CHROME-->
  <header class="page-head">
    <h1>测试伙伴 · 能力网关</h1>
    <div class="sub">本页只在你这台机器上可访问（绑定 127.0.0.1）。凭据写在本机配置文件里，
      不进聊天、不进容器、不进版本库。</div>
  </header>
<!--/CHROME-->

  <nav class="tabs" id="tabs" role="tablist" aria-label="配置分类">
    <button type="button" class="tab" role="tab" id="tabbtn-overview"
            data-tab="overview" aria-controls="tab-overview">概览</button>
    <button type="button" class="tab" role="tab" id="tabbtn-credentials"
            data-tab="credentials" aria-controls="tab-credentials">连接与凭据</button>
    <button type="button" class="tab" role="tab" id="tabbtn-environments"
            data-tab="environments" aria-controls="tab-environments">测试环境</button>
    <button type="button" class="tab" role="tab" id="tabbtn-partner"
            data-tab="partner" aria-controls="tab-partner">测试伙伴</button>
    <button type="button" class="tab" role="tab" id="tabbtn-workbench"
            data-tab="workbench" aria-controls="tab-workbench">工作台</button>
  </nav>

  <!-- ── 概览 ─────────────────────────────────────────────────────────── -->
  <section class="panel" id="tab-overview" role="tabpanel"
           aria-labelledby="tabbtn-overview">
    <div class="card">
      <h2><span class="hd">运行状态
        <button class="ghost mini sp" id="btn-refresh">刷新</button></span></h2>
      <div class="grid" id="status-grid"></div>

      <div class="group">
        <div class="gt">入口与路径</div>
        <div class="copyrow">
          <code class="copytext" id="entry-md"></code>
          <button class="ghost mini" type="button" data-copy="entry-md"
                  id="btn-copy-entry">复制链接</button>
        </div>
        <div class="hint" style="margin-top:6px">把这行 markdown 贴进聊天或伙伴的
          SOUL 提示词里，点它就能在右侧面板打开本页。面板里下载与弹窗都不可用，
          所以本页给产物一律是<b>绝对路径 + 复制按钮</b>，不做下载。</div>
        <div class="copyrow" style="margin-top:10px">
          <code class="copytext" id="config-dir"></code>
          <button class="ghost mini" type="button" data-copy="config-dir"
                  id="btn-copy-dir">复制路径</button>
        </div>
        <div class="hint" id="paths"></div>
      </div>
    </div>

    <div class="card">
      <h2>维护动作</h2>
      <div class="row" style="align-items:center">
        <div style="flex:0 0 auto">
          <button class="ghost" id="btn-restart">重启 TAPD 服务</button></div>
        <div style="flex:0 0 auto">
          <button class="ghost" id="btn-refresh-session">刷新聊天会话</button></div>
        <div style="flex:1 1 100%" class="hint">
          「刷新聊天会话」= 把 DeepTutor 里 test-partner 条目的开关拨一次（关→开）。
          网关重启后聊天侧的旧会话不会自动跟进，调用会报 Session terminated——
          这个按钮就是过去手工去 MCP 服务页拨开关那一下。
        </div>
      </div>
      <div class="steps" id="action-steps"></div>
    </div>
  </section>

  <!-- ── 连接与凭据 ───────────────────────────────────────────────────── -->
  <section class="panel" id="tab-credentials" role="tabpanel"
           aria-labelledby="tabbtn-credentials">
    <div class="card">
      <h2>TAPD 访问令牌</h2>
      <div id="token-state" class="hint" style="margin-top:0;margin-bottom:12px"></div>
      <div class="row">
        <div>
          <label for="token">个人访问令牌（TAPD 右上角头像 → 个人设置 → 个人访问令牌，
            只勾选你自己需要的项目）</label>
          <input type="password" id="token" autocomplete="off" spellcheck="false"
                 placeholder="粘贴令牌，输入内容不会显示">
        </div>
        <div style="flex:0 0 auto">
          <button id="btn-save">保存并生效</button>
        </div>
      </div>
      <div class="hint">保存会做四件事：写入本机配置 → 重启 TAPD 子服务 → 注册进 DeepTutor
        → 回读连接状态。只放行两个只读工具（get_stories_or_tasks / get_stories_fields_info）。
        令牌不会回显到本页面，也不会写进任何日志。</div>
      <div class="steps" id="token-steps"></div>
    </div>

    <div class="card reserved">
      <h2>其他连接 <span class="tag">预留</span></h2>
      <div class="row">
        <div>
          <label>数据库连接串（用于后续的数据校验能力）</label>
          <input type="text" disabled placeholder="预留：能力上线后在此填写，保存方式与 TAPD 令牌一致">
        </div>
      </div>
      <div class="row" style="margin-top:12px">
        <div>
          <label>Git 凭据（用于后续的代码取证能力）</label>
          <input type="text" disabled placeholder="预留：能力上线后在此填写">
        </div>
      </div>
      <div class="hint">新增能力的做法固定：网关加子服务 + 本页加一个分区，不再新增任何脚本。</div>
    </div>
  </section>

  <!-- ── 测试环境 ─────────────────────────────────────────────────────── -->
  <section class="panel" id="tab-environments" role="tabpanel"
           aria-labelledby="tabbtn-environments">
    <div class="card">
      <h2><span class="hd">测试环境
        <button class="ghost mini sp" id="btn-env-new">新增环境</button></span></h2>
      <div class="hint" style="margin-top:0;margin-bottom:12px">
        执行用例要的环境地址与 <code>{{token}}</code> 这类变量在这里配一次。值存在本机
        <code>config/environments.json</code>，聊天里只说环境名——
        <b>凭据不进聊天、不进模型上下文、不出这台机器</b>。
      </div>
      <div id="env-list"></div>

      <div id="env-form" style="display:none">
        <div class="group">
          <div class="gt">环境基本信息</div>
          <div class="row">
            <div>
              <label for="env-name">环境名（执行时就说这个名字，如「测试环境」）</label>
              <input type="text" id="env-name" autocomplete="off" spellcheck="false"
                     placeholder="测试环境">
            </div>
            <div style="flex:2 1 240px">
              <label for="env-base">环境地址 base_url</label>
              <input type="text" id="env-base" autocomplete="off" spellcheck="false"
                     placeholder="https://api-test.example.com">
            </div>
          </div>
          <div class="row" style="margin-top:10px">
            <div>
              <label for="env-note">备注（可选，只给自己看）</label>
              <input type="text" id="env-note" autocomplete="off"
                     placeholder="例：日常测试环境，数据可随便造">
            </div>
          </div>
        </div>
        <div class="group">
          <div class="gt">变量金库</div>
          <label>用例里写 <code>{{变量名}}</code> 引用；值是密码型输入，保存后不再回显</label>
          <div id="env-vars"></div>
          <button class="ghost mini" id="btn-var-add" type="button">加一行变量</button>
          <div class="hint">
            <b>三个保留变量名是「登录换新」用的</b>（HAR 里录的 token 会过期，
            配了账密之后每轮执行前自动重登录换新，不用再手工贴 token）：
            <code>login_username</code> 登录账号 ·
            <code>login_password</code> 登录口令 ·
            <code>auth_token_path</code> 可选，登录响应里 token 的取值路径
            （如 <code>$.data.token</code>），只在体检推断错了的时候才需要填。
            其余变量名随便起，对应用例里的 <code>{{变量名}}</code>。
          </div>
        </div>
        <div class="row" style="margin-top:14px">
          <div style="flex:0 0 auto"><button id="btn-env-save">保存环境</button></div>
          <div style="flex:0 0 auto">
            <button class="ghost" id="btn-env-cancel" type="button">取消</button></div>
        </div>
        <div class="hint" id="env-edit-hint"></div>
      </div>
      <div class="steps" id="env-steps"></div>
    </div>
  </section>

  <!-- ── 测试伙伴 ─────────────────────────────────────────────────────── -->
  <section class="panel" id="tab-partner" role="tabpanel"
           aria-labelledby="tabbtn-partner">
    <div class="card">
      <h2>测试伙伴</h2>
      <div id="partner-state" class="hint" style="margin-top:0;margin-bottom:14px">读取中…</div>
      <div class="row" style="align-items:flex-end">
        <div>
          <label for="partner-target">目标伙伴</label>
          <select id="partner-target"></select>
        </div>
        <div id="partner-name-box">
          <label for="partner-name">新伙伴名字</label>
          <input type="text" id="partner-name" spellcheck="false">
        </div>
        <div style="flex:0 0 auto">
          <button id="btn-partner-ensure">创建/修复测试伙伴</button>
        </div>
      </div>
      <div class="hint">这一下做四件事：把仓库 <code>partner/SOUL.md</code> 分隔线以下的正文写成伙伴人格
        → 挂上 test-partner 的全部工具（tapd 注册了就再加两个只读工具）
        → 把 har-testing / req-testing 两份技能复制进伙伴工作区 → 回读核对。
        <b>不会删伙伴、不会改名</b>：选既有伙伴时只做增量补齐，你原先额外挂的工具会留着。</div>
      <div class="steps" id="partner-steps"></div>
    </div>
  </section>

  <!-- ── 工作台 ───────────────────────────────────────────────────────────
       两个视图（批次列表 / 批次详情）在同一个面板里切，不换 tab、不发新页面：
       "回到列表"必须是一下就回得去的动作。 -->
  <section class="panel" id="tab-workbench" role="tabpanel"
           aria-labelledby="tabbtn-workbench">

    <div class="card" id="wb-list-card">
      <h2><span class="hd">交付批次
        <button class="ghost mini sp" type="button" id="btn-wb-refresh">刷新</button></span></h2>
      <div class="hint" style="margin-top:0;margin-bottom:12px">
        测试伙伴每落一次盘就是这里的一个批次。点进去能看这批用例、勾选几条、
        选个环境当场执行——<b>不用回聊天窗口下指令</b>。
      </div>
      <div id="wb-list"><div class="empty">读取中…</div></div>
    </div>

    <div id="wb-detail" style="display:none">
      <div class="card">
        <h2><span class="hd">
          <button class="ghost mini" type="button" id="btn-wb-back">← 批次列表</button>
          <span id="wb-title"></span></span></h2>
        <div class="hint" id="wb-meta" style="margin-top:0"></div>
        <div id="wb-degraded"></div>

        <div id="wb-cases-box">
          <div class="group">
            <div class="gt" id="wb-count-line">用例</div>
            <div class="picks">
              <button class="ghost mini" type="button" data-pick="all">全选</button>
              <button class="ghost mini" type="button" data-pick="none">全不选</button>
              <button class="ghost mini" type="button" data-pick="invert">反选</button>
              <button class="ghost mini" type="button" data-pick="executable">只选可执行</button>
              <select id="wb-module-pick" aria-label="按模块选"></select>
            </div>
            <div class="tablewrap">
              <table class="tbl" id="wb-cases"><tbody></tbody></table>
            </div>
            <div class="hint">点一行展开看请求详情。凭据位置显示成
              <code>{{变量名}}</code> 原样——值在「测试环境」里，本页永远不解析它。</div>
          </div>
        </div>
      </div>

      <div class="card" id="wb-exec-card">
        <h2>执行</h2>
        <div class="row" style="align-items:flex-end">
          <div>
            <label for="wb-env">测试环境（在「测试环境」tab 里配，凭据不经聊天）</label>
            <select id="wb-env"></select>
          </div>
          <div>
            <label for="wb-auth">鉴权方式</label>
            <select id="wb-auth">
              <option value="env">跟随环境</option>
              <option value="login">登录换新</option>
            </select>
          </div>
          <div style="flex:0 0 auto">
            <button id="btn-wb-exec" type="button">执行选中用例</button>
          </div>
        </div>
        <div class="hint" id="wb-auth-why" style="margin-top:8px"></div>
        <div id="wb-confirm"></div>
        <div id="wb-progress"></div>
        <div id="wb-results"></div>
      </div>

      <div class="card">
        <h2>产物</h2>
        <div class="hint" style="margin-top:0;margin-bottom:10px">
          面板里没有下载能力（iframe 沙箱），所以给的是<b>绝对路径 + 复制</b>。
        </div>
        <div id="wb-files"></div>
      </div>
    </div>
  </section>
</div>

<script>
"use strict";
var CSRF = "__CSRF_TOKEN__";
var state = __INITIAL_STATE__;

function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function item(k, cls, v) {
  return '<div class="item"><div class="k">' + esc(k) + '</div><div class="v">' +
    (cls === null ? "" : '<span class="dot ' + cls + '"></span>') + esc(v) + "</div></div>";
}

function renderStatus() {
  var g = state.gateway || {}, t = state.tapd || {}, d = state.deeptutor;
  var html = "";
  html += item("网关版本", null, "v" + (g.version || "?"));
  html += item("MCP 面", "ok", (g.mcp_host || "") + ":" + (g.mcp_port || "") + "/mcp");

  if (!d) {
    html += item("DeepTutor 连接", "", "读取中…");
  } else if (!d.ok) {
    html += item("DeepTutor 连接", "bad", d.error || "连不上");
  } else {
    var self = (d.entries || {})[d.self_entry] || {};
    var tapd = (d.entries || {}).tapd || {};
    html += item("DeepTutor · " + (d.self_entry || "test-partner"),
      self.state === "connected" ? "ok" : "bad",
      (self.state || "缺失") + "（" + (self.tool_count || 0) + " 个工具）");
    html += item("DeepTutor · tapd",
      tapd.state === "connected" ? "ok" : (tapd.present ? "warn" : ""),
      (tapd.present ? tapd.state : "未注册") + "（" + (tapd.tool_count || 0) + " 个工具）");
  }

  var tapdCls = t.running ? "ok" : (t.token_configured ? "bad" : "");
  var tapdTxt = t.running
    ? ("运行中 · 端口 " + t.port + (t.managed ? "（网关代管）" : "（外部进程）"))
    : (t.token_configured ? "未运行" : "未配置令牌");
  html += item("TAPD 子服务", tapdCls, tapdTxt);
  el("status-grid").innerHTML = html;

  var sec = ((state.config || {}).secrets || {}).TAPD_ACCESS_TOKEN || {};
  el("token-state").innerHTML = sec.configured
    ? '<span class="dot ok"></span>已配置 <code>' + esc(sec.masked) +
      "</code>（长度 " + sec.length + "）。想换一个就直接粘贴新令牌再保存。"
    : '<span class="dot bad"></span>未配置。粘贴令牌并保存后，TAPD 能力立即可用。';

  var paths = (state.config || {}).paths || {};
  el("entry-md").textContent = g.entry_markdown || "";
  el("config-dir").textContent = paths.config_dir || "";
  el("paths").textContent = "容器侧挂载地址：" + (g.mcp_url_for_container || "");
}

// ── 复制（不是下载） ───────────────────────────────────────────────────────
// 内嵌的 iframe 没有 allow-downloads，所以文件一律给绝对路径 + 这个按钮。
// clipboard 在沙箱里也可能被拒，拒了就把文本选中，用户 Ctrl+C 一样能拿走。

function selectText(node) {
  try {
    var range = document.createRange();
    range.selectNodeContents(node);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    return true;
  } catch (e) { return false; }
}

function copyFrom(id, btn) {
  var node = el(id);
  if (!node) { return; }
  var text = node.textContent || "";
  var label = btn.getAttribute("data-label") || btn.textContent;
  btn.setAttribute("data-label", label);
  var flash = function (msg) {
    btn.textContent = msg;
    window.setTimeout(function () { btn.textContent = label; }, 1800);
  };
  var fallback = function () {
    flash(selectText(node) ? "已选中，按 Ctrl+C" : "复制失败，请手工选中");
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { flash("已复制"); }, fallback);
  } else {
    fallback();
  }
}

Array.prototype.forEach.call(document.querySelectorAll("[data-copy]"), function (b) {
  b.onclick = function () { copyFrom(b.getAttribute("data-copy"), b); };
});

// ── tab 切换 ───────────────────────────────────────────────────────────────
// 纯前端：不发请求、不重建 DOM，所以切走再切回来，填了一半的表单还在。
// 当前 tab 记进 URL hash（#environments），刷新与直达都落在同一个 tab 上。
// 只动 hash 不动 query，`?embed=1` 不会被切 tab 弄丢。

var TABS = ["overview", "credentials", "environments", "partner", "workbench"];

function showTab(name) {
  if (TABS.indexOf(name) < 0) { name = TABS[0]; }
  TABS.forEach(function (t) {
    var panel = el("tab-" + t), btn = el("tabbtn-" + t);
    if (panel) { panel.className = "panel" + (t === name ? " active" : ""); }
    if (btn) { btn.setAttribute("aria-selected", t === name ? "true" : "false"); }
  });
  if (window.location.hash.replace(/^#/, "") !== name) {
    window.location.hash = name;
  }
  // WB-001：工作台的环境下拉原先只认页面加载时那一份（或批次详情里带的那份），
  // 在「测试环境」tab 新配一个再切回来仍显示「还没有配置任何测试环境」。
  // 切进工作台就重取一次——环境是在另一个 tab 改的，进来时必须以服务端为准。
  if (name === "workbench") { wbRefreshEnvs(); }
}

function wbRefreshEnvs() {
  fetch("/api/environments").then(function (r) { return r.json(); })
    .then(function (res) {
      if (!res || !res.environments) { return; }
      if (state && state.config) { state.config.environments = res.environments; }
      if (wbCurrent) { wbCurrent.environments = res.environments; }
      if (el("wb-env")) { wbRenderEnvSelect(); }
    })
    .catch(function () { /* 取不到就沿用现有列表，不打断页面 */ });
}

TABS.forEach(function (t) {
  var btn = el("tabbtn-" + t);
  if (btn) { btn.onclick = function () { showTab(t); }; }
});
window.addEventListener("hashchange", function () {
  showTab(window.location.hash.replace(/^#/, ""));
});

// ── 测试环境 ───────────────────────────────────────────────────────────────
// 页面自始至终拿不到变量的值：列表只有键名与掩码，编辑时值输入框是空的
// （留空提交 = 服务端保留原值，合并在网关进程内做）。

var editingName = "";   // 非空表示正在编辑既有环境（按这个旧名定位）
var pendingDelete = ""; // 非空表示这个环境正等行内二次确认（见下方注释）

function envList() { return ((state.config || {}).environments) || []; }

function renderEnvironments() {
  var rows = envList();
  var box = el("env-list");
  if (!rows.length) {
    box.innerHTML = '<div class="empty">还没有配置任何测试环境。点右上角「新增环境」'
      + '配一个，执行用例时说环境名就行，不用在聊天里贴凭证。</div>';
    return;
  }
  box.innerHTML = rows.map(function (e) {
    var chips = (e.variables || []).map(function (v) {
      return '<span class="chip">' + esc(v.key) + ' <code>' +
        esc(v.masked || "(空)") + "</code></span>";
    }).join("");
    // 二次确认长在页面里，不用 window.confirm：内嵌的 iframe 没有 allow-modals，
    // confirm() 会被静默阻止（直接返回 false），删除按钮就永远点不动。
    var confirming = pendingDelete === e.name;
    var acts = confirming
      ? '<button class="ghost mini" data-delno="' + esc(e.name) + '">取消</button>'
      : '<button class="ghost mini" data-edit="' + esc(e.name) + '">编辑</button>' +
        '<button class="ghost mini danger" data-del="' + esc(e.name) + '">删除</button>';
    var bar = confirming
      ? '<div class="confirm"><span class="q">删除「' + esc(e.name) +
        '」？它的 ' + ((e.variables || []).length) +
        ' 个变量值会一并从本机删掉，删了没法找回。</span>' +
        '<button class="mini danger solid" data-delyes="' + esc(e.name) +
        '">确认删除</button></div>'
      : "";
    return '<div class="env"><div class="head"><span class="nm">' + esc(e.name) +
      '</span><span class="url">' + esc(e.base_url) + '</span><span class="acts">' +
      acts + "</span></div>" +
      (e.note ? '<div class="note">' + esc(e.note) + "</div>" : "") +
      '<div class="chips">' + (chips ||
        '<span class="chip">没有变量（只提供环境地址）</span>') + "</div>" +
      bar + "</div>";
  }).join("");
  Array.prototype.forEach.call(box.querySelectorAll("[data-edit]"), function (b) {
    b.onclick = function () { openEnvForm(b.getAttribute("data-edit")); };
  });
  Array.prototype.forEach.call(box.querySelectorAll("[data-del]"), function (b) {
    b.onclick = function () {
      pendingDelete = b.getAttribute("data-del");
      renderEnvironments();
    };
  });
  Array.prototype.forEach.call(box.querySelectorAll("[data-delno]"), function (b) {
    b.onclick = function () { pendingDelete = ""; renderEnvironments(); };
  });
  Array.prototype.forEach.call(box.querySelectorAll("[data-delyes]"), function (b) {
    b.onclick = function () { deleteEnv(b.getAttribute("data-delyes")); };
  });
}

// 登录换新的三个保留变量名。**四处同源**：本表、server/har_parse.py 的占位符、
// server/execute.py 的取值、上面「变量金库」那段静态说明——改名要一起改。
var RESERVED_VARS = {
  "login_username": "保留名：登录换新用的登录账号（选「登录换新」时必配）。",
  "login_password": "保留名：登录换新用的登录口令（选「登录换新」时必配）。",
  "auth_token_path": "保留名：登录响应里 token 的取值路径，如 $.data.token。"
    + "只在体检自动推断的位置不对时才需要填，会压过推断结果。"
};

function varRowHint(row) {
  var key = row.querySelector("input.k").value.trim().toLowerCase();
  var why = row.querySelector(".why");
  var text = RESERVED_VARS[key] || "";
  why.textContent = text;
  why.className = "why" + (text ? " on" : "");
}

function addVarRow(key, existing) {
  var row = document.createElement("div");
  row.className = "varrow";
  row.innerHTML = '<input type="text" class="k" autocomplete="off" spellcheck="false" '
    + 'placeholder="变量名，如 token"><input type="password" class="v" '
    + 'autocomplete="new-password" spellcheck="false" placeholder="变量值">'
    + '<button class="ghost mini danger" type="button">删除</button>'
    + '<div class="why"></div>';
  row.querySelector("input.k").value = key || "";
  if (existing) {
    row.querySelector("input.v").placeholder = "留空 = 保留原值";
  }
  row.querySelector("button").onclick = function () { row.remove(); };
  // 说明跟着键名走：敲到一半就认出来，不用等保存
  row.querySelector("input.k").oninput = function () { varRowHint(row); };
  el("env-vars").appendChild(row);
  varRowHint(row);
}

function openEnvForm(name) {
  if (pendingDelete) { pendingDelete = ""; renderEnvironments(); }
  var found = null, rows = envList(), i;
  for (i = 0; i < rows.length; i++) {
    if (rows[i].name === name) { found = rows[i]; break; }
  }
  editingName = found ? found.name : "";
  el("env-name").value = found ? found.name : "";
  el("env-base").value = found ? found.base_url : "";
  el("env-note").value = (found && found.note) || "";
  el("env-vars").innerHTML = "";
  if (found && (found.variables || []).length) {
    found.variables.forEach(function (v) { addVarRow(v.key, true); });
  } else {
    addVarRow("", false);
  }
  el("env-edit-hint").textContent = found
    ? "正在编辑「" + found.name + "」。变量值留空表示保留原值；要清掉某个变量就删掉那一行。"
    : "变量名对应用例里的 {{变量名}}。只配环境地址、不配变量也可以。";
  el("env-form").style.display = "";
  renderSteps("env-steps", []);
  el("env-name").focus();
}

function closeEnvForm() {
  editingName = "";
  el("env-form").style.display = "none";
  el("env-vars").innerHTML = "";
}

function collectVars() {
  var out = [];
  Array.prototype.forEach.call(el("env-vars").querySelectorAll(".varrow"),
    function (row) {
      var k = row.querySelector("input.k").value.trim();
      if (k) { out.push({ key: k, value: row.querySelector("input.v").value }); }
    });
  return out;
}

function saveEnv() {
  var body = {
    name: el("env-name").value,
    base_url: el("env-base").value,
    note: el("env-note").value,
    variables: collectVars(),
    original_name: editingName
  };
  busy(true);
  renderSteps("env-steps", [{ name: "正在保存环境…", ok: true, detail: "" }]);
  post("/api/environments/save", body).then(function (res) {
    renderSteps("env-steps", [{ name: res.ok ? "保存环境" : "保存失败",
      ok: !!res.ok, detail: res.message || "" }]);
    if (res.environments) {
      state.config = state.config || {};
      state.config.environments = res.environments;
      renderEnvironments();
    }
    if (res.ok) { closeEnvForm(); }
    return reload();
  }).catch(function (e) {
    renderSteps("env-steps", [{ name: "保存失败", ok: false, detail: String(e) }]);
  }).then(function () { busy(false); });
}

function deleteEnv(name) {
  pendingDelete = "";
  busy(true);
  post("/api/environments/delete", { name: name }).then(function (res) {
    renderSteps("env-steps", [{ name: res.ok ? "删除环境" : "删除失败",
      ok: !!res.ok, detail: res.message || "" }]);
    if (res.environments) {
      state.config = state.config || {};
      state.config.environments = res.environments;
      renderEnvironments();
    }
    if (res.ok && editingName === name) { closeEnvForm(); }
    return reload();
  }).then(function () { busy(false); });
}

function renderSteps(target, steps) {
  var box = el(target);
  if (!steps || !steps.length) { box.innerHTML = ""; return; }
  box.innerHTML = steps.map(function (s) {
    var tail = (s.log_tail && s.log_tail.length)
      ? '<pre class="log">' + esc(s.log_tail.join("\\n")) + "</pre>" : "";
    return '<div class="step ' + (s.ok ? "ok" : "bad") + '"><div class="mark">' +
      (s.ok ? "\\u221a" : "\\u00d7") + '</div><div class="txt"><div class="n">' +
      esc(s.name) + '</div><div class="d">' + esc(s.detail) + "</div>" + tail +
      "</div></div>";
  }).join("");
}

function post(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
    body: JSON.stringify(body || {})
  }).then(function (r) { return r.json(); });
}

function reload() {
  return fetch("/api/status").then(function (r) { return r.json(); })
    .then(function (s) { state = s; renderStatus(); renderEnvironments(); });
}

function busy(on) {
  ["btn-save", "btn-restart", "btn-refresh-session", "btn-refresh",
   "btn-env-new", "btn-env-save", "btn-env-cancel"].forEach(function (i) {
    el(i).disabled = on;
  });
}

el("btn-save").onclick = function () {
  var input = el("token");
  var value = input.value;
  if (!value.trim()) {
    renderSteps("token-steps", [{ name: "保存令牌", ok: false, detail: "先粘贴令牌再点保存。" }]);
    return;
  }
  busy(true);
  renderSteps("token-steps", [{ name: "正在保存并生效…", ok: true,
    detail: "重启子服务时会真去 TAPD 校验一次令牌，请稍候。" }]);
  post("/api/tapd/token", { token: value }).then(function (res) {
    input.value = "";            // 令牌不留在 DOM 里
    renderSteps("token-steps", res.steps);
    if (res.state) { state = res.state; renderStatus(); }
    return reload();
  }).catch(function (e) {
    renderSteps("token-steps", [{ name: "保存失败", ok: false, detail: String(e) }]);
  }).then(function () { busy(false); });
};

el("btn-restart").onclick = function () {
  busy(true);
  renderSteps("action-steps", [{ name: "正在重启 TAPD 服务…", ok: true, detail: "" }]);
  post("/api/tapd/restart", {}).then(function (res) {
    renderSteps("action-steps", [{ name: "重启 TAPD 服务", ok: res.ok,
      detail: res.message || "", log_tail: res.log_tail }]);
    return reload();
  }).then(function () { busy(false); });
};

el("btn-refresh-session").onclick = function () {
  busy(true);
  renderSteps("action-steps", [{ name: "正在拨开关…", ok: true, detail: "" }]);
  post("/api/refresh-session", {}).then(function (res) {
    renderSteps("action-steps", [{ name: "刷新聊天会话", ok: res.ok,
      detail: res.message || "" }]);
    return reload();
  }).then(function () { busy(false); });
};

el("btn-refresh").onclick = function () { busy(true); reload().then(function () { busy(false); }); };

el("btn-env-new").onclick = function () { openEnvForm(null); };
el("btn-env-cancel").onclick = function () { closeEnvForm(); };
el("btn-var-add").onclick = function () { addVarRow("", false); };
el("btn-env-save").onclick = saveEnv;

/* ── 测试伙伴分区 ─────────────────────────────────────────────────────── */

var partnerState = null;

function partnerHealthLine(c) {
  if (c.error) { return '<span class="dot bad"></span>' + esc(c.name) + "：读不到体检信息（" + esc(c.error) + "）"; }
  var bad = [];
  if (!c.soul_ok) { bad.push("人格不是仓库版本"); }
  if (c.mcp_unrestricted) { /* 不限制也算通过 */ }
  else if (!c.tool_count) { bad.push("没挂任何 MCP 工具"); }
  if (c.missing_skills && c.missing_skills.length) { bad.push("技能缺 " + c.missing_skills.join("、")); }
  var tools = c.mcp_unrestricted ? "不限制" : (c.tool_count || 0) + " 个";
  var line = esc(c.name) + "（" + esc(c.partner_id) + "）：人格" +
    (c.soul_ok ? "√" : "×") + "　工具 " + tools + "　技能 " +
    esc((c.skills || []).join("、") || "无");
  return '<span class="dot ' + (bad.length ? "bad" : "ok") + '"></span>' + line +
    (bad.length ? "　—— " + esc(bad.join("；")) : "　—— 可以干活");
}

function renderPartner() {
  var s = partnerState, box = el("partner-state"), sel = el("partner-target");
  if (!s) { box.innerHTML = "读取中…"; return; }
  if (!s.ok) {
    box.innerHTML = '<span class="dot bad"></span>读不到伙伴列表：' + esc(s.error || "");
    sel.innerHTML = "";
    return;
  }
  var html = "";
  var mcp = s.mcp || {};
  if (!mcp.primary_present) {
    html += '<div><span class="dot bad"></span>DeepTutor 里看不到 test-partner 的工具——' +
      "先让本页顶部的状态区显示 connected，再来点按钮，否则伙伴挂不上工具。</div>";
  } else {
    html += '<div><span class="dot ok"></span>可挂载：test-partner ' + mcp.primary_count +
      " 个工具" + (mcp.tapd_count ? "，tapd " + mcp.tapd_count + " 个只读工具" :
      "，tapd 未注册（跳过，HAR 链路不依赖）") + "</div>";
  }
  if (!s.candidates || !s.candidates.length) {
    html += '<div style="margin-top:6px"><span class="dot"></span>还没有测试伙伴。' +
      "下面填个名字点按钮，一键建一个。</div>";
  } else {
    html += s.candidates.map(function (c) {
      return '<div style="margin-top:6px">' + partnerHealthLine(c) + "</div>";
    }).join("");
  }
  box.innerHTML = html;

  var keep = sel.value;
  var opts = ['<option value="">新建「' + esc(s.default_name) + '」</option>'];
  (s.partners || []).forEach(function (p) {
    opts.push('<option value="' + esc(p.partner_id) + '">修复现有：' +
      esc(p.name) + "（" + esc(p.partner_id) + "）</option>");
  });
  sel.innerHTML = opts.join("");
  sel.value = keep || "";
  if (sel.value !== (keep || "")) { sel.value = ""; }
  if (!el("partner-name").value) { el("partner-name").value = s.default_name || ""; }
  syncPartnerForm();
}

function syncPartnerForm() {
  // 修复既有伙伴时名字框没有意义（我们不改名），直接藏掉比灰着更少误导。
  el("partner-name-box").style.display = el("partner-target").value ? "none" : "";
}

function reloadPartner() {
  // 带上当前选中的伙伴：它不一定符合"看起来像测试伙伴"的自动规则，
  // 但用户既然选了它，就得看得见它坏在哪。
  var pid = el("partner-target").value;
  var url = "/api/partner/status" + (pid ? "?partner_id=" + encodeURIComponent(pid) : "");
  return fetch(url).then(function (r) { return r.json(); })
    .then(function (s) { partnerState = s; renderPartner(); });
}

el("partner-target").onchange = function () { syncPartnerForm(); reloadPartner(); };

el("btn-partner-ensure").onclick = function () {
  var pid = el("partner-target").value;
  var name = el("partner-name").value.trim();
  if (!pid && !name) {
    renderSteps("partner-steps", [{ name: "创建测试伙伴", ok: false,
      detail: "给伙伴起个名字再点。" }]);
    return;
  }
  el("btn-partner-ensure").disabled = true;
  renderSteps("partner-steps", [{ name: pid ? "正在修复既有伙伴…" : "正在创建测试伙伴…",
    ok: true, detail: "写人格、挂工具、复制技能，几秒钟。" }]);
  post("/api/partner/ensure", { name: name, partner_id: pid }).then(function (res) {
    renderSteps("partner-steps", res.steps || [{ name: "创建/修复", ok: false,
      detail: res.error || res.code || "没有返回步骤。" }]);
    return reloadPartner();
  }).catch(function (e) {
    renderSteps("partner-steps", [{ name: "创建/修复失败", ok: false, detail: String(e) }]);
  }).then(function () { el("btn-partner-ensure").disabled = false; });
};

/* ── 工作台 ───────────────────────────────────────────────────────────────
   两个视图共用一个面板：批次列表 ⇄ 批次详情。所有状态都在这几个变量里，
   没有框架、没有虚拟 DOM——重画就是把 innerHTML 换掉再重新绑事件。
   用例表几十行的量级，这么做比自己发明一套 diff 更不容易错。 */

var wbList = null;      // GET /api/deliveries 的结果
var wbCurrent = null;   // GET /api/deliveries/{id} 的结果（含 cases）
var wbPicked = {};      // 勾选集合：case_id -> true
var wbOpen = {};        // 展开的用例详情行
var wbResOpen = {};     // 展开的结果详情行
var wbRun = null;       // 当前/最近一轮执行
var wbTimer = null;     // 轮询定时器
var wbConfirming = false;   // 执行前的行内确认条亮着没有
var wbNotice = "";      // 执行面上的一行提示（错误/校验不过）

function kv(k, v) {
  return '<div class="kv"><b>' + esc(k) + "</b>：" + esc(v) + "</div>";
}

function fmtBytes(n) {
  if (typeof n !== "number") { return ""; }
  if (n < 1024) { return n + " B"; }
  if (n < 1048576) { return (n / 1024).toFixed(1) + " KB"; }
  return (n / 1048576).toFixed(1) + " MB";
}

function copyRow(id, text) {
  return '<div class="copyrow" style="margin-bottom:6px"><code class="copytext" id="' +
    esc(id) + '">' + esc(text) + '</code><button class="ghost mini" type="button" ' +
    'data-copy="' + esc(id) + '">复制路径</button></div>';
}

// 动态渲染出来的复制按钮要重新绑一次：页面加载时那次绑定只覆盖首屏的静态节点。
function bindCopies(root) {
  Array.prototype.forEach.call(root.querySelectorAll("[data-copy]"), function (b) {
    b.onclick = function () { copyFrom(b.getAttribute("data-copy"), b); };
  });
}

function each(root, selector, fn) {
  Array.prototype.forEach.call(root.querySelectorAll(selector), fn);
}

// 可点的行不是 <button>（它们是 div / tr），所以键盘可达要自己补：
// role + tabindex 让它进 Tab 序列，Enter/空格再手工触发。少了这几行，
// 整个工作台就只有鼠标能用。
function activate(node, fn) {
  node.onclick = fn;
  node.onkeydown = function (ev) {
    if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
      ev.preventDefault();
      fn();
    }
  };
}

/* ── 批次列表 ─────────────────────────────────────────────────────────── */

function wbLoadList() {
  el("wb-list").innerHTML = '<div class="empty">读取中…</div>';
  return fetch("/api/deliveries").then(function (r) { return r.json(); })
    .then(function (res) { wbList = res; wbRenderList(); })
    .catch(function (e) {
      el("wb-list").innerHTML = '<div class="empty">读不到批次列表：' +
        esc(String(e)) + "</div>";
    });
}

function wbRenderList() {
  var box = el("wb-list");
  var rows = (wbList && wbList.deliveries) || [];
  if (!rows.length) {
    box.innerHTML = '<div class="empty">' +
      esc((wbList && wbList.empty_reason) || "还没有任何交付批次。") + "</div>";
    return;
  }
  box.innerHTML = rows.map(function (d) {
    var bits = [d.generated_at || "生成时间未知"];
    bits.push(typeof d.case_count === "number" ? d.case_count + " 条用例" : "用例数未知");
    bits.push((d.files || []).length + " 个产物文件");
    if (d.executed && d.last_execution) {
      bits.push("上次执行：" + (d.last_execution.verdict || "已执行"));
    } else {
      bits.push("未执行过");
    }
    var tags = "";
    if (!d.structured) { tags += '<span class="tag">仅可查看产物</span>'; }
    if (d.receipt_broken) { tags += '<span class="tag">收据读不出</span>'; }
    if (d.executed) { tags += '<span class="tag">已执行</span>'; }
    return '<div class="batch" role="button" tabindex="0" data-open="' +
      esc(d.id) + '"><div class="head">' +
      '<span class="nm">' + esc(d.title) + "</span>" + tags + "</div>" +
      '<div class="meta">' + esc(bits.join("　·　")) + "</div>" +
      '<div class="meta">' + esc(d.id) + "</div></div>";
  }).join("");
  each(box, "[data-open]", function (node) {
    activate(node, function () { wbOpenDelivery(node.getAttribute("data-open")); });
  });
}

/* ── 批次详情 ─────────────────────────────────────────────────────────── */

function wbBackToList() {
  wbStopPolling();
  el("wb-detail").style.display = "none";
  el("wb-list-card").style.display = "";
  wbLoadList();
}

function wbOpenDelivery(id) {
  wbStopPolling();
  wbCurrent = null; wbPicked = {}; wbOpen = {}; wbResOpen = {};
  wbRun = null; wbConfirming = false; wbNotice = "";
  el("wb-list-card").style.display = "none";
  el("wb-detail").style.display = "";
  el("wb-title").textContent = "读取中…";
  el("wb-meta").textContent = "";
  el("wb-degraded").innerHTML = "";
  el("wb-cases").innerHTML = "";
  el("wb-files").innerHTML = "";
  return fetch("/api/deliveries/" + encodeURIComponent(id))
    .then(function (r) { return r.json(); })
    .then(function (res) {
      if (!res.ok) {
        el("wb-title").textContent = "打不开这个批次";
        el("wb-degraded").innerHTML = '<div class="empty">' +
          esc(res.message || res.code || "") + "</div>";
        el("wb-cases-box").style.display = "none";
        el("wb-exec-card").style.display = "none";
        return;
      }
      wbCurrent = res;
      wbRenderDetail();
    });
}

function wbRenderDetail() {
  var d = wbCurrent.delivery;
  el("wb-title").textContent = d.title;
  var meta = [d.generated_at || "生成时间未知", d.format || "格式未知"];
  if (typeof d.case_count === "number") { meta.push(d.case_count + " 条用例"); }
  if (d.validation_ok === true) { meta.push("落盘校验通过"); }
  if (d.validation_ok === false) { meta.push("落盘校验有错"); }
  if (d.last_execution) {
    meta.push("上次执行 " + (d.last_execution.executed_at || "") + "：" +
      (d.last_execution.verdict || ""));
  }
  el("wb-meta").textContent = meta.join("　·　");

  if (wbIsDegraded()) {
    el("wb-degraded").innerHTML = '<div class="confirm" style="border-color:var(--warn)">' +
      '<span class="q">' + esc(wbCurrent.degraded.message) + "</span></div>";
    el("wb-cases-box").style.display = "none";
    el("wb-exec-card").style.display = "none";
  } else {
    el("wb-degraded").innerHTML = "";
    el("wb-cases-box").style.display = "";
    el("wb-exec-card").style.display = "";
    wbRenderCases();
    wbRenderEnvSelect();
    wbRenderExec();
  }
  wbRenderFiles();
}

function wbIsDegraded() {
  return !!(wbCurrent && wbCurrent.degraded);
}

/* ── 用例表 ───────────────────────────────────────────────────────────── */

function wbCases() { return (wbCurrent && wbCurrent.cases) || []; }

function wbPickedIds() {
  return wbCases().filter(function (c) { return wbPicked[c.case_id]; })
    .map(function (c) { return c.case_id; });
}

function wbCaseDetail(c) {
  var h = "";
  if (c.preconditions) { h += kv("前置条件", c.preconditions); }
  if (c.steps && c.steps.length) { h += kv("操作步骤", c.steps.join("　→　")); }
  if (c.expected) { h += kv("预期结果", c.expected); }
  if (c.test_data) { h += kv("测试数据", c.test_data); }
  if (c.endpoints && c.endpoints.length) { h += kv("关联端点", c.endpoints.join("；")); }
  var r = c.request;
  if (!r) {
    return h + '<div class="kv">没有 request 块——这是人执行的用例，' +
      "工作台不会替你执行它。</div>";
  }
  h += kv("请求", (r.method || "") + " " + (r.url || ""));
  var headers = r.headers;
  if (headers && headers.length) {
    h += '<div class="kv"><b>请求头</b></div>';
    h += headers.map(function (x) {
      if (!x) { return ""; }
      return '<div class="kv">· ' + esc(x.key || x.name || "") + "： " +
        esc(x.value === undefined ? "" : x.value) + "</div>";
    }).join("");
  }
  if (r.body && r.body.raw) { h += kv("请求体", r.body.raw); }
  var asserts = r.assertions || [];
  if (!asserts.length) {
    h += '<div class="kv">没有断言——跑完无从判定成败，执行时会被跳过。</div>';
  } else {
    h += '<div class="kv"><b>断言</b></div>';
    h += asserts.map(function (a) {
      if (!a) { return ""; }
      return '<div class="kv">· ' + esc(a.type || "") +
        (a.path ? " " + esc(a.path) : "") + " 期望 " +
        esc(JSON.stringify(a.expected)) + "</div>";
    }).join("");
  }
  return h;
}

function wbRenderCases() {
  var cases = wbCases();
  var picked = wbPickedIds().length;
  el("wb-count-line").textContent = "用例 " + cases.length + " 条（" +
    wbCurrent.executable_count + " 条可执行）　已勾选 " + picked + " 条";

  var head = '<thead><tr><th style="width:30px"></th><th>编号</th><th>标题</th>' +
    "<th>模块</th><th>优先级</th><th>请求块</th><th>断言</th></tr></thead>";
  var body = cases.map(function (c) {
    var row = '<tr class="clickable" tabindex="0" data-row="' + esc(c.case_id) + '">' +
      '<td><input type="checkbox" data-cid="' + esc(c.case_id) + '"' +
      (wbPicked[c.case_id] ? " checked" : "") + "></td>" +
      '<td class="nowrap">' + esc(c.case_id) + "</td>" +
      "<td>" + esc(c.title) + "</td>" +
      "<td>" + esc(c.module || "-") + "</td>" +
      '<td class="nowrap">' + esc(c.priority || "-") + "</td>" +
      '<td class="nowrap">' + (c.executable
        ? '<span class="badge ok">可执行</span>'
        : '<span class="badge skip">不执行</span>') + "</td>" +
      '<td class="nowrap">' + esc(c.assertion_count) + "</td></tr>";
    if (wbOpen[c.case_id]) {
      row += '<tr><td class="detail" colspan="7">' + wbCaseDetail(c) + "</td></tr>";
    }
    return row;
  }).join("");
  var table = el("wb-cases");
  table.innerHTML = head + "<tbody>" + body + "</tbody>";

  each(table, "input[data-cid]", function (box) {
    box.onclick = function (ev) {
      ev.stopPropagation();          // 勾选不该顺手把详情也展开
      var cid = box.getAttribute("data-cid");
      if (box.checked) { wbPicked[cid] = true; } else { delete wbPicked[cid]; }
      wbRenderCases();
      wbRenderExec();
    };
  });
  each(table, "tr[data-row]", function (tr) {
    activate(tr, function () {
      var cid = tr.getAttribute("data-row");
      if (wbOpen[cid]) { delete wbOpen[cid]; } else { wbOpen[cid] = true; }
      wbRenderCases();
    });
  });

  var sel = el("wb-module-pick");
  var opts = ['<option value="">按模块选…</option>'];
  (wbCurrent.modules || []).forEach(function (m) {
    opts.push('<option value="' + esc(m) + '">' + esc(m) + "</option>");
  });
  sel.innerHTML = opts.join("");
}

function wbApplyPick(mode) {
  var cases = wbCases();
  if (mode === "all") {
    cases.forEach(function (c) { wbPicked[c.case_id] = true; });
  } else if (mode === "none") {
    wbPicked = {};
  } else if (mode === "invert") {
    var next = {};
    cases.forEach(function (c) { if (!wbPicked[c.case_id]) { next[c.case_id] = true; } });
    wbPicked = next;
  } else if (mode === "executable") {
    wbPicked = {};
    cases.forEach(function (c) { if (c.executable) { wbPicked[c.case_id] = true; } });
  }
  wbConfirming = false;
  wbRenderCases();
  wbRenderExec();
}

/* ── 执行面 ───────────────────────────────────────────────────────────── */

function wbEnvironments() {
  return (wbCurrent && wbCurrent.environments) ||
    ((state.config || {}).environments) || [];
}

function wbRenderEnvSelect() {
  var sel = el("wb-env"), keep = sel.value, envs = wbEnvironments();
  if (!envs.length) {
    sel.innerHTML = '<option value="">（还没有配置任何测试环境）</option>';
    return;
  }
  sel.innerHTML = envs.map(function (e) {
    return '<option value="' + esc(e.name) + '">' + esc(e.name) + " · " +
      esc(e.base_url) + "</option>";
  }).join("");
  sel.value = keep || envs[0].name;
  if (!sel.value) { sel.value = envs[0].name; }
}

function wbCurrentEnv() {
  var name = el("wb-env").value;
  var found = null;
  wbEnvironments().forEach(function (e) { if (e.name === name) { found = e; } });
  return found;
}

/* 鉴权方式：与 execute_cases 的 auth 参数一一对应（env / login）。
   「登录换新」只有在这个批次自带登录请求描述时才给点——批次里没有那份描述，
   点了也只会整轮失败，不如一开始就说清楚缺什么。 */

function wbAuthOffer() {
  return (wbCurrent && wbCurrent.auth_refresh) || {available: false, reason: ""};
}

function wbRenderAuthSelect() {
  var sel = el("wb-auth"), offer = wbAuthOffer();
  var loginOpt = sel.options[1];
  loginOpt.disabled = !offer.available;
  loginOpt.textContent = offer.available ? "登录换新" : "登录换新（本批次不可用）";
  if (!offer.available && sel.value === "login") { sel.value = "env"; }

  var why = el("wb-auth-why");
  if (sel.value === "login") {
    why.innerHTML = "执行前先重放登录请求 <code>" + esc(offer.endpoint || "") +
      "</code> 换一个新 token 注入后续用例。" +
      "<b>需要所选环境里配好 <code>login_username</code> 与 " +
      "<code>login_password</code> 两个变量</b>（在「测试环境」tab 里配，" +
      "账密不进聊天）；没配会整轮不执行并说明缺什么。";
  } else if (offer.available) {
    why.innerHTML = "用环境变量表里配好的值（现状行为）。这个批次支持「登录换新」：" +
      "环境里配一次账号口令，token 每轮自动换新，不用再手工贴过期的 token。";
  } else {
    why.textContent = "用环境变量表里配好的值。" + (offer.reason || "");
  }
}

function wbRenderExec() {
  var picked = wbPickedIds().length;
  var envs = wbEnvironments();
  var running = !!(wbRun && wbRun.state === "running");
  wbRenderAuthSelect();
  el("btn-wb-exec").disabled = running || !picked || !envs.length;
  el("btn-wb-exec").textContent = running
    ? "执行中…" : "执行选中的 " + picked + " 条用例";

  var box = el("wb-confirm");
  var notice = wbNotice
    ? '<div class="hint" style="color:var(--bad)">' + esc(wbNotice) + "</div>" : "";
  if (!envs.length) {
    box.innerHTML = notice + '<div class="hint">还没有配置测试环境——去「测试环境」tab ' +
      "配一个（环境名 + base_url + 变量值），凭据存在本机，不经聊天。</div>";
    return;
  }
  if (!wbConfirming) { box.innerHTML = notice; return; }

  var env = wbCurrentEnv() || {name: "", base_url: ""};
  box.innerHTML = notice +
    '<div class="confirm"><span class="q">' +
    "将用环境「" + esc(env.name) + "」向 <b>" + esc(env.base_url) +
    "</b> 发出 " + picked + " 条<b>真实请求</b>" +
    (el("wb-auth").value === "login"
      ? "（外加一次登录请求换新 token）" : "") + "。" +
    "写操作会真改那套环境里的数据，删除、下单这类用例请先确认这是测试环境。" +
    "</span>" +
    '<button class="mini danger solid" type="button" id="btn-wb-go">确认执行</button>' +
    '<button class="ghost mini" type="button" id="btn-wb-cancel">取消</button></div>';
  el("btn-wb-go").onclick = wbStartRun;
  el("btn-wb-cancel").onclick = function () {
    wbConfirming = false; wbRenderExec();
  };
}

function wbStartRun() {
  var ids = wbPickedIds();
  var env = el("wb-env").value;
  var auth = el("wb-auth").value;
  wbConfirming = false;
  wbNotice = "";
  wbRun = {state: "running", done: 0, total: ids.length,
           current_case_id: "", current_title: "", environment: env, auth: auth};
  wbResOpen = {};
  wbRenderExec();
  wbRenderRun();
  post("/api/deliveries/" + encodeURIComponent(wbCurrent.delivery.id) + "/execute",
       {env: env, case_ids: ids, auth: auth}).then(function (res) {
    if (!res.ok) {
      wbRun = null;
      wbNotice = res.message || res.code || "执行没起来。";
      wbRenderExec();
      wbRenderRun();
      return;
    }
    wbRun = res.run;
    wbRenderRun();
    wbPoll();
  }).catch(function (e) {
    wbRun = null;
    wbNotice = "执行请求发不出去：" + String(e);
    wbRenderExec();
    wbRenderRun();
  });
}

function wbStopPolling() {
  if (wbTimer) { window.clearTimeout(wbTimer); wbTimer = null; }
}

function wbPoll() {
  wbStopPolling();
  if (!wbRun || wbRun.state !== "running") { return; }
  wbTimer = window.setTimeout(function () {
    fetch("/api/runs/" + encodeURIComponent(wbRun.run_id))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.ok) {
          wbRun = null;
          wbNotice = res.message || "这一轮执行的记录不见了。";
          wbRenderExec();
          wbRenderRun();
          return;
        }
        wbRun = res.run;
        wbRenderRun();
        if (wbRun.state === "running") {
          wbPoll();
        } else {
          wbRenderExec();
          wbRefreshFiles();     // 执行报告是新落的文件，产物区要跟上
        }
      }).catch(function () { wbPoll(); });
  }, 800);
}

function wbRenderRun() {
  var box = el("wb-progress"), out = el("wb-results");
  if (!wbRun) { box.innerHTML = ""; out.innerHTML = ""; return; }
  var total = wbRun.total || 0, done = wbRun.done || 0;
  var pct = total ? Math.round(done * 100 / total) : 0;
  if (wbRun.state === "running") {
    var at = Math.min(done + 1, total);
    box.innerHTML = '<div class="hint" style="margin-top:14px">正在执行第 ' + at +
      "/" + total + " 条" +
      (wbRun.current_case_id ? "　·　" + esc(wbRun.current_case_id) + " " +
        esc(wbRun.current_title) : "") +
      '</div><div class="bar"><i style="width:' + pct + '%"></i></div>';
  } else if (wbRun.state === "error") {
    box.innerHTML = '<div class="step bad" style="margin-top:14px">' +
      '<div class="mark">\\u00d7</div><div class="txt"><div class="n">这一轮没跑起来</div>' +
      '<div class="d">' + esc(wbRun.error || "") + "</div></div></div>";
  } else {
    box.innerHTML = '<div class="hint" style="margin-top:14px">执行完成（' +
      esc(wbRun.started_at) + " → " + esc(wbRun.finished_at) +
      '）</div><div class="bar"><i style="width:100%"></i></div>';
  }
  out.innerHTML = wbRun.result ? wbResultsHtml(wbRun.result) : "";
  wbBindResults(out);
}

function wbVerdict(r) {
  if (r.skipped) { return '<span class="badge skip">跳过</span>'; }
  return r.passed ? '<span class="badge ok">通过</span>'
                  : '<span class="badge bad">失败</span>';
}

function wbResultDetail(r) {
  var h = "";
  if (r.method || r.url) { h += kv("请求", (r.method || "") + " " + (r.url || "")); }
  if (r.skipped) { return h + kv("跳过原因", r.reason || r.skip_code || ""); }
  if (r.error) { h += kv("错误", r.error); }
  (r.assertions || []).forEach(function (a) {
    h += '<div class="kv">' + (a.passed ? "√" : "×") + " " + esc(a.type || "") +
      (a.path ? " " + esc(a.path) : "") +
      "　期望 " + esc(JSON.stringify(a.expected)) +
      "　实际 " + esc(JSON.stringify(a.actual)) +
      (a.note ? "　（" + esc(a.note) + "）" : "") + "</div>";
  });
  return h || kv("详情", "没有更多信息");
}

function wbResultsHtml(result) {
  var s = result.summary || {};
  var rows = result.results || [];
  var head = '<div class="group"><div class="gt">结果　' +
    esc(s.verdict || "") + "</div>" +
    '<div class="grid" style="margin-bottom:12px">' +
    item("通过", "ok", s.passed || 0) +
    item("失败", (s.failed ? "bad" : ""), s.failed || 0) +
    item("跳过", (s.skipped ? "warn" : ""), s.skipped || 0) +
    item("环境", null, (result.environment || "-") + "　" +
      (result.base_url_host || "")) + "</div>";

  var table = '<div class="tablewrap"><table class="tbl"><thead><tr>' +
    "<th>编号</th><th>用例</th><th>结果</th><th>状态码</th><th>耗时ms</th>" +
    "</tr></thead><tbody>" +
    rows.map(function (r) {
      var open = wbResOpen[r.case_id];
      var line = '<tr class="clickable" tabindex="0" data-res="' +
        esc(r.case_id) + '">' +
        '<td class="nowrap">' + esc(r.case_id) + "</td>" +
        "<td>" + esc(r.name || "") + "</td>" +
        '<td class="nowrap">' + wbVerdict(r) + "</td>" +
        '<td class="nowrap">' + esc(r.status_code === null ||
          r.status_code === undefined ? "-" : r.status_code) + "</td>" +
        '<td class="nowrap">' + esc(r.elapsed_ms === null ||
          r.elapsed_ms === undefined ? "-" : r.elapsed_ms) + "</td></tr>";
      if (open) {
        line += '<tr><td class="detail" colspan="5">' + wbResultDetail(r) + "</td></tr>";
      }
      return line;
    }).join("") + "</tbody></table></div>";

  var tail = '<div class="hint">' + esc(result.report_hint || "") + "</div>";
  if (result.report_dir) {
    tail += copyRow("wb-report-dir", result.report_dir);
  }
  // 凭据来源收据：只有模式与登录端点，一个值都没有（execute_cases 的 auth 段）
  if (result.auth && result.auth.note) {
    tail += '<div class="hint">凭据来源：' + esc(result.auth.note) + "</div>";
  }
  tail += '<div class="hint">凭据变量的值不在结果里，也不在报告里：' +
    "报告只记环境名与 host。</div>";
  return head + table + tail + "</div>";
}

function wbBindResults(root) {
  each(root, "tr[data-res]", function (tr) {
    activate(tr, function () {
      var cid = tr.getAttribute("data-res");
      if (wbResOpen[cid]) { delete wbResOpen[cid]; } else { wbResOpen[cid] = true; }
      wbRenderRun();
    });
  });
  bindCopies(root);
}

/* ── 产物区 ───────────────────────────────────────────────────────────── */

function wbRenderFiles() {
  var box = el("wb-files");
  var files = (wbCurrent && wbCurrent.delivery && wbCurrent.delivery.files) || [];
  if (!files.length) {
    box.innerHTML = '<div class="empty">这个批次目录里没有文件。</div>';
    return;
  }
  box.innerHTML = files.map(function (f, i) {
    return '<div class="kv" style="margin-top:10px"><b>' + esc(f.name) + "</b>　" +
      esc(fmtBytes(f.bytes)) + "</div>" + copyRow("wb-file-" + i, f.path);
  }).join("");
  bindCopies(box);
}

function wbRefreshFiles() {
  if (!wbCurrent) { return; }
  var id = wbCurrent.delivery.id;
  fetch("/api/deliveries/" + encodeURIComponent(id))
    .then(function (r) { return r.json(); })
    .then(function (res) {
      // 只更新元信息与文件清单：勾选状态与展开状态是用户的操作，不能被刷没
      if (res.ok && wbCurrent && wbCurrent.delivery.id === id) {
        wbCurrent.delivery = res.delivery;
        wbRenderFiles();
      }
    }).catch(function () { /* 刷不到就算了，产物区不是关键路径 */ });
}

el("btn-wb-refresh").onclick = wbLoadList;
el("btn-wb-back").onclick = wbBackToList;
el("btn-wb-exec").onclick = function () {
  if (!wbPickedIds().length) {
    wbNotice = "一条用例都没勾。";
    wbRenderExec();
    return;
  }
  wbNotice = "";
  wbConfirming = true;      // 行内确认条，不是 window.confirm（沙箱里点不动）
  wbRenderExec();
};
el("wb-env").onchange = function () { if (wbConfirming) { wbRenderExec(); } };
// 换鉴权方式要重画整个执行面：说明文案跟着变，确认条上的措辞也跟着变
el("wb-auth").onchange = function () { wbRenderExec(); };
el("wb-module-pick").onchange = function () {
  var module = el("wb-module-pick").value;
  if (!module) { return; }
  wbCases().forEach(function (c) {
    if (c.module === module) { wbPicked[c.case_id] = true; }
  });
  el("wb-module-pick").value = "";
  wbConfirming = false;
  wbRenderCases();
  wbRenderExec();
};
each(document, "[data-pick]", function (b) {
  b.onclick = function () { wbApplyPick(b.getAttribute("data-pick")); };
});

showTab(window.location.hash.replace(/^#/, ""));
renderStatus();
renderEnvironments();
reload();
reloadPartner();
wbLoadList();
</script>
</body>
</html>
"""
