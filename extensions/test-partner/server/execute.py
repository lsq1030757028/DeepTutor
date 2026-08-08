"""用例确定性执行（`execute_cases` 工具的实现层）。

M2.6「用例执行能力」（2026-08-05 立项，UAT 第一轮反馈：HAR 链路只通生成没通执行）。
用例自带的 `request` 块（method/url/headers/body/assertions，规格与校验见
`server/case_validate.py`）本身就是一份可执行的请求描述——不必绕道 Apifox，
本模块直接逐条发请求、逐条判定断言，产出「几过几败」的事实。

## 与其它模块的关系

- 断言语义与 `server/postman.py` 的 pm.test 翻译**同源**（status / json_path /
  body_contains 三类，加类型时三处同改：case_validate.ASSERTION_TYPES、postman
  的翻译分支、本模块的 `_run_assertion`）。
- 入参形状走 `server/args_tolerance.py` 同款宽容解包。
- 报告落盘沿用 `server/delivery.py` 的 `deliveries/` 根目录，可与一次交付合并。

## 凭据从哪来（0.6 起的正门：`env`）

`variables` 参数意味着用户要在**聊天框里打出真实凭据**——那等于凭据进模型上下文、
进聊天历史库、进 LLM 供应商。我们在本模块里严防凭据出流，却让它从前门进来，
这是 0.5 的红线漏洞。0.6 的正门是 `env`：环境名 → 网关配置中心
（`server/gateway/config.py` 的 `environments.json`）→ base_url + variables，
**解析发生在网关进程内，值不经过模型**。`variables` 保留，但只是临时覆盖的后门。

## 登录换新（0.8 起的 `auth="login"`）

HAR 里录的 token 会过期，靠用户手工维护环境变量里的 token 不可持续。档A HAR
（`parse_har` 的 `replay.grade == "A"`）本身就意味着「这条链路可以重登录」，
`parse_har` 会连带产出 `replay.login_request`——登录请求的可执行描述。把它传进来
并设 `auth="login"`，本模块就在执行前先重放一次登录、从响应里提取新 token、
作为运行时变量注入后续所有请求。等价 Postman 的 pre-request auth：**录一次，长期能跑**。

`auth` 三个模式（默认 `env` = 0.6 的现状行为）：

| 模式 | 凭据从哪来 |
| --- | --- |
| `none` | 环境变量表一个都不注入（只认显式传入的 `variables`）；跑公开接口用 |
| `env` | 环境变量表铺底（**默认**，现状行为） |
| `login` | 环境变量表铺底 + 执行前重放登录请求换新 token，覆盖同名变量 |

账号口令走配置页「测试环境」的两个保留变量名 `login_username` / `login_password`
（不新开存储）；token 取值规则默认用 `parse_har` 推断的那条，环境里配一个
`auth_token_path` 变量即可显式覆盖。登录失败 / 取不到 token → **整轮不执行**并返回
可读原因，绝不带着坏 token 硬跑一堆 401。登录请求本身同样受下面四条红线约束。

## 安全红线（四条，代码里逐条落点，改动前先读这里）

1. **只向 base_url 所在 host 发请求**：渲染后的 URL 解析出的 `host:port` 与 base_url
   的不一致 → 整条用例 `skipped`，绝不发（`_host_key` + `SKIP_CROSS_HOST`）。
   同理不跟随重定向（`follow_redirects=False`），免得 302 把请求带去别的 host。
2. **变量未提供不乱发**：渲染后仍残留 `{{var}}` 的用例 `skipped`，reason 只写变量名
   （`SKIP_MISSING_VARS`）。
3. **变量值不出流**：`variables` 的值不进返回结果、不进落盘报告、不进任何日志。
   请求头压根不记录；所有出站文本过 `_scrub` 门（把够长的变量值换回 `{{名字}}`），
   因为响应体/异常串里可能回显凭证。收据只记 base_url 的 host，不记完整 base_url。
4. **不判绿不猜**：没有 `request` 块 = 人执行用例，`skipped`；有请求块但没有断言 =
   跑完无从判定成败，也 `skipped`（W05 补了断言再来），不拿「发出去了」冒充「通过」。

并发不做：同步逐条执行，保证日志可读与被测系统安全。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

try:                        # 运行环境里 mcp 2.0 带的是 pydantic 维护的 httpx2
    import httpx2 as httpx
except ImportError:         # pragma: no cover - 只装了经典 httpx 的环境
    import httpx            # type: ignore[no-redef]

from server import args_tolerance, case_validate

SCHEMA = "test-partner.case-execution/v1"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DELIVERIES_DIR = os.path.join(REPO_ROOT, "deliveries")

#: 单请求超时（秒）默认值与夹取范围
DEFAULT_TIMEOUT_S = 15
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 120
#: 单条断言的 actual 截断长度（整体输出体积限幅）
MAX_ACTUAL_CHARS = 200
#: 返回值里最多带回多少条结果（全量在落盘报告里）
MAX_RESULTS_RETURNED = 200
#: 变量值短于此长度不做出站替换——`1` / `abc` 这种值满文本乱换会把无关内容改坏
#: （与 case_validate._MIN_SENSITIVE_LEN 同口径）
MIN_SCRUB_LEN = 8
#: base_url 变量名（与 postman.BASE_URL_VAR 同源）
BASE_URL_VAR = "baseUrl"

# skipped 原因码 —— 与 SKILL.md 的 skipped 语义同源
SKIP_NO_REQUEST = "NO_REQUEST_BLOCK"
SKIP_CROSS_HOST = "CROSS_HOST_BLOCKED"
SKIP_MISSING_VARS = "MISSING_VARIABLES"
SKIP_NO_ASSERTION = "NO_ASSERTION"
SKIP_BAD_REQUEST = "REQUEST_SHAPE_INVALID"

# ── 登录换新 ────────────────────────────────────────────────────────────────
#: 三个保留变量名（与 `server/har_parse.py` 的占位符、配置页「测试环境」的行内说明
#: 同源，三处同改）。账密不新开存储，就住在环境变量表里。
LOGIN_USERNAME_VAR = "login_username"
LOGIN_PASSWORD_VAR = "login_password"
AUTH_TOKEN_PATH_VAR = "auth_token_path"
#: 换来的新 token 默认注入成哪个运行时变量（调用方可用 auth_token_variable 改）
DEFAULT_TOKEN_VAR = "token"
#: auth 三模式
AUTH_MODES = ("none", "env", "login")
DEFAULT_AUTH_MODE = "env"
#: 登录请求里不该原样发出去的脱敏占位（体检报告掩码留下的）
_REDACTED_MARKS = ("<redacted>", "***")
#: 登录失败时回显的响应正文片段长度（诊断用；过出站脱敏门 + 截断）
MAX_LOGIN_SNIPPET = 160

# 登录换新的中止码（命中即整轮不执行）
LOGIN_REQUEST_REQUIRED = "LOGIN_REQUEST_REQUIRED"
LOGIN_REQUEST_INVALID = "LOGIN_REQUEST_INVALID"
LOGIN_CREDENTIALS_MISSING = "LOGIN_CREDENTIALS_MISSING"
LOGIN_CROSS_HOST_BLOCKED = "LOGIN_CROSS_HOST_BLOCKED"
LOGIN_FAILED = "LOGIN_FAILED"
LOGIN_TOKEN_NOT_FOUND = "LOGIN_TOKEN_NOT_FOUND"

_VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_SEGMENT_RE = re.compile(r"^([^\[\]]*)((?:\[\d+\])*)$")
_SLUG_KEEP_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


class ExecuteError(ValueError):
    """执行层可预期的入参错误（base_url 缺失/非法等）。"""

    def __init__(self, code: str, message: str, hint: str = ""):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


class LoginError(ExecuteError):
    """登录换新失败。命中即**整轮不执行**——带着坏 token 硬跑一堆 401 没有信息量。

    消息与 hint 都是给人看的可读原因；账密与响应里可能回显的凭证都过出站脱敏门。
    """


# ── 变量渲染与出站脱敏 ──────────────────────────────────────────────────────

def render(text: Any, mapping: dict) -> str:
    """`{{var}}` 替换。mapping 里没有的变量原样留着（留给缺失变量闸去发现）。"""
    if not isinstance(text, str):
        return "" if text is None else str(text)
    return _VAR_RE.sub(
        lambda m: mapping[m.group(1)] if m.group(1) in mapping else m.group(0), text)


def missing_vars(text: Any, mapping: dict) -> list:
    """渲染后仍会残留的变量名（去重保序）。"""
    out: list = []
    for name in _VAR_RE.findall(text if isinstance(text, str) else ""):
        if name not in mapping and name not in out:
            out.append(name)
    return out


def _scrub_map(variables: dict) -> list:
    """出站替换表 [(变量值, "{{变量名}}")]，长值优先，短值不参与。"""
    pairs = [(str(v), "{{%s}}" % k) for k, v in variables.items()
             if str(v) and len(str(v)) >= MIN_SCRUB_LEN and k != BASE_URL_VAR]
    return sorted(pairs, key=lambda kv: len(kv[0]), reverse=True)


def scrub(text: Any, scrub_pairs: list) -> Any:
    """出站门：把够长的变量值换回 `{{名字}}`。

    响应体、异常串里都可能回显凭证——不能因为「我们没主动写」就放行。
    非字符串原样返回（数字/布尔不可能装下凭证）。
    """
    if not isinstance(text, str) or not scrub_pairs:
        return text
    for value, placeholder in scrub_pairs:
        if value in text:
            text = text.replace(value, placeholder)
    return text


def _truncate(text: Any, limit: int = MAX_ACTUAL_CHARS) -> Any:
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断，共 {len(text)} 字符）"


# ── URL 与 host 红线 ────────────────────────────────────────────────────────

def _host_key(url: str) -> str:
    """URL → `host:effective_port`（http 补 80，https 补 443）。解析不出返回空串。"""
    parts = urlsplit(str(url or "").strip())
    scheme = (parts.scheme or "").lower()
    try:
        host, port = parts.hostname, parts.port
    except ValueError:                      # 畸形端口
        return ""
    if not host:
        return ""
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else 0
    return f"{host.lower()}:{port}"


def normalize_base_url(base_url: Any) -> str:
    """base_url 规整：必须是 http(s) 绝对地址；去掉尾斜杠。非法抛 ExecuteError。"""
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        raise ExecuteError(
            "BASE_URL_REQUIRED", "base_url 必填：不给被测环境地址就无从执行",
            "传形如 https://api.example.com 的环境地址（用例里的 {{baseUrl}} 会替换成它）。")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ExecuteError(
            "BASE_URL_INVALID", f"base_url 不是合法的 http(s) 绝对地址：「{raw}」",
            "带上协议头，例如 https://api.example.com 或 http://127.0.0.1:8080。")
    return raw


# ── 测试环境解析（凭据的正门） ──────────────────────────────────────────────

def _environment_store(store: Any = None) -> Any:
    """拿到配置中心。`store` 是给测试注入临时配置目录用的，不在工具面上暴露。"""
    if store is not None:
        return store
    from server.gateway.config import default_config   # 局部导入：解析 env 时才需要
    return default_config()


def resolve_environment(name: str, store: Any = None) -> tuple:
    """环境名 →（base_url, variables）。**解析在本进程内完成，值不经过模型。**

    环境不存在时抛 `ExecuteError`，消息里**只列已配环境的名字**——
    列 base_url 尚可商量，列变量键值就等于把金库目录读给模型听。
    """
    try:
        store = _environment_store(store)
    except Exception as exc:  # noqa: BLE001 - 配置中心导入失败不该表现成"环境不存在"
        raise ExecuteError(
            "ENV_STORE_UNAVAILABLE",
            f"读不到本机的测试环境配置：{type(exc).__name__}: {exc}",
            "确认网关进程正常，或改为直接传 base_url。") from exc
    row = store.get_environment(name)
    if row is None:
        known = store.environment_names()
        raise ExecuteError(
            "ENV_NOT_FOUND",
            f"没有名为「{name}」的测试环境。"
            + (f"已配置的环境：{'、'.join(known)}。" if known
               else "本机还没有配置任何测试环境。"),
            "去配置页 http://localhost:3789 的「测试环境」分区新增一个"
            "（环境名 + base_url + 变量），凭据存在本机、不经聊天。")
    return (str(row.get("base_url") or ""),
            {str(k): str(v) for k, v in (row.get("variables") or {}).items()})


def resolve_url(url: str, base_url: str) -> str:
    """渲染后的 url → 最终请求地址。裸路径按 base_url 拼接。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        return raw
    if not raw.startswith("/"):
        raw = "/" + raw
    return base_url + raw


