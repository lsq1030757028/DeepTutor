# -*- coding: utf-8 -*-
"""digest — RFC 8785 (JCS) 规范化序列化 + SHA-256 摘要。

schema digestRules（approved-caseset-schema.json v1.0.0 $defs.digestRules）的可运行件：

- 哈希算法：SHA-256，输出 ``sha256:`` + 64 位小写十六进制。
- 规范化序列化：RFC 8785 JSON Canonicalization Scheme——对象键按 UTF-16 码元升序、
  无多余空白、UTF-8 编码、数字按 ECMAScript 最短往返表示、字符串最小转义。
- oracle_digest 包含字段：仅且必须 expected / failure_criteria / assertion_layers。
- source_case_digest 包含字段：case 全部字段，排除两个 digest 字段本身。

实现必须过 RFC 8785 测试向量（tests/test_journey_digest.py 抄自 RFC 附录）方可用于
digest——该测试即为准入闸。

依赖纪律：纯标准库，零第三方。
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

# ── ECMAScript Number::toString（RFC 8785 §3.2.2.3）─────────────────────────


def _es_number(value: float) -> str:
    """把有限 double 按 ECMAScript 最短往返规则转成字符串。"""
    if value != value or value in (math.inf, -math.inf):  # NaN / Inf
        raise ValueError("JCS 禁止 NaN/Infinity")
    if value == 0:
        return "0"  # -0 也序列化为 0（ES 规则）
    sign = "-" if value < 0 else ""
    value = abs(value)
    # Python repr 已是最短往返十进制；拆出十进制有效数字与指数
    text = repr(value)
    if "e" in text or "E" in text:
        mant, _, exp_s = text.lower().partition("e")
        exp = int(exp_s)
    else:
        mant, exp = text, 0
    if "." in mant:
        int_part, frac = mant.split(".")
    else:
        int_part, frac = mant, ""
    digits = (int_part + frac).lstrip("0")
    # n = 小数点应在的位置（ES 规范里的 n），k = 有效数字个数
    n = len(int_part.lstrip("0")) + exp if int_part.strip("0") else exp - (
        len(frac) - len(frac.lstrip("0"))
    )
    digits = digits.rstrip("0")
    k = len(digits)
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        first, rest = digits[0], digits[1:]
        mant_out = first + ("." + rest if rest else "")
        e = n - 1
        out = f"{mant_out}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + out


_ESCAPES = {
    '"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f",
    "\n": "\\n", "\r": "\\r", "\t": "\\t",
}


def _es_string(value: str) -> str:
    parts = ['"']
    for ch in value:
        if ch in _ESCAPES:
            parts.append(_ESCAPES[ch])
        elif ch < "\x20":
            parts.append(f"\\u{ord(ch):04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _sort_key(item: tuple[str, Any]) -> tuple[int, ...]:
    """RFC 8785 §3.2.3：键按 UTF-16 码元序比较。"""
    return tuple(
        unit
        for ch in item[0]
        for unit in (
            (ord(ch),)
            if ord(ch) < 0x10000
            else (
                0xD800 + ((ord(ch) - 0x10000) >> 10),
                0xDC00 + ((ord(ch) - 0x10000) & 0x3FF),
            )
        )
    )


def canonicalize(value: Any) -> str:
    """把 JSON 兼容值序列化成 RFC 8785 规范文本。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _es_string(value)
    if isinstance(value, int):
        # ES 的整数在 2^53 内安全；越界仍按 double 处理（JCS 语义）
        if abs(value) < 2**53:
            return str(value)
        return _es_number(float(value))
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e21:
            return _es_number(value)
        return _es_number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise TypeError(f"JCS 对象键必须是字符串，实为 {type(k).__name__}")
        items = sorted(value.items(), key=_sort_key)
        return "{" + ",".join(
            _es_string(k) + ":" + canonicalize(v) for k, v in items
        ) + "}"
    raise TypeError(f"JCS 不支持的类型：{type(value).__name__}")


def sha256_digest(value: Any) -> str:
    """JCS 序列化后取 SHA-256，输出 'sha256:<64hex>'。"""
    data = canonicalize(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def text_digest(text: str) -> str:
    """纯文本（如需求正文快照）的 SHA-256 摘要，UTF-8 编码，换行归一为 \\n。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ── digest 规则（字段包含/排除，schema 草案 digestRules）────────────────────

ORACLE_FIELDS = ("expected", "failure_criteria", "assertion_layers")
DIGEST_FIELDS = ("source_case_digest", "oracle_digest")


def oracle_digest(case: dict[str, Any]) -> str:
    """oracle_digest：仅且必须 expected/failure_criteria/assertion_layers 三键。"""
    payload = {k: case.get(k) for k in ORACLE_FIELDS}
    return sha256_digest(payload)


def source_case_digest(case: dict[str, Any]) -> str:
    """source_case_digest：case 全部字段，排除两个 digest 字段本身。"""
    payload = {k: v for k, v in case.items() if k not in DIGEST_FIELDS}
    return sha256_digest(payload)


def stamp_case_digests(case: dict[str, Any]) -> dict[str, Any]:
    """返回带两个 digest 的新 case（不改入参）。oracle 先算：source 摘要包含 oracle 摘要
    之外的全部业务字段，两者互不嵌套。"""
    out = dict(case)
    out["oracle_digest"] = oracle_digest(case)
    out["source_case_digest"] = source_case_digest(case)
    return out


def verify_case_digests(case: dict[str, Any]) -> list[str]:
    """准出门复算：任一不一致返回错误清单（非空即 BLOCK）。"""
    errors: list[str] = []
    expect_oracle = oracle_digest(case)
    expect_source = source_case_digest(case)
    if case.get("oracle_digest") != expect_oracle:
        errors.append(
            f"oracle_digest 不一致：记录 {case.get('oracle_digest')}，重算 {expect_oracle}")
    if case.get("source_case_digest") != expect_source:
        errors.append(
            f"source_case_digest 不一致：记录 {case.get('source_case_digest')}，重算 {expect_source}")
    return errors
