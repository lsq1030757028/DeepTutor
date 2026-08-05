"""Postman Collection v2.1 导出（`save_delivery` 的 `postman` 产物实现层）。

M2.5「用例可执行化」（2026-08-04 用户拍板：生成管线 + 标准格式导出，资产的家在
Apifox/Postman）。产物是标准 Collection v2.1 JSON，Apifox「导入 → Postman」原生可读。

结构映射（用例 → collection）：

| 用例 | collection |
| --- | --- |
| 交付标题 | `info.name`（`info._postman_id` 由标题+用例内容派生，同输入同 id） |
| `所属模块` | 一层文件夹（`item` 里的 folder，按首次出现顺序） |
| 一条用例 | 一个 `item`：`request` + `event[listen=test]` 测试脚本 |
| `request.headers` | `request.header[{key,value}]` |
| `request.url` | `request.url{raw,host,path,query}`（`{{baseUrl}}` 前缀原样保留） |
| `request.body` | `request.body{mode,raw,options.raw.language}` |
| `request.assertions` | 测试脚本里的 `pm.test(...)` |
| 六字段其余部分 | `request.description`（人读的用例正文，Apifox 里能看到） |

断言翻译（三种类型，与 `case_validate` 的 `ASSERTION_TYPES` 同源）：
- `status` → `pm.response.to.have.status(<expected>)`
- `body_contains` → `pm.expect(pm.response.text()).to.include("<expected>")`
- `json_path` → `pm.expect(jsonData[...]).to.eql(<expected>)`（`path` 走简易 jsonpath）

没有 `request` 块的用例照样进 collection，但生成**占位 item**（description 写明
「人执行用例，无结构化请求」）并计数——不静默丢用例，也不假装它能跑。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from server import case_validate

SCHEMA_URL = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
BASE_URL_VAR = "baseUrl"
BASE_URL_REF = "{{%s}}" % BASE_URL_VAR
PLACEHOLDER_NOTE = ("人执行用例，无结构化请求：本条用例没有 request 块，"
                    "导入后需要人工补全请求或手工执行。")

_VAR_PREFIX_RE = re.compile(r"^\{\{([^{}]+)\}\}(.*)$", re.S)
_SEGMENT_RE = re.compile(r"^([^\[\]]*)((?:\[\d+\])*)$")


def _js(value: Any) -> str:
    """Python 值 → JS 字面量。JSON 字面量在 JS 里合法，直接借 json.dumps 转义。"""
    return json.dumps(value, ensure_ascii=False)


def url_object(raw_url: str) -> dict:
    """URL 字符串 → Postman 的 url 对象 {raw, protocol?, host, port?, path, query?}。

    三种形态都收：`{{baseUrl}}/api/v1/orders?page=1`、绝对 URL、裸路径
    （裸路径自动补 `{{baseUrl}}` 前缀，否则导入后无法执行）。
    """
    raw = str(raw_url or "").strip()
    if not raw:
        return {"raw": BASE_URL_REF, "host": [BASE_URL_REF], "path": []}

    before, _, query = raw.partition("?")
    query = query.split("#", 1)[0]
    obj: dict = {}

    m = _VAR_PREFIX_RE.match(before)
    if m:
        obj["host"] = ["{{%s}}" % m.group(1)]
        path_str = m.group(2)
    elif "://" in before:
        parts = urlsplit(before)
        if parts.scheme:
            obj["protocol"] = parts.scheme
        try:
            host, port = parts.hostname or "", parts.port
        except ValueError:                      # 畸形端口：退化成整段当 host
            host, port = parts.netloc, None
        obj["host"] = host.split(".") if host else []
        if port:
            obj["port"] = str(port)
        path_str = parts.path
    else:
        obj["host"] = [BASE_URL_REF]
        path_str = before if before.startswith("/") else f"/{before}"
        raw = f"{BASE_URL_REF}{path_str}" + (f"?{query}" if query else "")

    obj["path"] = [seg for seg in str(path_str).split("/") if seg]
    if query:
        pairs = []
        for piece in query.split("&"):
            if not piece:
                continue
            key, _, value = piece.partition("=")
            pairs.append({"key": key, "value": value})
        obj["query"] = pairs
    return {"raw": raw, **obj}          # raw 排在最前，导出文件更好读


def origin_of(raw_url: str) -> str:
    """绝对 URL → `scheme://host[:port]`；非绝对 URL 返回空串。"""
    raw = str(raw_url or "").strip()
    if "://" not in raw or _VAR_PREFIX_RE.match(raw):
        return ""
    parts = urlsplit(raw.partition("?")[0])
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def json_path_accessor(path: str) -> str:
    """简易 jsonpath → JS 取值后缀：`$.data.items[0].id` → `["data"]["items"][0]["id"]`。"""
    text = re.sub(r"^\$\.?", "", str(path or "").strip())
    out = ""
    for seg in text.split("."):
        if not seg:
            continue
        m = _SEGMENT_RE.match(seg)
        if not m:                               # 兜底：整段当键名
            out += f"[{_js(seg)}]"
            continue
        name, idx = m.group(1), m.group(2)
        if name:
            out += f"[{_js(name)}]"
        for i in re.findall(r"\[(\d+)\]", idx):
            out += f"[{i}]"
    return out


def assertion_script(assertion: Any) -> list:
    """一条断言 → pm.test 脚本行；翻不了的返回空列表（导出层不猜，交给校验器拦）。"""
    if not isinstance(assertion, dict):
        return []
    kind = str(assertion.get("type", "")).strip().lower()
    expected = assertion.get("expected")

    if kind == "status":
        try:
            code = int(str(expected).strip())
        except (TypeError, ValueError):
            return []
        return [f"pm.test({_js(f'状态码为 {code}')}, function () {{",
                f"    pm.response.to.have.status({code});",
                "});"]

    if kind == "body_contains":
        needle = str(expected if expected is not None else "")
        if not needle:
            return []
        return [f"pm.test({_js(f'响应包含：{needle}')}, function () {{",
                f"    pm.expect(pm.response.text()).to.include({_js(needle)});",
                "});"]

    if kind == "json_path":
        path = str(assertion.get("path") or "").strip()
        if not path:
            return []
        accessor = json_path_accessor(path)
        if not accessor:
            return []
        return [f"pm.test({_js(f'{path} 等于 {expected}')}, function () {{",
                "    var jsonData = pm.response.json();",
                f"    pm.expect(jsonData{accessor}).to.eql({_js(expected)});",
                "});"]

    return []


def _description(case: dict, placeholder: bool = False) -> str:
    lines = []
    if placeholder:
        lines.append(PLACEHOLDER_NOTE)
        lines.append("")
    if case.get("preconditions"):
        lines.append(f"前置条件：{case['preconditions']}")
    if case.get("steps"):
        lines.append("操作步骤：")
        lines.extend(f"{i}. {s}" for i, s in enumerate(case["steps"], 1))
    if case.get("expected"):
        lines.append(f"预期结果：{case['expected']}")
    if case.get("priority"):
        lines.append(f"优先级：{case['priority']}")
    if case.get("test_data"):
        lines.append(f"测试数据：{case['test_data']}")
    if case.get("endpoints"):
        lines.append("关联端点：" + "; ".join(case["endpoints"]))
    lines.append(f"（用例编号 {case.get('case_id', '')}，由 test-partner 生成）")
    return "\n".join(lines)


def _headers_of(request: dict) -> list:
    out = []
    for h in request.get("headers") or []:
        if isinstance(h, dict):
            key = str(h.get("key") or h.get("name") or "").strip()
            if key:
                out.append({"key": key, "value": str(h.get("value", "")), "type": "text"})
        elif isinstance(h, str) and ":" in h:
            key, _, value = h.partition(":")
            out.append({"key": key.strip(), "value": value.strip(), "type": "text"})
    return out


def _body_of(request: dict) -> dict | None:
    body = request.get("body")
    if not isinstance(body, dict):
        return None
    mode = str(body.get("mode") or "").strip().lower()
    if mode in ("", "none"):
        return None
    raw = body.get("raw")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    language = str(body.get("language") or "json").strip().lower() or "json"
    return {"mode": "raw", "raw": raw, "options": {"raw": {"language": language}}}


def _item_of(case: dict, stats: dict) -> dict:
    name = " ".join(part for part in (case.get("case_id"), case.get("title")) if part)
    request = case.get("request")
    if not isinstance(request, dict) or not request:
        stats["placeholder_count"] += 1
        return {
            "name": name or "(无标题用例)",
            "request": {
                "method": "GET",
                "header": [],
                "url": url_object(BASE_URL_REF),
                "description": _description(case, placeholder=True),
            },
        }

    method = str(request.get("method") or "GET").strip().upper() or "GET"
    item = {
        "name": name or "(无标题用例)",
        "request": {
            "method": method,
            "header": _headers_of(request),
            "url": url_object(request.get("url")),
            "description": _description(case),
        },
    }
    body = _body_of(request)
    if body is not None:
        item["request"]["body"] = body

    exec_lines: list = []
    for assertion in request.get("assertions") or []:
        script = assertion_script(assertion)
        if script:
            exec_lines.extend(script)
            stats["assertion_count"] += 1
        else:
            stats["skipped_assertions"] += 1
    if exec_lines:
        item["event"] = [{
            "listen": "test",
            "script": {"type": "text/javascript", "exec": exec_lines},
        }]
    else:
        stats["items_without_test"] += 1
    return item


def build_collection(cases: Any, title: str = "测试用例",
                     source_fingerprint: str = "") -> tuple[dict, dict]:
    """用例数组 → (Collection v2.1 字典, 统计字典)。

    统计字典：item_count / folder_count / placeholder_count / assertion_count /
    skipped_assertions / items_without_test / base_url。
    """
    normalized = [c for c in case_validate.normalize_cases(cases) if c["_shape_ok"]]
    title = str(title or "").strip() or "测试用例"
    stats = {"item_count": 0, "folder_count": 0, "placeholder_count": 0,
             "assertion_count": 0, "skipped_assertions": 0, "items_without_test": 0}

    folders: dict = {}
    order: list = []
    origins: dict = {}
    for case in normalized:
        module = case.get("module") or "未分类"
        if module not in folders:
            folders[module] = []
            order.append(module)
        folders[module].append(_item_of(case, stats))
        stats["item_count"] += 1
        request = case.get("request")
        if isinstance(request, dict):
            origin = origin_of(request.get("url"))
            if origin:
                origins[origin] = origins.get(origin, 0) + 1

    stats["folder_count"] = len(order)
    # baseUrl 取用例里出现最多的绝对源；全是 {{baseUrl}} 相对写法时留空由用户填
    base_url = max(origins.items(), key=lambda kv: (kv[1], kv[0]))[0] if origins else ""
    stats["base_url"] = base_url

    seed = json.dumps([title, cases], ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    collection_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   "test-partner/" + hashlib.sha256(seed).hexdigest()))

    description = [f"由 test-partner 从测试用例导出，共 {stats['item_count']} 条。",
                   "可直接导入 Apifox / Postman 执行。"]
    if source_fingerprint:
        description.append(f"来源指纹：{source_fingerprint}")
    if stats["placeholder_count"]:
        description.append(
            f"其中 {stats['placeholder_count']} 条是占位 item（{PLACEHOLDER_NOTE}）。")

    collection = {
        "info": {
            "_postman_id": collection_id,
            "name": title,
            "description": "\n".join(description),
            "schema": SCHEMA_URL,
        },
        "item": [{"name": module, "item": folders[module]} for module in order],
        "variable": [{"key": BASE_URL_VAR, "value": base_url, "type": "string"}],
    }
    return collection, stats
