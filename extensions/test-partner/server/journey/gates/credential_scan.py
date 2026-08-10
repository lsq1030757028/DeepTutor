# -*- coding: utf-8 -*-
"""credential_scan — 凭据零落盘机械扫描（DoD 7 的可运行件）。

对产物目录树做两类扫描：
  1. known-secret：给定的已知凭据值（环境变量表里的值）在任何产物文件里出现
     = 命中（文本与二进制都扫，trace.zip 等二进制内嵌明文也拦）。
  2. high-entropy：文本文件里的高熵 token（覆盖「自定义高熵串」——用户自配的
     口令/token 即使不在已知清单里也要能被指出来复核）。
     允许清单（不报）：sha256:<64hex> 摘要、纯 hex ≥32（digest/commit 形态）、
     {{var}} 占位符。注意：允许清单是「形态白名单」，hex 编码的真凭据会漏——
     报告须注明本扫描证明机制在场、不证强度（DoD 7 原文口径）。

退出语义：命中 known-secret = 阻断；high-entropy 命中 = 需逐条给出处置
（allowlist_reason）后才算过——fail-closed，不许默默放行。
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

TEXT_EXT = {".json", ".jsonl", ".md", ".txt", ".py", ".html", ".csv", ".yaml",
            ".yml", ".log", ".ini", ".cfg", ".toml", ".xml", ".js", ".ts"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".pytest_cache"}
MAX_BYTES = 50 * 1024 * 1024

TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
HEX_RE = re.compile(r"^[0-9a-fA-F]{32,}$")
SHA_PREFIXED = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"^\{\{[A-Za-z_][A-Za-z0-9_]*\}\}$")
ENTROPY_THRESHOLD = 3.8   # bits/char；base64 随机串 ~6，英文单词 ~2-3
MIN_SECRET_LEN = 4


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _is_allowlisted(token: str) -> bool:
    return bool(SHA_PREFIXED.match(token) or HEX_RE.match(token)
                or PLACEHOLDER.match(token))


def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield os.path.join(dirpath, name)


def scan_tree(root: str, known_secrets: list[str] | None = None,
              allowlist: list[str] | None = None) -> dict[str, Any]:
    """扫描目录树。返回 {ok, known_hits, entropy_hits, scanned_files}。

    known_secrets：已知凭据值清单（值本身，不落盘本报告——命中只记文件与偏移）。
    allowlist：额外放行的具体 token（须由复核人逐条给出，fail-closed）。
    ok = 无 known 命中 且 无未放行的高熵命中。
    """
    secrets = [s for s in (known_secrets or []) if s and len(s) >= MIN_SECRET_LEN]
    secret_bytes = [s.encode("utf-8") for s in secrets]
    allowed = set(allowlist or [])
    known_hits: list[dict[str, Any]] = []
    entropy_hits: list[dict[str, Any]] = []
    scanned = 0
    for path in _iter_files(root):
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError:
            continue
        scanned += 1
        rel = os.path.relpath(path, root).replace("\\", "/")
        for i, sb in enumerate(secret_bytes):
            off = blob.find(sb)
            if off >= 0:
                known_hits.append({
                    "file": rel, "offset": off,
                    "secret_index": i,           # 只记序号，不回显值（红线 3）
                    "secret_len": len(secrets[i]),
                })
        ext = os.path.splitext(path)[1].lower()
        if ext not in TEXT_EXT:
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        seen: set[str] = set()
        for m in TOKEN.finditer(text):
            token = m.group(0)
            if token in seen or token in allowed or _is_allowlisted(token):
                continue
            seen.add(token)
            # 密钥形态判据：字母+数字混排，或带 base64 专有符号——纯字母驼峰散文不算
            has_digit = any(c.isdigit() for c in token)
            has_alpha = any(c.isalpha() for c in token)
            b64ish = any(c in "+/=" for c in token)
            if not ((has_digit and has_alpha) or b64ish):
                continue
            ent = shannon_entropy(token)
            if ent >= ENTROPY_THRESHOLD:
                entropy_hits.append({
                    "file": rel, "token_preview": token[:6] + "…" + token[-4:],
                    "token": token, "length": len(token),
                    "entropy": round(ent, 2),
                })
    return {
        "ok": not known_hits and not entropy_hits,
        "known_hits": known_hits,
        "entropy_hits": entropy_hits,
        "scanned_files": scanned,
        "note": ("known-secret 命中=阻断;entropy 命中须逐条 allowlist_reason 复核。"
                 "本扫描证明机制在场,不证明强度(hex 编码等形态可绕过允许清单)。"),
    }
