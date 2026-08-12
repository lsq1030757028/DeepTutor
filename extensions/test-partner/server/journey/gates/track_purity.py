# -*- coding: utf-8 -*-
"""track_purity — E22 轨道纯度闸（DoD#4b，0023 裁定 2 / 0025 §3.3）。

## 它把「如实说」升级成「拒绝」

M1 只有 `execute_run.detect_track`：从 bundle 的 op 集合**反推**本趟走的哪条轨，
它的模块注释自己写着「这里不负责拦，只负责如实说」。于是一份声明 `track="api"`
却混着 `click`/`fill` 的 caseset 会被照常编译、照常执行，只在事后被反推成 "ui"——
**报告是对的，靶被打歪了**。M2 主证据锁死 API 轨（0023），拦不住越轨就等于
DoD#4b 只能靠人看。本模块是那道拦截。

## 四条判据，全部确定性

| 码 | 判什么 | 为什么它是错的而不只是怪 |
|---|---|---|
| E22.unknown_op | op 不在词表里 | 词表覆盖不到的 op **不许默认当 API 轨**——见下节 |
| E22.track_mismatch | 声明的 track 与 op 实际隐含的轨不符 | 声明说「打算走哪条」，op 说「实际会发生什么」；不一致时靶与证据都会错位 |
| E22.mixed_tracks | 同一 caseset 里两条轨并存 | `detect_track` 对整份 manifest 只返回一个值，混轨会让 `target_identity.track` 变成一个说谎的字段 |
| E22.ui_evidence_on_api | API 轨用例要求 `playwright_trace` / `screenshot` | API 轨压根不起浏览器（`pw_harness.py:115-121`），要了必然缺证，用例注定判不了 |

## 未知 op 为什么必须判红，而不是「当成 API 轨放过去」

`execute_run.UI_TRACK_OPS` 是一张**手抄的 UI op 清单**，反推逻辑是「命中清单 = ui，
否则 api」。这个 else 分支是个静默的默认值：运行时新增一个 UI op 而没人回来改清单，
它就被判成 API 轨——**新增能力自动获得豁免**，且没有任何信号。实测当版那张清单就已经
漏了两个：运行时有 `_op_wait_load` 与 `_op_expect_hidden`（都要 `self.page`，铁定 UI 轨），
而 `UI_TRACK_OPS` 里没有它们 —— 一份只用这两个 op 的 UI 用例，反推结果是 "api"。

所以本模块的词表**不是又抄一份**，而是与运行时对拍：`vocabulary_gaps()` 枚举
`pw_runtime` 上全部 `_op_*` 方法，任何一个不在 UI/API 两张表里就报缺口，
由测试守。词表与运行时之间从此没有"忘了同步"这个状态——只有"红了去补"。

（沉淀自本线第三条取证纪律：建完一个闸/清单，必须单独验一次它的**作用域边界**，
而不只验它在样例上能工作。默认分支就是作用域边界最爱漏的地方。）
"""
from __future__ import annotations

from typing import Any

#: UI 轨 op：需要浏览器页面（`self.page`）才能执行。
UI_OPS: frozenset[str] = frozenset({
    "goto", "fill", "click", "wait_load",
    "expect_title_contains", "expect_url_contains",
    "expect_text", "expect_visible", "expect_hidden",
})

#: API 轨 op：纯 HTTP，`page=None` 也能跑。
API_OPS: frozenset[str] = frozenset({
    "request", "expect_status", "expect_json_path",
})

#: 数据层 op（L3）：**轨道中立** —— 既不需要浏览器，也不打被测系统的 HTTP 面，
#: 走的是另一条只读 DB 连接。UI 轨和 API 轨都可以用，因此它们既不使某条用例
#: 变成 UI 轨，也不构成混轨。
#:
#: 中立集是白名单里最危险的一格：往里塞错一个 op，它就在两条轨上都拿到豁免。
#: 所以中立性**不靠这份清单自称**，由 `test_l3_data_ops_are_track_neutral`
#: 行为断言守着——每个中立 op 必须能在 `page=None` 的 runner 上被调用到
#: （即它不碰 `self.page`）。清单里混进一个 UI op，那条测试会红。
DATA_OPS: frozenset[str] = frozenset({
    "db_query", "expect_db_rows", "expect_db_value",
    "db_snapshot", "expect_db_delta",
})

#: API 轨不可能产出的证据类别。要求了必然缺证（0025 §3.3）。
UI_ONLY_EVIDENCE: frozenset[str] = frozenset({"playwright_trace", "screenshot"})

TRACKS = ("api", "ui")


def runtime_ops() -> set[str]:
    """运行时真正实现了哪些 op —— 从 `pw_runtime` 现算，不抄。

    延迟 import：本模块被 compile-gate 在建目录前调用，而 `pw_runtime` 会拉
    playwright 相关符号；把 import 放进函数里，让「校验词表」这件事在没有
    浏览器栈的环境里也能跑（词表对拍属静态检查，不该要求装浏览器）。
    """
    from server.journey import pw_runtime

    names: set[str] = set()
    for attr in dir(pw_runtime.CaseRunner):
        if attr.startswith("_op_"):
            names.add(attr[len("_op_"):])
    return names


def vocabulary_gaps() -> dict[str, list[str]]:
    """词表与运行时的双向差集。两边都要报——两个方向是两种病。

    - `runtime_only`：运行时有、词表没有 → 它会走 else 分支被当成 API 轨放行。
    - `vocab_only`：词表有、运行时没有 → 词表在保护一个不存在的 op，
      通常意味着运行时删过东西而词表没跟，下一次判据会写在幻觉上。
    """
    runtime = runtime_ops()
    vocab = UI_OPS | API_OPS | DATA_OPS
    return {"runtime_only": sorted(runtime - vocab),
            "vocab_only": sorted(vocab - runtime)}


