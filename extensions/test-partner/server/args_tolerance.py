"""工具入参规整（`validate_cases` / `save_delivery` / `execute_cases` 共用）。

热修背景（2026-08-04 实机冒烟）：小模型（deepseek-v4-flash 一类）传大体积工具参数时
形状不稳——把 `cases` 整个数组序列化成 JSON 字符串塞进来、把全部实参包一层
`{"input": {...}}`、只有一条用例时直接传对象而不是数组。这些都是**能救的形状**，
以前一律硬拒（`cases 必须是用例数组`），一次往返白烧还未必改对。

本模块的口径是「修复优于拒绝」，但拒绝的边界仍然清楚：

| 收到的形状 | 处理 | 回显 |
| --- | --- | --- |
| `"[{...}]"`（JSON 字符串） | `json.loads` 解析 | 是 |
| `"{...}"`（单个用例的 JSON 字符串） | 解析后再包成单元素列表 | 是 |
| `{"cases": [...], "endpoints": [...]}` | 认作信封：取 `cases`，同级实参一并捡回 | 是 |
| `"{...}"`（`login_request` 序列化成字符串） | `json.loads` 解析 | 是 |
| `{"input": {...}}` / `{"arguments": ...}` / `{"params": ...}` | 剥单键包裹，最多 3 层 | 是 |
| `{...}`（单个用例对象） | 包成单元素列表 | 是 |
| 坏 JSON 字符串 | 报错，说清「收到字符串但不是合法 JSON」 | — |
| 包裹超过 3 层 | 报错（防自引用死循环） | — |
| 数字/布尔/None | 报错 | — |

**回显是硬要求**：每个规整动作都如实写进返回值的 `normalized` 数组，模型看得见自己
传歪了才有机会下次传对——静默纠正等于把错误形状喂成习惯。
"""

from __future__ import annotations

import json
import re
from typing import Any

#: 单键包裹的键名（模型爱把实参再包一层）
WRAPPER_KEYS = ("cases", "input", "inputs", "arguments", "args", "params",
                "parameters", "payload", "kwargs", "body", "data")
#: 信封里指向用例数组的键名（中英同收）
CASES_KEYS = ("cases", "case_list", "testcases", "test_cases", "用例", "用例列表")
#: 最多向内解几层包裹——超过就报错，不跟着自引用结构转
MAX_UNWRAP_DEPTH = 3
#: 最多解几次 JSON 字符串（双重编码见过，三重没见过）
MAX_JSON_PARSE = 2


class ArgsToleranceError(ValueError):
    """入参救不回来时的可预期错误（带 code/hint 与已完成的规整回显）。"""

    def __init__(self, code: str, message: str, hint: str = "",
                 normalized: list | None = None):
        self.code = code
        self.message = message
        self.hint = hint
        self.normalized = list(normalized or [])
        super().__init__(message)


def _lower_key_map(node: dict) -> dict:
    out: dict = {}
    for key in node:
        if isinstance(key, str):
            out.setdefault(key.strip().lower(), key)
    return out


def _peel_once(node: dict, extras_keys: tuple) -> tuple:
    """认一层包裹 →（内层值, 用到的键名, 顺手捡回的同级实参）；不是包裹返回 (None, None, {})。"""
    lower = _lower_key_map(node)

    # ① 信封：含 cases 键 —— 同级的 endpoints/format/title 一并捡回，别丢了
    for name in CASES_KEYS:
        key = lower.get(name)
        if key is None:
            continue
        extras = {e: node[lower[e]] for e in extras_keys
                  if e in lower and lower[e] != key}
        return node[key], key, extras

    # ② 单键包裹：{"input": {...}} / {"arguments": {...}} / {"params": {...}}
    if len(node) == 1:
        key = next(iter(node))
        if isinstance(key, str) and key.strip().lower() in WRAPPER_KEYS:
            return node[key], key, {}
    return None, None, {}


def _parse_json(text: str, what: str, notes: list) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ArgsToleranceError(
            f"{what.upper()}_JSON_INVALID",
            f"{what} 收到字符串但不是合法 JSON：{exc}",
            f"直接传 JSON 数组/对象本身，不要再序列化成字符串；"
            f"若确实要传字符串，先确认它能被 json.loads 解析（当前前 60 字："
            f"「{text.strip()[:60]}」）。",
            notes) from exc


