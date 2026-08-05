"""用例确定性校验（`validate_cases` 工具的实现层）。

规格来源：`docs/PLAN.md` M2「用例格式规格」（2026-08-04 用户拍板）。
**本模块是规则的唯一权威**：`skills/har-testing/SKILL.md` 的写作纪律逐条引用这里的
规则编号，禁止剧本说一套、校验拦另一套。改规则时两处同改。

执行声明词表移植自 blackbox-test-agent `backend/workflow_contract.py:37-41`
（`_EXECUTION_CLAIM_RE`，判定码 `TEST_PLAN_EXECUTION_CLAIM_FORBIDDEN`），
本仓另加一组「未来时自述调用」模式（`将调用` / `接下来我会` 之类）。

字段分层（PLAN M2）：
- 聊天展示 = 精简六字段：编号 / 标题 / 前置条件 / 操作步骤 / 预期结果 / 优先级；
- 落盘导出 = 全字段，另带 所属模块 / 用例类型 / 测试数据 / 关联端点。

M2.5「用例可执行化」新增**可选** `request` 块（有则按下表校验，没有仍然合法——
人执行用例不强求结构化请求）：

    request: {
      method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS",
      url: str,                       # 可含 {{baseUrl}} 变量；host/path 须与关联端点一致
      headers: [{key, value}],        # 可选；凭证值一律写 <redacted> 或 {{token}} 变量
      body: {mode: "raw"|"none", raw: str, language: "json"},   # 可选
      assertions: [{type: "status"|"json_path"|"body_contains", expected, path?}],
    }

`request` 块是 Postman/Apifox 产物的唯一素材来源（`server/postman.py` 逐字段翻译），
所以这里的规则同时是导出层的前置条件：翻不动的断言在这里就要拦住。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from server import args_tolerance
from server.har_parse import (REDACTED, _AUTH_HEADER_NAMES, _key_is_cred,
                              normalize_path)

SCHEMA = "test-partner.case-validation/v1"

PRIORITIES = ("高", "中", "低")
#: 场景级粒度启发式上限（PLAN M2：一个业务场景一条用例，步骤内多断言）
MAX_STEPS = 15
MIN_STEPS = 1
#: 未覆盖端点清单在输出里的条数上限（体积控制）
MAX_UNCOVERED_LISTED = 60

# ── request 块（M2.5 可执行化） ─────────────────────────────────────────────
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
#: 断言类型 —— 与 `server/postman.py` 的翻译分支一一对应，加类型时两处同改
ASSERTION_TYPES = ("status", "json_path", "body_contains")
BODY_MODES = ("raw", "none")
#: 单个占位原子（这些形态里装不下真凭证）：`{{token}}` 变量、`${VAR}` 变量、
#: `<redacted>` 之类尖括号占位、`***` 星号掩码
_PLACEHOLDER_ATOM_RE = re.compile(r"^(?:\{\{[^{}]+\}\}|\$\{[^{}]+\}|<[^<>]*>|\*+)$")
#: 认证 scheme —— `Bearer {{token}}` 是 Postman 里的正确写法，不是明文凭证
_AUTH_SCHEMES = {"bearer", "basic", "token", "jwt", "digest", "negotiate",
                 "hmac", "oauth", "apikey", "api-key", "mac"}
#: 凭证值最短长度——短于此的值判不出是不是凭证，不误伤（与 har_parse 哨兵同口径）
_MIN_SENSITIVE_LEN = 8

#: 六字段 → 可接受的输入键名（中英同收，模型怎么写都认）
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "case_id": ("case_id", "编号", "用例编号", "id", "case_no", "no"),
    "title": ("title", "标题", "用例名称", "name", "用例标题"),
    "preconditions": ("preconditions", "precondition", "前置条件", "前置", "pre"),
    "steps": ("steps", "操作步骤", "用例步骤", "步骤", "step"),
    "expected": ("expected", "expected_result", "预期结果", "预期", "期望结果"),
    "priority": ("priority", "优先级", "用例等级", "level"),
    # 落盘全字段（不参与六字段必填校验）
    "module": ("module", "所属模块", "模块", "belong_module"),
    "case_type": ("case_type", "用例类型", "类型", "type"),
    "test_data": ("test_data", "测试数据", "数据", "data"),
    "endpoints": ("endpoints", "endpoint", "关联端点", "接口", "关联接口", "api"),
    # 可选的可执行请求块（M2.5）：原样保留对象，不做文本化
    "request": ("request", "请求", "请求块", "req", "http_request"),
}

CORE_FIELDS = ("case_id", "title", "preconditions", "steps", "expected", "priority")
#: 执行声明扫描覆盖的字段（六字段全覆盖 + 测试数据）
_CLAIM_SCAN_FIELDS = ("title", "preconditions", "steps", "expected", "test_data")

# ── 执行声明词表 ────────────────────────────────────────────────────────────
#: 过去时自报执行/判绿——移植 blackbox workflow_contract.py:37-41，一处收窄：
#: 原式的「(?:测试|验证)?已经?通过」里前缀可选，会误伤「已通过登录接口拿到 token」
#: 这类正常的前置条件措辞；本仓把前缀改成必需，只拦「测试/验证已通过」的判绿自报。
_EXEC_CLAIM_PAST_RE = re.compile(
    r"(?<!未)(?<!尚未)(?:(?:已经?|实际)(?:执行|测试|验证)(?:完成)?|"
    r"(?:测试|验证)已经?通过|\bPASS(?:ED)?\b)",
    re.IGNORECASE,
)
#: 未来时自述工具调用（本仓扩展，只收第一人称/明确承诺的形态，避免误伤
#: 「系统将扣款」这类正常的预期结果措辞）
_EXEC_CLAIM_FUTURE_RE = re.compile(
    r"(?:接下来|下一步|随后)[^\n]{0,6}(?:我|我们)(?:会|将|要)"
    r"|(?:我|我们)(?:现在|马上|接下来|稍后)?(?:会|将|要|即将)?(?:调用|执行|运行|发起)"
    r"|(?:将|即将)调用"
)

#: 规则编号 → 人话说明（SKILL.md 引用同一份编号）
RULE_TEXT = {
    "E01_SHAPE": "用例不是一个对象（应为 JSON 对象/字典）",
    "E02_ID_EMPTY": "编号缺失或为空",
    "E03_ID_DUPLICATE": "编号与前面的用例重复（编号必须唯一）",
    "E04_TITLE_EMPTY": "标题缺失或为空",
    "E05_PRECONDITION_MISSING": "前置条件字段缺失（没有前置就显式写「无」）",
    "E06_STEPS_EMPTY": f"操作步骤为空（至少 {MIN_STEPS} 步）",
    "E07_EXPECTED_EMPTY": "预期结果缺失或为空",
    "E08_PRIORITY_INVALID": f"优先级不在 {{{'/'.join(PRIORITIES)}}} 之内",
    "E09_EXECUTION_CLAIM": "出现执行声明措辞（用例是待执行的计划，不得自称已执行/将调用）",
    "E10_REQUEST_SENSITIVE": ("请求块里出现疑似明文凭证（脱敏哨兵复检不过）"
                              f"——凭证值一律写成 {REDACTED} 或 {{{{token}}}} 变量"
                              "（`Bearer {{token}}` 这种 scheme + 占位的写法本就合规，不会被拦）"),
    "E11_REQUEST_METHOD_INVALID": f"请求块 method 不合法（只能是 {'/'.join(HTTP_METHODS)}）",
    "E12_REQUEST_URL_EMPTY": "请求块 url 缺失或为空",
    "E13_REQUEST_URL_MISMATCH": "请求块 url 的 host/path 与关联端点对不上（不许编造端点）",
    "E14_REQUEST_SHAPE": "请求块结构不对（request 必须是对象，headers/assertions 必须是数组）",
    "E15_ASSERTION_INVALID": (f"断言写法不合法（type 只能是 {'/'.join(ASSERTION_TYPES)}；"
                              "status 的 expected 是整数状态码；json_path 必须给 path）"),
    "W01_PRECONDITION_EMPTY": "前置条件为空字符串，建议显式写「无」",
    "W02_STEPS_TOO_MANY": f"步骤数超过场景级粒度上限 {MAX_STEPS}，建议拆成多条用例",
    "W03_ENDPOINT_UNKNOWN": "关联端点不在体检报告的端点清单里（不要给用例编造端点）",
    "W04_ENDPOINT_UNCOVERED": "体检报告里的这些端点没有任何用例覆盖",
    "W05_REQUEST_NO_ASSERTION": "请求块没有任何断言，导出的 Postman 用例跑完无从判定成败",
}

_ENDPOINT_REF_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s,，;；、）)\]】\"']*)",
    re.IGNORECASE,
)


def _pick(case: dict, field: str):
    for alias in FIELD_ALIASES[field]:
        if alias in case:
            return case[alias], alias
        for key in case:
            if isinstance(key, str) and key.strip().lower() == alias.lower():
                return case[key], key
    return None, None


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(_as_text(v) for v in value if _as_text(v))
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_as_text(v)}" for k, v in value.items())
    return str(value).strip()


def _as_steps(value) -> list:
    """步骤统一成字符串列表：列表原样收，字符串按换行/编号前缀拆。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [_as_text(v) for v in value if _as_text(v)]
    text = _as_text(value)
    if not text:
        return []
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def _as_endpoint_refs(value) -> list:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[;\n,，、]", str(value))
    return [str(i).strip() for i in items if str(i).strip()]


