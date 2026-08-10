# -*- coding: utf-8 -*-
"""sot_gate — confirmed_facts 的「被测构建」「doc×code 对齐」两栏的牙（移植件）。

原件：test_agent/loop/sot_gate.js（只读采石场；抄语义与测试向量，不抄依赖）。
语义：两栏各判 DONE / DEGRADED / EMPTY / MISSING_SECTION / HEAD_SHAPE；
非 DONE 的栏必须在「零access降级声明」段逐栏交出【谁来补】+【补不到的后果】，
两者不得是占位词，后果须是真后果（含后果类词且 ≥10 字）。fail-closed 偏严不偏松。

M1 用法：clarify 工具落 business_frame 时对其 confirmed_facts_md 字段跑本闸。
"""
from __future__ import annotations

import re
from typing import Any

# 标题编号前缀（BB-259/BP-1：编号是排版不是契约）
HEAD_NUM = re.compile(
    r"^\s*(?:第?\s*[0-9０-９一二三四五六七八九十百]+\s*[、.．)）]?\s*(?:节|章|部分|条)?"
    r"|[（(]\s*[0-9０-９一二三四五六七八九十]+\s*[)）]"
    r"|[①-⑳]"
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[、.．)）]?)\s*")

_HEAD_LINE = re.compile(r"^#{2,4}\s+\S")


def _head_text(line: str) -> str:
    t = re.sub(r"^#{1,6}\s*", "", str(line))
    t = re.sub(r"[*_`]", "", t).strip()
    before = t
    t = HEAD_NUM.sub("", t, count=1).strip()
    return t or before


COLUMNS = [
    {"key": "build_under_test", "label": "被测构建",
     "head": re.compile(r"^被测构建"),
     "shape": "## 被测构建(可带编号,如 `## 1. 被测构建`)",
     "decl": re.compile(r"被测构建|部署构建|构建确认|部署身份")},
    {"key": "doc_code_align", "label": "doc×code 对齐",
     "head": re.compile(r"^doc\s*[×xX*]\s*code"),
     "shape": "## doc×code 对齐(× 也可写 x/X/*,可带编号)",
     "decl": re.compile(r"doc\s*[×xX*]\s*code|反向对账|code\s*→?\s*SPEC|源码对账")},
]

DECL_HEAD_T = re.compile(r"^(零\s*access\s*)?(降级声明|补齐责任|补齐声明|降级与补齐)", re.I)


def _is_decl_head(line: str) -> bool:
    return bool(_HEAD_LINE.match(line)) and bool(DECL_HEAD_T.match(_head_text(line)))


UNDONE_MARK = re.compile(
    r"(未执行|未确认|未做|未验证|未取得|未探|待确认|待补|待做|无法确认|不可得|拿不到"
    r"|无目标仓库|没有仓库|跳过|暂无|N/A|不适用|TBD)", re.I)
SCAFFOLD = re.compile(
    r"^(验收点|一致|doc_missing|code_missing|冲突|处置|分支tip|部署构建|冻结部署marker"
    r"|一致\?|-+|:+)$")
PLACEHOLDER = {"", "-", "—", "–", "待定", "tbd", "TBD", "n/a", "N/A", "na", "NA",
               "无", "未知", "?", "？", "待补", "待确认", "同上", "x", "X"}
CONSEQ_MARK = re.compile(
    r"(不作数|不担保|不保证|不可信|封顶|假设|作废|失效|风险|导致|无法|不算|误判|返工"
    r"|重跑|重测|上浮|阻塞|BLOCK|PENDING|白测|漏|错|资损|不生效|不成立|只能|退回|推翻)", re.I)
OWNER_MARK = re.compile(
    r"(谁来补|谁补|由|责任|负责|owner|人工|开发|产品|测试|运维|用户|我|下一轮|N4"
    r"|执行前|提测方)", re.I)

_SEP_ROW = re.compile(r"^\s*\|[\s:|.\-—–]+\|?\s*$")


def _split_sections(text: str) -> list[dict[str, Any]]:
    secs: list[dict[str, Any]] = []
    cur = None
    for l in re.split(r"\r?\n", text):
        if _HEAD_LINE.match(l):
            cur = {"head": l, "body": []}
            secs.append(cur)
        elif cur is not None:
            cur["body"].append(l)
    return secs


def _cells_of(l: str) -> list[str]:
    body = re.sub(r"^\s*\|", "", l)
    body = re.sub(r"\|\s*$", "", body)
    return [c.strip() for c in body.split("|")]


def _substantive(body: list[str]) -> str:
    parts = []
    for l in body:
        if not l.strip() or _SEP_ROW.match(l):
            continue
        if re.match(r"^\s*\|", l):
            for c in _cells_of(l):
                t = re.sub(r"[<>\[\]]", "", c).strip()
                if not t or SCAFFOLD.match(t) or t in PLACEHOLDER:
                    continue
                parts.append(t)
        else:
            t = re.sub(r"^[>\-*\s]+", "", l).strip()
            if t:
                parts.append(t)
    return " ".join(parts)


