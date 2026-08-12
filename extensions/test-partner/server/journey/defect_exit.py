# -*- coding: utf-8 -*-
"""defect_exit — 缺陷根因出口（工程稿 §7 第 1 行「IN，零自动提单」/ 0027 §缺陷提单）。

## 两件事，别混

1. **产出 `defect_draft`**：从本次 run 的证据把缺陷结构化成一份可提交的草稿
   （根因 + 归属层 + 复现路径 + 证据指针）。这一步**不碰 TAPD**，纯本地。
2. **提交**：由 DT 侧的 skill 走 `ask_user` 人工确认后才发生。**零自动提单**
   （工程稿 §7 裁决；ADR-M2-02 第 2 条 TAPD 白名单只读）。

## 本模块最重要的东西不是草稿生成器，是 `write_release_gate`

0027 §2 定了放权次序，并把它标成**硬拦不能靠自觉**：

> 第一步（本轮）：白名单**只放只读工具**。
> 第二步：建缺陷/改状态的**写工具，与确认闸同批放行**。
> 禁忌：**先放写权限、承诺后续加约束**的中间态——那个中间态里 AI 可以绕过确认直接提单。

"承诺后续加"这件事本身没有任何机械保证：**顺序反了不会有任何报错**，
只会在某个赶工的下午多出一个能直接写 TAPD 的工具，而确认闸还在待办里。
所以本模块把这条次序做成一道会判红的闸，而不是文档里的一句话。

## 未知工具按「写」处理（与 E22 未知 op 判红同一条道理）

官方 `mcp-server-tapd` 约 43 个工具，我们没有逐个核过它们的读写性。
判据因此不是"在写清单里就算写"，而是**不在只读白名单里就算写**：

- 少给的后果：一个其实只读的工具被要求配确认闸 —— 麻烦，但安全；
- 多给的后果：一个能改 TAPD 的工具被当成只读放行 —— 静默、不可逆、动的是别人的数据。

两种错的代价不对称，所以默认必须偏向前者。
"""
from __future__ import annotations

from typing import Any

from server.journey import artifacts
from server.journey.oracle import READ_TOOLS

#: 缺陷归属层。与 bug-bank 的 `layer` 字段同词表——出口产物要能直接进那个库，
#: 换了词表就得在提交前做一次翻译，而翻译层是丢信息的地方。
LAYERS = ("体验", "功能", "工具容错", "诚实", "流程", "测试方法", "安全")

E_NO_CONFIRM_GATE = "E_TAPD_WRITE_WITHOUT_CONFIRM_GATE"


def write_release_gate(enabled_tools: Any, *, confirm_gate_present: bool) -> dict[str, Any]:
    """放权次序闸：**写工具不许先于确认闸放行**（0027 §2 第三步的机械化身）。

    参数
    ----
    enabled_tools : TAPD MCP 条目当前放行的工具名列表。
    confirm_gate_present : 提交前的人工确认闸是否**已经可用**。
        注意这里要的是"可用"不是"计划中"——判据由调用方给，
        且调用方必须拿运行时事实来判（工具在不在挂载面上），不许传常量 True。

    返回 {ok, code?, write_tools, problems}
    """
    tools = [str(t) for t in (enabled_tools or [])]
    # 不在只读白名单里的一律按写处理（见模块文档串：两种错的代价不对称）
    write_tools = sorted({t for t in tools if t not in READ_TOOLS and t != "*"})
    if "*" in tools:
        write_tools = sorted(set(write_tools) | {"*(通配：等于放开全部 43 个工具)"})

    if not write_tools:
        return {"ok": True, "write_tools": [], "problems": []}
    if confirm_gate_present:
        return {"ok": True, "write_tools": write_tools, "problems": []}
    return {
        "ok": False,
        "code": E_NO_CONFIRM_GATE,
        "write_tools": write_tools,
        "problems": [
            f"TAPD 条目放行了非只读工具 {write_tools}，但提交前的人工确认闸尚未就位。"
            f"0027 §2 把这条次序标成**硬拦不能靠自觉**：先放写权限、承诺后续补约束的"
            f"中间态里，模型可以绕过确认直接提单，而**顺序反了不会有任何报错**。"
            f"只读白名单是 {sorted(READ_TOOLS)}；**不在其中的一律按写处理**"
            f"（少给=麻烦，多给=静默改别人的数据，两种错的代价不对称）。"
            f"要放行请与确认闸同批。"],
    }