def coerce_cases(cases: Any, extras_keys: tuple = ()) -> tuple:
    """`cases` 入参规整 →（用例列表, 捡回的同级实参, 规整回显）。救不回来抛 ArgsToleranceError。"""
    notes: list = []
    extras: dict = {}
    value = cases
    peels = parses = 0

    while True:
        if isinstance(value, str):
            if parses >= MAX_JSON_PARSE:
                raise ArgsToleranceError(
                    "CASES_WRAPPED_TOO_DEEP",
                    f"cases 被反复序列化超过 {MAX_JSON_PARSE} 层，已停止解析",
                    "直接传 JSON 数组本身，不要多次 json.dumps。", notes)
            parsed = _parse_json(value, "cases", notes)
            parses += 1
            notes.append("cases 收到 JSON 字符串，已 json.loads 解析成结构（下次直接传数组）")
            value = parsed
            continue
        if isinstance(value, dict):
            inner, key, picked = _peel_once(value, extras_keys)
            if key is not None:
                if peels >= MAX_UNWRAP_DEPTH:
                    raise ArgsToleranceError(
                        "CASES_WRAPPED_TOO_DEEP",
                        f"cases 的包裹层数超过 {MAX_UNWRAP_DEPTH} 层，已停止向内解",
                        "cases 直接传用例数组本身，不要层层包 "
                        f"{{\"{key}\": ...}}。", notes)
                for name, picked_value in picked.items():
                    if name not in extras:
                        extras[name] = picked_value
                        notes.append(f"从包裹里捡回同级实参「{name}」")
                notes.append(f"剥掉一层包裹「{key}」（下次把 cases 直接放在实参根上）")
                value = inner
                peels += 1
                continue
        break

    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, dict):
        value = [value]
        notes.append("cases 收到单个用例对象，已包成单元素数组（下次传数组，哪怕只有一条）")
    if not isinstance(value, list):
        raise ArgsToleranceError(
            "CASES_TYPE_INVALID",
            f"cases 既不是数组也不是能救回来的形状（收到 {type(value).__name__}）",
            "cases 传 JSON 数组，每个元素是一条用例对象。", notes)
    return value, extras, notes


def coerce_text(value: Any, notes: list, what: str, default: str = "") -> str:
    """标量文本入参规整：单键包裹剥掉、单元素数组取第一个、None/空回默认值。"""
    seen = 0
    while seen < MAX_UNWRAP_DEPTH:
        if isinstance(value, dict):
            if len(value) != 1:
                break
            key = next(iter(value))
            value = value[key]
            notes.append(f"{what} 收到对象包裹「{key}」，已取其中的值")
        elif isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
            notes.append(f"{what} 收到单元素数组，已取第一个元素")
        else:
            break
        seen += 1
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (int, float, bool)):
        notes.append(f"{what} 收到 {type(value).__name__}，已按文本处理")
        return str(value)
    return default


def coerce_endpoints(value: Any, notes: list) -> Any:
    """`endpoints` 是可选入参：JSON 字符串解得开就解，解不开就放行（校验层自会忽略）。"""
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            notes.append("endpoints 是字符串且不是合法 JSON，已忽略（覆盖率不核对）")
            return None
        notes.append("endpoints 收到 JSON 字符串，已 json.loads 解析")
        value = parsed
    if isinstance(value, dict):
        lower = _lower_key_map(value)
        for name in ("endpoints", "endpoint", "items", "list"):
            key = lower.get(name)
            if key is not None:
                notes.append(f"endpoints 收到对象包裹「{key}」，已取其中的数组")
                return value[key]
        return None
    return value


def tolerant_validate_args(cases: Any, endpoints: Any = None) -> tuple:
    """`validate_cases` 的入口规整 →（cases 列表, endpoints, 回显）。"""
    cases, extras, notes = coerce_cases(cases, extras_keys=("endpoints",))
    if extras.get("endpoints") is not None and endpoints is None:
        endpoints = extras["endpoints"]
    endpoints = coerce_endpoints(endpoints, notes)
    return cases, endpoints, notes


def coerce_mapping(value: Any, notes: list, what: str) -> dict:
    """字典入参规整（`variables`）：JSON 字符串解开、`[{key,value}]` 数组认成映射。

    解不开就当没传（返回空字典并回显），不硬拒——变量给不全的用例在执行层会被标
    skipped，比整轮报错强。
    """
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
            notes.append(f"{what} 收到 JSON 字符串，已 json.loads 解析")
        except ValueError:
            notes.append(f"{what} 是字符串且不是合法 JSON，已忽略（按未提供变量处理）")
            return {}
    if isinstance(value, (list, tuple)):        # [{key, value}] 形态（Postman 习惯）
        out: dict = {}
        for item in value:
            if isinstance(item, dict):
                key = item.get("key") or item.get("name")
                if key is not None:
                    out[str(key)] = item.get("value")
        if out:
            notes.append(f"{what} 收到 [{{key,value}}] 数组，已认成键值映射")
        return out
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if value is not None:
        notes.append(f"{what} 收到 {type(value).__name__}，不是键值映射，已忽略")
    return {}


