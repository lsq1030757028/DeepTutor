# -*- coding: utf-8 -*-
"""downstream_gate — 「下游消费面盘点」拦截闸（移植件，设计阶段拦）。

原件：test_agent/loop/downstream_gate.js（只读采石场；抄语义与测试向量，不抄依赖）。
语义：test_analysis 必须显式盘点——业务对象 × 生命周期六档
（获得→使用→同类共存→计量结算→撤销退回→记账对账），每档要么有判别用例、
要么显式不覆盖（谁覆盖 + 后果）；盘点缺失/环节缺行/没人接/整条零覆盖 → 打回。
「同类共存」是必答档。表头驱动定位列（BB-259/BP-2），标题编号是排版不是契约。

M1 用法：analyze 工具落 test_analysis 时对其 analysis_md 字段跑本闸。
"""
from __future__ import annotations

import json
import re
from typing import Any

HEAD_NUM = re.compile(
    r"^\s*(?:第?\s*[0-9０-９一二三四五六七八九十百]+\s*[、.．)）]?\s*(?:节|章|部分|条)?"
    r"|[（(]\s*[0-9０-９一二三四五六七八九十]+\s*[)）]"
    r"|[①-⑳]"
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[、.．)）]?)\s*")
_HEAD_LINE = re.compile(r"^#{2,4}\s+\S")


def _head_text(line: str) -> str:
    t = re.sub(r"^#{1,6}\s*", "", str(line))
    t = re.sub(r"[*_`]", "", t).strip()
    return HEAD_NUM.sub("", t, count=1).strip() or t


SECTION_TITLE = re.compile(r"^(下游消费面盘点|下游消费面|消费面盘点)")
SECTION_NEAR = re.compile(r"(下游|消费面|盘点)")
TABLE_SHAPE = ("| 业务对象 | 生命周期环节 | 判别用例 | 不覆盖声明(谁覆盖·后果) |"
               "  ← 表头行必须在,列名可换同义词(对象/产出物 · 环节/阶段 · 用例/覆盖用例 · 声明/不覆盖)")

STAGES = [
    {"key": "acquire", "label": "获得/产生",
     "syn": re.compile(r"(获得|领取|发放|创建|生成|产生|购买|入库|开通|下发|绑定)")},
    {"key": "use", "label": "使用/消费",
     "syn": re.compile(r"(使用|消费|应用|生效|执行|兑换|核销|投放)")},
    {"key": "coexist", "label": "同类共存",
     "syn": re.compile(r"(同类共存|共存|并存|同时存在|同时使用|多种同时|同类多|叠加)")},
    {"key": "settle", "label": "计量结算",
     "syn": re.compile(r"(结算|计费|计量|扣减|对价|定价|额度|计价)")},
    {"key": "reverse", "label": "撤销退回",
     "syn": re.compile(r"(退回|退还|撤销|回退|作废|取消|退款|失效|过期)")},
    {"key": "account", "label": "记账对账",
     "syn": re.compile(r"(记账|对账|台账|流水|统计|账目|报表)")},
]

NO_OBJECT_DECL = re.compile(
    r"本(需求|轮|次)(不|未|没有)(产出|新增|引入|涉及)[^。\n]{0,40}"
    r"(业务对象|可被下游消费|下游消费|下游)")
ID_SHAPE = r"[A-Za-z]{1,8}[-_]?\d+[A-Za-z0-9_-]*"
CASE_REF = re.compile(r"\b(" + ID_SHAPE + r")\b")
NOT_COVERED_MARK = re.compile(r"(不覆盖|未覆盖|不测|不涉及|out of scope|超出范围|本轮不)", re.I)
OWNER_MARK = re.compile(
    r"(谁覆盖|由|负责|owner|责任|人工|开发|产品|测试|运维|用户|该需求|另一|下一轮"
    r"|后续需求|提测方|我)", re.I)
