"""把 HAR 体检报告裁成能进 prompt 的素材。**零 DeepTutor import。**

## 这一层为什么必须存在

`parse_har` 的报告是给人和工具看的，字段全、体量大：一份几百条请求的 HAR
能出上百个端点、每个端点带脱敏样例。整份塞进 prompt 有三个问题：

1. **超预算**——`CallSpec` 的 token 上限是写死的，超了就截断，而截断在
   `complete()` 那层不可检测（见 `model.py` 防线二）；
2. **稀释重点**——模型要在噪声里找业务端点，挑得更差；
3. **多花钱**——按 token 计费，塞进去的每个字节都在账单上。

所以这里做减法：**按业务价值排序、按预算截断、只留模型真正用得上的字段。**

## 出境闸挂在这里，不挂在调用点

本模块是生成链路里**唯一**接触 HAR 素材的地方，所以
`scrub_for_prompt()` 就挂在这一层的出口。挂在调用点意味着"每个调用点都要记得"，
那是迟早会漏的设计——漏一处就是 PII 出境（BB-430）。

裁剪**在脱敏之前**做：先扔掉不要的，再脱敏留下的。反过来会白白脱敏一堆
马上就要丢掉的内容，纯浪费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.generate.scrub import scrub_for_prompt

#: 进 prompt 的端点数上限。业务端点通常几十个量级；给到 40 已经覆盖绝大多数场景，
#: 再多主要是噪声，而它们每一个都在账单上。
MAX_ENDPOINTS_IN_PROMPT = 40
#: 单个端点样例请求体的字符上限。够模型看出字段结构即可，不需要完整业务数据。
MAX_SAMPLE_BODY_CHARS = 400
#: 整份素材的字符上限（粗口径预算闸，token 无法在无依赖前提下精确算）。
MAX_MATERIAL_CHARS = 24000


@dataclass(frozen=True)
class Material:
    """交给 prompt 的素材，以及它是怎么被裁出来的。

    `notes` 不是日志——它要在界面上说得出"我少给了模型什么"，
    因为那直接影响生成质量，用户有权知道。
    """

    endpoints: list[dict[str, Any]]
    login: dict[str, Any] | None
    stats: dict[str, Any]
    notes: list[str]
    scrub_hits: dict[str, int]


def _endpoint_value(ep: dict[str, Any]) -> tuple:
    """端点的业务价值排序键。越靠前越该进 prompt。

    判据只用报告里已有的事实，不猜：
    - 写操作（POST/PUT/PATCH/DELETE）比读操作更值得测——它们改状态
    - 出现过 4xx/5xx 的端点已经有真实的失败样本，是现成的负例素材
    - 调用次数多的更可能在主链路上
    """
    method = str(ep.get("method", "")).upper()
    is_write = method in {"POST", "PUT", "PATCH", "DELETE"}
    # 字段名以 har_parse 实际产出为准（`statuses` / `count`，不是 status_codes /
    # request_count）——这两个名字是实测出来的，改前先跑一遍别照记忆改。
    codes = ep.get("statuses") or {}
    has_error = any(str(c).startswith(("4", "5")) for c in codes)
    return (is_write, has_error, int(ep.get("count") or 0))


def _slim_endpoint(ep: dict[str, Any]) -> dict[str, Any]:
    """只留模型用得上的字段。

    丢掉 domain/host 这类；样例只留请求体形状与状态码——
    模型需要知道"有哪些字段、长什么样"，不需要完整业务数据。
    """
    out: dict[str, Any] = {
        "method": ep.get("method"),
        "path": ep.get("path"),
        "calls": ep.get("count"),
    }
    codes = ep.get("statuses")
    if codes:
        out["statuses"] = codes
    sample = ep.get("sample") or {}
    body = sample.get("body")
    if isinstance(body, str) and body:
        out["sample_body"] = body[:MAX_SAMPLE_BODY_CHARS]
    elif isinstance(body, (dict, list)) and body:
        out["sample_body"] = body
    keys = sample.get("query_keys")
    if keys:
        out["query_keys"] = keys
    status = sample.get("response_status")
    if status is not None:
        out["sample_response_status"] = status
    return out


def build_material(report: dict[str, Any],
                   *, max_endpoints: int = MAX_ENDPOINTS_IN_PROMPT) -> Material:
    """报告 → 素材。**这是 HAR 内容通往模型的唯一通道。**

    顺序是有意的：先排序、再截断、最后统一脱敏。
    """
    notes: list[str] = []
    endpoints = list(report.get("endpoints") or [])
    total = len(endpoints)

    endpoints.sort(key=_endpoint_value, reverse=True)
    kept = endpoints[:max_endpoints]
    if total > len(kept):
        notes.append(
            f"端点太多（{total} 个），只把最可能是业务主链路的 {len(kept)} 个交给了 AI"
            "（写操作、出现过报错、调用次数多的优先）"
        )

    slim = [_slim_endpoint(e) for e in kept]

    replay = report.get("replay") or {}
    login = replay.get("login_request") if replay.get("replayable") else None
    if login is None and replay.get("missing"):
        notes.append("这份抓包里没识别出可重放的登录请求，鉴权相关的用例会受限")

    summary = report.get("summary") or {}
    stats = {
        "requests": summary.get("entry_count"),
        "noise_filtered": summary.get("noise_entries_filtered"),
        "endpoints": summary.get("endpoint_count"),
    }

    payload = {"endpoints": slim, "login": login, "stats": stats}
    cleaned, hits = scrub_for_prompt(payload)

    # 粗口径预算闸：脱敏后仍超长就继续砍端点，宁可少给也不能被静默截断。
    import json as _json
    while len(_json.dumps(cleaned, ensure_ascii=False)) > MAX_MATERIAL_CHARS and len(cleaned["endpoints"]) > 1:
        cleaned["endpoints"] = cleaned["endpoints"][:-1]
    if len(cleaned["endpoints"]) < len(slim):
        notes.append(
            f"素材超出预算，进一步压到 {len(cleaned['endpoints'])} 个端点"
            "——被截断的输出无法被可靠识别，所以宁可少给"
        )

    if hits:
        detail = "、".join(f"{k} {v} 处" for k, v in sorted(hits.items()))
        notes.append(f"发给 AI 之前已把可识别的个人信息换成占位符（{detail}）")

    return Material(endpoints=cleaned["endpoints"], login=cleaned["login"],
                    stats=stats, notes=notes, scrub_hits=hits)
