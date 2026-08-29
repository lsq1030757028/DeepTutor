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
from typing import Any, Callable
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError

from server.journey import artifacts, redlines
from server.journey.digest import text_digest

TIERS = ("checklist", "standard", "deep")

#: 环境状态。`ready` = 有真实可达地址（默认）；`pending` = **环境还没好，用户明说了
#: 先出用例**。
#:
#: 为什么要有 pending（2026-08-29 UAT 实测，V5）：探活闸原本是接入的硬前置，
#: 于是"环境没好"这件本该如实说出口的事，在闸面前只剩两条路——停摆，或**编一个
#: 地址骗过闸**。模型选了后者：先编 `https://tbd-pending-environment.invalid`
#: 被自己的闸拦下，再换一个"能连通的占位主机"绕过去。
#: **闸把诚实变成了死路，模型就只能不诚实**，这不是模型的毛病，是闸的位置错了。
#:
#: 按 2026-08-29「闸位重定标」决策（防编造闸从拦入口移到拦断言与写入出口）：
#: 出用例根本不需要环境——澄清、分析、用例草稿全是对着需求正文做的。所以探活闸
#: 下移到**真要执行**那一刻（`tools.execute`），接入阶段允许显式挂起。
#: 显式挂起与"忘了填"必须可分：pending 是用户拍板的状态，会写进 intake_profile
#: 并在产物上留标注，不是默认值。
ENVIRONMENT_STATUSES = ("ready", "pending")

#: 挂起批次在产物上的标注。执行闸按这条拒绝，用户在工作台也看得见。
ENVIRONMENT_PENDING_NOTE = "环境待定，执行前必须补真实地址"

#: 风险×复杂度机械启发式的词表（可解释，不是判官）
#:
#: 2026-08-29 复验（Round 2 卡点 A）补设备控制族。原词表只覆盖**表单/交易**语义
#: （新增/创建/下单/支付/审批…），而被测需求是「切换对话模式、调亮度、进待机」这类
#: **设备状态写入**，一个词都不命中，于是 560 字、13 条写用例的需求被判成
#: 「0 write verbs」→ 推荐 checklist → 用户按推荐走就永远到不了用例库。
#:
#: 词表按**动作族**扩，不按单个需求补：改/调/切/设/开关/下发/控制 是一族，
#: 绑定/解绑/重置/清除/取消 是一族。宁可多命中——多命中只会把档位推高，
#: 而档位推高的代价是多花时间，档位推低的代价是用户以为测过了。
_WRITE_HINTS = re.compile(
    r"(新增|创建|新建|删除|移除|修改|编辑|更新|保存|提交|审批|导入|上传|下单|支付|退款"
    r"|切换|设置|设定|配置|开启|关闭|打开|启用|停用|下发|控制|调节|调整|调高|调低"
    r"|绑定|解绑|重置|清除|清空|取消|恢复|唤醒|待机|重启|升级|同步|发送|推送|播放|暂停)")
_ROLE_HINTS = re.compile(r"(角色|权限|管理员|销售员|操作员|普通用户)")

#: 「没检测到写动词」到「确实没有写操作」之间隔着一整个词表的覆盖面。
#: 正文长到这个数还一个写动词都不命中时，判为**写意图未知**而不是「无写操作」，
#: 推荐档下限抬到 standard。理由见 `propose_tier`。
_WRITE_UNKNOWN_MIN_CHARS = 200


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
    """机械定档建议：风险×复杂度 → checklist / standard / deep + 可解释理由。

    ## checklist 只在「确知没有写操作」时才推荐（2026-08-29 复验后加）

    原实现里 checklist 是**分数低的默认落点**，而分数低有两种完全不同的成因：
    需求真的小，或者**词表没覆盖到这个领域的写动词**。两者在分数上不可区分，
    于是后者被当成前者：13 条写设备状态的需求拿到「0 write verbs」+ checklist 推荐，
    而 checklist 明说不建用例库——用户按推荐走，拿到一份检查清单就以为测完了。

    所以「没检测到」不再等价于「没有」：正文有一定长度却零命中时判为
    **写意图未知**，推荐下限抬到 standard。要落回 checklist，得由调用方
    显式传 `writes_expected=False`——那是一句有主体的断言，不是一次匹配失败。

    抬高档位的代价是多花时间，压低档位的代价是用户以为测过了。不对称，所以偏严。
    """
    text = requirement_text or ""
    plain = re.sub(r"\s", "", text)
    n_chars = len(plain)
    n_writes = len(_WRITE_HINTS.findall(text))
    n_roles = len(set(_ROLE_HINTS.findall(text)))
    write_unknown = (n_writes == 0 and writes_expected is None
                     and n_chars >= _WRITE_UNKNOWN_MIN_CHARS)
    # 理由栏如实写「未检测到」而不是「0 处写操作」：后者是业务结论，而这里
    # 只做了一次词表匹配。模型会照着理由栏对用户说话（Round 2 的「0 write verbs」
    # 就是这么上屏的）。
    reasons = [f"需求正文 {n_chars} 字",
               ("未检测到写动词（词表未必覆盖本领域，不等于没有写操作）"
                if n_writes == 0 else f"写操作动词 {n_writes} 处"),
               f"角色词 {n_roles} 类"]
    if writes_expected is not None:
        reasons.append(f"调用方声明含写操作={writes_expected}")
    score = ((2 if n_chars >= 1500 else 1 if n_chars >= 300 else 0)
             + (2 if n_writes >= 8 else 1 if n_writes >= 2 else 0)
             + (1 if n_roles >= 2 else 0)
             + (1 if writes_expected else 0))
    tier = "checklist" if score <= 1 else ("standard" if score <= 4 else "deep")
    if write_unknown and tier == "checklist":
        tier = "standard"
        reasons.append("写意图未确认，推荐档下限抬到 standard——checklist 不建用例库，"
                       "万一有写操作就等于漏测")
    return {"proposed_tier": tier, "score": score, "reasons": reasons,
            "write_intent": ("detected" if n_writes else
                             "unknown" if write_unknown else "declared_none"),
            "card": {
                "question": "本批次按哪个档位走？",
                "options": [
                    {"value": "checklist",
                     "label": "checklist：只出一份检查清单供人工勾选，"
                              "不建用例库、不能执行；需要用例库请选 standard"},
                    {"value": "standard",
                     "label": "standard：完整链（澄清→分析→用例库→采纳→执行），"
                              "「正式测一条需求」要的就是这一档"},
                    {"value": "deep",
                     "label": "deep：标准链 + 全量故障注入与双趟复执行"},
                ],
                "recommended": tier,
            }}