CONSEQ_MARK = re.compile(
    r"(不作数|不担保|不保证|不可信|封顶|假设|作废|失效|风险|导致|无法|不算|误判|返工"
    r"|重跑|重测|上浮|阻塞|BLOCK|PENDING|白测|漏|错|资损|不生效|不成立|只能|留着|没人"
    r"|查不出|发现不了|看不出|才暴露|暴露不了|逃逸|无人接)", re.I)
PLACEHOLDER = {"", "-", "—", "–", "待定", "tbd", "TBD", "n/a", "N/A", "na",
               "无", "未知", "?", "？", "待补", "待确认", "/"}
_SEP_ROW = re.compile(r"^\s*\|[\s:|.\-—–]+\|?\s*$")

COL_PAT = {
    "obj": re.compile(r"(业务对象|对象|产出物|产出对象|实体|entity|object)", re.I),
    "stage": re.compile(r"(环节|生命周期|阶段|stage|lifecycle)", re.I),
    "case": re.compile(r"(判别用例|用例|覆盖用例|case|测试点)", re.I),
    "decl": re.compile(r"(不覆盖|声明|谁覆盖|覆盖声明|decl)", re.I),
}


def _cells_of(l: str) -> list[str]:
    body = re.sub(r"^\s*\|", "", l)
    body = re.sub(r"\|\s*$", "", body)
    return [c.strip() for c in body.split("|")]


def _is_placeholder(v: str) -> bool:
    t = re.sub(r"[<>\[\]]", "", v or "").strip()
    return not t or t in PLACEHOLDER or t.lower() in PLACEHOLDER


def _section(text: str) -> dict[str, Any] | None:
    lines = re.split(r"\r?\n", text)
    start = -1
    for i, l in enumerate(lines):
        if _HEAD_LINE.match(l) and SECTION_TITLE.match(_head_text(l)):
            start = i
            break
    if start == -1:
        near = next((l for l in lines
                     if _HEAD_LINE.match(l) and SECTION_NEAR.search(_head_text(l))), None)
        return {"head_shape": str(near).strip()[:40]} if near else None
    body = []
    for l in lines[start + 1:]:
        if _HEAD_LINE.match(l):
            break
        body.append(l)
    return {"head": lines[start], "body": body}


def _header_map(cs: list[str]) -> dict[str, Any] | None:
    def find(pat: re.Pattern, exclude=()) -> int:
        for i, c in enumerate(cs):
            if i in exclude:
                continue
            if pat.search(c):
                return i
        return -1

    obj = find(COL_PAT["obj"])
    stage = find(COL_PAT["stage"])
    if obj < 0 or stage < 0:
        return None
    decl = -1
    for i, c in enumerate(cs):
        if i in (obj, stage):
            continue
        if decl < 0 and COL_PAT["decl"].search(c) and not COL_PAT["case"].search(c):
            decl = i
    cse = -1
    for i, c in enumerate(cs):
        if i in (obj, stage, decl):
            continue
        if cse < 0 and COL_PAT["case"].search(c):
            cse = i
    if cse < 0:
        for i, c in enumerate(cs):
            if i in (obj, stage):
                continue
            if COL_PAT["case"].search(c):
                cse = i
                break
    if cse < 0:
        return None
    if decl < 0 and COL_PAT["decl"].search(cs[cse]):
        decl = cse
    return {"obj": obj, "stage": stage, "case": cse,
            "decl": decl if decl >= 0 else None, "width": len(cs)}


def _lead_label(s: str) -> str:
    return re.split(r"[(（:：—\-]", str(s or ""))[0].strip()


def _stage_of(s: str) -> str | None:
    lead = _lead_label(s)
    if lead:
        for st in STAGES:
            if st["syn"].search(lead):
                return st["key"]
    for st in STAGES:
        if st["syn"].search(s or ""):
            return st["key"]
    return None


