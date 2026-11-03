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

# ── 「作者写的内容」这一侧：只认真秘密的形态与上下文（2026-08-29 Round 3）──────
#
# 起因（BB 见提交信息）：`E_OWNER_REQUIRED business_frame.json 疑似包含凭据`
# 连拦 4 次，拦的是系统自己生成的中文用例文件，里面一个凭据都没有。
# draft_cases 因此跑了 6 次，单轮 23 分 45 秒，是那一轮最费时的一段。
#
# 机制：熵闸对**任何**「≥20 字符、有数字有字母、熵 ≥3.8」的 ASCII 串报警。
# 它拦得住随机口令，也同样拦得住 `expect_json_path_data_0_dialogue_mode`
# 这种模型写出来的长断言名，和 `eyJtb2RlIjoi…` 这种 base64 请求体样例
# （已用真实产物复现，见 tests/test_gate_credscan_authored.py 的样本表）。
# **「看起来随机」不是「是秘密」**，而作者内容里长得随机的东西太多了。
#
# 于是这一侧改成两条并联的**正面判据**，而不是一条反面的熵判据：
#   · 形态：已知凭据前缀（sk- / ghp_ / AKIA / JWT / PEM …）；
#   · 上下文：token 紧跟在 password / token / api_key / authorization
#     这类键名后面。
# 两条都不命中就不拦——它可能仍然是个随机串，但没有任何证据说它是秘密。
#
# **这一改动只作用于 `scan_text_content`（作者内容的落盘前闸）。**
# `scan_tree`（DoD 7 的产物证据扫描）一个字节不动，仍做全量熵扫；
# known-secret 精确匹配同样不受影响——真正在守东西的是那两条，
# 而它们都不依赖这条熵启发式。取舍与代价写在 `scan_text_content` 的 docstring 里。

#: 已知凭据前缀。命中即视为秘密形态，不看熵。
SECRET_PREFIXES = (
    "sk-", "sk_live_", "sk_test_", "pk_live_", "rk_live_",   # OpenAI / Stripe
    "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_",   # GitHub
    "glpat-", "gldt-",                                        # GitLab
    "xoxb-", "xoxp-", "xoxa-", "xoxr-", "xoxs-",              # Slack
    "AKIA", "ASIA",                                           # AWS
    "AIza", "ya29.",                                          # Google
    "hf_", "npm_", "dop_v1_", "SG.", "shpat_", "sq0atp-",
)

#: JWT：三段 base64url，第一段以 eyJ 打头。**只在带 `.` 分段时才算**——
#: 光看 `eyJ` 会把任何 base64 编码的 JSON 请求体当成令牌（实测误拦样本之一）。
JWT_FORM = re.compile(r"^eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}$")

#: PEM 私钥块。熵很低（全是可打印 base64 + 固定头），熵闸本来就抓不到它，
#: 这里按整段文本单独认。
PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

#: 秘密上下文：token 紧跟在这些键名/标签之后。取 token 之前 48 个字符来判。
#:
#: **中文词必须在**：这是个中文产品，作者内容里写的是「凭据」「密钥」「口令」，
#: 只列英文键名等于对一半的内容视而不见（实测：`内联凭据 kJ8x2Qw9…` 漏拦）。
SECRET_CONTEXT = re.compile(
    r"(?i)(password|passwd|\bpwd\b|secret|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|credential|authorization|bearer|"
    r"x-api-key|refresh[_-]?token|access[_-]?token|auth[_-]?token|\btoken\b|"
    r"cookie|passphrase|凭据|密钥|私钥|口令|密码|令牌|访问码|授权码"
    r")[\"'\s:=>\],\-：，。]{0,12}$")

#: token **自己**就报了家门的形态：`tapdToken_xxx` / `apiSecret-xxx`。
#: 不收录裸 `key`——`sort_key` / `db_key` 这类太常见，收了又是一轮误报。
SELF_LABELLED_SECRET = re.compile(
    r"(?i)(token|secret|password|passwd|apikey|api_key|credential|"
    r"privatekey|private_key|passphrase)")

#: 上下文窗口。48 个字符足够覆盖 `"api_key": "` 这类 JSON 前缀，
#: 又短到不会把同一行里另一个键名的影响带过来。
SECRET_CONTEXT_WINDOW = 48

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