# ── 断言判定（与 postman.py 的 pm.test 翻译同源） ───────────────────────────

def json_path_get(data: Any, path: str) -> tuple:
    """简易 jsonpath 取值 →（是否取到, 值）。`$.data.items[0].id` 形态。"""
    text = re.sub(r"^\$\.?", "", str(path or "").strip())
    node = data
    for seg in text.split("."):
        if not seg:
            continue
        m = _SEGMENT_RE.match(seg)
        keys: list = []
        if m:
            if m.group(1):
                keys.append(m.group(1))
            keys.extend(int(i) for i in re.findall(r"\[(\d+)\]", m.group(2)))
        else:
            keys.append(seg)
        for key in keys:
            if isinstance(key, int):
                if not isinstance(node, (list, tuple)) or key >= len(node):
                    return False, None
                node = node[key]
            else:
                if not isinstance(node, dict) or key not in node:
                    return False, None
                node = node[key]
    return True, node


def _run_assertion(assertion: Any, response, body_text: str, json_state: dict) -> dict:
    """一条断言 → {type, expected, actual, passed[, path]}。写法非法的判 False 并说明。"""
    if not isinstance(assertion, dict):
        return {"type": "(非法)", "expected": None, "actual": None, "passed": False,
                "note": "断言不是对象，无法判定（先跑 validate_cases 看 E15）"}
    kind = str(assertion.get("type") or "").strip().lower()
    expected = assertion.get("expected")
    out: dict = {"type": kind or "(空)", "expected": expected, "passed": False}

    if kind == "status":
        try:
            code = int(str(expected).strip())
        except (TypeError, ValueError):
            out.update(actual=response.status_code, note="expected 不是整数状态码（E15）")
            return out
        out.update(expected=code, actual=response.status_code,
                   passed=response.status_code == code)
        return out

    if kind == "body_contains":
        needle = str(expected if expected is not None else "")
        if not needle:
            out.update(actual=None, note="expected 为空（E15）")
            return out
        hit = needle in body_text
        out.update(passed=hit,
                   actual=("命中" if hit else
                           f"未命中；响应正文共 {len(body_text)} 字符，开头："
                           f"{body_text[:MAX_ACTUAL_CHARS]}"))
        return out

    if kind == "json_path":
        path = str(assertion.get("path") or "").strip()
        out["path"] = path
        if not path:
            out.update(actual=None, note="缺 path（E15）")
            return out
        if "error" in json_state:
            out.update(actual=f"响应不是合法 JSON：{json_state['error']}")
            return out
        found, value = json_path_get(json_state.get("data"), path)
        if not found:
            out.update(actual=f"路径 {path} 在响应里不存在")
            return out
        out.update(actual=value, passed=value == expected)
        return out

    out.update(actual=None, note=f"断言类型「{assertion.get('type')}」不认识（E15）")
    return out