def _s0(h: str) -> str:
    return str(h).strip()[:40]


def _col_status(secs: list[dict[str, Any]], col: dict[str, Any]) -> dict[str, Any]:
    sec = next((s for s in secs if col["head"].search(_head_text(s["head"]))), None)
    if sec is None:
        near = next((s for s in secs if col["decl"].search(_head_text(s["head"]))), None)
        if near is not None:
            return {"status": "HEAD_SHAPE", "text": "", "near_head": _s0(near["head"])}
        return {"status": "MISSING_SECTION", "text": ""}
    text = _substantive(sec["body"])
    if UNDONE_MARK.search(text) or UNDONE_MARK.search(sec["head"]):
        return {"status": "DEGRADED", "text": text}
    if len(re.sub(r"\s", "", text)) < 8:
        return {"status": "EMPTY", "text": text}
    return {"status": "DONE", "text": text}


def _find_decl(secs: list[dict[str, Any]], col: dict[str, Any]) -> dict[str, str] | None:
    sec = next((s for s in secs if _is_decl_head(s["head"])), None)
    if sec is None:
        return None
    for l in sec["body"]:
        if not l.strip() or _SEP_ROW.match(l):
            continue
        if not col["decl"].search(l):
            continue
        # 表头行跳过
        if re.match(r"^\s*\|", l) and any(re.search(r"缺栏|栏位", c) for c in _cells_of(l)):
            continue
        owner, conseq = "", ""
        if re.match(r"^\s*\|", l):
            cs = _cells_of(l)
            idx = next((i for i, c in enumerate(cs) if col["decl"].search(c)), -1)
            owner = cs[idx + 1] if 0 <= idx + 1 < len(cs) else ""
            conseq = " ".join(cs[idx + 2:]) if idx >= 0 else ""
        else:
            mo = re.search(r"(谁来补|谁补|补齐人|责任人|owner)\s*[=:：]\s*([^·;；|]+)", l, re.I)
            mc = re.search(r"(补不到的后果|后果|影响)\s*[=:：]\s*(.+)$", l)
            owner = mo.group(2) if mo else ""
            conseq = mc.group(2) if mc else ""
        return {"line": l.strip(), "owner": owner.strip(), "conseq": conseq.strip()}
    return None


def _bad_field(v: str) -> bool:
    t = re.sub(r"[<>\[\]]", "", v or "").strip()
    return not t or t in PLACEHOLDER or t.lower() in PLACEHOLDER


def check(text: str) -> dict[str, Any]:
    """返回 {verdict: PASS|REJECT, columns, problems}。"""
    secs = _split_sections(text)
    out: dict[str, Any] = {"verdict": "PASS", "columns": {}, "problems": []}
    has_decl_sec = any(_is_decl_head(s["head"]) for s in secs)
    for col in COLUMNS:
        st = _col_status(secs, col)
        out["columns"][col["key"]] = {"label": col["label"], "status": st["status"]}
        if st["status"] == "HEAD_SHAPE":
            out["problems"].append(
                f"「{col['label']}」= 标题格式不符(不是缺内容):找到疑似小节「{st['near_head']}」,"
                f"但闸认不出。 期望形状:{col['shape']}。"
                "标题词必须是小节标题的开头(编号前缀 OK,别的前缀不行)")
            continue
        if st["status"] == "MISSING_SECTION":
            out["problems"].append(
                f"confirmed_facts 缺「{col['label']}」节(全文没有任何标题沾边)"
                f"—— 连\"没做\"都没申报。 期望形状:{col['shape']}")
            continue
        if st["status"] == "DONE":
            continue
        d = _find_decl(secs, col)
        out["columns"][col["key"]]["decl"] = (
            {"owner": d["owner"], "conseq": d["conseq"]} if d else None)
        if not has_decl_sec or not d:
            out["problems"].append(
                f"「{col['label']}」= {st['status']}(空/标未执行),但降级声明段里没有它的条目 "
                "—— 零 access 可以不做,但必须写清「谁来补 / 补不到的后果」才准过闸")
            continue
        if _bad_field(d["owner"]):
            out["problems"].append(
                f"「{col['label']}」降级声明缺【谁来补】(或填了占位词:{d['owner'] or '空'})"
                " —— 补齐责任落不到人 = 这个缺口没有出口")
        if _bad_field(d["conseq"]):
            out["problems"].append(
                f"「{col['label']}」降级声明缺【补不到的后果】(或填了占位词:{d['conseq'] or '空'})")
        elif len(re.sub(r"\s", "", d["conseq"])) < 10 or not CONSEQ_MARK.search(d["conseq"]):
            out["problems"].append(
                f"「{col['label']}」的【补不到的后果】不是后果,只是复述\"没做\":{d['conseq'][:30]}"
                " —— 要写\"这栏补不到会让什么结论不作数/什么风险留着\"")
        elif not OWNER_MARK.search(d["owner"]) and len(d["owner"]) < 2:
            out["problems"].append(f"「{col['label']}」的【谁来补】看不出是谁:{d['owner']}")
    if out["problems"]:
        out["verdict"] = "REJECT"
    return out
