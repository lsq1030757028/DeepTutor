"""Per-workspace TAPD field baseline for the Test-mode candidate list.

## 为什么需要这一层

「测试人」在 TAPD 里是**自定义字段**，字段号按项目各不相同：67600006 上是
``custom_field_10``，换一个项目就不成立。把字段号写进提示词等于把一个项目的
偶然事实固化成全局规则——模型会拿着它去查别的项目，然后安静地返回零条。

所以字段语义走这一层：per-workspace 查表，查不到就**明说没锚住**，
由模型按 AC21 的口径自检交叉验证，而不是猜一个字段号硬查。

## 过渡态（重要）

正式基线由 `tapd-capability` 的基线机制提供（其草稿态任务落地前不可用）。
本模块先读一份随包的过渡基线，每条都带 ``status``：``draft-unconfirmed`` 的
条目**必须向用户披露未经核对**——它是「先能用」的兜底，不是「已确认」的事实。
接正式基线时替换 :func:`load_baselines` 的取数口即可，调用方不动。

覆盖取数口：环境变量 ``DEEPTUTOR_TAPD_FIELD_BASELINE``（指向同格式 JSON）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

#: 随包过渡基线的位置。
DEFAULT_BASELINE_PATH = Path(__file__).with_name("tapd_field_baseline.json")

#: 覆盖用环境变量名。
BASELINE_PATH_ENV = "DEEPTUTOR_TAPD_FIELD_BASELINE"

#: 渲染文案里项目名缺失时的回落。**绝不回落到 workspace_id**——
#: 提示词纪律明令不得向用户暴露 workspace_id，注入块自己漏出去等于自拆闸。
_PROJECT_FALLBACK_ZH = "当前项目"
_PROJECT_FALLBACK_EN = "the current one"

#: 候选清单的默认口径（decisions.md「待定① 收口」拍板）。
DEFAULT_LIMIT = 20
DEFAULT_ORDER = "created desc"


@dataclass(frozen=True, slots=True)
class FieldBaseline:
    """一个 workspace 的字段语义基线。"""

    workspace_id: str
    project: str = ""
    status: str = "missing"
    tester_field: str = ""
    evidence: str = ""
    note: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"

    @property
    def usable(self) -> bool:
        """能拿来出数——但 draft 态出的数必须带披露。"""
        return bool(self.tester_field) and self.status in {"confirmed", "draft-unconfirmed"}


@dataclass(frozen=True, slots=True)
class CandidateQueryTemplate:
    """候选需求清单的查询模板。

    ``must_pass_explicitly`` 不是装饰：工具 description 没声明排序与时间过滤参数，
    调用方不显式传就拿不到想要的顺序（施工约束①，来源
    ``analysis-chatmode-story-pull-20260818.md`` §五）。
    """

    workspace_id: str
    baseline: FieldBaseline
    tester_field: str = ""
    order: str = DEFAULT_ORDER
    limit: int = DEFAULT_LIMIT
    exclude_closed: bool = True
    must_pass_explicitly: tuple[str, ...] = field(
        default_factory=lambda: ("order", "limit")
    )

    @property
    def anchored(self) -> bool:
        """字段语义有没有锚住。没锚住不等于不能查，等于**必须交叉验证并披露**。"""
        return bool(self.tester_field)


def _baseline_path() -> Path:
    override = str(os.environ.get(BASELINE_PATH_ENV, "") or "").strip()
    return Path(override) if override else DEFAULT_BASELINE_PATH


def load_baselines(path: Path | None = None) -> dict[str, FieldBaseline]:
    """读全部 workspace 基线。**读不到就是空**，不造假条目。"""
    target = path or _baseline_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = raw.get("workspaces") if isinstance(raw, Mapping) else None
    if not isinstance(entries, Mapping):
        return {}
    out: dict[str, FieldBaseline] = {}
    for workspace_id, entry in entries.items():
        if not isinstance(entry, Mapping):
            continue
        key = str(workspace_id or "").strip()
        if not key:
            continue
        out[key] = FieldBaseline(
            workspace_id=key,
            project=str(entry.get("project") or ""),
            status=str(entry.get("status") or "missing"),
            tester_field=str(entry.get("tester_field") or ""),
            evidence=str(entry.get("evidence") or ""),
            note=str(entry.get("note") or ""),
        )
    return out


def baseline_for(workspace_id: Any, path: Path | None = None) -> FieldBaseline:
    """取一个 workspace 的基线。没有就返回 ``status="missing"`` 的空基线。"""
    key = str(workspace_id or "").strip()
    if not key:
        return FieldBaseline(workspace_id="")
    return load_baselines(path).get(key, FieldBaseline(workspace_id=key))


def candidate_query_template(
    workspace_id: Any,
    path: Path | None = None,
) -> CandidateQueryTemplate:
    """按 workspace 组出候选清单的查询模板。"""
    baseline = baseline_for(workspace_id, path)
    return CandidateQueryTemplate(
        workspace_id=baseline.workspace_id,
        baseline=baseline,
        tester_field=baseline.tester_field if baseline.usable else "",
    )


def render_candidate_shape_block(
    template: CandidateQueryTemplate,
    *,
    language: str,
) -> str:
    """宿主注入块：告诉模型这个项目的候选清单怎么查、以及有多信得过。

    三种状态三种话术，**不可混**：已确认 / 过渡草稿（须披露）/ 没锚住（须交叉验证）。
    """
    zh = str(language or "").lower().startswith("zh")
    if not template.workspace_id:
        return ""

    if zh:
        head = "[候选需求清单口径（宿主提供）]"
        common = (
            f"默认口径：测试人 = 当前用户 + 未结束状态，按创建时间新→旧，条数上限 "
            f"{template.limit}。\n"
            f"**排序与条数必须你在调用参数里显式传**（`order=\"{template.order}\"`、"
            f"`limit={template.limit}`）——工具说明没声明这两项，不传就拿不到这个顺序。\n"
            "探索阶段的只读查询不进旅程账本。"
        )
        if template.baseline.confirmed:
            body = (
                f"「测试人」字段 = `{template.tester_field}`（项目"
                f"{template.baseline.project or _PROJECT_FALLBACK_ZH}，基线已确认）。\n"
                f"{common}"
            )
        elif template.anchored:
            body = (
                f"「测试人」字段 = `{template.tester_field}`（项目"
                f"{template.baseline.project or _PROJECT_FALLBACK_ZH}）。\n"
                f"**这条基线是过渡草稿，未经核对**：出数可以，但要向用户说明这一点，"
                f"并按口径自检交叉验证一次。依据：{template.baseline.evidence or '未记录'}\n"
                f"{common}"
            )
        else:
            body = (
                "当前项目的「测试人」字段**没有锚住**。\n"
                "不要猜字段号。先确认字段语义，或改用另一个候选口径出数，"
                "并明确告诉用户这一项没锚住、你用的是什么口径。\n"
                f"{common}"
            )
        return f"{head}\n{body}"

    head = "[Candidate requirement list shape (host supplied)]"
    common = (
        f"Default shape: tester = current user + not closed, newest created first, "
        f"row limit {template.limit}.\n"
        f"**Pass ordering and limit explicitly** (`order=\"{template.order}\"`, "
        f"`limit={template.limit}`) — the tool description declares neither, and "
        "without them you will not get this order.\n"
        "Read-only queries during exploration do not enter the journey ledger."
    )
    if template.baseline.confirmed:
        body = (
            f"The \"tester\" field is `{template.tester_field}` (project "
            f"{template.baseline.project or _PROJECT_FALLBACK_EN}, baseline confirmed).\n"
            f"{common}"
        )
    elif template.anchored:
        body = (
            f"The \"tester\" field is `{template.tester_field}` (project "
            f"{template.baseline.project or _PROJECT_FALLBACK_EN}).\n"
            "**This baseline is a transitional draft and has not been reviewed**: you may "
            "query with it, but say so to the user and cross-check the shape once. "
            f"Evidence: {template.baseline.evidence or 'not recorded'}\n"
            f"{common}"
        )
    else:
        body = (
            "The \"tester\" field for the current project is **not anchored**.\n"
            "Do not guess a field number. Confirm the field's meaning first, "
            "or query by another candidate shape — and tell the user plainly that this "
            "part is unanchored and which shape you used.\n"
            f"{common}"
        )
    return f"{head}\n{body}"


__all__ = [
    "BASELINE_PATH_ENV",
    "CandidateQueryTemplate",
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_LIMIT",
    "DEFAULT_ORDER",
    "FieldBaseline",
    "baseline_for",
    "candidate_query_template",
    "load_baselines",
    "render_candidate_shape_block",
]