def _parse_rows(body: list[str]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    last_obj = ""
    stopped = False
    hmap = None
    for l in body:
        if not re.match(r"^\s*\|", l) or _SEP_ROW.match(l):
            continue
        cs = _cells_of(l)
        if len(cs) < 3:
            continue
        if hmap is None:
            m = _header_map(cs)
            if m:
                hmap = m
                continue
        elif (_header_map(cs) and all(not re.search(r"\d", c) for c in cs)
              and COL_PAT["obj"].search(cs[hmap["obj"]] if hmap["obj"] < len(cs) else "")):
            continue
        if stopped:
            continue
        M = hmap or {"obj": 0, "stage": 1, "case": 2, "decl": None, "width": len(cs)}
        stage_cell = cs[M["stage"]] if M["stage"] < len(cs) else ""
        obj_cell = cs[M["obj"]] if M["obj"] < len(cs) else ""
        if (not _stage_of(stage_cell)
                and re.match(r"^[A-Za-z一-龥][^|]{0,12}$", obj_cell)
                and re.search(r"Charter|判别\s*Example|执行配方|说明|备注", " ".join(cs))):
            stopped = True
            continue
        obj = re.sub(r"[<>\[\]]", "", str(obj_cell))
        obj = re.sub(r"^[\s\-*·]*[①-⑳0-9]+[\.、)）]?\s*", "", obj).strip()
        if not obj or re.match(r'^(同上|同前|"|〃|″|同左|ditto)$', obj, re.I):
            obj = last_obj
        else:
            last_obj = obj
        if M["decl"] is not None:
            if M["decl"] == M["case"]:
                decl_cell = str(cs[M["case"]] if M["case"] < len(cs) else "")
            else:
                decl_cell = str(cs[M["decl"]] if M["decl"] < len(cs) else "")
        else:
            decl_cell = " ".join(cs[max(M["case"], M["stage"]) + 1:])
        rows.append({"obj": obj, "stage": stage_cell,
                     "case_cell": cs[M["case"]] if M["case"] < len(cs) else "",
                     "decl_cell": decl_cell or "", "raw": l.strip()})
    return {"rows": rows, "header_found": hmap is not None}


def collect_case_ids_from_text(t: str) -> set[str]:
    return set(m.group(1) for m in re.finditer(r"\b(" + ID_SHAPE + r")\b", t))


def collect_case_ids_from_json(t: str) -> set[str]:
    ids: set[str] = set()
    try:
        j = json.loads(t)
        arr = j.get("cases", j) if isinstance(j, dict) else j
        if isinstance(arr, list):
            for c in arr:
                if isinstance(c, dict) and c.get("id"):
                    ids.add(str(c["id"]))
    except (ValueError, AttributeError):
        pass
    ids |= collect_case_ids_from_text(t)
    return ids


def check(text: str, case_ids: set[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"verdict": "PASS", "objects": {}, "problems": [],
                           "warns": [], "stats": {}}
    sec = _section(text)
    if sec is None:
        out["problems"].append(
            "缺「下游消费面盘点」节 —— 本次产出的业务对象之后被谁消费、每个环节测没测,"
            "一个字没交代。对着需求文档做完整性检查查不出这一类(文档本身没这一节),必须显式盘点。"
            " 期望标题:`## 下游消费面盘点`(可带编号,如 `## 三、下游消费面盘点`)")
        out["verdict"] = "REJECT"
        return out
    if "head_shape" in sec:
        out["problems"].append(
            f"「下游消费面盘点」= 标题格式不符(不是缺内容):找到疑似小节「{sec['head_shape']}」,"
            "但闸认不出。 期望标题:`## 下游消费面盘点`(同义词:下游消费面 / 消费面盘点;编号前缀 OK)")
        out["verdict"] = "REJECT"
        return out
    parsed = _parse_rows(sec["body"])
    rows = parsed["rows"]
    body_text = "\n".join(sec["body"])
    if rows and not parsed["header_found"]:
        out["problems"].append(
            "盘点表【表头认不出来】(不是内容缺失)—— 闸靠表头列名定位"
            "「业务对象/环节/判别用例/不覆盖声明」四列,认不出就只能按位置猜,"
            "猜错会报一堆假的\"内容缺失\"。期望形状:\n       " + TABLE_SHAPE)
        out["verdict"] = "REJECT"
        return out
    if not rows:
        if NO_OBJECT_DECL.search(body_text) and len(re.sub(r"\s", "", body_text)) >= 20:
            out["warns"].append(
                "盘点为空但有\"本次不产出可被下游消费的业务对象\"的显式声明 —— 准入,"
                "声明的准确性由评审判")
            return out
        out["problems"].append(
            "「下游消费面盘点」节是空的 —— 要么逐个对象列环节,要么显式声明"
            "\"本次不产出可被下游消费的业务对象\"并说明理由")
        out["verdict"] = "REJECT"
        return out
    for r in rows:
        if not r["obj"]:
            out["problems"].append(f"盘点行缺业务对象:{r['raw'][:50]}")
            continue
        o = out["objects"].setdefault(
            r["obj"], {"stages": {}, "covered": 0, "uncovered": 0, "extra": []})
        key = _stage_of(r["stage"])
        covered = (not _is_placeholder(r["case_cell"])
                   and bool(CASE_REF.search(r["case_cell"]))
                   and not NOT_COVERED_MARK.search(r["case_cell"]))
        entry = {"stage": r["stage"], "covered": covered, "case_cell": r["case_cell"]}
        if key:
            o["stages"][key] = entry
        else:
            o["extra"].append(entry)
        if covered:
            o["covered"] += 1
            if case_ids is not None:
                refs = re.findall(ID_SHAPE, r["case_cell"])
                missing = [x for x in refs if x not in case_ids]
                if refs and len(missing) == len(refs):
                    out["problems"].append(
                        f"「{r['obj']} · {r['stage']}」声明的用例在用例集里找不到:"
                        f"{','.join(missing)} —— 声明了覆盖但没有用例 = 假覆盖")
        else:
            o["uncovered"] += 1
            d = r["decl_cell"]
            if _is_placeholder(d):
                out["problems"].append(
                    f"「{r['obj']} · {r['stage']}」既没有判别用例、也没有不覆盖声明 "
                    "—— 这一环节是空的,不是\"不适用\"")
            else:
                segs = [s.strip() for s in re.split(r"[·;；,，]", d) if s.strip()]
                has_owner = any(OWNER_MARK.search(s) for s in segs) or "谁覆盖" in d
                has_conseq = bool(CONSEQ_MARK.search(d))
                if not has_owner:
                    out["problems"].append(
                        f"「{r['obj']} · {r['stage']}」声明不覆盖但没写【谁覆盖】"
                        "—— 不覆盖可以,没人接不行")
                if not has_conseq:
                    out["problems"].append(
                        f"「{r['obj']} · {r['stage']}」声明不覆盖但没写【不覆盖的后果】"
                        "—— 要写清这一环节不测会留下什么风险")
                if len(re.sub(r"\s", "", d)) < 12:
                    out["problems"].append(
                        f"「{r['obj']} · {r['stage']}」的不覆盖声明太短,"
                        f"看不出谁接和留什么风险:{d}")
    for name, o in out["objects"].items():
        miss = [s for s in STAGES if s["key"] not in o["stages"]]
        if miss:
            coexist_missing = any(s["key"] == "coexist" for s in miss)
            extra_note = ("(其中【同类共存】是必答档:新增同类第 N 种对象时,"
                          "\"N 种同时存在/同时用在一笔交易\"是独立失败面,单种跑通不代表它对)"
                          if coexist_missing else "")
            out["problems"].append(
                f"「{name}」盘点缺环节:{'、'.join(s['label'] for s in miss)}"
                f" —— 通用生命周期骨架不许缺行,不适用要在\"不覆盖\"列申报{extra_note}")
        if o["covered"] == 0:
            out["problems"].append(
                f"「{name}」整条生命周期零覆盖({o['uncovered']} 个环节全声明不覆盖)"
                "—— 盘点等于没做:要么至少一个环节给判别用例,"
                "要么把该对象整体移出本次范围并写清谁裁的")
        out["stats"][name] = {"covered": o["covered"], "uncovered": o["uncovered"]}
    if out["problems"]:
        out["verdict"] = "REJECT"
    return out