# ── 登录换新（auth="login"）────────────────────────────────────────────────
#
# 一次登录换新分五步，任何一步不成立都抛 `LoginError` 让整轮停下：
#   ① login_request 形状合法（method/url 齐全，body 里没有解不开的脱敏占位）
#   ② 账密齐全（渲染后不残留 {{login_username}} / {{login_password}}）
#   ③ 登录端点与 base_url 同 host（跨 host 绝不发——红线 1 对登录请求同样成立）
#   ④ 登录响应 2xx
#   ⑤ 按取值规则拿到一个非空 token
# 账密只在 `_perform_login` 里流动：渲染出来直接发走，不进返回值、不进报告、不进日志。


def _coerce_login_request(login_request: Any) -> dict:
    """`login_request` 入参规整：JSON 字符串解开、单键包裹剥掉。救不回来抛 LoginError。"""
    value = login_request
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise LoginError(
                LOGIN_REQUEST_INVALID,
                f"login_request 收到字符串但不是合法 JSON：{exc}",
                "把 parse_har 报告里 replay.login_request 那个对象原样传进来。") from exc
    if isinstance(value, dict) and len(value) == 1:
        only = next(iter(value))
        if str(only).strip().lower() in ("login_request", "loginrequest", "login"):
            value = value[only]
    if not isinstance(value, dict) or not value:
        raise LoginError(
            LOGIN_REQUEST_REQUIRED,
            "auth=\"login\" 要有登录请求的可执行描述，但 login_request 没给（或不是对象）。",
            "先跑 parse_har：档A HAR 的报告里 replay.login_request 就是它，原样传进来。"
            "报告里 replay.auth_refresh.supported 为 false 时说明这份 HAR 做不了登录换新，"
            "改用 auth=\"env\"。")
    return value


def _render_login_headers(login_request: dict, mapping: dict) -> dict:
    """登录请求头：脱敏占位值的头整条丢掉（发 `<redacted>` 出去毫无意义）。"""
    out: dict = {}
    raw = login_request.get("headers")
    for h in raw if isinstance(raw, (list, tuple)) else []:
        if isinstance(h, dict):
            key = str(h.get("key") or h.get("name") or "").strip()
            value = str(h.get("value", ""))
        elif isinstance(h, str) and ":" in h:
            key, _, value = h.partition(":")
            key, value = key.strip(), value.strip()
        else:
            continue
        if not key or value.strip().lower() in _REDACTED_MARKS:
            continue
        out[key] = render(value, mapping)
    return out


def _login_body_raw(login_request: dict) -> str:
    body = login_request.get("body")
    if not isinstance(body, dict):
        return ""
    mode = str(body.get("mode") or "").strip().lower()
    if mode in ("", "none") or body.get("raw") is None:
        return ""
    raw = body["raw"]
    return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)


