"""模型产出的用例 → 消费侧唯一形状（BB-487 / BB-488 的修复落点）。

## 为什么需要这一层

生成侧的提示词和消费侧的解析器是**两处独立演化的契约**，中间没有编译期约束。
实际发生的后果（2026-08-08 用户 UAT 抓到）：提示词让模型把断言写成 `request`
的**兄弟节点**、键名用 `kind`/`expect`，而四个消费点
（`case_validate` / `execute` / `postman` / `gateway.workbench`）
**全都**从 `request.assertions` 读、键名认 `type`/`expected`。

于是每一条 AI 生成的用例落盘后 `executable=false`：勾选框全禁用、执行按钮
永远灰着，工作台的执行这一半对真实生成内容完全不可用。而生成那一步的返回值
看起来是好的——断言确实在，只是在没人读的位置上。

**提示词已同步改成直接产出规范形状**（见 `prompts.py`）。本模块仍然存在，
理由是模型不是编译器：它会漏、会用同义词、会偶尔退回旧写法。
这与仓里既有的 `args_tolerance` 同一条纪律——**宽容地救回来，并如实回显救了什么**，
而不是让一条格式偏差把整批用例悄悄变成空壳。

## 边界：救形状，不造内容

能从别处**推导**的字段才补（端点从 outline 抄、模块从路径取）；
需要判断的内容（前置条件、步骤、预期结果）一律不代笔——
模型没写就是没写，留空让 `case_validate` 的必填校验去报，
**不许本模块编一句"预期返回成功"充数**。那样导出的用例表看着满了，
实际是假的，比空着更坏。
"""

from __future__ import annotations

from typing import Any

#: 断言里 type 字段的同义键名。`kind` 排第一是因为旧提示词用的就是它。
_ASSERTION_TYPE_KEYS = ("type", "kind", "assert_type", "断言类型")
#: 断言里期望值的同义键名。
_ASSERTION_EXPECTED_KEYS = ("expected", "expect", "value", "期望", "预期")
#: 断言里 json path 的同义键名。
_ASSERTION_PATH_KEYS = ("path", "json_path", "jsonpath", "路径")

#: `intent` → 用例类型。模型只会吐这三种（提示词限定的），别的原样当功能测试。
_INTENT_TO_CASE_TYPE = {
    "正常": "功能测试",
    "异常": "异常测试",
    "鉴权": "安全测试",
}
#: `intent` → 用例等级。正常路径是主链路，异常与鉴权次之。
_INTENT_TO_PRIORITY = {"正常": "高", "异常": "中", "鉴权": "中"}


