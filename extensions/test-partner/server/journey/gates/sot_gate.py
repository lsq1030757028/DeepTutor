# -*- coding: utf-8 -*-
"""sot_gate — confirmed_facts 的「被测构建」「doc×code 对齐」两栏的牙（移植件）。

原件：test_agent/loop/sot_gate.js（只读采石场；抄语义与测试向量，不抄依赖）。
语义：两栏各判 DONE / DEGRADED / EMPTY / MISSING_SECTION / HEAD_SHAPE；
非 DONE 的栏必须在「零access降级声明」段逐栏交出【谁来补】+【补不到的后果】，
两者不得是占位词，后果须是真后果（含后果类词且 ≥10 字）。fail-closed 偏严不偏松。

M1 用法：clarify 工具落 business_frame 时对其 confirmed_facts_md 字段跑本闸。

## 降级声明条目认哪些形状（BB-673 修）

2026-08-28 UAT 实测：模型三次交出**语义完整**的降级声明（谁来补、后果都真写了），
三次被拒——旧解析只认「表格行」或「栏名+谁来补+后果同在一行且用冒号分隔」，
而错误文案从没把这个形状要求说出来，模型照提示补齐照样被拒，10 分钟重试循环。
偏严要偏在**内容**上（占位词、假后果照拦），不该偏在**排版**上。现在认四种：

1. 表格行：`| 被测构建 | <谁来补> | <补不到的后果> |`
2. 单行：`被测构建：谁来补：<人>；补不到的后果：<后果>`
3. 多行块：声明段内以栏名开头的行起，到下一个栏名行止，
   块内「谁来补：…」「补不到的后果：…」各占一行
4. 小节：`## 降级声明` 下的 `### 被测构建` 小节（标题会被切段，不再因此判丢）

键值分隔符认 `：` `:` `=` 与破折号（`——`/`—`/`--`）；行内的 markdown
强调符（`**` `_` 反引号）先剥掉再匹配。
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


#: 键值分隔：冒号/等号/破折号。中文里「谁来补——某人」与「谁来补：某人」
#: 是同一句话，只认冒号就是在拿排版拒人（BB-673 第一击）。
_KV_SEP = r"\s*(?:[=:：]|—+|--)\s*"
_OWNER_RE = re.compile(
    r"(?:谁来补|谁补|补齐人|责任人|owner)" + _KV_SEP
    + r"((?:(?!补不到的后果)[^·;；|])+)", re.I)
_CONSEQ_RE = re.compile(r"(?:补不到的后果|后果|影响)" + _KV_SEP + r"(.+)$")


def _norm_line(line: str) -> str:
    """剥掉 markdown 强调符再匹配——`**谁来补**：` 与 `谁来补：` 是同一句话。"""
    return re.sub(r"[*_`]", "", str(line))


def _decl_blocks(secs: list[dict[str, Any]], col: dict[str, Any]
                 ) -> list[list[str]] | None:
    """收集属于某栏的降级声明条目块。没有声明段返回 None，有段无条目返回 []。

    块的边界（对应模块文档串的四种形状）：
    - 声明段正文里：命中本栏关键词的行起，到命中**别的栏**关键词的行止——
      「栏名一行、谁来补/后果各一行」的多行写法因此不再判丢（BB-673 第三击）；
    - 声明段之后紧跟的小节（`### 被测构建`）：`_split_sections` 会把小节标题
      切成独立 section，旧实现因此看不见它（BB-673 第二击）。连续的栏名小节
      都算声明区域，遇到第一个不是栏名的标题就离开。
    """
    idx = next((i for i, s in enumerate(secs) if _is_decl_head(s["head"])), None)
    if idx is None:
        return None
    blocks: list[list[str]] = []
    body = secs[idx]["body"]
    start: int | None = None
    for j, raw in enumerate(body):
        line = _norm_line(raw)
        if col["decl"].search(line):
            if start is None:
                start = j
        elif start is not None and any(
                c["decl"].search(line) for c in COLUMNS if c is not col):
            blocks.append(body[start:j])
            start = None
    if start is not None:
        blocks.append(body[start:])
    for sec in secs[idx + 1:]:
        head = _head_text(sec["head"])
        if col["decl"].search(head):
            blocks.append([sec["head"], *sec["body"]])
        elif not any(c["decl"].search(head) for c in COLUMNS):
            break
    return blocks


def _parse_decl_block(block: list[str], col: dict[str, Any]) -> dict[str, str]:
    owner, conseq, first = "", "", ""
    for raw in block:
        if not raw.strip() or _SEP_ROW.match(raw):
            continue
        if not first:
            first = raw.strip()
        line = _norm_line(raw)
        if re.match(r"^\s*\|", raw):
            # 表头行跳过
            if any(re.search(r"缺栏|栏位", c) for c in _cells_of(raw)):
                continue
            if col["decl"].search(line):
                cs = _cells_of(raw)
                hit = next((i for i, c in enumerate(cs)
                            if col["decl"].search(_norm_line(c))), -1)
                if not owner:
                    owner = cs[hit + 1] if 0 <= hit + 1 < len(cs) else ""
                if not conseq:
                    conseq = " ".join(cs[hit + 2:]) if hit >= 0 else ""
            continue
        if not owner:
            mo = _OWNER_RE.search(line)
            if mo:
                owner = mo.group(1)
        if not conseq:
            mc = _CONSEQ_RE.search(line)
            if mc:
                conseq = mc.group(1)
    return {"line": first, "owner": owner.strip(), "conseq": conseq.strip()}


def _find_decl(secs: list[dict[str, Any]], col: dict[str, Any]) -> dict[str, str] | None:
    blocks = _decl_blocks(secs, col)
    if not blocks:
        return None
    parsed = [_parse_decl_block(b, col) for b in blocks]
    return next((p for p in parsed if p["owner"] or p["conseq"]), parsed[0])


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
                "—— 零 access 可以不做,但必须写清「谁来补 / 补不到的后果」才准过闸。"
                f"照这个形状写(任选其一):在「## 降级声明」段里加一行"
                f"「{col['label']}:谁来补:<具体的人或角色>;补不到的后果:<真实后果>」,"
                f"或表格行「| {col['label']} | <谁来补> | <补不到的后果> |」,"
                f"或小节「### {col['label']}」下「谁来补:…」「补不到的后果:…」各一行")
            continue
        if _bad_field(d["owner"]):
            out["problems"].append(
                f"「{col['label']}」降级声明缺【谁来补】(或填了占位词:{d['owner'] or '空'})"
                " —— 补齐责任落不到人 = 这个缺口没有出口。"
                "在该条目里写一行「谁来补:<具体的人或角色>」(冒号/等号/破折号都认)")
        if _bad_field(d["conseq"]):
            out["problems"].append(
                f"「{col['label']}」降级声明缺【补不到的后果】(或填了占位词:{d['conseq'] or '空'})"
                " —— 在该条目里写一行「补不到的后果:<这栏补不到会让什么结论不作数"
                "/什么风险留着>」(冒号/等号/破折号都认)")
        elif len(re.sub(r"\s", "", d["conseq"])) < 10 or not CONSEQ_MARK.search(d["conseq"]):
            out["problems"].append(
                f"「{col['label']}」的【补不到的后果】不是后果,只是复述\"没做\":{d['conseq'][:30]}"
                " —— 要写\"这栏补不到会让什么结论不作数/什么风险留着\"")
        elif not OWNER_MARK.search(d["owner"]) and len(d["owner"]) < 2:
            out["problems"].append(f"「{col['label']}」的【谁来补】看不出是谁:{d['owner']}")
    if out["problems"]:
        out["verdict"] = "REJECT"
    return out