def _set_cookie_value(response, name: str) -> str:
    """从响应的 Set-Cookie 里取某个 cookie 的值。取不到返回空串。"""
    headers = getattr(response, "headers", None)
    raw: list = []
    if headers is not None:
        getter = getattr(headers, "get_list", None)
        if callable(getter):
            raw = list(getter("set-cookie"))
        else:                                   # pragma: no cover - 老版 httpx 兜底
            single = headers.get("set-cookie")
            raw = [single] if single else []
    for line in raw:
        head = str(line).split(";", 1)[0]
        key, _, value = head.partition("=")
        if not name or key.strip().lower() == name.strip().lower():
            if value.strip():
                return value.strip()
    return ""


#: `auth_token_path` 里指定"从 Set-Cookie 取"的前缀（`cookie:SESSIONID`）
COOKIE_RULE_PREFIX = "cookie:"


def extract_login_token(response, body_text: str, rule: Any,
                        override_path: str = "") -> tuple:
    """按取值规则从登录响应里取新 token →（token, 规则描述）。取不到 token 为空串。

    `override_path` 来自环境里的 `auth_token_path` 变量，**压过体检推断的规则**——
    推断只是省事，用户说了算。两种写法：`$.data.token` 走 JSON 体，
    `cookie:SESSIONID` 走响应的 Set-Cookie（体检不推断这种，见
    `har_parse.infer_token_extract` 的注释；要用就在环境里显式写）。
    """
    rule = rule if isinstance(rule, dict) else {}
    path = str(override_path or "").strip()
    if path.lower().startswith(COOKIE_RULE_PREFIX):
        cookie_name = path[len(COOKIE_RULE_PREFIX):].strip()
        return (_set_cookie_value(response, cookie_name),
                f"set_cookie {cookie_name}（环境变量 {AUTH_TOKEN_PATH_VAR} 显式指定）")
    if path:
        source, cookie_name = "json_body", ""
        described = f"json_body {path}（环境变量 {AUTH_TOKEN_PATH_VAR} 显式指定）"
    else:
        source = str(rule.get("source") or "json_body").strip().lower()
        path = str(rule.get("path") or "").strip()
        cookie_name = str(rule.get("cookie_name") or "").strip()
        described = (f"set_cookie {cookie_name}" if source == "set_cookie"
                     else f"json_body {path}")

    if source == "set_cookie":
        return _set_cookie_value(response, cookie_name), described
    if not path:
        return "", described
    try:
        data = json.loads(body_text) if str(body_text).strip() else None
    except ValueError:
        return "", described
    found, value = json_path_get(data, path)
    if not found or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "", described
    return str(value).strip(), described


