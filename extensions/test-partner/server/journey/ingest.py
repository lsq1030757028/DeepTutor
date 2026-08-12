# -*- coding: utf-8 -*-
"""ingest — 原子工具 1：接入 + 定档。

输入：URL + 凭证引用 + 需求文档 → 输出 `intake_profile`（终点 + 档位确认卡数据）。
牙（挂产物）：溯源/能力锁——base_url 可达性探测 + 实例指纹采集（sot_gate 前置字段）。

定档分流（checklist / standard / deep）：机械启发式给推荐档 + 确认卡数据，
档位确认是人闸（ask_user；0015 授权补充下由 manager 代持，tier_confirmed_via 记出处）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError

from server.journey import artifacts, redlines
from server.journey.digest import text_digest

TIERS = ("checklist", "standard", "deep")

#: 风险×复杂度机械启发式的词表（可解释，不是判官）
_WRITE_HINTS = re.compile(r"(新增|创建|删除|修改|编辑|下单|支付|退款|提交|审批|导入|上传)")
_ROLE_HINTS = re.compile(r"(角色|权限|管理员|销售员|操作员|普通用户)")


class _NoAutomaticRedirect(HTTPRedirectHandler):
    """Expose 30x before a second request is sent so the host gate runs first."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def probe_target(base_url: str, timeout_s: int = 10) -> dict[str, Any]:
    """可达性探测 + 实例指纹素材。只发 GET /，不带凭证。"""
    validated = redlines.safe_target_url(base_url)
    if not validated["ok"]:
        return {"reachable": False, "error": validated["error"]}
    url = validated["url"]
    opener = build_opener(_NoAutomaticRedirect())
    current = url + "/"
    for _hop in range(6):
        try:
            req = Request(current, method="GET")
            with opener.open(req, timeout=timeout_s) as resp:  # noqa: S310
                status = resp.status
                body = resp.read(4096)
                final_url = resp.geturl()
                headers = dict(resp.headers)
            break
        except HTTPError as exc:
            headers = dict(exc.headers or {})
            if 300 <= exc.code < 400 and headers.get("Location"):
                next_url = urljoin(current, headers["Location"])
                if not redlines.same_host(url, next_url):
                    return {"reachable": False,
                            "error": f"GET / 重定向落点越出等价类: {next_url}"}
                current = next_url
                continue
            status = exc.code
            body = exc.read(4096) if exc.fp else b""
            final_url = current
            break
        except (URLError, OSError, ValueError) as exc:
            return {"reachable": False, "error": str(exc)}
    else:
        return {"reachable": False, "error": "GET / 重定向次数超过 5 次"}
    text = body.decode("utf-8", "replace")
    title_m = re.search(r"<title[^>]*>([^<]{0,120})</title>", text, re.I)
    return {
        "reachable": True,
        "status": status,
        "page_title": (title_m.group(1).strip() if title_m else ""),
        "body_head_sha256": hashlib.sha256(body).hexdigest(),
        # ── API 轨的两维（M2 / 设计稿 §8.2）────────────────────────────────
        # `page_title` 是 HTML 概念，在 JSON 后端上恒返空串——指纹少一维却不报警。
        # 这两维替它在 API 轨上承担"后端是不是换了一个服务"的判别力。
        # **只落键名与 Server 值，不落任何 header 的值**（凭据纪律）。
        "service_banner": _service_banner(headers),
        "content_type": (headers.get("Content-Type", "").split(";")[0].strip()
                         or None),
    }


def _service_banner(headers: dict[str, str]) -> str | None:
    """`Server` 值 + 版本类头的**键名**（不是值）。取不到记 None。

    键名足以判别"换了一个服务"，值可能带内网信息或版本细节，不落。
    """
    parts: list[str] = []
    server = str(headers.get("Server") or "").strip()
    if server:
        parts.append(server)
    for name in sorted(headers):
        lowered = name.lower()
        if lowered.startswith("x-") and (
                "version" in lowered or lowered == "x-request-id"):
            parts.append(f"+{name}")
    return " ".join(parts) or None


