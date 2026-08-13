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
import zipfile
from typing import Any

TEXT_EXT = {".json", ".jsonl", ".md", ".txt", ".py", ".html", ".csv", ".yaml",
            ".yml", ".log", ".ini", ".cfg", ".toml", ".xml", ".js", ".ts"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".pytest_cache"}
MAX_BYTES = 50 * 1024 * 1024
SCAN_CHUNK_BYTES = 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2000
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_RATIO = 200

TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
HEX_RE = re.compile(r"^[0-9a-fA-F]{32,}$")
SHA_PREFIXED = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"^\{\{[A-Za-z_][A-Za-z0-9_]*\}\}$")
# journey 公开标识符（非凭据）：批次/run/caseset id 与其带前缀词的粘连形态。
# acs-/b-/r- + 8 位日期 + 短随机 hex 是设计内可见 id，不构成泄密面。
ID_FORM = re.compile(
    r"^(?:[a-z_]+=)?(?:acs|b|r)-[0-9]{8}-[0-9a-f]{6,}$")
#: case_id 也是设计内的公开标识符（`<slug>/R<n>-C<nnn>`，形态由 schema 定义）。
#:
#: 为什么要单列：URL 路径那条排除规则要求 `/` 两侧有**纯字母**且 ≥3 长的词段，
#: 而 slug 一旦带连字符或数字（`queenie-ko-main`）就不满足，于是 `queenie-ko-main/R4-C001`
#: 被当成高熵凭据，**整个 bundle 拒编译**。实测就是这么撞上的：既有测试用的 slug 是
#: 纯字母的 `exectest`，所以这个洞一直没露面。
#:
#: 一个会对系统自己的标识符报警的凭据扫描，第一次被撞见时人们会去改标识符，
#: 第二次就会去关掉它——**误报是让闸被关掉的最短路径**，所以按形态豁免，
#: 而不是让使用者绕着走。known-secret 仍然全量精确匹配、不受任何豁免影响。
CASE_ID_FORM = re.compile(r"^[a-z0-9-]+/R[0-9]+-C[0-9]{3}$")
# API 回包里的公开对象标识。它们与 journey id 一样可出现在日志/URL/响应里，
# 不是认证材料；known-secret 精确匹配仍在形态豁免之前执行，不受这些规则影响。
PREFIXED_UUID_FORM = re.compile(
    r"^[a-z][a-z0-9]{1,15}_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
PREFIXED_HEX_ID_FORM = re.compile(r"^[a-z][a-z0-9]{1,15}_[0-9a-f]{20,64}$", re.I)
SLUG_HEX_ID_FORM = re.compile(r"^[a-z0-9][a-z0-9_-]{2,48}-[0-9a-f]{8,64}$", re.I)
EVIDENCE_PATH_FORM = re.compile(
    r"^[a-z0-9_]+/(?:result|transcript|db_snapshot|final_dom)$", re.I)
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


def _builtin_allowlist_reason(token: str) -> str:
    """返回内建形态豁免理由；空串表示不豁免。"""
    checks = (
        (SHA_PREFIXED, "sha256 digest"),
        (HEX_RE, "hex digest/commit shape"),
        (PLACEHOLDER, "template placeholder"),
        (ID_FORM, "journey public id"),
        (CASE_ID_FORM, "journey case id"),
        (PREFIXED_UUID_FORM, "public prefixed UUID"),
        (PREFIXED_HEX_ID_FORM, "public prefixed hex object id"),
        (SLUG_HEX_ID_FORM, "public slug with short object id"),
        (EVIDENCE_PATH_FORM, "journey evidence path"),
    )
    for pattern, reason in checks:
        if pattern.match(token):
            return reason
    return ""


def _is_allowlisted(token: str) -> bool:
    return bool(_builtin_allowlist_reason(token))


def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield os.path.join(dirpath, name)


def _entropy_findings(text: str, label: str,
                      allowed_reasons: dict[str, str]
                      ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hits: list[dict[str, Any]] = []
    allowlisted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in TOKEN.finditer(text):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        allowlist_reason = (_builtin_allowlist_reason(token)
                            or allowed_reasons.get(token, ""))
        if "/" in token and any(
                seg.isalpha() and len(seg) >= 3 for seg in token.split("/")):
            continue
        has_digit = any(char.isdigit() for char in token)
        has_alpha = any(char.isalpha() for char in token)
        b64ish = any(char in "+=" for char in token)
        if not ((has_digit and has_alpha) or b64ish):
            continue
        entropy = shannon_entropy(token)
        if entropy < ENTROPY_THRESHOLD:
            continue
        finding = {
            "file": label,
            "token_preview": token[:6] + "…" + token[-4:],
            "token": token,
            "length": len(token),
            "entropy": round(entropy, 2),
        }
        if allowlist_reason:
            allowlisted.append({
                key: value for key, value in {
                    **finding, "allowlist_reason": allowlist_reason,
                }.items() if key != "token"
            })
        else:
            hits.append(finding)
    return hits, allowlisted


def scan_text_content(text: str, *, label: str = "payload") -> dict[str, Any]:
    """Pre-persistence entropy gate for batch-side text/JSON payloads."""
    entropy_hits, allowlisted_hits = _entropy_findings(text, label, {})
    return {
        "ok": not entropy_hits,
        "entropy_hits": entropy_hits,
        "allowlisted_hits": allowlisted_hits,
    }


def _scan_zip(path: str, rel: str, secrets: list[str],
              allowed_reasons: dict[str, str]) -> tuple[
                  list[dict[str, Any]], list[dict[str, Any]],
                  list[dict[str, Any]], list[str]]:
    """Scan archive members with explicit anti-zip-bomb limits."""
    known: list[dict[str, Any]] = []
    entropy: list[dict[str, Any]] = []
    allowed: list[dict[str, Any]] = []
    rejected: list[str] = []
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                return known, entropy, allowed, [f"{rel}:too-many-entries"]
            for info in infos:
                label = f"{rel}!{info.filename}"
                if info.is_dir():
                    continue
                total += info.file_size
                ratio = info.file_size / max(info.compress_size, 1)
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES or \
                        total > MAX_ARCHIVE_TOTAL_BYTES or ratio > MAX_ARCHIVE_RATIO:
                    rejected.append(f"{label}:unsafe-size-or-ratio")
                    continue
                data = archive.read(info)
                for index, secret in enumerate(secrets):
                    value = secret.encode("utf-8")
                    offset = data.find(value)
                    if offset >= 0:
                        known.append({
                            "file": label, "offset": offset,
                            "secret_index": index, "secret_len": len(secret),
                        })
                # Playwright uses extension-less/member-specific names such as
                # ``trace.network``.  Decode any valid UTF-8 member instead of
                # relying on the outer filesystem extension allowlist.
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                new_hits, new_allowed = _entropy_findings(
                    text, label, allowed_reasons)
                entropy.extend(new_hits)
                allowed.extend(new_allowed)
    except (OSError, zipfile.BadZipFile, RuntimeError):
        rejected.append(f"{rel}:invalid-archive")
    return known, entropy, allowed, rejected


def scan_tree(root: str, known_secrets: list[str] | None = None,
              allowlist: dict[str, str] | list[str] | None = None,
              skip_rel: list[str] | None = None) -> dict[str, Any]:
    """扫描目录树。返回 {ok, known_hits, entropy_hits, scanned_files}。

    known_secrets：已知凭据值清单（值本身，不落盘本报告——命中只记文件与偏移）。
    allowlist：额外放行的具体 token→reason；旧式无理由 list 不再放行（fail-closed）。
    skip_rel：豁免的相对路径（如 bundle 内嵌的确定性运行时源码——其完整性由
      manifest 里登记的 sha256 保证，长标识符不是凭据）；known_secrets 仍全量扫，
      只对高熵启发式豁免。
    ok = 无 known 命中 且 无未放行的高熵命中。
    """
    # Known values are an exact zero-persistence contract.  Short values may be
    # noisy, but silently dropping them would turn “scan passed” into a false
    # claim.  Entropy heuristics retain their own length threshold below.
    secrets = [str(s) for s in (known_secrets or []) if str(s)]
    secret_bytes = [s.encode("utf-8") for s in secrets]
    allowed_reasons = ({str(k): str(v).strip() for k, v in allowlist.items()}
                       if isinstance(allowlist, dict) else {})
    skip_set = set(skip_rel or [])
    known_hits: list[dict[str, Any]] = []
    entropy_hits: list[dict[str, Any]] = []
    allowlisted_hits: list[dict[str, Any]] = []
    entropy_skipped_large_files: list[str] = []
    archive_rejections: list[str] = []
    scanned = 0
    for path in _iter_files(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                found: set[int] = set()
                overlap = b""
                absolute = 0
                max_secret_len = max((len(value) for value in secret_bytes), default=1)
                while True:
                    chunk = fh.read(SCAN_CHUNK_BYTES)
                    if not chunk:
                        break
                    window = overlap + chunk
                    window_start = absolute - len(overlap)
                    for i, sb in enumerate(secret_bytes):
                        if i in found:
                            continue
                        offset = window.find(sb)
                        if offset >= 0:
                            found.add(i)
                            known_hits.append({
                                "file": rel, "offset": window_start + offset,
                                "secret_index": i,
                                "secret_len": len(secrets[i]),
                            })
                    absolute += len(chunk)
                    overlap = window[-(max_secret_len - 1):] \
                        if max_secret_len > 1 else b""
                blob = b""
                if size <= MAX_BYTES and rel not in skip_set and \
                        os.path.splitext(path)[1].lower() in TEXT_EXT:
                    fh.seek(0)
                    blob = fh.read()
        except OSError:
            continue
        scanned += 1
        ext = os.path.splitext(path)[1].lower()
        if ext == ".zip":
            zip_known, zip_entropy, zip_allowed, zip_rejected = _scan_zip(
                path, rel, secrets, allowed_reasons)
            known_hits.extend(zip_known)
            entropy_hits.extend(zip_entropy)
            allowlisted_hits.extend(zip_allowed)
            archive_rejections.extend(zip_rejected)
        if rel in skip_set:
            continue
        if ext not in TEXT_EXT:
            continue
        if size > MAX_BYTES:
            entropy_skipped_large_files.append(rel)
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new_hits, new_allowed = _entropy_findings(
            text, rel, allowed_reasons)
        entropy_hits.extend(new_hits)
        allowlisted_hits.extend(new_allowed)
    return {
        "ok": not known_hits and not entropy_hits and not archive_rejections,
        "known_hits": known_hits,
        "entropy_hits": entropy_hits,
        "allowlisted_hits": allowlisted_hits,
        "entropy_skipped_large_files": entropy_skipped_large_files,
        "archive_rejections": archive_rejections,
        "scanned_files": scanned,
        "note": ("known-secret 命中=阻断;entropy 命中须逐条 allowlist_reason 复核。"
                 "大文件仍逐字节扫 known-secret，仅跳过高熵全文解码。"
                 "本扫描证明机制在场,不证明强度(hex 编码等形态可绕过允许清单)。"),
    }