def _repro_from_case(case: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """复现路径 = 用例步骤 + 实际打过的请求，不是"见日志"。"""
    steps = list(case.get("steps") or [])
    for t in (result.get("http_transcript") or []):
        steps.append(f"{t.get('method')} {t.get('url')} → {t.get('status')}")
    for q in (result.get("db_transcript") or []):
        steps.append(f"[数据层] {q.get('sql')} → {q.get('row_count')} 行")
    return steps


def build_drafts(batch_id: str, run_id: str, *,
                 root: str | None = None) -> dict[str, Any]:
    """从一次 run 的失败用例产出 `defect_draft` 列表。**不提交、不碰 TAPD。**

    只对 `failed` 出草稿：
    - `blocked` 是环境问题不是缺陷（提了会污染缺陷库的逃逸率口径）；
    - `observed`（探测层）没有判决权，按定义不产生缺陷；
    - `skipped` 连跑都没跑。

    这三条排除是有代价的：真缺陷可能伪装成 blocked。所以每条排除都**计数留痕**，
    让"这轮为什么只有 N 条草稿"能被追问。
    """
    caseset = artifacts.load_artifact(batch_id, "approved_caseset", root=root)
    by_id = {c.get("case_id"): c for c in caseset.get("cases") or []}
    rows = _read_results(run_id, root=root)

    drafts: list[dict[str, Any]] = []
    excluded: dict[str, list[str]] = {"blocked": [], "observed": [], "skipped": [],
                                      "passed": []}
    for r in rows:
        outcome = r.get("outcome")
        cid = r.get("case_id")
        if outcome != "failed":
            excluded.setdefault(str(outcome), []).append(cid)
            continue
        case = by_id.get(cid) or {}
        failed_assertions = [a for a in (r.get("assertions") or []) if not a.get("passed")]
        drafts.append({
            "artifact": "defect_draft",
            "case_id": cid,
            "title": (case.get("title") or cid)[:120],
            # 现象 = 断言层面的事实，不是形容词
            "phenomenon": "；".join(
                f"{a.get('kind')} 期望 {a.get('expected')!r} 实得 {a.get('actual')!r}"
                for a in failed_assertions) or "（无失败断言明细）",
            # 归因**留空并标注**，不替人写。自动生成的根因是本线最不该产的东西：
            # 它读起来像结论，实际是把"我们没查"包装成"我们查过了"。
            "root_cause": "",
            "root_cause_status": "pending_human",
            "root_cause_hint": _root_cause_hint(failed_assertions),
            "layer_candidates": list(LAYERS),
            "repro": _repro_from_case(case, r),
            "evidence_refs": [
                f"run:{run_id}/results.jsonl#{cid}",
                f"run:{run_id}/{_slug(cid)}/transcript.json",
            ],
            "source_anchor": case.get("source_anchor") or {},
            "source_case_digest": case.get("source_case_digest", ""),
            "oracle_digest": case.get("oracle_digest", ""),
            "batch_id": batch_id,
            "run_id": run_id,
            # 提交状态：草稿永远从"未提交"开始，且只有 ask_user 确认能改它
            "submission": {"state": "draft", "confirmed_by": "", "tapd_ref": ""},
        })
    payload = {"run_id": run_id, "drafts": drafts,
               "excluded_counts": {k: len(v) for k, v in excluded.items()},
               "excluded_case_ids": excluded,
               "note": "零自动提单：提交须经 ask_user 人工确认（工程稿 §7 / 0027）"}
    # **刻意不进 ARTIFACT_FILES 登记表**：那张表同时是九格账本的来源，
    # 往里加一格会把「缺陷草稿」变成旅程的第十个必经步骤——而它不是
    # （没有失败用例的 run 根本不该有这一格）。落成批次目录下的独立文件。
    import json
    import os
    path = os.path.join(artifacts.batch_dir(batch_id, root=root), "defect_draft.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return {"ok": True, "defect_draft": payload, "path": path,
            "count": len(drafts), "excluded_counts": payload["excluded_counts"]}


def _root_cause_hint(failed: list[dict[str, Any]]) -> str:
    """只给**方向**，不给结论。方向来自断言的种类，是机械可判的。"""
    kinds = {str(a.get("kind") or "").split(":")[0] for a in failed}
    if kinds & {"db_value", "db_rows", "db_delta"} and not (kinds & {"status", "json_path"}):
        return ("接口层全过、只有数据层不符——查『写进去的和回执说的是不是同一个值』，"
                "以及是否有第二处真相（同一语义存在两张表/两个字段）")
    if kinds & {"status"}:
        return "HTTP 状态不符——先分清是路由不存在、鉴权失败，还是业务拒绝"
    if kinds & {"json_path"}:
        return "业务字段不符——查字段是否改名/改口径，别先假定是逻辑错"
    return "查失败断言的期望值出处：是需求写的，还是我们推的"


def _slug(case_id: str) -> str:
    from server.journey.pw_harness import case_slug
    return case_slug(str(case_id))


def _read_results(run_id: str, *, root: str | None = None) -> list[dict[str, Any]]:
    import json
    import os
    path = os.path.join(artifacts.run_dir(run_id, root=root), "results.jsonl")
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


__all__ = ["E_NO_CONFIRM_GATE", "LAYERS", "build_drafts", "write_release_gate"]