def propose_tier(requirement_text: str, writes_expected: bool | None = None) -> dict[str, Any]:
    """机械定档建议：风险×复杂度 → checklist / standard / deep + 可解释理由。"""
    text = requirement_text or ""
    plain = re.sub(r"\s", "", text)
    n_chars = len(plain)
    n_writes = len(_WRITE_HINTS.findall(text))
    n_roles = len(set(_ROLE_HINTS.findall(text)))
    reasons = [f"需求正文 {n_chars} 字", f"写操作动词 {n_writes} 处", f"角色词 {n_roles} 类"]
    if writes_expected is not None:
        reasons.append(f"调用方声明含写操作={writes_expected}")
    score = (2 if n_chars >= 1500 else 1 if n_chars >= 300 else 0) \
        + (2 if n_writes >= 8 else 1 if n_writes >= 2 else 0) \
        + (1 if n_roles >= 2 else 0) \
        + (1 if writes_expected else 0)
    tier = "checklist" if score <= 1 else ("standard" if score <= 4 else "deep")
    return {"proposed_tier": tier, "score": score, "reasons": reasons,
            "card": {
                "question": "本批次按哪个档位走？",
                "options": [
                    {"value": "checklist", "label": "checklist：小需求 → R 规则+checklist,人勾完成,不建用例库"},
                    {"value": "standard", "label": "standard：完整链(澄清→分析→用例→采纳→编译→执行→覆盖图)"},
                    {"value": "deep", "label": "deep：标准链+全量故障注入与双趟复执行"},
                ],
                "recommended": tier,
            }}


def ingest(title: str, base_url: str, *, source_kind: str, source_ref: str,
           requirement_text: str = "", environment_ref: str = "",
           tier: str = "", tier_confirmed_via: str = "",
           requirement_entity: str = "",
           requirement_entity_confirmed_via: str = "",
           requirement_entity_decision: dict[str, Any] | None = None,
           owner: str = "") -> dict[str, Any]:
    """建批次 + 落 intake_profile。tier 未给时只回确认卡数据（不落产物——
    档位是 intake_profile 的必备字段，人闸没走完就没有这个产物）。"""
    validated = redlines.safe_target_url(base_url)
    if not validated["ok"]:
        return {"ok": False, "error": validated["error"]}
    safe_base_url = validated["url"]
    probe = probe_target(safe_base_url)
    proposal = propose_tier(requirement_text)
    if not tier:
        return {"ok": False, "need": "tier_confirmation",
                "probe": probe, **proposal}
    if tier not in TIERS:
        return {"ok": False, "error": f"tier 必须是 {TIERS} 之一，实为 {tier!r}"}
    requirement_entity = str(requirement_entity or "").strip()
    requirement_entity_confirmed_via = str(
        requirement_entity_confirmed_via or ""
    ).strip()
    if bool(requirement_entity) != bool(requirement_entity_confirmed_via):
        return {
            "ok": False,
            "error": "需求实体及其人工确认来源必须成对提供；机器不得自行猜测写入对象。",
        }
    if requirement_entity and not requirement_entity_decision:
        return {
            "ok": False,
            "error": "需求实体必须来自服务端验真的交互式用户决定；确认来源字符串仅供审计。",
        }
    if not probe.get("reachable"):
        # 溯源/能力锁：终点不可达 = 不建批次（fail-closed，不能对空气接单）
        return {"ok": False, "error": "接入终点不可达，不建批次", "probe": probe}
    if requirement_text:
        from server.journey.gates import credential_scan
        requirement_scan = credential_scan.scan_text_content(
            requirement_text, label="requirement.txt")
        if not requirement_scan["ok"]:
            return {
                "ok": False,
                "error": "需求正文疑似包含凭据，已在创建批次前拒绝；请移除或变量化后重试",
                "credential_scan": {
                    "entropy_hit_count": len(requirement_scan["entropy_hits"]),
                },
            }
    batch = artifacts.create_batch(title, owner=owner, base_url=safe_base_url,
                                   environment_ref=environment_ref,
                                   source_ref=source_ref)
    bid = batch["batch_id"]
    if requirement_text:
        # 需求正文快照落批次目录（oracle 出处锚，content_digest 的对象）
        import os
        snap = os.path.join(artifacts.batch_dir(bid), "requirement.txt")
        with open(snap, "w", encoding="utf-8") as fh:
            fh.write(requirement_text)
    profile = artifacts.save_artifact(bid, "intake_profile", {
        "base_url": safe_base_url,
        "environment_ref": environment_ref,
        "source": {"kind": source_kind, "ref": source_ref,
                   "content_digest": (text_digest(requirement_text)
                                      if requirement_text else "")},
        "target_probe": probe,
        "tier": tier,
        "tier_proposal": {k: proposal[k] for k in ("proposed_tier", "score", "reasons")},
        "tier_confirmed_via": tier_confirmed_via or "unspecified",
        "requirement_entity": requirement_entity,
        "requirement_entity_confirmed_via": requirement_entity_confirmed_via,
        "requirement_entity_decision": dict(requirement_entity_decision or {}),
    })
    artifacts.append_event(bid, {"type": "tier_confirm", "tier": tier,
                                 "via": tier_confirmed_via or "unspecified"})
    return {"ok": True, "batch_id": bid, "intake_profile": profile}
