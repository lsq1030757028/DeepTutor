# -*- coding: utf-8 -*-
"""oracle — TAPD 需求正文作 oracle：取数、快照冻结、漂移闸（ADR-M2-02）。

M1 的 oracle 是人贴的一段文字；M2 的 oracle 是**带出处与摘要的快照**。
三条硬纪律（ADR-M2-02 第 4/5/6 条）：

1. **单次取数、多次消费**：澄清/分析/用例只读快照，不回打 TAPD（可复算、可离线回放）。
2. **漂移闸在 adopt**：采纳时重取一次比对 `content_digest`，不一致即阻断，**不提供绕过开关**。
3. **零写操作**：只读白名单，提单等写动作不在 M2。

## 通道是可切换的（2026-08-11 manager 裁定，不是可选项）

TAPD MCP 的落地形态已经被现实推翻两次：0012 拍的是「进容器 + stdio」，
实际先是「宿主 HTTP + 服务端存令牌」，现在又变成「宿主 HTTP + 每请求带 Bearer、
服务端零存储」。**所以这里不写死任何一种**：端点、MCP 服务器条目名、传输方式、
鉴权形态全部从网关配置读（`services.tapd.oracle`），换通道只改配置不改代码。

两种传输：

- `deeptutor_plugins`（默认，也是当前唯一验通的一种）：经 DT 的
  `POST /api/v1/plugins/tools/mcp_<server>_<tool>/execute` 拿**服务器原文**
  （`manager.py:137-154`，不经 LLM）。**令牌不由我们持有**——容器侧 MCP 条目
  自己带（新通道是每请求 Bearer，用户在 DT 的 MCP Services 页面自填）。
- `direct_http`：网关直连 MCP 端点。仅在 DT 侧条目尚未配好时的排障路径；
  Bearer 从密钥库按键名取，**用完即弃、不落盘、不进日志**。

## 为什么 fields 白名单是硬要求（施工期实测，写进设计的东西）

`get_stories_or_tasks` **不传 `options.fields` 时返回 54 栏但 `description` 恒为空串**
（实测：默认调用 description_len=0；显式传 fields 后拿到 15535 字节正文）。
若照默认调用做快照，`content_digest` 会算在一份**只有标题的空壳**上，而且全链无人报警——
这是总则 7「错误当数据」的同族形态。故：

- 取数**必须**带 `fields` 白名单且含 `description`；
- 拿回来必须判两种截然不同的失败，**不许合并成一个码**（0021 红线六「成对错误态分开说」）：
  - `E_ORACLE_FIELD_MISSING`：返回行里**根本没有** description 这个键 → 是我们没要到字段/通道换了口径，
    **这是我们的 bug，不是需求的状态**；
  - `E_ORACLE_BODY_EMPTY`：键在、值为空 → 需求正文**确实**没写，该找需求方而不是查代码。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import Any

from server.journey import artifacts
from server.journey.digest import sha256_digest, text_digest
from server.journey.mcp_payload import McpPayloadError, parse_mcp_payload

E_ORACLE_FETCH_FAILED = "E_ORACLE_FETCH_FAILED"
E_ORACLE_NOT_FOUND = "E_ORACLE_NOT_FOUND"
E_ORACLE_FORBIDDEN = "E_ORACLE_FORBIDDEN"
E_ORACLE_FIELD_MISSING = "E_ORACLE_FIELD_MISSING"
E_ORACLE_BODY_EMPTY = "E_ORACLE_BODY_EMPTY"
E_ORACLE_DRIFT = "E_ORACLE_DRIFT"

#: 取数字段白名单。**description 必须在内**（见模块文档串的实测）。
#: 其余字段是给澄清/定档用的上下文，不是随手加的：
#: status/priority 影响定档，custom_field_10 是本工作区的测试人字段（config.yaml:158），
#: begin/due 用来判"这需求是不是还在窗口内"。
DEFAULT_FIELDS: tuple[str, ...] = (
    "id", "name", "description", "status", "priority", "workspace_id",
    "custom_field_10", "begin", "due", "modified", "creator",
)

#: 只读工具白名单。与 `gateway/config.py` 的 `enabled_tools` 同源语义——
#: 这里再列一遍是因为 oracle 侧要按名拼 DT 的工具名，两处不一致会 404 而不是越权。
READ_TOOLS = ("get_stories_or_tasks", "get_stories_fields_info")

#: 通道默认值。**任何一项都可被 `services.tapd.oracle` 覆盖。**
DEFAULT_CHANNEL: dict[str, Any] = {
    # deeptutor_plugins | direct_http
    "transport": "deeptutor_plugins",
    # 容器内 MCP 条目名。旧通道叫 tapd；新通道（每请求 Bearer 那条）由用户在
    # DT 的 MCP Services 页面自己命名，配置里改这一项即可切换。
    "server_name": "tapd",
    "story_tool": "get_stories_or_tasks",
    "fields_tool": "get_stories_fields_info",
    # direct_http 专用：端点与 Bearer 的**键名**（不是值）。
    "endpoint": "",
    "bearer_secret_key": "",
    "timeout_s": 60,
}


class OracleError(RuntimeError):
    def __init__(self, message: str, *, code: str, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail

    def as_payload(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": str(self),
                "detail": self.detail}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _scrub_secret(text: str, secret: str) -> str:
    return text.replace(secret, "<redacted>") if secret else text


# ── 通道配置 ────────────────────────────────────────────────────────────────


def channel_config(config: Any = None) -> dict[str, Any]:
    """读通道配置并按默认值补齐。配置缺字段不算坏（与 gateway config 同口径）。"""
    from server.gateway.config import default_config
    cfg = config or default_config()
    try:
        service = cfg.service_settings("tapd") or {}
    except Exception:  # noqa: BLE001 - 配置读不到就用默认，排障路径不该在这里炸
        service = {}
    merged = dict(DEFAULT_CHANNEL)
    override = service.get("oracle")
    if isinstance(override, dict):
        merged.update({k: v for k, v in override.items() if v not in (None, "")})
    # 端点缺省时从既有 mcp_url 推——旧配置里只有 mcp_url，别逼用户填两遍
    if not merged["endpoint"]:
        merged["endpoint"] = str(service.get("mcp_url") or "")
    return merged


def _dt_tool_name(channel: dict[str, Any], tool: str) -> str:
    if tool not in READ_TOOLS:
        raise OracleError(f"工具 {tool!r} 不在只读白名单 {READ_TOOLS}",
                          code=E_ORACLE_FORBIDDEN)
    return f"mcp_{channel['server_name']}_{tool}"


# ── 取数 ────────────────────────────────────────────────────────────────────


def _http_json(url: str, body: dict[str, Any], *, timeout_s: int,
               bearer: str = "") -> Any:
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout_s) as resp:  # noqa: S310
            raw = _scrub_secret(resp.read().decode("utf-8"), bearer)
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = _scrub_secret(
            exc.read().decode("utf-8", "replace")[:400], bearer)
        if exc.code in (401, 403):
            raise OracleError(
                f"TAPD 通道拒绝了这次调用（HTTP {exc.code}）。令牌无权或没配。",
                code=E_ORACLE_FORBIDDEN, detail=detail) from exc
        raise OracleError(f"TAPD 通道返回 HTTP {exc.code}：{detail}",
                          code=E_ORACLE_FETCH_FAILED, detail=detail) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OracleError(f"TAPD 通道不可达：{exc}",
                          code=E_ORACLE_FETCH_FAILED) from exc


def warm_up(api_base: str = "", *, timeout_s: int = 30,
            config: Any = None) -> bool:
    """预热 DT 的 MCP 连接（治 `ensure_started` 冷启动坑，ADR-M2-02 第 5 条末）。

    冷启动时首个调用可能拿到「未连接」字符串——那会被 `parse_mcp_payload` 判成
    `E_MCP_UNAVAILABLE` 而中止接入，用户看到的是一次莫名其妙的失败。先 GET 一次。
    """
    base = (api_base or _api_base(config)).rstrip("/")
    try:
        headers = {"Accept": "application/json"}
        bearer = _deeptutor_bearer(config)
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        req = urllib.request.Request(
            base + "/api/v1/settings/mcp", headers=headers, method="GET")
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(req, timeout=timeout_s) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _api_base(config: Any = None) -> str:
    from server.gateway.config import default_config
    cfg = config or default_config()
    settings = cfg.load_settings().get("deeptutor") or {}
    return str(settings.get("api_base") or "http://127.0.0.1:3782").rstrip("/")


def _deeptutor_bearer(config: Any = None) -> str:
    """Read the DeepTutor API token just-in-time; never persist or log it."""
    from server.gateway.config import default_config
    cfg = config or default_config()
    return str(cfg.get_secret("DEEPTUTOR_TOKEN") or "").strip()


def call_read_tool(tool: str, params: dict[str, Any], *,
                   channel: dict[str, Any] | None = None,
                   config: Any = None) -> Any:
    """调一次 TAPD 只读工具，返回**已判别过的**业务对象。

    返回前必过 `parse_mcp_payload`——MCP 适配器的错误串长得像成功
    （`success=true` + 普通字符串），不判就会被当需求正文快照下来。
    """
    ch = channel or channel_config(config)
    transport = ch.get("transport") or "deeptutor_plugins"
    if transport == "deeptutor_plugins":
        url = f"{_api_base(config)}/api/v1/plugins/tools/{_dt_tool_name(ch, tool)}/execute"
        envelope = _http_json(url, {"params": params},
                              timeout_s=int(ch.get("timeout_s") or 60),
                              bearer=_deeptutor_bearer(config))
        if not isinstance(envelope, dict):
            raise OracleError("DT tool-execute 返回体不是对象", code=E_ORACLE_FETCH_FAILED)
        raw = envelope.get("content")
    elif transport == "direct_http":
        endpoint = str(ch.get("endpoint") or "")
        if not endpoint:
            raise OracleError("direct_http 传输缺 endpoint 配置",
                              code=E_ORACLE_FETCH_FAILED)
        bearer = _read_bearer(ch, config)
        parts = urlsplit(endpoint)
        if parts.scheme != "https" and parts.hostname not in {
                "127.0.0.1", "localhost", "::1"}:
            raise OracleError(
                "direct_http 携带 Bearer 时只允许 HTTPS；本机 loopback 例外。",
                code=E_ORACLE_FORBIDDEN)
        envelope = _http_json(endpoint, {"tool": tool, "params": params},
                              timeout_s=int(ch.get("timeout_s") or 60), bearer=bearer)
        raw = envelope.get("content") if isinstance(envelope, dict) else envelope
    else:
        raise OracleError(f"未知 transport：{transport!r}", code=E_ORACLE_FETCH_FAILED)

    try:
        return parse_mcp_payload(raw, source=f"TAPD/{tool}")
    except McpPayloadError as exc:
        raise OracleError(str(exc), code=E_ORACLE_FETCH_FAILED,
                          detail=exc.raw_head) from exc


def _read_bearer(channel: dict[str, Any], config: Any = None) -> str:
    """按**键名**从密钥库取 Bearer。值只在本函数返回值里活一瞬，不落盘不进日志。"""
    key = str(channel.get("bearer_secret_key") or "")
    if not key:
        return ""
    from server.gateway.config import default_config
    cfg = config or default_config()
    return str(cfg.get_secret(key) or "")


def fetch_story(workspace_id: str, story_id: str, *,
                fields: tuple[str, ...] = DEFAULT_FIELDS,
                channel: dict[str, Any] | None = None,
                config: Any = None) -> dict[str, Any]:
    """取一条需求的原文行。**description 缺失/为空分两种码报**，见模块文档串。"""
    ws = str(workspace_id or "").strip()
    sid = str(story_id or "").strip()
    if not ws or not sid:
        raise OracleError("workspace_id 与 story_id 都不能为空",
                          code=E_ORACLE_FETCH_FAILED)
    payload = call_read_tool(
        "get_stories_or_tasks",
        {"workspace_id": ws, "options": {"id": sid, "fields": ",".join(fields)}},
        channel=channel, config=config)

    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise OracleError(
            f"TAPD 里找不到需求 {sid}（工作区 {ws}）。核对需求号与工作区。",
            code=E_ORACLE_NOT_FOUND)
    row = rows[0]
    story = row.get("Story") if isinstance(row, dict) and "Story" in row else row
    if not isinstance(story, dict):
        raise OracleError("TAPD 返回行形态不认识（既不是 {Story:...} 也不是对象）",
                          code=E_ORACLE_FETCH_FAILED, detail=str(row)[:200])

    if "description" not in story:
        raise OracleError(
            f"取回的需求行里没有 description 字段——**这是取数侧的问题，不是需求没写正文**。"
            f"多半是 fields 白名单没带上它，或换通道后 options 口径变了。"
            f"当前拿到 {len(story)} 个字段：{sorted(story)[:12]}",
            code=E_ORACLE_FIELD_MISSING, detail=sorted(story))
    if not str(story.get("description") or "").strip():
        raise OracleError(
            f"需求 {sid} 的正文是空的——字段要到了，内容确实没写。"
            "这条不能当 oracle 用（会让 content_digest 算在空壳上），去找需求方补正文。",
            code=E_ORACLE_BODY_EMPTY)
    return story


# ── 正文规整 ────────────────────────────────────────────────────────────────

_TAG_BREAK = re.compile(r"</(div|p|li|tr|h[1-6])\s*>|<br\s*/?>", re.I)
_TAG_ANY = re.compile(r"<[^>]+>")


def plain_text(html_text: str) -> str:
    """把 TAPD 富文本压成纯文本。**只用于展示与定档启发式，不参与 digest。**

    digest 一律算在**服务器原文**上——规整规则一旦改动，历史 digest 就全废了，
    那正是「digest 规则不得随手变」这条纪律要防的事。
    """
    text = _TAG_BREAK.sub("\n", html_text or "")
    text = _TAG_ANY.sub("", text)
    for src, dst in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(src, dst)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── 快照与漂移闸 ────────────────────────────────────────────────────────────


def oracle_dir(batch_id: str, *, owner: str | None = None,
               root: str | None = None) -> str:
    return os.path.join(artifacts.batch_dir(batch_id, owner=owner, root=root), "oracle")


def content_digest_of(story: dict[str, Any]) -> str:
    """快照摘要 = 对**取回的原文行**做 JCS + SHA-256（与 caseset 同一套规则）。"""
    return sha256_digest(story)


def snapshot(batch_id: str, story: dict[str, Any], *, workspace_id: str,
             story_id: str, tool: str = "get_stories_or_tasks",
             fields: tuple[str, ...] = DEFAULT_FIELDS,
             owner: str | None = None, root: str | None = None) -> dict[str, Any]:
    """冻结一份 oracle 快照，返回 `intake_profile.oracle_source` 该长的样子。"""
    fetched_at = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    d = oracle_dir(batch_id, owner=owner, root=root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"tapd-{workspace_id}-{story_id}-{fetched_at}.json")
    body = {"provider": "tapd", "workspace_id": workspace_id, "story_id": story_id,
            "tool": tool, "fetched_at": fetched_at, "fields": list(fields),
            "story": story}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

    text = plain_text(str(story.get("description") or ""))
    return {
        "provider": "tapd",
        "workspace_id": workspace_id,
        "story_id": story_id,
        "tool": tool,
        "fetched_at": fetched_at,
        "content_digest": content_digest_of(story),
        "text_digest": text_digest(text),
        "fields": list(fields),
        "snapshot_rel": os.path.relpath(
            path, artifacts.batch_dir(batch_id, owner=owner, root=root)),
        "title": str(story.get("name") or ""),
        "plain_len": len(text),
    }


def load_snapshot(batch_id: str, *, owner: str | None = None,
                  root: str | None = None) -> dict[str, Any]:
    """读最近一份快照（澄清/分析/用例都只读它，不回打 TAPD）。"""
    d = oracle_dir(batch_id, owner=owner, root=root)
    if not os.path.isdir(d):
        raise OracleError("本批次没有 oracle 快照", code=E_ORACLE_NOT_FOUND)
    names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    if not names:
        raise OracleError("本批次没有 oracle 快照", code=E_ORACLE_NOT_FOUND)
    with open(os.path.join(d, names[-1]), encoding="utf-8") as fh:
        return json.load(fh)


def check_drift(batch_id: str, *, owner: str | None = None,
                root: str | None = None, channel: dict[str, Any] | None = None,
                config: Any = None) -> dict[str, Any]:
    """漂移闸（adopt 前置）：重取一次比对 digest。

    **没有绕过开关**（ADR-M2-02 第 6 条判否）：宁可挡住采纳，也不引入一个
    "以快照为准强制采纳"的旁路——那个旁路一旦存在，漂移闸等于不存在。
    """
    try:
        profile = artifacts.load_artifact(batch_id, "intake_profile",
                                          owner=owner, root=root)
    except artifacts.ArtifactError as exc:
        return {"ok": False, "code": E_ORACLE_NOT_FOUND, "message": str(exc)}
    src = profile.get("oracle_source") or {}
    if (src.get("provider") or "") != "tapd":
        # 非 TAPD oracle（M1 形态的本地文档）不走漂移闸——如实说明，不假装检查过
        return {"ok": True, "code": "SKIPPED_NON_TAPD",
                "message": "本批次的 oracle 不是 TAPD 需求，漂移闸不适用。"}
    try:
        story = fetch_story(src["workspace_id"], src["story_id"],
                            fields=tuple(src.get("fields") or DEFAULT_FIELDS),
                            channel=channel, config=config)
    except OracleError as exc:
        return exc.as_payload()
    fresh = content_digest_of(story)
    if fresh != src.get("content_digest"):
        return {"ok": False, "code": E_ORACLE_DRIFT,
                "message": "需求已变更，请重新澄清。采纳被阻断——"
                           "用旧快照冻结一份 caseset 会让 oracle_digest 指向一份"
                           "已经不存在的需求。",
                "detail": {"snapshot_digest": src.get("content_digest"),
                           "current_digest": fresh,
                           "story_id": src.get("story_id")}}
    return {"ok": True, "code": "OK", "content_digest": fresh}
