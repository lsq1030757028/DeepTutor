"""DeepTutor 部署注册表客户端：把子服务注册进去、读状态、拨开关刷会话。

`tapd-runtime/register_tapd.py` 的逻辑收编到这里。那个脚本继续保留（排障用、
可脱离本包单跑），但**权威实现是本文件**——配置页走的是这条路。

## 接口出处

DeepTutor v1.5.8 容器内 `deeptutor/api/routers/mcp_settings.py`：

- `GET    /api/v1/settings/mcp`               读 servers + 实时连接 status
- `PUT    /api/v1/settings/mcp/servers/<名>`  单条 upsert（同名覆盖，其余条目逐字节不动）
- `DELETE /api/v1/settings/mcp/servers/<名>`

用单条 upsert 而不是整表 PUT：整表 PUT 会让本模块对它不认识的字段负责——
读回来再写回去，任何没建模的字段都会被静默抹掉。

同版本容器内 `deeptutor/api/routers/partners.py`（2026-08-05 实测抄的字段形状）：

- `GET    /api/v1/partners`                   列全部伙伴
- `GET    /api/v1/partners/tool-options`      可挂的工具全集（含 `mcp_tools`，带 `server` 字段）
- `POST   /api/v1/partners`                   创建（`soul` / `assets` **只在创建时能传**）
- `GET    /api/v1/partners/<id>`              单个伙伴
- `PATCH  /api/v1/partners/<id>`              改配置（**不收 `soul`、不收 `assets`**）
- `GET|PUT /api/v1/partners/<id>/soul`        读写这个伙伴的 SOUL.md 正文
- `GET|POST /api/v1/partners/<id>/assets`     读/追加技能、知识库、笔记本

分成三条路是服务端的形状决定的，不是本模块想拆：PATCH 的请求模型里根本没有
`soul` 和 `assets` 两个字段，改人格只能走 `PUT /<id>/soul`，绑技能只能走
`POST /<id>/assets`。

## 为什么走 API 而不是让用户在页面上填

条目字段有 10 个（type/url/tool_timeout/enabled_tools/auth/catalog_entry/...），
页面上填错一个就是"连不上但不知道为什么"。字段形状是 2026-08-05 对着现网既有条目
实测抄下来的，不是凭记忆写的——`auth` 和 `catalog_entry` 两个就是实测才发现的。

## 鉴权

这组路由挂了 `require_admin`。本地部署 `AUTH_ENABLED=false` 时所有请求都算 admin，
不需要凭据；多用户部署下需要 admin 令牌，在配置页填 `DEEPTUTOR_TOKEN`，
以 `Authorization: Bearer` 发出，**只发给 api_base 指向的本机地址**。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from server.gateway.config import GatewayConfig, default_config

TIMEOUT = 20

#: 默认只放行两个只读工具。理由见 config.py 的 DEFAULT_SETTINGS 注释。
DEFAULT_ENABLED_TOOLS = ["get_stories_or_tasks", "get_stories_fields_info"]


class DeepTutorError(RuntimeError):
    """带错误码的可读失败。`code` 给页面做分支，`args[0]` 是给人看的中文。"""

    def __init__(self, message: str, code: str = "DEEPTUTOR_ERROR") -> None:
        super().__init__(message)
        self.code = code


class DeepTutorClient:
    """DeepTutor 本地 API 的薄客户端（只用标准库 urllib，不引 HTTP 依赖）。"""

    def __init__(self, config: GatewayConfig | None = None,
                 api_base: str | None = None) -> None:
        self.config = config or default_config()
        self._api_base_override = api_base

    @property
    def api_base(self) -> str:
        if self._api_base_override:
            return self._api_base_override.rstrip("/")
        deeptutor = self.config.load_settings().get("deeptutor") or {}
        return str(deeptutor.get("api_base") or "http://127.0.0.1:3782").rstrip("/")

    # ── 传输 ────────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        """发一次请求并解析 JSON。返回值可能是 dict 也可能是 list——
        `/api/v1/partners` 就直接回一个数组，所以这里不把类型收窄成 dict。"""
        url = f"{self.api_base}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        token = (self.config.get_secret("DEEPTUTOR_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in (401, 403):
                raise DeepTutorError(
                    f"DeepTutor 拒绝了这次调用（HTTP {exc.code}）。这组配置接口只允许 "
                    "admin 调用；你的 DeepTutor 如果开了登录（AUTH_ENABLED=true），"
                    "需要先拿到 admin 令牌填进本页面的 DEEPTUTOR_TOKEN。"
                    f"服务端原话：{detail}",
                    code="DEEPTUTOR_FORBIDDEN") from exc
            raise DeepTutorError(
                f"DeepTutor 返回 HTTP {exc.code}（{method} {path}）。服务端原话：{detail}",
                code="DEEPTUTOR_HTTP_ERROR") from exc
        except urllib.error.URLError as exc:
            raise DeepTutorError(
                f"连不上 DeepTutor（{self.api_base}）：{exc.reason}。"
                "DeepTutor 没在跑，或者端口不是 3782。先确认容器活着："
                "docker ps --filter name=deeptutor",
                code="DEEPTUTOR_UNREACHABLE") from exc
        except OSError as exc:
            raise DeepTutorError(
                f"连 DeepTutor 时出错（{self.api_base}）：{exc}",
                code="DEEPTUTOR_UNREACHABLE") from exc

        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeepTutorError(
                f"DeepTutor 的响应不是 JSON（{method} {path}）：{raw[:200]}",
                code="DEEPTUTOR_BAD_RESPONSE") from exc

    # ── 读 ──────────────────────────────────────────────────────────────────

    def get_settings(self) -> dict:
        return self._request("GET", "/api/v1/settings/mcp")

    @staticmethod
    def find_status(settings: dict, name: str) -> dict | None:
        for row in settings.get("status") or []:
            if row.get("name") == name:
                return row
        return None

    def entry_status(self, name: str) -> dict[str, Any]:
        """单个条目的连接状态投影，页面直接拿去点灯。

        `state` 取值：`connected` / 其他（DeepTutor 报什么就是什么）/ `missing`（没这条）。
        """
        settings = self.get_settings()
        row = self.find_status(settings, name)
        if row is None:
            return {"name": name, "present": False, "state": "missing",
                    "tool_count": 0, "tools": [], "error": ""}
        tools = row.get("tools") or []
        return {
            "name": name,
            "present": True,
            "state": row.get("status") or "",
            "tool_count": len(tools),
            "tools": [t.get("name", "") for t in tools],
            "error": row.get("error") or "",
        }

    def overview(self, names: list[str]) -> dict[str, Any]:
        """一次 GET 读出多个条目的状态（页面状态区要的就是这个）。"""
        settings = self.get_settings()
        result: dict[str, Any] = {"api_base": self.api_base, "entries": {}}
        for name in names:
            row = self.find_status(settings, name)
            if row is None:
                result["entries"][name] = {"name": name, "present": False,
                                           "state": "missing", "tool_count": 0,
                                           "tools": [], "error": ""}
                continue
            tools = row.get("tools") or []
            result["entries"][name] = {
                "name": name, "present": True, "state": row.get("status") or "",
                "tool_count": len(tools),
                "tools": [t.get("name", "") for t in tools],
                "error": row.get("error") or "",
            }
        return result

    # ── 写 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def build_tapd_entry(existing: dict | None, mcp_url: str,
                         enabled_tools: list[str] | None = None,
                         tool_timeout: int = 30) -> dict:
        """按现网既有条目的字段形状造 tapd 条目。

        存在同名条目时以它为底再覆盖我们管的那几个键——用户手工加过的字段
        （自己收窄过的 enabled_tools、或这个 DeepTutor 版本有而本模块不认识的新字段）
        不会因为一次保存就被抹掉。
        """
        entry = dict(existing) if existing else {}
        entry.update({
            "type": "streamableHttp",
            "command": "",
            "args": [],
            "env": {},
            "cwd": "",
            "url": mcp_url,
            "headers": {},
            "tool_timeout": tool_timeout,
            "disabled_tools": entry.get("disabled_tools") or [],
            "enabled": True,
            "auth": entry.get("auth", ""),
            "catalog_entry": entry.get("catalog_entry", ""),
        })
        # enabled_tools：用户自己改过就尊重他的，只在缺失/为空/`["*"]` 时才灌默认值
        # （`["*"]` 视为没收窄过——43 个工具里 17 个是写操作）。
        current = existing.get("enabled_tools") if existing else None
        if not current or current == ["*"]:
            entry["enabled_tools"] = list(enabled_tools or DEFAULT_ENABLED_TOOLS)
        else:
            entry["enabled_tools"] = list(current)
        return entry

    def register_tapd(self, mcp_url: str, enabled_tools: list[str] | None = None,
                      tool_timeout: int = 30, name: str = "tapd") -> dict[str, Any]:
        """把 tapd 条目 upsert 进注册表并回读状态。返回 `entry_status()` 的形状。

        回读是必要的：连接是异步建的，PUT 的响应里那个 status 未必是稳定态。
        """
        settings = self.get_settings()
        servers = settings.get("servers") or {}
        entry = self.build_tapd_entry(servers.get(name), mcp_url,
                                      enabled_tools, tool_timeout)
        result = self._request("PUT", f"/api/v1/settings/mcp/servers/{name}", entry)
        if not result:
            raise DeepTutorError("PUT 返回了空响应，无法确认是否写入成功。",
                                 code="DEEPTUTOR_EMPTY_PUT")
        status = self.entry_status(name)
        status["created"] = name not in servers
        status["enabled_tools"] = entry["enabled_tools"]
        return status

    def remove_entry(self, name: str) -> None:
        self._request("DELETE", f"/api/v1/settings/mcp/servers/{name}")

    def toggle_entry(self, name: str) -> dict[str, Any]:
        """把条目的开关拨一次（关 → 开），用来刷新聊天侧的 MCP 会话。

        为什么需要这个：重启 MCP 服务器后，聊天侧的会话不会自动跟进，调用会报
        Session terminated——而设置页显示 Connected 并不代表聊天侧会话活着。
        一直以来的手工动作就是去 MCP 服务页把开关拨一下，这里把它做成按钮。

        只写这一条，其余字段从现网条目原样带回去。
        """
        settings = self.get_settings()
        servers = settings.get("servers") or {}
        existing = servers.get(name)
        if not existing:
            raise DeepTutorError(
                f"DeepTutor 的注册表里没有名为 {name} 的条目，没有可拨的开关。",
                code="ENTRY_MISSING")
        off = dict(existing, enabled=False)
        self._request("PUT", f"/api/v1/settings/mcp/servers/{name}", off)
        on = dict(existing, enabled=True)
        self._request("PUT", f"/api/v1/settings/mcp/servers/{name}", on)
        return self.entry_status(name)

    # ── 伙伴（partners） ────────────────────────────────────────────────────
    #
    # 这一组只做传输，不做决策：该挂哪些工具、人格正文从哪来、要不要新建，
    # 全在 `partner_setup.py` 里。这样单测那边换掉本类就能不打网络。

    def list_partners(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/api/v1/partners")
        return list(result) if isinstance(result, list) else []

    def get_partner(self, partner_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/partners/{partner_id}")

    def partner_tool_options(self) -> dict[str, Any]:
        """伙伴能挂的工具全集。`mcp_tools` 每项形如
        `{"name": "mcp_test-partner_ping", "server": "test-partner", ...}`——
        `name` 就是往 `mcp_tools` 里填的那个字符串。"""
        return self._request("GET", "/api/v1/partners/tool-options")

    def create_partner(self, payload: dict[str, Any]) -> dict[str, Any]:
        """创建伙伴。`soul`、`assets` 两个字段只有这条路收，见模块 docstring。"""
        return self._request("POST", "/api/v1/partners", payload)

    def update_partner(self, partner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """改伙伴配置。**只发想改的键**：服务端按 `model_fields_set` 判断，
        没发的键原样保留，发了 `null` 的键是"显式设成 null"（含义完全不同）。"""
        return self._request("PATCH", f"/api/v1/partners/{partner_id}", payload)

    def get_partner_soul(self, partner_id: str) -> str:
        result = self._request("GET", f"/api/v1/partners/{partner_id}/soul")
        return str((result or {}).get("content") or "")

    def put_partner_soul(self, partner_id: str, content: str) -> dict[str, Any]:
        return self._request("PUT", f"/api/v1/partners/{partner_id}/soul",
                             {"content": content})

    def get_partner_assets(self, partner_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/partners/{partner_id}/assets")

    def add_partner_assets(self, partner_id: str,
                           skills: list[str] | None = None) -> dict[str, Any]:
        """追加技能到伙伴工作区（服务端是复制，已存在的跳过——天然幂等）。"""
        return self._request("POST", f"/api/v1/partners/{partner_id}/assets",
                             {"knowledge_bases": [], "skills": list(skills or []),
                              "notebooks": []})

    def delete_partner(self, partner_id: str) -> dict[str, Any]:
        """销毁伙伴。**配置页不接这个**——它只在实机验证脚本里用来收尾临时伙伴。"""
        return self._request("DELETE", f"/api/v1/partners/{partner_id}")