def _first(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in node:
            return node[key]
    return None


def normalize_assertion(raw: Any) -> dict[str, Any] | None:
    """一条断言 → `{type, expected, path?}`。认不出形状就返回 None。

    不合法的断言**丢掉而不是留着**：留一条 `type` 为空的断言，
    执行层会当成"有断言"从而认为这条用例可执行，跑起来再逐条判空——
    那是把格式问题伪装成测试失败。
    """
    if not isinstance(raw, dict):
        return None
    kind = _first(raw, _ASSERTION_TYPE_KEYS)
    kind = str(kind or "").strip().lower()
    if not kind:
        return None
    out: dict[str, Any] = {"type": kind}
    expected = _first(raw, _ASSERTION_EXPECTED_KEYS)
    if expected is not None:
        out["expected"] = expected
    path = _first(raw, _ASSERTION_PATH_KEYS)
    if path:
        out["path"] = str(path)
    # `op: exists` 这种写法（旧提示词的例子里有）：翻成本仓认的 json_path + 无期望值
    op = str(raw.get("op") or "").strip().lower()
    if op == "exists" and "expected" not in out:
        out["expected"] = None
    return out


def _endpoint_text(entry: Any) -> str:
    """`{method, path}` → `"POST /api/x"`。给「关联端点」列用。"""
    if not isinstance(entry, dict):
        return ""
    method = str(entry.get("method") or "").strip().upper()
    path = str(entry.get("path") or entry.get("url") or "").strip()
    return f"{method} {path}".strip()


def _module_from_path(path: str) -> str:
    """从 `/api/order/create` 取「order」当模块名。

    取第二段而不是第一段：第一段几乎总是 `api`，拿它当模块名等于没分。
    取不到就留空——空值会走 `delivery` 的「未分类」默认，不在这里编。
    """
    parts = [p for p in str(path or "").split("/") if p and p != "api"]
    return parts[0] if parts else ""


def normalize_generated_case(raw: Any, outline: Any = None) -> tuple[dict[str, Any] | None, list[str]]:
    """模型产出的一条用例 → 落盘/执行认的形状。

    `outline` 是清单阶段那条对应的记录（带 `endpoint` 与 `intent`），
    用来补模型在详情阶段不必重复的结构性字段。

    返回 `(用例, 救回动作的说明)`。说明会一路回传到界面——
    静默修形状会让"为什么这条用例长这样"变成不可追的事。
    """
    notes: list[str] = []
    if not isinstance(raw, dict):
        return None, ["有一条产出不是对象，已丢弃"]

    case: dict[str, Any] = dict(raw)
    outline = outline if isinstance(outline, dict) else {}

    # ── id → case_id ──
    if "case_id" not in case and case.get("id"):
        case["case_id"] = case.pop("id")
    case_id = str(case.get("case_id") or "").strip()

    # ── 断言归位：顶层 → request.assertions ──
    request = case.get("request")
    request = dict(request) if isinstance(request, dict) else {}

    raw_assertions = case.pop("assertions", None)
    if raw_assertions is None:
        raw_assertions = request.get("assertions")
    elif request.get("assertions"):
        # 两处都有：以 request 里的为准（那是消费侧的位置），顶层的丢弃并说明
        notes.append(f"{case_id}：断言同时出现在 request 内外，按 request 内的为准")
        raw_assertions = request.get("assertions")
    else:
        notes.append(f"{case_id}：断言写在了 request 外层，已归位到 request.assertions")

    cleaned: list[dict[str, Any]] = []
    dropped = 0
    for one in (raw_assertions if isinstance(raw_assertions, (list, tuple)) else []):
        norm = normalize_assertion(one)
        if norm is None:
            dropped += 1
        else:
            cleaned.append(norm)
    if dropped:
        notes.append(f"{case_id}：{dropped} 条断言认不出写法，已丢弃（留着会让用例假装可执行）")
    if cleaned:
        request["assertions"] = cleaned
    if request:
        case["request"] = request

    # ── 结构性字段：能推导的补，需要判断的不代笔 ──
    endpoint = outline.get("endpoint") if isinstance(outline.get("endpoint"), dict) else None
    if not case.get("endpoints"):
        text = _endpoint_text(endpoint) or _endpoint_text(
            {"method": request.get("method"), "path": request.get("url")})
        if text:
            case["endpoints"] = [text]
    if not case.get("module"):
        path = (endpoint or {}).get("path") or request.get("url") or ""
        module = _module_from_path(path)
        if module:
            case["module"] = module

    intent = str(case.pop("intent", None) or outline.get("intent") or "").strip()
    if intent:
        if not case.get("case_type"):
            case["case_type"] = _INTENT_TO_CASE_TYPE.get(intent, "功能测试")
        if not case.get("priority"):
            case["priority"] = _INTENT_TO_PRIORITY.get(intent, "中")

    return case, notes


def normalize_generated_cases(cases: Any, outlines: Any = None) -> tuple[list, list[str]]:
    """整批规整。`outlines` 按 case_id 对齐，对不上就当没有。"""
    by_id: dict[str, dict[str, Any]] = {}
    for entry in (outlines if isinstance(outlines, (list, tuple)) else []):
        if isinstance(entry, dict):
            key = str(entry.get("case_id") or entry.get("id") or "").strip()
            if key:
                by_id[key] = entry

    out: list[dict[str, Any]] = []
    notes: list[str] = []
    for raw in (cases if isinstance(cases, (list, tuple)) else []):
        key = ""
        if isinstance(raw, dict):
            key = str(raw.get("case_id") or raw.get("id") or "").strip()
        case, case_notes = normalize_generated_case(raw, by_id.get(key))
        notes.extend(case_notes)
        if case is not None:
            out.append(case)
    return out, notes