def _perform_login(login_request: Any, client, base_url: str, mapping: dict,
                   scrub_pairs: list, timeout_s: int,
                   override_token_path: str = "") -> dict:
    """重放登录请求换一个新 token。成功返回 `{token, endpoint, token_source}`。

    **凭据只在本函数内流动**：账密渲染进请求就直接发走；返回值只有新 token
    （调用方立刻把它加进出站脱敏表），以及不含任何值的端点与规则描述。
    """
    spec = _coerce_login_request(login_request)

    method = str(spec.get("method") or "").strip().upper()
    if method not in case_validate.HTTP_METHODS:
        raise LoginError(
            LOGIN_REQUEST_INVALID,
            f"登录请求的 method「{spec.get('method')}」不合法，不执行本轮。",
            "login_request 原样用 parse_har 报告里的 replay.login_request。")
    url_tpl = str(spec.get("url") or "").strip()
    if not url_tpl:
        raise LoginError(LOGIN_REQUEST_INVALID, "登录请求的 url 为空，不执行本轮。",
                         "login_request 原样用 parse_har 报告里的 replay.login_request。")

    header_tpl = _render_login_headers(spec, {})    # 空 mapping = 只做形状规整
    body_tpl = _login_body_raw(spec)

    # ② 账密齐全（顺带把登录请求里其它没给值的变量也一并点名）
    missing: list = []
    for text in [url_tpl, body_tpl] + list(header_tpl.values()):
        for name in missing_vars(text, mapping):
            if name not in missing:
                missing.append(name)
    if missing:
        reserved = [n for n in missing
                    if n in (LOGIN_USERNAME_VAR, LOGIN_PASSWORD_VAR)]
        raise LoginError(
            LOGIN_CREDENTIALS_MISSING,
            "登录换新缺变量：" + "、".join("{{%s}}" % m for m in missing)
            + "，本轮不执行。"
            + ("账号口令是登录换新的必要输入。" if reserved else ""),
            "去配置页 http://localhost:3789 的「测试环境」里给这个环境加上这几个变量"
            f"（账号口令用保留名 {LOGIN_USERNAME_VAR} / {LOGIN_PASSWORD_VAR}）。"
            "**不要在聊天里贴账密**——值存在本机，不进模型上下文。")

    final_url = resolve_url(render(url_tpl, mapping), base_url)
    display_url = resolve_url(render(url_tpl, {BASE_URL_VAR: base_url}), base_url)
    endpoint = f"{method} {urlsplit(display_url).path or '/'}"

    # ③ 跨 host 红线（红线 1 对登录请求同样成立）——在发请求之前
    base_key, url_key = _host_key(base_url), _host_key(final_url)
    if not url_key or url_key != base_key:
        raise LoginError(
            LOGIN_CROSS_HOST_BLOCKED,
            f"登录端点的 host「{url_key or '(解析不出)'}」与 base_url host"
            f"「{base_key}」不一致，按安全红线不发这个登录请求，本轮不执行。",
            "登录和被测接口不在同一个 host 时，本工具不替你把账密发去第三方地址。"
            "确认 base_url 指的是同一套环境，或改用 auth=\"env\"。")

    headers = {k: render(v, mapping) for k, v in header_tpl.items()}
    body = render(body_tpl, mapping) if body_tpl else ""
    for mark in _REDACTED_MARKS:
        if mark in body:
            raise LoginError(
                LOGIN_REQUEST_INVALID,
                f"登录请求体里还留着脱敏占位「{mark}」，说明这个登录除账号口令之外"
                "还要别的凭证（验证码、签名之类），本工具不猜，本轮不执行。",
                "改用 auth=\"env\"，把有效 token 配进环境变量表。")

    # ④ 发登录请求（不跟随重定向——client 建的时候就是 follow_redirects=False）
    try:
        response = client.request(method, final_url, headers=headers,
                                  content=body.encode("utf-8") if body else None,
                                  timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - 传输层异常按登录失败如实记录
        raise LoginError(
            LOGIN_FAILED,
            "登录请求没发出去或没等到响应（"
            + str(_truncate(scrub(f"{type(exc).__name__}: {exc}", scrub_pairs),
                            MAX_LOGIN_SNIPPET))
            + f"），本轮不执行。登录端点：{endpoint}",
            "确认 base_url 指的环境是通的，或改用 auth=\"env\"。") from exc

    body_text = ""
    try:
        body_text = response.text or ""
    except Exception:  # noqa: BLE001 - 响应体解码异常不该带塌错误信息本身
        body_text = ""
    if not 200 <= int(response.status_code) < 300:
        raise LoginError(
            LOGIN_FAILED,
            f"登录请求返回 {response.status_code}（登录端点 {endpoint}），本轮不执行。"
            + (f"响应片段：{_truncate(scrub(body_text, scrub_pairs), MAX_LOGIN_SNIPPET)}"
               if body_text.strip() else ""),
            f"多半是配置页那个环境里的 {LOGIN_USERNAME_VAR} / {LOGIN_PASSWORD_VAR} "
            "不对或已失效，去页面上改一次；改不动就用 auth=\"env\" 走手工 token。")

    # ⑤ 取新 token
    token, described = extract_login_token(response, body_text,
                                           spec.get("token_extract"),
                                           override_token_path)
    if not token:
        raise LoginError(
            LOGIN_TOKEN_NOT_FOUND,
            f"登录成功（{response.status_code}）但按规则「{described}」取不到 token，"
            "本轮不执行——不支持自动登录换新，不猜别的位置。"
            f"登录端点：{endpoint}",
            f"在配置页那个环境里加一个 {AUTH_TOKEN_PATH_VAR} 变量指定取值路径"
            "（如 $.data.token），或改用 auth=\"env\"。"
            "parse_har 报告的 replay.login_request.token_extract.candidates 里"
            "列了体检看到的备选路径。")
    return {"token": token, "endpoint": endpoint, "token_source": described}


# ── 主流程 ──────────────────────────────────────────────────────────────────

def _select(normalized: list, case_ids: list) -> tuple:
    """按编号筛选 →（选中的用例, 没对上的编号）。不传 case_ids 就全选。"""
    if not case_ids:
        return list(normalized), []
    wanted = [str(c).strip() for c in case_ids if str(c).strip()]
    lower = {w.lower() for w in wanted}
    picked = [c for c in normalized if str(c.get("case_id", "")).strip().lower() in lower]
    seen = {str(c.get("case_id", "")).strip().lower() for c in picked}
    unknown = [w for w in wanted if w.lower() not in seen]
    return picked, unknown


def _skip(case: dict, code: str, reason: str) -> dict:
    return {"case_id": case.get("case_id", ""), "name": case.get("title", ""),
            "passed": None, "skipped": True, "skip_code": code, "reason": reason,
            "assertions": [], "status_code": None, "elapsed_ms": None, "error": None}


def _execute_one(case: dict, client, base_url: str, mapping: dict,
                 scrub_pairs: list, timeout_s: int) -> dict:
    """执行一条用例。任何红线命中都在发请求之前返回 skipped。"""
    cid = case.get("case_id", "")
    name = case.get("title", "")
    request = case.get("request")

    if not isinstance(request, dict) or not request:
        return _skip(case, SKIP_NO_REQUEST,
                     "没有 request 块（人执行用例），本工具不执行——请人工执行后回填结论")

    method = str(request.get("method") or "").strip().upper()
    if method not in case_validate.HTTP_METHODS:
        return _skip(case, SKIP_BAD_REQUEST,
                     f"method「{request.get('method')}」不合法（E11），不执行")
    url_tpl = str(request.get("url") or "").strip()
    if not url_tpl:
        return _skip(case, SKIP_BAD_REQUEST, "request.url 为空（E12），不执行")

    assertions = request.get("assertions")
    if not isinstance(assertions, (list, tuple)) or not assertions:
        return _skip(case, SKIP_NO_ASSERTION,
                     "请求块没有断言（W05），跑完无从判定成败——补上断言再执行")

    # ── 变量渲染 + 缺失变量闸（红线 2）──
    headers_raw = request.get("headers")
    header_pairs: list = []
    for h in headers_raw if isinstance(headers_raw, (list, tuple)) else []:
        if isinstance(h, dict):
            key = str(h.get("key") or h.get("name") or "").strip()
            if key:
                header_pairs.append((key, str(h.get("value", ""))))
        elif isinstance(h, str) and ":" in h:
            key, _, value = h.partition(":")
            header_pairs.append((key.strip(), value.strip()))

    body = request.get("body")
    body_raw = None
    if isinstance(body, dict):
        mode = str(body.get("mode") or "").strip().lower()
        if mode not in ("", "none") and body.get("raw") is not None:
            body_raw = body["raw"] if isinstance(body["raw"], str) else \
                json.dumps(body["raw"], ensure_ascii=False)

    missing: list = []
    for text in [url_tpl] + [v for _, v in header_pairs] + \
            ([body_raw] if body_raw else []):
        for name_ in missing_vars(text, mapping):
            if name_ not in missing:
                missing.append(name_)
    if missing:
        return _skip(case, SKIP_MISSING_VARS,
                     f"用例里的变量没有提供值：{', '.join('{{%s}}' % m for m in missing)}"
                     "——补进 variables 再执行（不发请求，免得把 {{占位}} 原样打过去）")

    final_url = resolve_url(render(url_tpl, mapping), base_url)
    # 展示用 URL 只替换 {{baseUrl}}，其余变量保持占位形态——短变量值也不会漏出去
    display_url = resolve_url(render(url_tpl, {BASE_URL_VAR: base_url}), base_url)

    # ── 跨 host 红线（红线 1）——在发请求之前 ──
    base_key, url_key = _host_key(base_url), _host_key(final_url)
    if not url_key or url_key != base_key:
        skipped = _skip(case, SKIP_CROSS_HOST,
                        f"请求 host「{url_key or '(解析不出)'}」与 base_url host"
                        f"「{base_key}」不一致，按安全红线不发请求"
                        "——只向 base_url 所在 host 发请求")
        skipped["url"] = scrub(display_url, scrub_pairs)
        skipped["method"] = method
        return skipped

    result: dict = {
        "case_id": cid, "name": name, "method": method,
        "url": scrub(display_url, scrub_pairs),
        "passed": False, "skipped": False, "assertions": [],
        "status_code": None, "elapsed_ms": None, "error": None,
    }
    headers = {k: render(v, mapping) for k, v in header_pairs}
    content = render(body_raw, mapping).encode("utf-8") if body_raw else None

    started = time.perf_counter()
    try:
        response = client.request(method, final_url, headers=headers,
                                  content=content, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - 传输层异常按「本条失败」如实记录
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        result["error"] = _truncate(scrub(
            f"{type(exc).__name__}: {exc}", scrub_pairs))
        result["reason"] = "请求没发出去或没等到响应（超时/连接失败），本条判失败"
        return result
    result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    result["status_code"] = response.status_code

    # 断言按**原始**响应体判定（脱敏只在出站那一步做，否则 body_contains 会假失败）；
    # 判定出来的 actual/expected 统一过 `_scrub` 门再写进结果。
    body_text = ""
    try:
        body_text = response.text or ""
    except Exception:  # noqa: BLE001 - 响应体解码异常不该带塌整轮执行
        body_text = ""
    json_state: dict = {}
    try:
        json_state["data"] = json.loads(body_text) if body_text.strip() else None
        if not body_text.strip():
            json_state = {"error": "响应正文为空"}
    except ValueError as exc:
        json_state = {"error": str(exc)[:80]}

    checked: list = []
    for assertion in assertions:
        one = _run_assertion(assertion, response, body_text, json_state)
        one["expected"] = _truncate(scrub(one.get("expected"), scrub_pairs))
        one["actual"] = _truncate(scrub(one.get("actual"), scrub_pairs))
        checked.append(one)
    result["assertions"] = checked
    result["passed"] = all(a["passed"] for a in checked)
    return result


def _slugify(title: str) -> str:
    slug = _SLUG_KEEP_RE.sub("-", str(title or "").strip()).strip("-")
    return slug[:40] or "execution"


def _resolve_report_dir(delivery_dir: Any, notes: list,
                        deliveries_root: Any = "") -> str:
    """报告目录：能合并进传入的交付目录就合并，否则在 deliveries/ 下新建。

    只接受 deliveries 根之下的既有目录——工具不该按模型给的任意路径往磁盘上写。
    根默认是模块常量 `DELIVERIES_DIR`（MCP 工具那条线），但工作台走 HTTP 面时
    批次在**每用户的** scope 目录下（决策 0009），不传根的话合法目录会被这道闸
    误判成"任意路径"、报告落进镜像内只读路径直接 PermissionError——
    这是在容器里实测踩到的，不是理论风险。
    """
    raw = str(delivery_dir or "").strip().strip("\"'")
    root = os.path.abspath(str(deliveries_root or "").strip() or DELIVERIES_DIR)
    if raw:
        target = os.path.abspath(raw)
        inside = os.path.commonpath([target, root]) == root if os.path.splitdrive(
            target)[0].lower() == os.path.splitdrive(root)[0].lower() else False
        if inside and os.path.isdir(target):
            return target
        notes.append(
            f"delivery_dir「{raw}」不是 deliveries/ 下的既有目录，已改为新建执行报告目录")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(root, f"{stamp}-execution")
    out, bump = base, 1
    while os.path.exists(out):
        bump += 1
        out = f"{base}-{bump}"
    os.makedirs(out)
    return out


def _assertion_digest(result: dict) -> str:
    if result.get("skipped"):
        return result.get("reason", "")
    if not result.get("assertions"):
        return result.get("error") or ""
    bits = []
    for a in result["assertions"]:
        mark = "过" if a.get("passed") else "败"
        head = a.get("type", "")
        if a.get("path"):
            head = f"{head} {a['path']}"
        if a.get("passed"):
            bits.append(f"{head}={a.get('expected')} {mark}")
        else:
            bits.append(f"{head} 期望 {a.get('expected')} / 实际 {a.get('actual')} {mark}")
    return "；".join(bits)


def _auth_digest(auth_receipt: Any) -> str:
    """报告里那一行「凭据来源」。只由模式、登录端点与变量名拼成，不含任何值。"""
    receipt = auth_receipt if isinstance(auth_receipt, dict) else {}
    note = str(receipt.get("note") or "")
    if receipt.get("refreshed"):
        return (f"{note}（取值规则 {receipt.get('token_source', '')}）"
                if receipt.get("token_source") else note)
    return note or f"auth={receipt.get('mode', DEFAULT_AUTH_MODE)}"


def _write_reports(out_dir: str, payload: dict) -> list:
    json_path = os.path.join(out_dir, "execution_report.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    def cell(text: Any) -> str:
        return str("" if text is None else text).replace(
            "|", "\\|").replace("\n", "<br>")

    s = payload["summary"]
    lines = [f"# 执行报告 · {payload['title']}", "",
             f"- 执行时间：{payload['executed_at']}",
             f"- 被测环境 host：{payload['base_url_host']}"
             + (f"（环境「{payload['environment']}」）" if payload.get("environment")
                else ""),
             f"- 本次收到用例 {payload.get('received_case_count', 0)} 条，"
             f"本轮选中执行 {s['total']} 条",
             f"- 用例总数 {s['total']}，实发 {s['executed']}，"
             f"通过 {s['passed']}，失败 {s['failed']}，跳过 {s['skipped']}",
             f"- 结论：{s['verdict']}",
             "- 凭据来源：" + _auth_digest(payload.get("auth")), "",
             "凭据变量的值不进本报告（只用于真实请求）。失败条目按事实记录，"
             "不代表用例写得不对——被测系统本身有问题也会是这个结果。", "",
             "| 编号 | 用例 | 结果 | 状态码 | 耗时ms | 断言摘要 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for r in payload["results"]:
        verdict = "跳过" if r.get("skipped") else ("通过" if r.get("passed") else "失败")
        lines.append("| " + " | ".join([
            cell(r.get("case_id")), cell(r.get("name")), verdict,
            cell(r.get("status_code")), cell(r.get("elapsed_ms")),
            cell(_assertion_digest(r)),
        ]) + " |")
    lines.append("")
    md_path = os.path.join(out_dir, "execution_report.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return [json_path, md_path]


def _notify(progress: Any, done: int, total: int, case: dict | None) -> None:
    """回调进度。**回调里抛异常不许影响执行**——它只是个进度条。"""
    if not callable(progress):
        return
    try:
        progress({
            "done": done,
            "total": total,
            "current_case_id": str((case or {}).get("case_id") or ""),
            "current_title": str((case or {}).get("title") or ""),
        })
    except Exception:  # noqa: BLE001 - 进度回调坏了不该带塌这一轮执行
        pass


def execute_cases(cases: Any, base_url: Any = "", variables: Any = None,
                  case_ids: Any = None, timeout_s: Any = DEFAULT_TIMEOUT_S,
                  delivery_dir: Any = "", title: Any = "", env: Any = "",
                  auth: Any = DEFAULT_AUTH_MODE, login_request: Any = None,
                  auth_token_variable: Any = DEFAULT_TOKEN_VAR,
                  client: Any = None, env_store: Any = None,
                  progress: Any = None, deliveries_root: Any = "") -> dict:
    """逐条执行带 `request` 块的用例并落执行报告。

    返回 `{ok, summary, results, auth, report_hint, ...}`。`ok` 表示**这轮执行跑起来了**，
    不表示用例全过——用例失败在 `summary.failed` 里如实计数，不影响 `ok`。

    `env` 是凭据的正门：传环境名，网关在本进程内解析出 base_url 与 variables
    （见 `resolve_environment`）。合并优先级——**显式传入的覆盖环境值**：

    - `base_url`：显式传了就用显式的，没传用环境的；两个都没有 → BASE_URL_REQUIRED。
    - `variables`：先铺环境的，再用显式传入的逐键覆盖（只覆盖同名键，其余环境变量保留）。

    `auth` 决定凭据怎么来（见模块 docstring 的三模式表）：`none` 不注入环境变量、
    `env` 用环境变量表（默认）、`login` 在此之上先重放 `login_request` 换一个新 token
    注入成 `auth_token_variable`（默认 `token`）。登录失败整轮不执行，返回 `ok: false`
    与可读原因，**不落报告**——一份全是 401 的报告没有信息量。

    `client`（httpx transport）、`env_store`（配置中心）与 `deliveries_root`
    （报告落盘的合法根，工作台传每用户的批次根）都是同进程注入口，
    不在 MCP 工具面上暴露。`progress` 同理：工作台在后台线程里跑这个函数，靠它把
    「第 N/M 条，当前用例名」喂给轮询端点——**执行是同步逐条的，所以进度是真的**，
    不是估算出来的。回调形状 `{done, total, current_case_id, current_title}`，
    每条开跑前报一次、整轮结束再报一次（`done == total`）。
    """
    notes: list = []
    env_variables: dict = {}
    try:
        (cases, base_url, variables, case_ids, timeout_s, delivery_dir, title,
         env, auth, login_request, auth_token_variable,
         notes) = args_tolerance.tolerant_execute_args(
            cases, base_url, variables, case_ids, timeout_s, delivery_dir, title, env,
            auth, login_request, auth_token_variable)
        env = str(env or "").strip()
        if auth not in AUTH_MODES:
            notes.append(f"auth「{auth}」不是 none/env/login 之一，"
                         f"已按默认的「{DEFAULT_AUTH_MODE}」处理")
            auth = DEFAULT_AUTH_MODE
        auth_token_variable = str(auth_token_variable or "").strip() or DEFAULT_TOKEN_VAR
        if env:
            env_base_url, env_variables = resolve_environment(env, env_store)
            if not str(base_url or "").strip():
                base_url = env_base_url
            elif env_base_url and str(base_url).strip().rstrip("/") != env_base_url:
                notes.append(
                    f"base_url 显式传入，覆盖了环境「{env}」里配的地址")
        base_url = normalize_base_url(base_url)
        if not isinstance(cases, (list, tuple)) or not cases:
            raise ExecuteError("CASES_EMPTY", "cases 必须是非空的用例数组",
                               "先用 validate_cases 过一遍，再把同一份用例数组传进来。")
    except args_tolerance.ArgsToleranceError as exc:
        return {"ok": False, "schema": SCHEMA, "error": exc.code,
                "message": exc.message, "hint": exc.hint, "normalized": exc.normalized}
    except ExecuteError as exc:
        return {"ok": False, "schema": SCHEMA, "error": exc.code,
                "message": exc.message, "hint": exc.hint, "normalized": notes}

    received_case_count = len(cases)
    timeout_s = max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, int(timeout_s or DEFAULT_TIMEOUT_S)))
    explicit_variables = {str(k): str(v) for k, v in (variables or {}).items()
                          if str(k).strip()}
    # auth="none"：环境变量表一个都不注入（base_url 仍来自环境——它不是凭据）
    if auth == "none" and env_variables:
        notes.append(f"auth=\"none\"：环境「{env}」的 {len(env_variables)} 个变量本轮不注入")
        env_variables = {}
    # 环境变量先铺底，显式传入的逐键盖上去（临时覆盖某一个变量不必重配环境）
    variables = dict(env_variables)
    variables.update(explicit_variables)
    overridden = sorted(set(env_variables) & set(explicit_variables))
    if overridden:
        notes.append("显式 variables 覆盖了环境里的同名变量："
                     + "、".join(overridden))
    mapping = dict(variables)
    mapping[BASE_URL_VAR] = base_url          # base_url 入参永远压过 variables 里的同名项
    scrub_pairs = _scrub_map(variables)

    normalized = [c for c in case_validate.normalize_cases(cases) if c["_shape_ok"]]
    selected, unknown_ids = _select(normalized, case_ids)

    # 凭据来源收据：只记模式、登录端点与变量名，**一个值都不记**
    auth_receipt: dict = {
        "mode": auth,
        "refreshed": False,
        "credential_values_recorded": False,
        "note": {
            "none": "本轮不注入环境凭据（auth=\"none\"）："
                    "用例里的 {{变量}} 只认显式传入的 variables。",
            "env": ("本轮凭据取自环境「%s」的变量表。" % env if env
                    else "本轮凭据取自显式传入的 variables。"),
            "login": "本轮通过登录换新获取凭证。",
        }[auth],
    }

    results: list = []
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout_s, follow_redirects=False)
    try:
        if auth == "login":
            try:
                outcome = _perform_login(login_request, client, base_url, mapping,
                                         scrub_pairs, timeout_s,
                                         variables.get(AUTH_TOKEN_PATH_VAR, ""))
            except LoginError as exc:
                # 整轮不执行：不落报告、不返回半截结果，只给可读原因
                auth_receipt["error"] = exc.code
                return {"ok": False, "schema": SCHEMA, "error": exc.code,
                        "message": exc.message, "hint": exc.hint,
                        "auth": auth_receipt, "environment": env,
                        "received_case_count": received_case_count,
                        "normalized": notes}
            # 新 token 立刻进出站脱敏表——它和账密一样一个字节都不许出流
            variables[auth_token_variable] = outcome["token"]
            mapping[auth_token_variable] = outcome["token"]
            scrub_pairs = _scrub_map(variables)
            auth_receipt.update(
                refreshed=True,
                login_endpoint=outcome["endpoint"],
                token_variable=auth_token_variable,
                token_source=outcome["token_source"],
                note=("本轮通过登录换新获取凭证：重放登录请求 "
                      f"{outcome['endpoint']}，新 token 注入成 "
                      "{{%s}} 供后续用例使用。账密与新 token 都不进本报告。"
                      % auth_token_variable))
        for done, case in enumerate(selected):
            _notify(progress, done, len(selected), case)
            results.append(_execute_one(case, client, base_url, mapping,
                                        scrub_pairs, timeout_s))
        _notify(progress, len(selected), len(selected), None)
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 关闭失败不该盖掉执行结果
                pass

    executed = [r for r in results if not r.get("skipped")]
    passed = [r for r in executed if r.get("passed")]
    failed = [r for r in executed if not r.get("passed")]
    skipped = [r for r in results if r.get("skipped")]
    summary = {
        "total": len(selected),
        "executed": len(executed),
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "verdict": (f"{len(passed)} 过 {len(failed)} 败"
                    + (f"，{len(skipped)} 条跳过未执行" if skipped else "")
                    # 有跳过就不许说「全部通过」——跳过的那些根本没跑
                    + ("；全部通过" if executed and not failed and not skipped else "")),
    }

    title = str(title or "").strip() or "用例执行"
    executed_at = time.strftime("%Y-%m-%d %H:%M:%S")
    report: dict = {
        "schema": SCHEMA,
        "title": title,
        "executed_at": executed_at,
        # 收据式记录：只记 host，不记完整 base_url，更不记任何变量值
        "base_url_host": _host_key(base_url),
        # 环境名不是凭据（它就是个标签），记下来才知道这轮打的是哪套环境
        "environment": env,
        "variable_names": sorted(variables),
        "variable_source": ("环境「%s」+ 显式传入" % env if env and explicit_variables
                            else "环境「%s」" % env if env
                            else "显式传入" if explicit_variables else "（无变量）"),
        "variable_values_recorded": False,
        # 凭据怎么来的（模式 / 登录端点 / 注入的变量名），值一个都不在里面
        "auth": auth_receipt,
        "timeout_s": timeout_s,
        # 本次收到几条 vs 实际执行几条：执行范围默认是**全部**用例，
        # 两个数对不上就说明调用方自己缩了范围（模型只传了一部分进来）
        "received_case_count": received_case_count,
        "case_count_input": len(normalized),
        "summary": summary,
        "results": results,
        "generator": {"server": "test-partner", "tool": "execute_cases"},
        "note": ("执行结果是事实记录：失败条目可能是被测系统的问题，"
                 "也可能是用例写得不对，本工具不替任何一方下结论。"),
    }
    if unknown_ids:
        report["unknown_case_ids"] = unknown_ids
    if notes:
        report["normalized"] = list(notes)

    files: list = []
    report_error = ""
    try:
        out_dir = _resolve_report_dir(delivery_dir, notes, deliveries_root)
        if notes:
            report["normalized"] = list(notes)
        files = _write_reports(out_dir, report)
    except Exception as exc:  # noqa: BLE001 - 落盘失败不该吞掉已经拿到的执行结果
        out_dir = ""
        report_error = f"执行报告落盘失败：{type(exc).__name__}: {exc}"

    result = {
        "ok": True,
        "schema": SCHEMA,
        "summary": summary,
        # 执行范围对账：收到几条 / 形状合法几条 / 本轮选中几条。
        # 缩减范围只能是用户明确要求的结果，不该是模型顺手少传几条造成的。
        "received_case_count": received_case_count,
        "selected_case_count": len(selected),
        "results": results[:MAX_RESULTS_RETURNED],
        "base_url_host": report["base_url_host"],
        "environment": env,
        "auth": auth_receipt,
        "report_dir": out_dir,
        "files": files,
        "report_hint": (
            f"执行报告已落盘：{out_dir}（execution_report.json + execution_report.md）。"
            "报告在宿主机上，凭据变量的值不在报告里。" if out_dir else report_error),
    }
    if len(results) > MAX_RESULTS_RETURNED:
        result["results_truncated"] = len(results) - MAX_RESULTS_RETURNED
    if unknown_ids:
        result["unknown_case_ids"] = unknown_ids
        result["warnings"] = [f"case_ids 里这些编号在用例数组里找不到：{', '.join(unknown_ids)}"]
    if report_error:
        result["report_error"] = report_error
    if notes:
        result["normalized"] = notes
    return result