def ingest(title: str, base_url: str, *, source_kind: str, source_ref: str,
           requirement_text: str = "", environment_ref: str = "",
           tier: str = "", tier_confirmed_via: str = "",
           requirement_entity: str = "",
           requirement_entity_confirmed_via: str = "",
           requirement_entity_decision: dict[str, Any] | None = None,
           before_create: Callable[[], dict[str, Any] | None] | None = None,
           supersedes: str = "", owner: str = "",
           environment_status: str = "") -> dict[str, Any]:
    """建批次 + 落 intake_profile。tier 未给时只回确认卡数据（不落产物——
    档位是 intake_profile 的必备字段，人闸没走完就没有这个产物）。

    `environment_status="pending"` 走诚实挂起：不探活、不要地址，批次照建，
    产物标 `ENVIRONMENT_PENDING_NOTE`，执行闸在 `tools.execute` 拦。
    """
    environment_status = str(environment_status or "").strip().lower()
    if environment_status and environment_status not in ENVIRONMENT_STATUSES:
        return {"ok": False,
                "error": f"environment_status 只能是 {ENVIRONMENT_STATUSES} 之一，"
                         f"实为 {environment_status!r}"}
    pending_env = environment_status == "pending"
    if pending_env:
        # 挂起就不许再带地址：带了说明它不是真的挂起，而是**编了一个占位地址**
        # 想两头讨好——那正是本条要根治的行为。
        if str(base_url or "").strip():
            # error 会被上层当 message 渲染给用户，所以它只说人话；
            # 参数名与照做步骤走 machine（2026-08-29 Round 2 复验：
            # `environment_status="pending"` 直接怼到了用户脸上）。
            return {"ok": False,
                    "error": "说了环境待定就不要再给地址了，我先按待定往下走。",
                    "machine": "环境挂起时禁止同时传 base_url：占位地址、.invalid "
                               "域名、随手找的可连通主机都算编造。重调时清空 "
                               "base_url,真地址拿到后再开新批次或补录。"}
        safe_base_url = ""
        probe = {"reachable": False, "skipped": True,
                 "error": ENVIRONMENT_PENDING_NOTE}
    else:
        if not str(base_url or "").strip():
            return {"ok": False,
                    "error": "还没有测试环境地址。环境没好也能先把用例出出来，"
                             "我按这条走；等地址有了再补。",
                    "machine": "没有 base_url。环境未就绪时改传 "
                               "environment_status=\"pending\" 且不传 base_url,"
                               "照常走到用例草稿;绝不要编一个占位地址。"}
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
    supersedes = str(supersedes or "").strip()
    if supersedes:
        try:
            artifacts.load_batch(supersedes)
        except artifacts.ArtifactError:
            return {"ok": False, "code": "E_NO_BATCH",
                    "error": f"要替代的批次不存在：{supersedes}。"
                             "替代关系必须指向真实批次，否则账本里会出现悬空指针。"}
    if not probe.get("reachable") and not pending_env:
        # 溯源/能力锁：终点不可达 = 不建批次（fail-closed，不能对空气接单）
        #
        # 原因必须进 error 本身，不能只留在 probe 里（AC3）：调用方拿到的是这个
        # 串，会照着它对用户说话。域名解析不了 / 被重定向到别的站 / 协议不对——
        # 三种原因三条不同的下一步，糊成「不可达」三个字等于让用户自己猜。
        reason = str(probe.get("error") or "").strip() or "未知原因"
        return {"ok": False,
                "error": f"接入终点不可达，不建批次。地址={safe_base_url}；"
                         f"探活失败原因：{reason}",
                "probe": probe}
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
    if before_create is not None:
        blocked = before_create()
        if blocked is not None:
            return blocked
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
        "environment_status": "pending" if pending_env else "ready",
        "environment_note": ENVIRONMENT_PENDING_NOTE if pending_env else "",
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
        "supersedes": supersedes,
    })
    artifacts.append_event(bid, {"type": "tier_confirm", "tier": tier,
                                 "via": tier_confirmed_via or "unspecified",
                                 "source": "intake"})
    if supersedes:
        # AC17 改需求实体：旧批次标被替代、新批次记来源，两边都留可查的事件。
        # 旧批次的产物一个不删——「被替代」是状态不是销毁，_PREREQ 链保持完整。
        artifacts.append_event(supersedes, {
            "type": "batch_superseded", "superseded_by": bid,
            "reason": "requirement_entity_change"})
        artifacts.append_event(bid, {
            "type": "batch_supersedes", "supersedes": supersedes})
    return {"ok": True, "batch_id": bid, "intake_profile": profile}
