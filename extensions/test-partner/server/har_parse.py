"""HAR 体检（`parse_har` 工具的实现层）。

逻辑移植自 blackbox-test-agent 的 `backend/intake_har.py`（只读参考，未改动那个仓）：

| 本文件 | blackbox 来源 |
| --- | --- |
| `_entries_of` / `_header` / `_parse_json` | `intake_har.py:79-100` |
| `_entry_host`（剥 userinfo）/ `_entry_path` / `_entry_method` | `intake_har.py:103-122` |
| path 归一化 `_is_opaque_atom` / `_norm_segment` / `_norm_path` | `intake_har.py:126-168` |
| 凭证词表 `_CRED_KEY_*` / `_TOKEN_KEYS` / `_AUTH_HEADER_NAMES` 等 | `intake_har.py:47-64` |
| 噪声路径 `_NOISE_PATH_RE` | `intake_har.py:67-70` |
| 判档 `_walk_token` / `_find_login` / `_token_reuse_hits` / `health_check` | `intake_har.py:180-308` |
| 业务范围 `business_scope` | `intake_har.py:313-375` |
| 脱敏哨兵集 `collect_sensitive_values` | `intake_har.py:380-457` |

与 blackbox 的差异（本仓有意为之）：
- blackbox 的脱敏闸②（`assert_sanitized`，`intake_har.py:460-467`）检出泄漏即抛错拒产底稿；
  这里改成**强制替换**（`_enforce_redaction`）：凭证值一旦出现在报告任何字符串里就替换成
  `<redacted>` 并在 `redaction.enforced_substitutions` 里计数。MCP 工具的返回值要给模型看，
  硬约束是「凭证值不出现在任何输出里」，替换比抛错更能满足它，且仍然留痕不静默。
- blackbox 只保留业务 host 的端点；这里保留全部非噪声 host 的端点（带 `host` 字段），
  但业务 host 排在前面——体检报告的读者是人和模型，需要看全貌。
- 新增：状态码分布、业务域分组、鉴权方式清单（只报键名与计数，不报值）、输出体积截断。
- 新增（0.8 登录换新）：档A HAR 额外产出 `replay.login_request`——登录请求的**可执行描述**，
  账号与口令的位置换成 `{{login_username}}` / `{{login_password}}` 占位，真值一律不出报告；
  外加 `token_extract` 取值规则（从登录响应里怎么拿新 token），推断不出就如实说不支持。
- 原始 HAR 不落任何暂存文件（blackbox 的 `stash_raw_har` 不移植）：本仓不需要重放原件。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlsplit

# 样例体的 PII 脱敏复用出境闸那一套词表与占位符（BB-424 收口）。方向安全：
# `server/generate/` 零反向 import，不成环。刻意不另造第三套规则——BB-424 的
# 成因就是「第二套脱敏词表漏了一整类」，词表只此一份（server/generate/scrub.py）。
from server.generate.scrub import scrub_payload

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX_DIR = os.path.join(REPO_ROOT, "inbox")

REPORT_SCHEMA = "test-partner.har-report/v1"

#: 逐端点摘要的默认上限（输出体积控制：单条约 80 字符，120 条约 10K 字符 ≈ 3K tokens）
DEFAULT_MAX_ENDPOINTS = 120
#: 单个业务域展示的示例端点条数
DOMAIN_SAMPLE_LIMIT = 5
#: 业务域分组的展示条数上限（按 request_count 降序取前 N；summary.domain_count 仍报全部域数）
MAX_DOMAINS_LISTED = 40
#: 报告里保留的 host 条数上限
MAX_HOSTS_LISTED = 30

# ── 请求样例的体积闸（M2.5：样例是给模型填 request 块用的，比端点行贵得多） ──
#: 带 `sample` 的端点条数上限（业务 host 优先；超出的端点只有端点行没有样例）
DEFAULT_MAX_SAMPLES = 40
#: 单条样例请求体的字符上限
MAX_SAMPLE_BODY_CHARS = 600
#: 全报告样例请求体的字符总预算（超了之后的样例只留 method/url/头，不带体）
MAX_SAMPLE_BODY_BUDGET = 8000
#: 单条样例保留的请求头条数上限
MAX_SAMPLE_HEADERS = 8
#: 单条样例列出的 query 键名上限
MAX_SAMPLE_QUERY_KEYS = 8
#: 样例请求头里保留**原值**的「形状头」白名单——只有它们的值进报告
_SAMPLE_HEADER_ALLOW = ("content-type", "accept", "accept-language", "x-requested-with")
#: 形状头值本身也截断（有的 content-type 带超长 boundary）
MAX_SAMPLE_HEADER_VALUE = 120
REDACTED = "<redacted>"

# ── 凭证词表（移植 intake_har.py:47-64） ────────────────────────────────────
_CRED_KEY_SUBSTR = ("password", "passwd", "secret", "token", "apikey",
                    "authorization", "credential", "sign", "signature", "hmac",
                    "accesskey", "sessionkey", "cookie")
_CRED_KEY_EXACT = {"pwd", "auth", "otp", "smscode", "verifycode", "pin"}
_PASSWORD_KEYS = {"password", "passwd", "pwd", "secret", "pin", "passcode"}
_TOKEN_KEYS = {"token", "access_token", "accesstoken", "id_token", "idtoken",
               "jwt", "session_token", "sessiontoken", "auth_token", "authtoken"}
_AUTH_HEADER_NAMES = {"authorization", "x-token", "token", "x-auth-token",
                      "x-access-token", "access-token", "auth-token",
                      "x-api-key", "api-key", "x-apikey"}

# ── 登录换新词表（0.8） ─────────────────────────────────────────────────────
#: 登录换新的三个保留变量名。用户在配置页「测试环境」的变量表里配它们，
#: `execute_cases(auth="login")` 按名取用。**三处同源，改名要一起改**：
#: 本文件（占位符与提示文案）、`server/execute.py`（取值）、
#: `server/gateway/webapp.py`（配置页行内说明）。
LOGIN_USERNAME_VAR = "login_username"
LOGIN_PASSWORD_VAR = "login_password"
AUTH_TOKEN_PATH_VAR = "auth_token_path"
#: base_url 变量名（与 postman.BASE_URL_VAR / execute.BASE_URL_VAR 同源）
BASE_URL_VAR = "baseUrl"
#: 登录请求体里「账号」字段的常见键名（比较前先过 `_norm_key` 归一化）
_LOGIN_USERNAME_KEYS = {"username", "user", "useraccount", "account", "accountname",
                        "loginname", "loginid", "login", "mobile", "mobilephone",
                        "phone", "phonenumber", "tel", "telephone", "email"}
#: 登录响应里 token 的常见路径（按优先级；命中即用）。推断结果会写进报告让人可改。
_TOKEN_PATH_PREFERENCE = ("$.data.token", "$.data.access_token", "$.data.accessToken",
                          "$.token", "$.access_token", "$.accessToken",
                          "$.data.id_token", "$.data.idToken", "$.data.jwt",
                          "$.result.token", "$.jwt")
#: 报告里列出的备选 token 路径条数上限
MAX_TOKEN_CANDIDATES = 5

#: Set-Cookie 的属性键（右值是 cookie 元数据不是凭证，拆对时跳过防误伤）
_COOKIE_ATTR_KEYS = {"path", "domain", "expires", "max-age", "samesite",
                     "secure", "httponly", "version", "comment", "priority",
                     "partitioned"}
_MIN_TOKEN_LEN = 16
_MIN_SENSITIVE_LEN = 8      # 短于此的值不进哨兵集（避免 "0"/"eq" 之类误伤扫描）

#: 静态资源/埋点噪声路径（移植 intake_har.py:67-70）
_NOISE_PATH_RE = re.compile(
    r"\.(?:js|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|map|mp4|mp3)(?:\?|$)"
    r"|(?:analytics|/log/|/track|/beacon|/collect|sensorsdata|/report/"
    r"|/heartbeat|/ping)", re.I)

#: 业务域归并时跳过的框架前缀段
_FRAMEWORK_SEGMENTS = {"api", "apis", "rest", "restful", "gateway", "gw",
                       "service", "services", "svc", "openapi", "web", "app",
                       "server", "backend", "public", "prod"}
_VERSION_SEG_RE = re.compile(r"^v\d+(?:\.\d+)*$", re.I)

REDACTION_DECLARATION = (
    "本报告的端点行只由 method、归一化 path、host、状态码、计数与键名拼成："
    "query 值整段剥除（只留非凭证键名），path 逐段归一化（数字/UUID/长十六进制 → :id，"
    "高熵段 → :opaque），host 取 hostname(:port) 并剥 userinfo。"
    "请求样例（sample）只取：method、归一化 URL、非凭证 query 键名、白名单请求头"
    "（content-type/accept 之类，其余头一律丢弃，凭证头只留键名、值换成 <redacted>）、"
    "凭证键已掩码的请求体样例、响应状态码；响应体一律不进报告。"
    "登录换新描述（replay.login_request）同此口径，只多一条：账号口令的**位置**用 "
    "{{login_username}} / {{login_password}} 占位标出（占位是位置不是值，真账密只在"
    "用户本机配置页里）；token 取值规则只出路径与键名。"
    "收尾另跑一遍凭证哨兵扫描：凭证键之下整棵子树的字符串、凭证头拆出的单值、"
    "全部 query 值、响应体 token 形值、登录请求体里的账号与口令值——"
    "任一出现在报告文本里即被替换成 <redacted> 并计数。"
    "样例请求体另过一道个人信息闸（BB-424）：按值形态识别的身份证、手机号、邮箱、"
    "银行卡、IP、长标识，以及键名像姓名的字段里的中文姓名，替换成 <类型> 保形占位符"
    "（占位保留字段语义，真值不出报告），命中数记在 redaction.pii_hits。"
    "该闸按形态识别，抓不到自由文本里的姓名、住址、生日、护照号、车牌等——"
    "不等于报告已无个人信息，对外分享前仍请自行过目。"
)


class HarInputError(ValueError):
    """输入层可预期的错误（文件缺失、JSON 坏损、非 HAR 结构）。"""

    def __init__(self, code: str, message: str, hint: str = ""):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


# ── HAR 结构小工具（移植 intake_har.py:79-122） ──────────────────────────────

def _entries_of(har: Any) -> list:
    if isinstance(har, dict):
        return (har.get("log") or {}).get("entries") or []
    if isinstance(har, list):
        return har
    return []


def _header(hlist, name):
    for h in hlist or []:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == name.lower():
            return h.get("value", "")
    return None


def _parse_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _entry_host(entry) -> str:
    """业务 host = hostname(:port)；**剥 userinfo**——`https://u:pass@h/` 的
    netloc 含口令，原样进报告即凭证进输出（blackbox intake_har.py:103-114）。"""
    parts = urlsplit(str((entry.get("request") or {}).get("url", "")))
    try:
        host = parts.hostname or ""
        port = parts.port
    except ValueError:      # 非法端口等畸形 URL：退化为空 host（不回落 netloc）
        return ""
    if not host:
        return ""
    return f"{host}:{port}" if port else host


def _entry_path(entry) -> str:
    return urlsplit(str((entry.get("request") or {}).get("url", ""))).path or ""


def _entry_method(entry) -> str:
    return str((entry.get("request") or {}).get("method", "GET")).upper()


def _entry_status(entry):
    status = (entry.get("response") or {}).get("status")
    return status if isinstance(status, int) else None


# ── path 段归一化（移植 intake_har.py:126-168） ──────────────────────────────
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEXISH_RE = re.compile(r"^[0-9a-f]{12,}$", re.I)
_ATOM_SPLIT_RE = re.compile(r"[-_.~]")
_OPAQUE_CHARSET_RE = re.compile(r"^[A-Za-z0-9+/=%]+$")
_MIN_OPAQUE_ATOM = 16      # 混合字母数字的不透明原子阈值
_MIN_ALPHA_ATOM = 24       # 纯字母串更可能是业务词，阈值放高


def _is_opaque_atom(atom: str) -> bool:
    """不透明原子：长十六进制 / 足够长的字母数字混合串（token、会话值、JWT 段）。"""
    if _HEXISH_RE.match(atom):
        return True
    if not _OPAQUE_CHARSET_RE.match(atom):
        return False
    if len(atom) >= _MIN_ALPHA_ATOM:
        return True
    if len(atom) < _MIN_OPAQUE_ATOM:
        return False
    return bool(re.search(r"\d", atom)) and bool(re.search(r"[A-Za-z]", atom))


def _norm_segment(seg: str) -> str:
    if seg.isdigit() or _UUID_RE.match(seg) or _HEXISH_RE.match(seg):
        return ":id"
    if any(_is_opaque_atom(a) for a in _ATOM_SPLIT_RE.split(seg)):
        return ":opaque"
    return seg


def normalize_path(path: str) -> str:
    """归一化路径：去尾斜杠 + 数字/UUID/长十六进制段 → :id、高熵段 → :opaque。

    这是脱敏防线①的主力——凭证若长在 path 段（一次性券、JWT、回显的会话值），
    哨兵集结构上收不到它，只有不透传才拦得住。副作用：端点计数口径不被随机段打散。
    """
    p = str(path or "").rstrip("/") or "/"
    return "/".join(_norm_segment(s) if s else s for s in p.split("/")) or "/"


def _norm_key(key) -> str:
    """键名归一化：小写 + 去掉分隔符。`user_name` / `userName` / `User-Name` 同归一。"""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _key_is_cred(key) -> bool:
    norm = _norm_key(key)
    if norm in _CRED_KEY_EXACT:
        return True
    return any(tok in norm for tok in _CRED_KEY_SUBSTR)


# ── 可回放判档（移植 intake_har.py:180-308） ─────────────────────────────────

def _walk_token(body, prefix=""):
    if isinstance(body, dict):
        for k, v in body.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if (str(k).lower() in _TOKEN_KEYS and isinstance(v, str)
                    and len(v) >= _MIN_TOKEN_LEN):
                return p, v
            r = _walk_token(v, p)
            if r:
                return r
    if isinstance(body, list):
        for i, v in enumerate(body):
            r = _walk_token(v, f"{prefix}[{i}]")
            if r:
                return r
    return None


def _post_input_keys(entry) -> list:
    text = ((entry.get("request") or {}).get("postData") or {}).get("text")
    if not text:
        return []
    obj = _parse_json(text)
    if isinstance(obj, dict):
        return [str(k) for k in obj]
    try:
        return [str(k) for k in parse_qs(text)]
    except Exception:  # noqa: BLE001
        return []


def _token_reuse_hits(entries, login_idx, token) -> int:
    hits = 0
    for j in range(login_idx + 1, len(entries)):
        req = entries[j].get("request") or {}
        if any(token in str(h.get("value", ""))
               for h in req.get("headers") or [] if isinstance(h, dict)):
            hits += 1
        if token in urlsplit(str(req.get("url", ""))).query:
            hits += 1
        if token in ((req.get("postData") or {}).get("text") or ""):
            hits += 1
        if any(token in str(c.get("value", ""))
               for c in req.get("cookies") or [] if isinstance(c, dict)):
            hits += 1
    return hits


def _find_login(entries):
    """login = 响应体产 token 的 POST（优先 token 在后续被复用者）。"""
    candidates = []
    for i, e in enumerate(entries):
        if _entry_method(e) != "POST":
            continue
        body = _parse_json(((e.get("response") or {}).get("content") or {}).get("text"))
        hit = _walk_token(body)
        if hit:
            candidates.append((i, hit[0], hit[1]))
    for i, path, val in candidates:
        if _token_reuse_hits(entries, i, val) > 0:
            return {"seq": i, "token_path": path, "_token_value": val, "reused": True}
    if candidates:
        i, path, val = candidates[0]
        return {"seq": i, "token_path": path, "_token_value": val, "reused": False}
    return None


def _cookie_stats(entries) -> dict:
    req_cookie = set_cookie = arr = 0
    for e in entries:
        if _header((e.get("request") or {}).get("headers"), "cookie"):
            req_cookie += 1
        if _header((e.get("response") or {}).get("headers"), "set-cookie"):
            set_cookie += 1
        arr += (len((e.get("request") or {}).get("cookies") or [])
                + len((e.get("response") or {}).get("cookies") or []))
    return {"request_cookie_headers": req_cookie,
            "response_set_cookie_headers": set_cookie,
            "cookies_array_entries": arr}


def _auth_header_present(entries) -> int:
    count = 0
    for e in entries:
        req = e.get("request") or {}
        for name in _AUTH_HEADER_NAMES:
            v = _header(req.get("headers"), name)
            if v and str(v).strip():
                count += 1
                break
    return count


def health_check(har) -> dict:
    """可回放三向判档（语义对齐 blackbox intake_har.health_check）。

    返回 {replayable, grade(A/B/None), grade_label, missing[]}；不含任何凭证值。
    """
    entries = _entries_of(har)
    cookies = _cookie_stats(entries)
    login = _find_login(entries)
    reuse_hits = (_token_reuse_hits(entries, login["seq"], login["_token_value"])
                  if login else 0)
    header_auth = _auth_header_present(entries)
    zero_cookie = not any(cookies.values())
    cookie_session = (cookies["response_set_cookie_headers"] > 0
                      and cookies["request_cookie_headers"] > 0)
    auth_visible = reuse_hits > 0 or header_auth > 0

    if not auth_visible and zero_cookie:
        return {"replayable": False, "grade": None, "grade_label": "不可回放",
                "missing": ["鉴权凭证：token 零复用且零 cookie（疑被浏览器导出剥离）"
                            "——请勾选「敏感数据」后重抓 HAR，或换抓包代理采集。"]}
    if not auth_visible and cookie_session:
        return {"replayable": True, "grade": "B", "grade_label": "档B静态",
                "missing": []}
    keys = ({k.lower().replace("-", "_") for k in _post_input_keys(entries[login["seq"]])}
            if login else set())
    if keys & _PASSWORD_KEYS:
        return {"replayable": True, "grade": "A", "grade_label": "档A可重登录",
                "missing": []}
    return {"replayable": True, "grade": "B", "grade_label": "档B静态", "missing": []}


# ── 业务范围识别（移植 intake_har.py:313-375，扩成全 host 端点视图） ──────────

def business_hosts_of(har) -> list:
    """业务 host：login 锚定（∪ token 复用 host）；无 login 退化到业务鉴权头在场兜底。"""
    entries = _entries_of(har)
    login = _find_login(entries)
    hosts: set = set()
    if login is not None:
        h = _entry_host(entries[login["seq"]])
        if h:
            hosts.add(h)
        tok = login["_token_value"]
        for j in range(login["seq"] + 1, len(entries)):
            if _token_reuse_hits([entries[login["seq"]], entries[j]], 0, tok):
                hh = _entry_host(entries[j])
                if hh:
                    hosts.add(hh)
    else:
        for e in entries:
            req = e.get("request") or {}
            for name in _AUTH_HEADER_NAMES:
                v = _header(req.get("headers"), name)
                if v and str(v).strip():
                    hh = _entry_host(e)
                    if hh:
                        hosts.add(hh)
                    break
    return sorted(hosts)


def _domain_of(path: str) -> str:
    """业务域 = 跳过框架前缀（api/v1/...）后的首个有意义路径段。"""
    for seg in [s for s in str(path).split("/") if s]:
        low = seg.lower()
        if low in _FRAMEWORK_SEGMENTS or _VERSION_SEG_RE.match(low):
            continue
        if seg.startswith(":"):
            continue
        return seg
    return "(root)"


def collect_endpoints(har) -> tuple[list, dict]:
    """归并端点：(method, 归一化 path, host) 一条，带次数与状态码分布。

    返回 (endpoints, stats)；endpoints 未排序未截断。
    """
    entries = _entries_of(har)
    business = set(business_hosts_of(har))
    bucket: dict = {}
    noise = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        host = _entry_host(e)
        raw_path = _entry_path(e)
        if _NOISE_PATH_RE.search(raw_path or ""):
            noise += 1
            continue
        key = (host, _entry_method(e), normalize_path(raw_path))
        ep = bucket.setdefault(key, {"host": key[0], "method": key[1], "path": key[2],
                                     "count": 0, "_status": Counter(),
                                     "_entry": None, "_score": (-1, -1)})
        ep["count"] += 1
        status = _entry_status(e)
        ep["_status"][str(status) if status is not None else "无响应"] += 1
        score = _sample_score(e)
        if score > ep["_score"]:        # 同分保留先到者（抓包顺序即业务顺序）
            ep["_score"] = score
            ep["_entry"] = e

    endpoints = []
    for ep in bucket.values():
        endpoints.append({
            "host": ep["host"],
            "method": ep["method"],
            "path": ep["path"],
            "count": ep["count"],
            "statuses": dict(sorted(ep["_status"].items())),
            "business": ep["host"] in business,
            "domain": _domain_of(ep["path"]),
            # 内部字段：build_report 取样后必须 pop 掉，原始 entry 绝不进报告
            "_entry": ep["_entry"],
        })
    endpoints.sort(key=lambda x: (not x["business"], x["host"], x["path"], x["method"]))
    all_hosts = sorted({_entry_host(e) for e in entries
                        if isinstance(e, dict) and _entry_host(e)})
    stats = {
        "entry_count": len(entries),
        "noise_entries_filtered": noise,
        "business_hosts": sorted(business),
        "other_hosts": [h for h in all_hosts if h not in business],
    }
    return endpoints, stats


# ── 鉴权方式识别（只报键名与计数，绝不报值） ────────────────────────────────

def detect_auth_methods(har) -> list:
    """识别鉴权方式。输出只含 kind / location（键名）/ scheme / requests 计数。"""
    entries = _entries_of(har)
    found: dict = {}

    def bump(kind: str, location: str, scheme: str = ""):
        key = (kind, location, scheme)
        found.setdefault(key, {"kind": kind, "location": location,
                               "scheme": scheme, "requests": 0})
        found[key]["requests"] += 1

    for e in entries:
        if not isinstance(e, dict):
            continue
        req = e.get("request") or {}
        auth = _header(req.get("headers"), "authorization")
        if auth and str(auth).strip():
            scheme = str(auth).strip().split(" ", 1)[0]
            scheme = scheme if re.fullmatch(r"[A-Za-z-]{1,20}", scheme) else "自定义"
            bump("bearer_token" if scheme.lower() == "bearer" else
                 "basic_auth" if scheme.lower() == "basic" else "authorization_header",
                 "header:authorization", scheme)
        for name in sorted(_AUTH_HEADER_NAMES - {"authorization"}):
            v = _header(req.get("headers"), name)
            if v and str(v).strip():
                bump("custom_token_header", f"header:{name}")
        if _header(req.get("headers"), "cookie"):
            bump("cookie_session", "header:cookie")
        for qkey in parse_qs(urlsplit(str(req.get("url", ""))).query):
            if _key_is_cred(qkey):
                bump("credential_in_query", f"query:{qkey}")
    methods = sorted(found.values(), key=lambda m: (-m["requests"], m["location"]))
    return methods


# ── 请求样例（M2.5：给模型填用例 request 块的真实素材） ──────────────────────

def _mask_credentials(node):
    """凭证键的值整个换成 <redacted>（键名保留，让模型知道这里要填什么）。

    这是样例的**结构化掩码**，跑在收尾哨兵扫描之前：哨兵扫的是「值恰好等于某个已知
    凭证串」，结构化掩码扫的是「键名像凭证」——两道叠加，短口令、弱 token 也拦得住。
    """
    if isinstance(node, dict):
        return {k: (REDACTED if _key_is_cred(k) else _mask_credentials(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_mask_credentials(v) for v in node]
    return node


def _truncate_raw(body: dict) -> dict:
    raw = body.get("raw") or ""
    if len(raw) > MAX_SAMPLE_BODY_CHARS:
        body["raw"] = raw[:MAX_SAMPLE_BODY_CHARS] + "…(样例截断)"
        body["truncated"] = True
    return body


def _sample_headers(req: dict) -> list:
    """样例请求头：白名单头留原值，凭证头只留键名（值 <redacted>），其余整条丢弃。"""
    out: list = []
    seen: set = set()
    for h in req.get("headers") or []:
        if not isinstance(h, dict):
            continue
        name = str(h.get("name", "")).strip()
        low = name.lower()
        if not low or low.startswith(":") or low in seen:
            continue
        if low in _SAMPLE_HEADER_ALLOW:
            value = str(h.get("value", ""))[:MAX_SAMPLE_HEADER_VALUE]
        elif low in _AUTH_HEADER_NAMES or _key_is_cred(low):
            value = REDACTED
        else:
            continue
        seen.add(low)
        out.append({"key": name, "value": value})
        if len(out) >= MAX_SAMPLE_HEADERS:
            break
    return out


def _merge_pii_hits(sink: dict | None, hits: dict) -> None:
    if sink is None:
        return
    for label, n in hits.items():
        sink[label] = sink.get(label, 0) + n


def _sample_body(req: dict, pii_hits: dict | None = None,
                 sensitive: set | None = None,
                 cred_counter: list | None = None) -> dict:
    """样例请求体：JSON 与表单按结构掩码后回吐，其它类型只报类型不报内容。

    三道叠加，**顺序有意义**（BB-424）：
    1. `_mask_credentials` 按凭证键名掩成 <redacted>；
    2. 凭证哨兵按**精确真值**替换成 <redacted>（`sensitive` 来自
       `collect_sensitive_values`）——必须跑在形态闸之前，否则手机号/邮箱形的
       凭证值会先被形态闸改掉一段，哨兵的精确匹配就落空，凭证残段漏出；
    3. `scrub_payload` 按值形态把个人信息（身份证/手机号/邮箱/长标识/键名像
       姓名的中文姓名等）换成保形占位符。凭证词表只认凭证是 BB-424 的成因，
       个人信息这一半交给出境闸的同一套规则，不另造词表。
    命中数分别汇进 `cred_counter`（凭证）与 `pii_hits`（个人信息）留痕。
    """
    post = req.get("postData") or {}
    text = post.get("text")
    if not text or not str(text).strip():
        return {"mode": "none"}

    def sentinel(node):
        if sensitive and cred_counter is not None:
            return _enforce_redaction(node, sensitive, cred_counter)
        return node

    mime = str(post.get("mimeType") or "").split(";")[0].strip().lower()
    obj = _parse_json(text)
    if isinstance(obj, (dict, list)):
        cleaned, hits = scrub_payload(sentinel(_mask_credentials(obj)))
        _merge_pii_hits(pii_hits, hits)
        return _truncate_raw({
            "mode": "raw", "language": "json",
            "raw": json.dumps(cleaned, ensure_ascii=False),
        })
    if "x-www-form-urlencoded" in mime:
        pairs = []
        for key, values in parse_qs(str(text), keep_blank_values=True).items():
            for value in values:
                if _key_is_cred(key):
                    pairs.append(f"{key}={REDACTED}")
                    continue
                cleaned, hits = scrub_payload(sentinel(value), str(key))
                _merge_pii_hits(pii_hits, hits)
                pairs.append(f"{key}={cleaned}")
        return _truncate_raw({"mode": "raw", "language": "text", "raw": "&".join(pairs)})
    return {"mode": "none",
            "note": f"非 JSON/表单请求体（{mime or '未声明类型'}），不取样"}


def build_sample(entry: dict, endpoint: dict, body_budget: list | None = None,
                 pii_hits: dict | None = None, sensitive: set | None = None,
                 cred_counter: list | None = None) -> dict:
    """从一条 HAR entry 抽请求样例。URL 用**归一化 path**，不带 query 值。

    `body_budget` 是全报告共享的请求体字符预算（`[剩余量]` 的可变单元素列表）：
    见底就只留 method/url/头，不再带体——单条截断挡不住「一百条各 600 字符」。
    `pii_hits` / `sensitive` / `cred_counter` 见 `_sample_body`（三道闸的顺序）。
    """
    req = entry.get("request") or {}
    parts = urlsplit(str(req.get("url", "")))
    scheme = parts.scheme or "https"
    host = endpoint.get("host") or ""
    query_keys = [k for k in parse_qs(parts.query, keep_blank_values=True)
                  if not _key_is_cred(k)][:MAX_SAMPLE_QUERY_KEYS]
    if body_budget is not None and body_budget[0] <= 0:
        body = {"mode": "none", "note": "样例请求体总量已达体积闸，本条不取样"}
    else:
        body = _sample_body(req, pii_hits, sensitive, cred_counter)
        if body_budget is not None:
            body_budget[0] -= len(body.get("raw") or "")
    return {
        "method": endpoint["method"],
        "url": (f"{scheme}://{host}{endpoint['path']}" if host else endpoint["path"]),
        "query_keys": query_keys,
        "headers": _sample_headers(req),
        "body": body,
        "response_status": _entry_status(entry),
    }


def _sample_score(entry: dict) -> tuple:
    """挑样例 entry：优先 2xx，其次带请求体（既成功又有体的最有参考价值）。"""
    status = _entry_status(entry)
    ok = 1 if isinstance(status, int) and 200 <= status < 300 else 0
    has_body = 1 if ((entry.get("request") or {}).get("postData") or {}).get("text") else 0
    return (ok, has_body)


# ── 登录换新：登录请求的可执行描述 + token 取值规则（0.8） ───────────────────
#
# HAR 里录的 token 会过期，靠用户手填环境变量维持不可持续。档A（HAR 里有带账号口令的
# 登录请求）本来就意味着「这条链路可以重登录」——那就把登录请求本身也产出成可执行描述，
# 让 `execute_cases(auth="login")` 每轮先换一次新 token。等价 Postman 的 pre-request auth。
#
# 脱敏纪律与请求样例同源，只多一条：账号与口令的**位置**要标出来（不然执行层不知道
# 往哪填），所以它们的值换成 `{{login_username}}` / `{{login_password}}` 占位而不是
# `<redacted>`——占位是位置信息，不是值。其余凭证键照旧 `<redacted>`。
#
# URL 用 `{{baseUrl}}<归一化 path>` 而不是 HAR 里录到的绝对地址：登录换新要打的是
# **用户在配置页配的那套环境**，不是当初抓包的那台机器。这也让执行层的跨 host 红线
# 天然成立——凭据只会发往用户自己配的 base_url。录到的原始 host 留在 `recorded_host`
# 里供人核对（登录若真在另一个 host 上，重放会 404 失败，而不是把账密发去第三方）。

#: 登录换新不支持时的原因码（如实说，不猜）
LOGIN_NO_REQUEST = "NO_LOGIN_REQUEST"
LOGIN_NO_PASSWORD_FIELD = "NO_PASSWORD_FIELD"
LOGIN_NO_USERNAME_FIELD = "NO_USERNAME_FIELD"
LOGIN_NO_TOKEN_RULE = "NO_TOKEN_RULE"
LOGIN_BODY_TOO_LARGE = "LOGIN_BODY_TOO_LARGE"


def _walk_all_tokens(body, prefix="") -> list:
    """遍历响应体，产出全部「token 形键名 → 够长字符串」的 (点号路径, 值)。

    `_walk_token` 只取第一条（判档够用），这里要全量：备选路径要列进报告让用户能改。
    """
    out: list = []
    if isinstance(body, dict):
        for k, v in body.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if (str(k).lower() in _TOKEN_KEYS and isinstance(v, str)
                    and len(v) >= _MIN_TOKEN_LEN):
                out.append((p, v))
            else:
                out.extend(_walk_all_tokens(v, p))
    elif isinstance(body, list):
        for i, v in enumerate(body):
            out.extend(_walk_all_tokens(v, f"{prefix}[{i}]"))
    return out


def infer_token_extract(entry):
    """从登录响应推断「新 token 怎么取」。推断不出返回 None——不猜。

    只推断 JSON 体里的路径（`$.data.token` / `$.token` / `$.access_token` …）。
    **不推断 Set-Cookie**：登录请求是靠"响应里产出了 token"认出来的（`_find_login`），
    所以凡是被认成登录的请求，其响应体里必有 token 形字段——写一个 Set-Cookie
    兜底分支永远不会被执行到。会话 cookie 型的登录压根不会走到这里（那种 HAR 判档B，
    执行时走环境变量表）。执行层仍认 `set_cookie` 这种规则，用户可以在环境里用
    `auth_token_path=cookie:名字` 显式指定，见 `server/execute.py:extract_login_token`。

    返回 `{source, path, inferred, candidates, override_variable, note}`，
    只含路径与键名，不含任何值。
    """
    body = _parse_json(((entry.get("response") or {}).get("content") or {}).get("text"))
    paths = ["$." + p for p, _ in _walk_all_tokens(body)]
    chosen = next((p for p in _TOKEN_PATH_PREFERENCE if p in paths),
                  paths[0] if paths else "")
    if not chosen:
        return None
    return {
        "source": "json_body",
        "path": chosen,
        "inferred": True,
        "candidates": paths[:MAX_TOKEN_CANDIDATES],
        "override_variable": AUTH_TOKEN_PATH_VAR,
        "note": (f"登录响应的 JSON 里按 {chosen} 取新 token。"
                 f"取错了就在配置页那个环境里加一个 {AUTH_TOKEN_PATH_VAR} 变量覆盖它"
                 f"（填路径如 $.data.token；要从 cookie 取就填 cookie:名字）。"),
    }


def _login_body(req) -> tuple:
    """登录请求体 →（body 描述, 账号字段名, 口令字段名）。认不出口令字段返回 (None, "", "")。

    账号与口令的值换成保留变量占位；其余凭证键仍是 `<redacted>`，非凭证字段照抄形状。
    """
    post = req.get("postData") or {}
    text = post.get("text")
    if not text or not str(text).strip():
        return None, "", ""
    mime = str(post.get("mimeType") or "").split(";")[0].strip().lower()
    user_ph = "{{%s}}" % LOGIN_USERNAME_VAR
    pwd_ph = "{{%s}}" % LOGIN_PASSWORD_VAR

    obj = _parse_json(text)
    if isinstance(obj, dict):
        user_key = pwd_key = ""
        out: dict = {}
        for key, value in obj.items():
            norm = _norm_key(key)
            if not pwd_key and norm in _PASSWORD_KEYS:
                pwd_key, out[key] = str(key), pwd_ph
            elif not user_key and norm in _LOGIN_USERNAME_KEYS:
                user_key, out[key] = str(key), user_ph
            elif _key_is_cred(key):
                out[key] = REDACTED
            else:
                out[key] = _mask_credentials(value)
        if not pwd_key:
            return None, "", ""
        return ({"mode": "raw", "language": "json",
                 "raw": json.dumps(out, ensure_ascii=False)}, user_key, pwd_key)

    if "x-www-form-urlencoded" in mime or (obj is None and "=" in str(text)):
        pairs: list = []
        user_key = pwd_key = ""
        for key, values in parse_qs(str(text), keep_blank_values=True).items():
            norm = _norm_key(key)
            for value in values:
                if not pwd_key and norm in _PASSWORD_KEYS:
                    pwd_key = key
                    pairs.append(f"{key}={pwd_ph}")
                elif not user_key and norm in _LOGIN_USERNAME_KEYS:
                    user_key = key
                    pairs.append(f"{key}={user_ph}")
                elif _key_is_cred(key):
                    pairs.append(f"{key}={REDACTED}")
                else:
                    pairs.append(f"{key}={value}")
        if not pwd_key:
            return None, "", ""
        return ({"mode": "raw", "language": "text", "raw": "&".join(pairs)},
                user_key, pwd_key)

    return None, "", ""


def login_credential_values(har) -> set:
    """登录请求体里账号与口令的**真值**，喂给收尾的脱敏哨兵扫描。

    口令本来就被凭证词表收走了（`password` 之类是凭证键），账号不是——`username`
    不像凭证，`collect_sensitive_values` 收不到它，于是它会原样留在那条登录端点的
    请求样例里。0.8 起账号是登录换新的正式凭据（保留变量 `login_username`），
    那它就得跟口令同等对待：整份报告里都不许出现。

    只收够长的值（`_MIN_SENSITIVE_LEN`）——`alice` 这种短账号满文本替换会误伤。
    """
    entries = _entries_of(har)
    login = _find_login(entries)
    if login is None:
        return set()
    post = ((entries[login["seq"]].get("request") or {}).get("postData") or {})
    text = post.get("text")
    if not text or not str(text).strip():
        return set()
    pairs: list = []
    obj = _parse_json(text)
    if isinstance(obj, dict):
        pairs = [(k, v) for k, v in obj.items() if isinstance(v, str)]
    else:
        try:
            pairs = [(k, v) for k, values in parse_qs(str(text),
                                                      keep_blank_values=True).items()
                     for v in values]
        except Exception:  # noqa: BLE001 - 拆不开就当没有账号字段，不影响其它防线
            return set()
    out: set = set()
    for key, value in pairs:
        norm = _norm_key(key)
        if (norm in _LOGIN_USERNAME_KEYS or norm in _PASSWORD_KEYS) \
                and len(value) >= _MIN_SENSITIVE_LEN:
            out.add(value)
    return out


def _unsupported(code: str, reason: str) -> dict:
    return {"auth_refresh": {"supported": False, "code": code, "reason": reason}}


def login_replay_plan(har) -> dict:
    """登录换新的可行性与可执行描述 → `{auth_refresh[, login_request]}`。

    这一段直接进报告的 `replay` 里。**不支持就说不支持**（带原因码与人话理由），
    绝不给一个半成品的 `login_request` 让执行层去猜。
    """
    entries = _entries_of(har)
    login = _find_login(entries)
    if login is None:
        return _unsupported(
            LOGIN_NO_REQUEST,
            "HAR 里没有识别到「提交后响应里产出 token」的登录请求，"
            "所以做不了登录换新——执行时用配置页环境里配好的 token 变量。")

    entry = entries[login["seq"]]
    req = entry.get("request") or {}
    body, user_key, pwd_key = _login_body(req)
    if body is None:
        keys = _post_input_keys(entry)
        return _unsupported(
            LOGIN_NO_PASSWORD_FIELD,
            "找到了登录请求，但它的请求体里没有口令字段"
            + (f"（字段只有：{'、'.join(keys[:8])}）" if keys
               else "（请求体为空或不是 JSON/表单）")
            + "——可能是验证码、扫码或 SSO 登录，重放不了。执行时用环境里配好的 token 变量。")
    if not user_key:
        keys = _post_input_keys(entry)
        return _unsupported(
            LOGIN_NO_USERNAME_FIELD,
            f"登录请求体里认出了口令字段「{pwd_key}」，但认不出账号字段"
            + (f"（其余字段：{'、'.join(k for k in keys[:8] if k != pwd_key)}）"
               if keys else "")
            + "——不猜哪个是账号。执行时用环境里配好的 token 变量。")
    if len(body.get("raw") or "") > MAX_SAMPLE_BODY_CHARS:
        return _unsupported(
            LOGIN_BODY_TOO_LARGE,
            f"登录请求体超过 {MAX_SAMPLE_BODY_CHARS} 字符，截断后就不是合法请求了，"
            "不产出可执行描述。执行时用环境里配好的 token 变量。")

    rule = infer_token_extract(entry)
    if rule is None:
        return _unsupported(
            LOGIN_NO_TOKEN_RULE,
            "登录请求能重放，但从登录响应里推断不出新 token 的取值规则"
            "（JSON 体里没有 token 形字段）——不支持自动登录换新。"
            f"想手工指定就在环境里加 {AUTH_TOKEN_PATH_VAR} 变量，"
            "填 $.data.token 这样的路径（从 cookie 取就填 cookie:名字）。")

    path = normalize_path(_entry_path(entry))
    headers = [h for h in _sample_headers(req) if h["value"] != REDACTED]
    if not any(h["key"].lower() == "content-type" for h in headers):
        headers.insert(0, {"key": "Content-Type",
                           "value": ("application/json" if body["language"] == "json"
                                     else "application/x-www-form-urlencoded")})
    query_keys = [k for k in parse_qs(urlsplit(str(req.get("url", ""))).query,
                                      keep_blank_values=True)
                  if not _key_is_cred(k)][:MAX_SAMPLE_QUERY_KEYS]

    login_request = {
        "method": _entry_method(entry),
        # 打的是「执行时选的那个环境」，不是抓包时那台机器——见本节顶部注释
        "url": "{{%s}}%s" % (BASE_URL_VAR, path),
        "path": path,
        "recorded_host": _entry_host(entry),
        "query_keys": query_keys,
        "headers": headers,
        "body": body,
        "credential_fields": {"username": user_key, "password": pwd_key},
        "credential_variables": {"username": LOGIN_USERNAME_VAR,
                                 "password": LOGIN_PASSWORD_VAR},
        "token_extract": rule,
        "response_status": _entry_status(entry),
        "note": ("这是登录请求的可执行描述：账号与口令的位置用保留变量占位标出，"
                 "真值不在这里。把它原样传给 execute_cases 的 login_request 参数、"
                 "并设 auth=\"login\"，每轮执行前会先重放它换一个新 token。"),
    }
    return {
        "auth_refresh": {
            "supported": True,
            "login_endpoint": f"{login_request['method']} {path}",
            "token_variable_default": "token",
            "reserved_variables": [LOGIN_USERNAME_VAR, LOGIN_PASSWORD_VAR,
                                   AUTH_TOKEN_PATH_VAR],
            "hint": (f"用户只需在配置页 http://localhost:3789 的「测试环境」里配一次 "
                     f"{LOGIN_USERNAME_VAR} 与 {LOGIN_PASSWORD_VAR} 两个变量，"
                     "执行时传 auth=\"login\"，token 每轮自动换新，"
                     "不用再手工贴过期的 token。"),
        },
        "login_request": login_request,
    }


# ── 脱敏哨兵集（移植 intake_har.py:380-457） ─────────────────────────────────

def collect_sensitive_values(har) -> set:
    """收集全部「凭证键对应的值」+ query 值 + token 形值，作泄漏哨兵集。"""
    found: set = set()

    def add(v):
        if isinstance(v, str) and len(v) >= _MIN_SENSITIVE_LEN:
            found.add(v)
            if " " in v:            # "Bearer xxx"：裸 token 段也进哨兵集
                tail = v.split(" ", 1)[1].strip()
                if len(tail) >= _MIN_SENSITIVE_LEN:
                    found.add(tail)

    def add_all_strings(node):
        """凭证键之下整棵子树的字符串全收（容器里的深层 sign 不能漏）。"""
        if isinstance(node, str):
            add(node)
        elif isinstance(node, dict):
            for v in node.values():
                add_all_strings(v)
        elif isinstance(node, list):
            for v in node:
                add_all_strings(v)

    def add_cred_header(value):
        """凭证头值：整串入集，并按 `;`/`,` 拆对取 `=` 右值。

        跳过 Set-Cookie 属性（Path/Domain/...）与路径形值——把 `Path=/api/v1/orders`
        的右值收进哨兵，端点段就会被自己的哨兵判成泄漏（fail-closed 不等于 fail-wrong）。
        """
        add(value)
        if not isinstance(value, str):
            return
        for piece in re.split(r"[;,]", value):
            piece = piece.strip()
            if "=" not in piece:
                continue
            key, _, val = piece.partition("=")
            val = val.strip()
            if key.strip().lower() in _COOKIE_ATTR_KEYS:
                continue
            if val.startswith("/") or val.lower().startswith("http"):
                continue        # 路径/URL 形值：不是凭证，收了会误伤端点段
            add(val)

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if _key_is_cred(k):
                    add_all_strings(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for e in _entries_of(har):
        if not isinstance(e, dict):
            continue
        req = e.get("request") or {}
        resp = e.get("response") or {}
        for h in (req.get("headers") or []) + (resp.get("headers") or []):
            if isinstance(h, dict) and _key_is_cred(h.get("name", "")):
                add_cred_header(h.get("value"))
        for c in (req.get("cookies") or []) + (resp.get("cookies") or []):
            if isinstance(c, dict):
                add(c.get("value"))
        for vals in parse_qs(urlsplit(str(req.get("url", ""))).query).values():
            for v in vals:
                add(v)
        walk(_parse_json((req.get("postData") or {}).get("text")))
        walk(_parse_json((resp.get("content") or {}).get("text")))
        tok = _walk_token(_parse_json((resp.get("content") or {}).get("text")))
        if tok:
            add(tok[1])
    return found


def _enforce_redaction(node, sensitive: set, counter: list):
    """就地替换：凭证值一旦出现在报告任何字符串里就换成 <redacted> 并计数。

    对应 blackbox 的 `assert_sanitized`（intake_har.py:460-467）——那边抛错拒产底稿，
    这边替换后继续，因为 MCP 工具的返回值直接给模型看，硬约束是「不出现」。
    """
    if isinstance(node, str):
        out = node
        for value in sensitive:
            if value and value in out:
                out = out.replace(value, "<redacted>")
                counter[0] += 1
        return out
    if isinstance(node, dict):
        return {k: _enforce_redaction(v, sensitive, counter) for k, v in node.items()}
    if isinstance(node, list):
        return [_enforce_redaction(v, sensitive, counter) for v in node]
    return node


# ── 输入解析 ────────────────────────────────────────────────────────────────

def resolve_har_path(har_path: str) -> str:
    """宿主机绝对路径直接用；相对路径按 `inbox/` 下的文件名解析（禁止跳出 inbox）。"""
    raw = str(har_path).strip().strip('"')
    if not raw:
        raise HarInputError("HAR_PATH_EMPTY", "har_path 为空")
    if os.path.isabs(raw):
        path = os.path.normpath(raw)
    else:
        path = os.path.normpath(os.path.join(INBOX_DIR, raw))
        if os.path.commonpath([os.path.abspath(path), INBOX_DIR]) != INBOX_DIR:
            raise HarInputError(
                "HAR_PATH_ESCAPES_INBOX",
                f"相对路径只能指向 inbox/ 内的文件，不能跳出：{raw}",
                "把 HAR 放进仓库根的 inbox/ 目录，或改传宿主机绝对路径。")
    if not os.path.exists(path):
        raise HarInputError(
            "HAR_FILE_NOT_FOUND",
            f"找不到 HAR 文件：{path}",
            f"确认文件已放进 {INBOX_DIR}（相对路径按此目录解析），或改传绝对路径。")
    if not os.path.isfile(path):
        raise HarInputError("HAR_PATH_NOT_FILE", f"路径不是文件：{path}")
    return path


def load_har(har_path: str = "", har_content: str = "") -> tuple[Any, dict]:
    """读入 HAR，返回 (har 对象, source 元信息)。异常一律是 HarInputError。"""
    if har_content and str(har_content).strip():
        text = str(har_content)
        source = {"kind": "content", "name": "(inline)"}
    elif har_path and str(har_path).strip():
        path = resolve_har_path(har_path)
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            raise HarInputError("HAR_FILE_UNREADABLE", f"HAR 文件读不出来：{exc}") from exc
        source = {"kind": "path", "name": os.path.basename(path), "path": path}
    else:
        raise HarInputError(
            "HAR_INPUT_MISSING", "har_path 与 har_content 都为空",
            "把 .har 放进仓库根的 inbox/ 后传文件名，或传宿主机绝对路径，"
            "小 HAR 也可以直接把内容贴进 har_content。")

    try:
        har = json.loads(text)
    except ValueError as exc:
        raise HarInputError(
            "HAR_JSON_BROKEN", f"HAR 不是合法 JSON：{exc}",
            "确认是浏览器 DevTools「Export HAR」导出的完整文件，没有被截断或二次编辑。") from exc
    entries = _entries_of(har)
    if not isinstance(har, (dict, list)):
        raise HarInputError("HAR_SHAPE_INVALID", "HAR 顶层既不是对象也不是数组")
    if isinstance(har, dict) and "log" not in har and not isinstance(har.get("entries"), list):
        raise HarInputError(
            "HAR_NOT_A_HAR", "这个 JSON 里没有 HAR 的 log 结构",
            "HAR 文件应形如 {\"log\": {\"entries\": [...]}}。")
    if not entries:
        raise HarInputError(
            "HAR_EMPTY", "HAR 里没有任何请求条目（log.entries 为空）",
            "重抓一次流量，确认录制期间确实有请求发生。")
    source["bytes"] = len(text.encode("utf-8", "ignore"))
    source["fingerprint"] = "sha256:" + hashlib.sha256(
        json.dumps(har, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()
    return har, source


# ── 报告组装 ────────────────────────────────────────────────────────────────

def _time_span(entries) -> dict:
    stamps = sorted(str(e.get("startedDateTime")) for e in entries
                    if isinstance(e, dict) and e.get("startedDateTime"))
    if not stamps:
        return {}
    return {"first": stamps[0], "last": stamps[-1]}


def build_report(har, source: dict, max_endpoints: int = DEFAULT_MAX_ENDPOINTS,
                 include_samples: bool = True,
                 max_samples: int = DEFAULT_MAX_SAMPLES) -> dict:
    entries = _entries_of(har)
    endpoints, stats = collect_endpoints(har)
    health = health_check(har)
    health.update(login_replay_plan(har))       # replay.auth_refresh [+ login_request]
    auth = detect_auth_methods(har)

    domains: dict = {}
    for ep in endpoints:
        d = domains.setdefault(ep["domain"], {"domain": ep["domain"], "endpoint_count": 0,
                                              "request_count": 0, "samples": []})
        d["endpoint_count"] += 1
        d["request_count"] += ep["count"]
        if len(d["samples"]) < DOMAIN_SAMPLE_LIMIT:
            d["samples"].append(f"{ep['method']} {ep['path']}")
    domain_list = sorted(domains.values(),
                         key=lambda d: (-d["request_count"], d["domain"]))
    total_domains = len(domain_list)
    shown_domains = domain_list[:MAX_DOMAINS_LISTED]

    total_endpoints = len(endpoints)
    shown = endpoints[:max(1, int(max_endpoints or DEFAULT_MAX_ENDPOINTS))]
    truncated = None
    if total_endpoints > len(shown):
        truncated = {
            "endpoints_total": total_endpoints,
            "endpoints_shown": len(shown),
            "note": (f"端点过多，只列前 {len(shown)} 条（业务 host 优先）。"
                     f"业务域分组的计数覆盖全部 {total_endpoints} 条；"
                     "需要完整清单请提高 max_endpoints 或按业务域分批体检。"),
        }
    if total_domains > len(shown_domains):
        truncated = dict(truncated or {})
        truncated["domains_total"] = total_domains
        truncated["domains_shown"] = len(shown_domains)
        truncated["domains_note"] = (
            f"业务域过多，只列请求量前 {len(shown_domains)} 个域，"
            f"略去 {total_domains - len(shown_domains)} 个；"
            "summary.domain_count 仍是全部域数。")
    sample_cap = max(0, int(max_samples if max_samples is not None else DEFAULT_MAX_SAMPLES))
    body_budget = [MAX_SAMPLE_BODY_BUDGET]
    pii_hits: dict = {}
    # 凭证哨兵集要在取样**之前**算好：样例体内先跑精确值哨兵、再跑 PII 形态闸
    # （顺序理由见 _sample_body）；收尾对整份报告再跑一遍哨兵兜底，同一个计数器。
    sensitive = collect_sensitive_values(har) | login_credential_values(har)
    cred_counter = [0]
    sampled = 0
    for ep in shown:
        ep.pop("business", None)
        entry = ep.pop("_entry", None)
        if include_samples and isinstance(entry, dict) and sampled < sample_cap:
            ep["sample"] = build_sample(entry, ep, body_budget, pii_hits,
                                        sensitive, cred_counter)
            sampled += 1
    for ep in endpoints:                # 未展示的端点也把原始 entry 摘干净
        ep.pop("_entry", None)
    if include_samples and sampled < len(shown):
        truncated = dict(truncated or {})
        truncated["samples_shown"] = sampled
        truncated["samples_note"] = (
            f"请求样例只给前 {sampled} 个端点（体积闸 max_samples）；"
            "其余端点只有端点行，没有样例——要给它们写 request 块就先分批体检。")

    report = {
        "ok": True,
        "schema": REPORT_SCHEMA,
        "source": source,
        "summary": {
            "entry_count": stats["entry_count"],
            "endpoint_count": total_endpoints,
            "noise_entries_filtered": stats["noise_entries_filtered"],
            "domain_count": len(domain_list),
            "endpoints_with_sample": sampled,
            "time_span": _time_span(entries),
        },
        "hosts": {
            "business": stats["business_hosts"][:MAX_HOSTS_LISTED],
            "other": stats["other_hosts"][:MAX_HOSTS_LISTED],
            "business_resolved": bool(stats["business_hosts"]),
        },
        "replay": health,
        "auth": {
            "methods": auth,
            "resolved": bool(auth),
            "note": ("只报鉴权方式与键名，凭证值一律不出报告。"
                     if auth else
                     "未识别到任何鉴权在场——可能是未登录流量，或导出时被浏览器剥离了敏感数据。"),
        },
        "domains": shown_domains,
        "endpoints": shown,
        "truncated": truncated,
        "redaction": {
            "policy": REDACTION_DECLARATION,
            "sensitive_values_detected": 0,
            "enforced_substitutions": 0,
            # 样例体里按形态识别的个人信息命中数（BB-424 留痕：脱了多少、什么
            # 类型要说得出，静默替换会让读者以为原值还在）。空 dict = 没命中。
            "pii_hits": pii_hits,
        },
    }

    # 账号也算凭据（0.8 起它是保留变量 login_username）——凭证词表收不到它。
    # 哨兵集与计数器在取样前已建好（样例体内先跑过一遍），这里对整份报告兜底再扫。
    report = _enforce_redaction(report, sensitive, cred_counter)
    report["redaction"]["sensitive_values_detected"] = len(sensitive)
    report["redaction"]["enforced_substitutions"] = cred_counter[0]
    return report


def parse_har_report(har_path: str = "", har_content: str = "",
                     max_endpoints: int = DEFAULT_MAX_ENDPOINTS,
                     include_samples: bool = True,
                     max_samples: int = DEFAULT_MAX_SAMPLES) -> dict:
    """`parse_har` 工具的主入口：出错返回带 error 字段的可读结果，不抛裸异常。"""
    try:
        har, source = load_har(har_path=har_path, har_content=har_content)
        return build_report(har, source, max_endpoints=max_endpoints,
                            include_samples=include_samples, max_samples=max_samples)
    except HarInputError as exc:
        return {"ok": False, "schema": REPORT_SCHEMA, "error": exc.code,
                "message": exc.message, "hint": exc.hint,
                "inbox_dir": INBOX_DIR}
    except Exception as exc:  # noqa: BLE001 - 工具边界收口，不把裸异常抛给 agent
        return {"ok": False, "schema": REPORT_SCHEMA, "error": "HAR_PARSE_FAILED",
                "message": f"体检过程出错：{type(exc).__name__}: {exc}",
                "hint": "HAR 结构可能不标准；可换一个导出工具重抓，或把小样本贴进 har_content 复现。",
                "inbox_dir": INBOX_DIR}