def track_of_op(op: str) -> str | None:
    """op 属于哪条轨。返回 `"data"` 表示**轨道中立**，不是第三条轨。

    中立与未知的区别要守住：未知 op 判红（它可能是拼错的，也可能是新增 UI op
    忘了登记），中立 op 放行。两者都"不属于声明的那条轨"，但只有一个是错的。
    """
    if op in UI_OPS:
        return "ui"
    if op in API_OPS:
        return "api"
    if op in DATA_OPS:
        return "data"
    return None


def _err(where: str, problem: str, **rest: Any) -> dict[str, Any]:
    return {"code": "E22", "where": where, "problem": problem, **rest}


def check_case(case: dict[str, Any], *, label: str = "") -> list[dict[str, Any]]:
    """单条用例的轨道纯度。返回问题列表（空 = 过）。

    不可编译的用例（`admissible` 非真）**照样查**：它今天不编译，明天被采纳了就编译，
    而越轨的 op 是在生成侧写下的，越早红越便宜。
    """
    where = label or str(case.get("case_id") or "?")
    problems: list[dict[str, Any]] = []
    recipe = ((case.get("automation") or {}).get("recipe")) or {}
    declared = str(recipe.get("track") or "")
    if declared not in TRACKS:
        problems.append(_err(f"{where}.automation.recipe.track",
                             f"track 必须是 {list(TRACKS)} 之一，实为 {declared!r}"))

    implied: set[str] = set()
    for i, action in enumerate(recipe.get("actions") or []):
        op = str((action or {}).get("op") or "")
        track = track_of_op(op)
        if track is None:
            problems.append(_err(
                f"{where}.automation.recipe.actions[{i}]",
                f"op {op!r} 不在轨道词表里。**不默认当 API 轨放行**——"
                f"未知 op 若被默认放过，新增的 UI op 就会自动拿到豁免且没有任何信号。"
                f"要么它是拼错的，要么词表该补（补在 gates/track_purity.py，"
                f"补完 vocabulary_gaps() 的对拍测试会跟着绿）。"))
            continue
        if track == "data":
            continue  # 轨道中立：不进 implied，既不判混轨也不判越轨
        implied.add(track)

    if declared in TRACKS:
        # 判据是**逐个 op 与声明的轨对不对得上**，不是「集合包不包含」。
        # 包含式判法（`declared in implied` 就算过）会放过混轨那一半：
        # 声明 api、op 里既有 request 又有 click 时，implied 含 api，包含成立，
        # 于是那个 click 一声不响地混进 API 轨 bundle 里。
        offenders = sorted({op for op in (
            str((a or {}).get("op") or "") for a in (recipe.get("actions") or []))
            if track_of_op(op) not in (None, "data", declared)})
        if offenders:
            other = sorted({track_of_op(op) for op in offenders})
            mixed = len(implied) > 1
            problems.append(_err(
                f"{where}.automation.recipe",
                f"声明 track={declared!r}，但这些 op 属于 {other}：{offenders}。"
                + ("同一条用例里两条轨的 op 并存——执行侧按轨道分流成 "
                   "run_ui_case / run_api_case 两个入口，一条用例只能进一个。"
                   if mixed else
                   "声明说的是「打算走哪条」，op 说的是「实际会发生什么」——以 op 为准。")
                + "这条拒编译。"))

    if declared == "api":
        layers = case.get("assertion_layers") or {}
        if isinstance(layers, dict):
            for lname, layer in layers.items():
                req = (layer or {}).get("required_evidence") or []
                bad = sorted(set(req) & UI_ONLY_EVIDENCE)
                if bad:
                    problems.append(_err(
                        f"{where}.assertion_layers.{lname}.required_evidence",
                        f"API 轨用例要求了只有 UI 轨才产出的证据 {bad}。"
                        f"API 轨不起浏览器（pw_harness.run_api_case 里 page=None、无 tracing），"
                        f"这些证据必然缺席，用例执行完注定落 undecidable——"
                        f"即一条**生下来就判不了**的用例。API 轨的证据是 http_transcript。"))
    return problems


def check_caseset(caseset: dict[str, Any]) -> dict[str, Any]:
    """整份 ApprovedCaseSet 的轨道纯度。返回 {ok, problems}。

    **不改 caseset schema**（v1.0.0 已冻结）：本闸只读既有字段，不新增字段、不写回。
    """
    problems: list[dict[str, Any]] = []
    cases = caseset.get("cases") or []
    for case in cases:
        problems += check_case(case)

    declared_tracks = sorted({
        str((((c.get("automation") or {}).get("recipe")) or {}).get("track") or "")
        for c in cases
    } & set(TRACKS))
    if len(declared_tracks) > 1:
        problems.append(_err(
            "$.cases",
            f"同一份 caseset 里混着两条轨：{declared_tracks}。"
            f"`detect_track` 对整份 manifest 只返回一个值，混轨会让 run_receipt 的 "
            f"target_identity.track 变成一个说谎的字段（一半用例不是那条轨）。"
            f"拆成两个批次跑。"))
    return {"ok": not problems, "problems": problems}


__all__ = ["API_OPS", "DATA_OPS", "TRACKS", "UI_ONLY_EVIDENCE", "UI_OPS",
           "check_case", "check_caseset", "runtime_ops", "track_of_op",
           "vocabulary_gaps"]