def coerce_id_list(value: Any, notes: list, what: str) -> list:
    """编号列表入参规整（`case_ids`）：JSON 字符串、逗号分隔字符串、单个编号都收。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                value = json.loads(text)
                notes.append(f"{what} 收到 JSON 字符串，已 json.loads 解析")
            except ValueError:
                value = text
        if isinstance(value, str):
            parts = [p.strip() for p in re.split(r"[,，;；\s]+", value) if p.strip()]
            if len(parts) > 1:
                notes.append(f"{what} 收到分隔字符串，已拆成 {len(parts)} 个编号")
            return parts
    if isinstance(value, dict):
        for name in ("case_ids", "ids", "编号"):
            if name in value:
                return coerce_id_list(value[name], notes, what)
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def coerce_int(value: Any, notes: list, what: str, default: int) -> int:
    """整数入参规整（`timeout_s`）：字符串数字认，认不出回默认值并回显。"""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        notes.append(f"{what} 收到布尔值，已按默认值 {default} 处理")
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        notes.append(f"{what}「{value}」不是数字，已按默认值 {default} 处理")
        return default


def coerce_object(value: Any, notes: list, what: str) -> Any:
    """对象入参规整（`login_request`）：JSON 字符串解开，其余原样放行。

    解不开就原样返回——调用方（执行层）自己有形状闸，那里报错比这里更能说清后果。
    """
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return value            # 交给调用方报错：它知道这个字段是干什么的
        notes.append(f"{what} 收到 JSON 字符串，已 json.loads 解析")
        return parsed
    return value


def tolerant_execute_args(cases: Any, base_url: Any = "", variables: Any = None,
                          case_ids: Any = None, timeout_s: Any = 15,
                          delivery_dir: Any = "", title: Any = "",
                          env: Any = "", auth: Any = "env",
                          login_request: Any = None,
                          auth_token_variable: Any = "token") -> tuple:
    """`execute_cases` 的入口规整 →（cases, base_url, variables, case_ids,
    timeout_s, delivery_dir, title, env, auth, login_request,
    auth_token_variable, 回显）。

    实参被整体包进 `cases` 时，同级的 base_url/variables/case_ids/env/auth 等一并捡回。
    """
    cases, extras, notes = coerce_cases(
        cases, extras_keys=("base_url", "baseurl", "variables", "vars", "case_ids",
                            "ids", "timeout_s", "timeout", "delivery_dir", "title",
                            "env", "environment", "auth", "login_request",
                            "auth_token_variable"))
    base_url = extras.get("base_url", extras.get("baseurl", base_url))
    if variables is None:
        variables = extras.get("variables", extras.get("vars"))
    if case_ids is None:
        case_ids = extras.get("case_ids", extras.get("ids"))
    if "timeout_s" in extras or "timeout" in extras:
        timeout_s = extras.get("timeout_s", extras.get("timeout"))
    delivery_dir = extras.get("delivery_dir", delivery_dir)
    title = extras.get("title", title)
    env = extras.get("env", extras.get("environment", env))
    auth = extras.get("auth", auth)
    if login_request is None:
        login_request = extras.get("login_request")
    auth_token_variable = extras.get("auth_token_variable", auth_token_variable)

    base_url = coerce_text(base_url, notes, "base_url")
    delivery_dir = coerce_text(delivery_dir, notes, "delivery_dir")
    title = coerce_text(title, notes, "title")
    env = coerce_text(env, notes, "env")
    auth = coerce_text(auth, notes, "auth", default="env").lower()
    auth_token_variable = coerce_text(auth_token_variable, notes,
                                      "auth_token_variable", default="token")
    login_request = coerce_object(login_request, notes, "login_request")
    variables = coerce_mapping(variables, notes, "variables")
    case_ids = coerce_id_list(case_ids, notes, "case_ids")
    timeout_s = coerce_int(timeout_s, notes, "timeout_s", 15)
    return (cases, base_url, variables, case_ids, timeout_s, delivery_dir, title,
            env, auth, login_request, auth_token_variable, notes)


def tolerant_delivery_args(cases: Any, fmt: Any = "", title: Any = "",
                           source_fingerprint: Any = "") -> tuple:
    """`save_delivery` 的入口规整 →（cases 列表, fmt, title, 指纹, 回显）。

    实参被整体包进 `cases` 时，同级的 format/title/source_fingerprint 一并捡回——
    这种形状下顶层三个参数必然还是默认值，所以信封里的值优先。
    """
    cases, extras, notes = coerce_cases(
        cases, extras_keys=("format", "fmt", "title", "source_fingerprint",
                            "fingerprint"))
    fmt = extras.get("format", extras.get("fmt", fmt))
    title = extras.get("title", title)
    source_fingerprint = extras.get("source_fingerprint",
                                    extras.get("fingerprint", source_fingerprint))
    fmt = coerce_text(fmt, notes, "format")
    title = coerce_text(title, notes, "title")
    source_fingerprint = coerce_text(source_fingerprint, notes, "source_fingerprint")
    return cases, fmt, title, source_fingerprint, notes