#: 形态扫描独立于熵扫描跑，原因是两条真实漏网：
#:
#: · `AKIAIOSFODNN7EXAMPLE` 熵只有 3.6，**熵闸先把它丢了**，形态判据根本没机会看；
#: · JWT 带 `.` 分段，而 `TOKEN` 的字符集不含 `.`，一个 JWT 会被切成三段，
#:   每段都不像凭据。
#:
#: 也就是说：把形态判据挂在熵扫描的结果上，等于让一条**更严**的规则去过滤
#: 一条**更准**的规则的输入。所以形态这一路直接扫原文。
#: 前缀必须**顶在 token 开头**，不能是词中间的一截。
#: 实测：没有这个前置断言时，`ask_user_tool_call_id` 的值 `ask-entity-test-1`
#: 里的 `sk-entity-test-1` 命中了 OpenAI 的 `sk-` 前缀，整份 intake_profile
#: 被判成凭据——修误报的改动自己造了一个新的误报，被既有测试当场抓住。
_NOT_SECRET_CHAR = r"(?<![A-Za-z0-9+/=_\-])"
_SECRET_SHAPE_SCANNERS = (
    (re.compile(_NOT_SECRET_CHAR
                + r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"), "jwt"),
    (re.compile(_NOT_SECRET_CHAR + "(?:"
                + "|".join(re.escape(p) for p in SECRET_PREFIXES)
                + r")[A-Za-z0-9+/=_\-]{8,}"),
     "known secret prefix"),
)


def secret_shape_reason(token: str) -> str:
    """这个 token 本身像不像凭据。空串 = 只是个随机串，不是秘密的证据。"""
    if JWT_FORM.match(token):
        return "jwt"
    for prefix in SECRET_PREFIXES:
        if token.startswith(prefix) and len(token) > len(prefix) + 8:
            return f"known secret prefix {prefix!r}"
    labelled = SELF_LABELLED_SECRET.search(token)
    if labelled:
        return f"token names itself {labelled.group(1)!r}"
    return ""


def _shape_findings(text: str, label: str) -> list[dict[str, Any]]:
    """按已知凭据形态扫原文，不经过熵闸。"""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern, reason in _SECRET_SHAPE_SCANNERS:
        for match in pattern.finditer(text):
            token = match.group(0)
            if token in seen or _builtin_allowlist_reason(token):
                continue
            seen.add(token)
            found.append({
                "file": label,
                "token_preview": token[:6] + "…" + token[-4:],
                "length": len(token),
                "entropy": round(shannon_entropy(token), 2),
                "secret_reason": reason,
            })
    return found


def secret_context_reason(text: str, start: int) -> str:
    """token 前面那截文字是不是在说「这是个秘密」。"""
    window = text[max(0, start - SECRET_CONTEXT_WINDOW):start]
    hit = SECRET_CONTEXT.search(window)
    return f"secret-ish key {hit.group(1)!r}" if hit else ""


def scan_text_content(text: str, *, label: str = "payload") -> dict[str, Any]:
    """作者内容落盘前的凭据闸。**只拦有证据是秘密的东西。**

    ## 与 `scan_tree` 的分工（2026-08-29 改这一半，另一半没动）

    `scan_tree` 是 DoD 7 的产物证据扫描，对着**跑出来的**产物做全量熵扫，
    宁可多报——它出的是待复核清单，不阻断谁。
    这个函数不一样：它挂在 `artifacts._assert_batch_payload_safe` 上，
    是**写不写得进去**的硬闸，而被它拦的内容是模型刚写出来的用例与规则。

    2026-08-29 Round 3 实测：它连拦 4 次系统自己生成的中文用例文件
    （`business_frame.json` / `case_draft.json`，里面一个凭据都没有），
    `draft_cases` 因此跑了 6 次，那一轮 23 分 45 秒基本耗在这里。
    复现出来的两类误拦样本：模型写的长断言名
    `expect_json_path_data_0_dialogue_mode`，和 base64 请求体样例
    `eyJtb2RlIjoi…`——两者都满足「≥20 字符、有数字有字母、熵 ≥3.8」。

    **「看起来随机」不是「是秘密」。** 作者内容里长得随机的东西太多了，
    而一个总在对自己产物报警的闸，第一次被撞见时人们绕着走，第二次就把它关掉。

    所以判据从反面的熵启发式换成两条并联的正面证据：token 自身是已知凭据形态
    （`secret_shape_reason`），或者它前面写着 password / token / api_key
    这类键名（`secret_context_reason`）。整段文本里出现 PEM 私钥头也直接算。

    ## 代价，如实写

    一个**裸的**随机口令，既不带已知前缀、也不跟在任何秘密键名后面，
    从此过得了这道闸。守它的仍然是那两条真正在守东西的机制：
    known-secret 精确匹配（配置里的凭据值，一字不差就拦，本次一个字节没动），
    以及 `scan_tree` 对产物的全量熵扫。这条闸让出的是「无根据的怀疑」，
    不是「已知的凭据」。
    """
    if PEM_BLOCK.search(text):
        return {
            "ok": False,
            "entropy_hits": [{"file": label, "token_preview": "-----BEGIN…KEY-----",
                              "length": 0, "entropy": 0.0,
                              "secret_reason": "PEM private key block"}],
            "allowlisted_hits": [],
        }
    entropy_hits, allowlisted_hits = _entropy_findings(text, label, {})
    hits: list[dict[str, Any]] = list(_shape_findings(text, label))
    shaped = {row["token_preview"] for row in hits}
    for finding in entropy_hits:
        if finding.get("token_preview") in shaped:
            continue
        token = str(finding.get("token") or "")
        start = text.find(token)
        reason = (secret_shape_reason(token)
                  or (secret_context_reason(text, start) if start >= 0 else ""))
        if reason:
            hits.append({**finding, "secret_reason": reason})
        else:
            # 不拦，但记下来：熵高却没有秘密证据的串仍然可查，
            # 免得"放宽了什么"只能靠读 diff 才知道。
            allowlisted_hits.append({
                key: value for key, value in {
                    **finding, "allowlist_reason": "no secret shape or context",
                }.items() if key != "token"
            })
    return {
        "ok": not hits,
        "entropy_hits": hits,
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