def endpoint_key(method: str, path: str) -> str:
    return f"{str(method).upper().strip()} {normalize_path(path)}"


def _parse_endpoint_ref(ref: str):
    """把 'GET /api/v1/orders/123' 或 '/api/v1/orders' 解析成归一化键。"""
    ref = str(ref).strip()
    if not ref:
        return None
    m = _ENDPOINT_REF_RE.search(ref)
    if m:
        return endpoint_key(m.group(1), m.group(2))
    parts = ref.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in {
            "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return endpoint_key(parts[0], parts[1])
    if ref.startswith("/"):
        return endpoint_key("", ref).strip()
    return None


def normalize_case(case: Any, index: int) -> dict:
    """把一条用例归一成内部形态；`_shape_ok=False` 表示输入根本不是对象。"""
    if not isinstance(case, dict):
        return {"_shape_ok": False, "_index": index,
                "case_id": f"#{index + 1}", "_present": set()}
    out: dict = {"_shape_ok": True, "_index": index, "_present": set()}
    for field in FIELD_ALIASES:
        value, alias = _pick(case, field)
        if alias is not None:
            out["_present"].add(field)
        if field == "steps":
            out[field] = _as_steps(value)
        elif field == "endpoints":
            out[field] = _as_endpoint_refs(value)
        elif field == "request":
            out[field] = value          # 结构原样保留，形状对不对交给 E14
        else:
            out[field] = _as_text(value)
    if not out.get("case_id"):
        out["case_id"] = f"#{index + 1}"
        out["_id_missing"] = True
    return out


def normalize_cases(cases: Any) -> list:
    if not isinstance(cases, (list, tuple)):
        return []
    return [normalize_case(c, i) for i, c in enumerate(cases)]


def _scan_execution_claim(text: str):
    for regex, label in ((_EXEC_CLAIM_PAST_RE, "过去时自报执行/判绿"),
                         (_EXEC_CLAIM_FUTURE_RE, "未来时自述调用工具")):
        m = regex.search(text or "")
        if m:
            return m.group(0), label
    return None, None


def _endpoint_index(endpoints: Any) -> dict:
    """接受 parse_har 的端点清单（dict 列表）或 'GET /path' 字符串列表。"""
    index: dict = {}
    if not isinstance(endpoints, (list, tuple)):
        return index
    for ep in endpoints:
        if isinstance(ep, dict):
            method = ep.get("method") or ep.get("Method") or ""
            path = ep.get("path") or ep.get("Path") or ""
            if not path:
                continue
            key = endpoint_key(method, path)
            index[key] = f"{str(method).upper()} {normalize_path(path)}".strip()
        elif isinstance(ep, str):
            key = _parse_endpoint_ref(ep)
            if key:
                index[key] = key
    return index


def _endpoint_hosts(endpoints: Any) -> set:
    """体检报告端点清单里出现过的 host 集合（用于 request.url 的 host 一致性）。"""
    hosts: set = set()
    if not isinstance(endpoints, (list, tuple)):
        return hosts
    for ep in endpoints:
        if isinstance(ep, dict) and ep.get("host"):
            hosts.add(str(ep["host"]).strip().lower())
    return hosts


_URL_VAR_PREFIX_RE = re.compile(r"^\{\{[^{}]+\}\}")


def split_request_url(url: str) -> tuple:
    """request.url → (host 或 None, path, query 字符串)。

    收三种写法：`{{baseUrl}}/api/v1/orders?page=1`、绝对 URL、裸路径。
    变量前缀形态返回 host=None（host 一致性无从校验，也没必要校验）。
    """
    raw = str(url or "").strip()
    if not raw:
        return None, "", ""
    if _URL_VAR_PREFIX_RE.match(raw):
        rest = _URL_VAR_PREFIX_RE.sub("", raw, count=1)
        before, _, query = rest.partition("?")
        return None, before, query
    if "://" in raw:
        parts = urlsplit(raw)
        try:
            host, port = parts.hostname or "", parts.port
        except ValueError:
            host, port = None, None
        host_text = (f"{host}:{port}" if host and port else host) or None
        return host_text, parts.path, parts.query
    before, _, query = raw.partition("?")
    return None, before, query


def is_placeholder_value(value: Any) -> bool:
    """值是不是「占位」而非真凭证 —— E10 的放行判据。

    放行四类（都装不下真凭证，拦了纯属误伤，还会逼模型把正确写法改坏）：
    1. 变量引用：`{{token}}`、`{{ authToken }}`、`${API_KEY}`；
    2. 尖括号占位：`<redacted>`、`<your-token>`；
    3. 星号掩码：`***`、`********`；
    4. 认证 scheme + 上面任一占位：`Bearer {{token}}`、`Basic <redacted>`、`Token ***`。

    真凭证特征照拦：`Bearer eyJhbGciOi...`（scheme 后面跟的是真值，不是占位）、
    长随机串、明文口令——它们都过不了占位原子的整值匹配。
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    if _PLACEHOLDER_ATOM_RE.match(text):
        return True
    parts = text.split()
    if len(parts) == 2 and parts[0].rstrip(":").lower() in _AUTH_SCHEMES:
        return bool(_PLACEHOLDER_ATOM_RE.match(parts[1]))
    return False


def _looks_sensitive(value: Any) -> bool:
    """值是不是「疑似明文凭证」：字符串、非空、非占位、长到像个凭证。

    数字与布尔值一律放行——`{"token_expires_in": 7200}` 这类键名带 token 的正常字段
    不该被判成泄漏（fail-closed 不等于 fail-wrong，与 har_parse 的哨兵同一条口径）。
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or is_placeholder_value(text):
        return False
    return len(text) >= _MIN_SENSITIVE_LEN


def _scan_credential_literals(node: Any, path: str = "", under_cred: bool = False) -> list:
    """递归找「凭证键之下的明文叶子值」。返回 [(字段路径, 键名)]。"""
    hits: list = []
    if isinstance(node, dict):
        for key, value in node.items():
            where = f"{path}.{key}" if path else str(key)
            hits.extend(_scan_credential_literals(
                value, where, under_cred or _key_is_cred(key)))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits.extend(_scan_credential_literals(value, f"{path}[{i}]", under_cred))
    elif under_cred and _looks_sensitive(node):
        hits.append((path, path.split(".")[-1] or path))
    return hits


def _request_texts(request: dict) -> list:
    """请求块里所有会被写进产物的字符串（供哨兵集逐值复检）。"""
    texts = [str(request.get("url") or "")]
    for h in request.get("headers") or []:
        if isinstance(h, dict):
            texts.append(str(h.get("value", "")))
        else:
            texts.append(str(h))
    body = request.get("body")
    if isinstance(body, dict) and body.get("raw") is not None:
        raw = body["raw"]
        texts.append(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
    return texts


def _validate_request(case: dict, ep_index: dict, ep_hosts: set,
                      sensitive_values: Any, err, warn) -> list:
    """校验可选的 request 块，返回它锚定的端点键（供覆盖率合并）。E10-E15 / W05。"""
    cid = case["case_id"]
    request = case.get("request")
    if request is None and "request" not in case["_present"]:
        return []                                       # 没写 request 块：合法
    if not isinstance(request, dict) or not request:
        err(cid, "请求块", "E14_REQUEST_SHAPE", "request 不是非空对象")
        return []

    # ── method ──
    method = str(request.get("method") or "").strip().upper()
    if method not in HTTP_METHODS:
        err(cid, "请求块.method", "E11_REQUEST_METHOD_INVALID",
            f"当前值「{request.get('method')}」" if request.get("method") else "当前为空")

    # ── url ──
    url = str(request.get("url") or "").strip()
    keys: list = []
    if not url:
        err(cid, "请求块.url", "E12_REQUEST_URL_EMPTY")
    else:
        host, path, _query = split_request_url(url)
        if not path:
            err(cid, "请求块.url", "E12_REQUEST_URL_EMPTY", f"url「{url}」里没有路径")
        else:
            key = endpoint_key(method if method in HTTP_METHODS else "", path)
            keys.append(key)
            declared = {k for k in (_parse_endpoint_ref(r) for r in case["endpoints"]) if k}
            if declared and key not in declared:
                err(cid, "请求块.url", "E13_REQUEST_URL_MISMATCH",
                    f"url 解析成「{key}」，关联端点是「{'; '.join(sorted(declared))}」")
            elif not declared and ep_index and key not in ep_index:
                err(cid, "请求块.url", "E13_REQUEST_URL_MISMATCH",
                    f"url 解析成「{key}」，体检报告里没有这个端点")
            if host and ep_hosts and host.lower() not in ep_hosts:
                err(cid, "请求块.url", "E13_REQUEST_URL_MISMATCH",
                    f"host「{host}」不在体检报告的 host 清单里")

    # ── headers / body 形状 ──
    headers = request.get("headers")
    if headers is not None and not isinstance(headers, (list, tuple)):
        err(cid, "请求块.headers", "E14_REQUEST_SHAPE", "headers 必须是数组")
    body = request.get("body")
    if body is not None:
        if not isinstance(body, dict):
            err(cid, "请求块.body", "E14_REQUEST_SHAPE", "body 必须是对象")
        else:
            mode = str(body.get("mode") or "").strip().lower()
            if mode and mode not in BODY_MODES:
                err(cid, "请求块.body", "E14_REQUEST_SHAPE",
                    f"body.mode 只能是 {'/'.join(BODY_MODES)}，当前「{body.get('mode')}」")

    # ── assertions ──
    assertions = request.get("assertions")
    if assertions is None:
        warn(cid, "请求块.assertions", "W05_REQUEST_NO_ASSERTION")
    elif not isinstance(assertions, (list, tuple)):
        err(cid, "请求块.assertions", "E14_REQUEST_SHAPE", "assertions 必须是数组")
    elif not assertions:
        warn(cid, "请求块.assertions", "W05_REQUEST_NO_ASSERTION")
    else:
        for i, assertion in enumerate(assertions):
            where = f"请求块.assertions[{i}]"
            if not isinstance(assertion, dict):
                err(cid, where, "E15_ASSERTION_INVALID", "断言不是对象")
                continue
            kind = str(assertion.get("type") or "").strip().lower()
            if kind not in ASSERTION_TYPES:
                err(cid, where, "E15_ASSERTION_INVALID",
                    f"type「{assertion.get('type')}」不认识")
                continue
            expected = assertion.get("expected")
            if kind == "status":
                try:
                    code = int(str(expected).strip())
                except (TypeError, ValueError):
                    err(cid, where, "E15_ASSERTION_INVALID",
                        f"status 的 expected 不是整数：「{expected}」")
                else:
                    if not 100 <= code <= 599:
                        err(cid, where, "E15_ASSERTION_INVALID",
                            f"状态码超出 100-599：{code}")
            elif kind == "body_contains":
                if not str(expected or "").strip():
                    err(cid, where, "E15_ASSERTION_INVALID", "body_contains 的 expected 为空")
            elif kind == "json_path":
                if not str(assertion.get("path") or "").strip():
                    err(cid, where, "E15_ASSERTION_INVALID", "json_path 缺 path")
                if "expected" not in assertion:
                    err(cid, where, "E15_ASSERTION_INVALID", "json_path 缺 expected")

    # ── 脱敏复检（E10）：结构化凭证键扫描 + 可选哨兵集逐值比对 ──
    for i, header in enumerate(headers if isinstance(headers, (list, tuple)) else []):
        if not isinstance(header, dict):
            continue
        hkey = str(header.get("key") or header.get("name") or "")
        if ((_key_is_cred(hkey) or hkey.strip().lower() in _AUTH_HEADER_NAMES)
                and _looks_sensitive(header.get("value"))):
            err(cid, f"请求块.headers[{i}]", "E10_REQUEST_SENSITIVE",
                f"请求头「{hkey}」写了明文值")
    for where, key in _scan_credential_literals(
            {k: v for k, v in request.items() if k not in ("assertions", "headers")}):
        err(cid, f"请求块.{where}", "E10_REQUEST_SENSITIVE", f"凭证键「{key}」写了明文值")
    body_obj = None
    if isinstance(body, dict) and isinstance(body.get("raw"), str):
        try:
            body_obj = json.loads(body["raw"])
        except ValueError:
            body_obj = None
    if body_obj is not None:
        for where, key in _scan_credential_literals(body_obj):
            err(cid, "请求块.body.raw", "E10_REQUEST_SENSITIVE",
                f"请求体里凭证键「{key}」（{where}）写了明文值")
    if url:
        for qkey, qvals in parse_qs(split_request_url(url)[2], keep_blank_values=True).items():
            if _key_is_cred(qkey) and any(_looks_sensitive(v) for v in qvals):
                err(cid, "请求块.url", "E10_REQUEST_SENSITIVE",
                    f"query 里凭证键「{qkey}」写了明文值")
    if sensitive_values:
        blob = "\n".join(_request_texts(request))
        for value in sensitive_values:
            if isinstance(value, str) and len(value) >= _MIN_SENSITIVE_LEN and value in blob:
                err(cid, "请求块", "E10_REQUEST_SENSITIVE",
                    f"命中脱敏哨兵集里的值（前 6 位「{value[:6]}…」）")
                break
    return keys


def _input_error(problem: str, code: str = "E00_INPUT", hint: str = "",
                 normalized: list | None = None) -> dict:
    """入参层面就走不下去的返回（形状与正常返回一致，模型不用分支处理）。"""
    result = {
        "ok": False, "schema": SCHEMA,
        "errors": [{"case_id": None, "field": "cases", "code": code,
                    "problem": problem}],
        "warnings": [], "coverage": {"covered": [], "uncovered": []},
        "summary": {"case_count": 0, "error_count": 1, "warning_count": 0,
                    "verdict": f"未通过：{problem}"},
    }
    if hint:
        result["hint"] = hint
    if normalized:
        result["normalized"] = list(normalized)
    return result


def validate_cases(cases: Any, endpoints: Any = None,
                   sensitive_values: Any = None) -> dict:
    """确定性校验。返回 {ok, errors, warnings, coverage, summary}。

    错误定位到「条目 + 字段」，供模型定向修复；覆盖率缺口只算 warning。

    `sensitive_values` 是可选的脱敏哨兵集（`har_parse.collect_sensitive_values` 的产物）：
    传了就对 request 块逐值复检。**这个入参不在 MCP 工具面上暴露**——凭证值一旦经工具
    入参流转就等于进了模型上下文，正好是脱敏要防的事；它只给同进程调用方（测试、
    未来的服务端内编排）用。不传时仍有结构化凭证键扫描兜底。

    入参形状先过 `args_tolerance`（JSON 字符串、单键包裹、单个用例对象都救回来），
    救回的动作如实回显在返回值的 `normalized` 里。
    """
    errors: list = []
    warnings: list = []
    try:
        cases, endpoints, notes = args_tolerance.tolerant_validate_args(cases, endpoints)
    except args_tolerance.ArgsToleranceError as exc:
        return _input_error(exc.message, exc.code, exc.hint, exc.normalized)

    def err(case_id, field, code, detail=""):
        errors.append({"case_id": case_id, "field": field, "code": code,
                       "problem": RULE_TEXT[code] + (f"：{detail}" if detail else "")})

    def warn(case_id, field, code, detail=""):
        warnings.append({"case_id": case_id, "field": field, "code": code,
                         "problem": RULE_TEXT[code] + (f"：{detail}" if detail else "")})

    if not isinstance(cases, (list, tuple)):
        return _input_error("cases 必须是用例数组（JSON 列表）", normalized=notes)
    if not cases:
        return _input_error("用例数组为空，至少要有一条用例",
                            hint="至少给一条用例；cases 传 JSON 数组。", normalized=notes)

    normalized = normalize_cases(cases)
    ep_index = _endpoint_index(endpoints)
    ep_hosts = _endpoint_hosts(endpoints)
    covered: set = set()
    seen_ids: set = set()
    with_request = 0

    for case in normalized:
        cid = case["case_id"]
        if not case["_shape_ok"]:
            err(cid, "case", "E01_SHAPE")
            continue

        if case.get("_id_missing") or not case["case_id"] or case["case_id"].startswith("#"):
            err(cid, "编号", "E02_ID_EMPTY")
        elif case["case_id"] in seen_ids:
            err(cid, "编号", "E03_ID_DUPLICATE")
        seen_ids.add(case["case_id"])

        if not case["title"]:
            err(cid, "标题", "E04_TITLE_EMPTY")

        if "preconditions" not in case["_present"]:
            err(cid, "前置条件", "E05_PRECONDITION_MISSING")
        elif not case["preconditions"]:
            warn(cid, "前置条件", "W01_PRECONDITION_EMPTY")

        step_count = len(case["steps"])
        if step_count < MIN_STEPS:
            err(cid, "操作步骤", "E06_STEPS_EMPTY")
        elif step_count > MAX_STEPS:
            warn(cid, "操作步骤", "W02_STEPS_TOO_MANY", f"当前 {step_count} 步")

        if not case["expected"]:
            err(cid, "预期结果", "E07_EXPECTED_EMPTY")

        if case["priority"] not in PRIORITIES:
            err(cid, "优先级", "E08_PRIORITY_INVALID",
                f"当前值「{case['priority']}」" if case["priority"] else "当前为空")

        for field in _CLAIM_SCAN_FIELDS:
            text = _as_text(case.get(field))
            hit, label = _scan_execution_claim(text)
            if hit:
                err(cid, _cn_field(field), "E09_EXECUTION_CLAIM", f"{label}——「{hit}」")

        request_keys = _validate_request(case, ep_index, ep_hosts, sensitive_values,
                                         err, warn)
        if request_keys or isinstance(case.get("request"), dict):
            with_request += 1

        refs = list(case["endpoints"])
        for field in ("steps", "expected", "title", "test_data"):
            for m in _ENDPOINT_REF_RE.finditer(_as_text(case.get(field))):
                refs.append(f"{m.group(1)} {m.group(2)}")
        for ref in refs:
            key = _parse_endpoint_ref(ref)
            if key is None:
                continue
            if ep_index:
                if key in ep_index:
                    covered.add(key)
                else:
                    warn(cid, "关联端点", "W03_ENDPOINT_UNKNOWN", ref)
            else:
                covered.add(key)
        # request.url 锚定的端点也算覆盖（url 与关联端点的一致性已由 E13 保证）
        for key in request_keys:
            if not ep_index or key in ep_index:
                covered.add(key)

    coverage: dict = {"covered": sorted(covered), "uncovered": []}
    if ep_index:
        uncovered = sorted(label for key, label in ep_index.items() if key not in covered)
        coverage = {
            "endpoint_total": len(ep_index),
            "covered_count": len(ep_index) - len(uncovered),
            "covered": sorted(ep_index[k] for k in ep_index if k in covered),
            "uncovered_count": len(uncovered),
            "uncovered": uncovered[:MAX_UNCOVERED_LISTED],
        }
        if len(uncovered) > MAX_UNCOVERED_LISTED:
            coverage["uncovered_truncated"] = len(uncovered) - MAX_UNCOVERED_LISTED
        if uncovered:
            warn(None, "覆盖率", "W04_ENDPOINT_UNCOVERED",
                 f"{len(uncovered)}/{len(ep_index)} 个端点无用例覆盖")

    result = {
        "ok": not errors,
        "schema": SCHEMA,
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
        "summary": {
            "case_count": len(normalized),
            "cases_with_request": with_request,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "verdict": ("通过：格式合规，可以落盘" if not errors else
                        f"未通过：{len(errors)} 处错误需定向修复后重验"),
        },
    }
    if notes:
        result["normalized"] = notes
    return result


_CN_FIELD = {"case_id": "编号", "title": "标题", "preconditions": "前置条件",
             "steps": "操作步骤", "expected": "预期结果", "priority": "优先级",
             "module": "所属模块", "case_type": "用例类型",
             "test_data": "测试数据", "endpoints": "关联端点", "request": "请求块"}


def _cn_field(field: str) -> str:
    return _CN_FIELD.get(field, field)
