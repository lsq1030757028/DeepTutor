# -*- coding: utf-8 -*-
"""render_draft — 缺陷草稿 → 固定 6 段预览（**只预览，没有提交路径**）。

## 为什么是一个脚本而不是一段提示词

三条硬纪律（字段编号不硬编码 / 六段结构禁改写 / 每缺陷只挂 1 个主复现 log）
写进提示词只是"教"，模型照不照做没人拦。落成这个脚本之后，它们变成**对草稿
数据的判红**：结构错、多挂一个 log、写死了字段编号，脚本直接 exit 2
并给错误码，渲染不出预览就没有可给用户确认的东西，链路自然断在这里。

## 这个脚本刻意没有的东西

**没有提交函数、没有网络调用、没有 TAPD 客户端。** 这不是没写完，是 0027
裁定二的形状：写工具与确认闸同批放行，先给写能力后补确认闸中间那段窗口期
AI 就能绕过确认直接提单。M2 阶段确认闸（`ask_user` + 本预览）先落地，写工具
仍在只读白名单外面（六处白名单定义点由 `tests/test_defect_write_gate.py` 守）。
所以本脚本的唯一出口是 stdout 上的预览文本。传 `--submit` 会 exit 3 并说明原因。

## 用法

    python -X utf8 render_draft.py --draft <draft.json> [--format html|md|both]

exit 0 = 预览已生成；2 = 草稿被纪律闸拦下；3 = 请求了不存在的写能力。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

#: 六段结构。**顺序即语义**，`references/bug-format.md` 的第 2 节是同一份东西的
#: 人类可读版，两边由 tests/test_defect_filing_skill.py 对拍，改一边不改另一边即判红。
SECTIONS: tuple[str, ...] = (
    "环境", "复现步骤", "期望结果", "实际结果", "日志证据", "初步判断",
)

#: 字段编号的形状。缺陷侧一律不许出现（见 check_no_field_numbers 的实证）。
FIELD_NUMBER_RE = re.compile(r"custom_field_\d+")

#: 正文禁止项：优先级归 TAPD 字段、完整日志归附件。
PRE_BLOCK_RE = re.compile(r"<\s*pre\b", re.I)
PRIORITY_IN_BODY_RE = re.compile(r"优先级")

_HERE = os.path.dirname(os.path.abspath(__file__))
FIELD_DEFAULTS_PATH = os.path.join(_HERE, os.pardir, "references", "field-defaults.json")


class GateError(Exception):
    """草稿没过纪律闸。`code` 是给人和给测试看的同一个标识。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ── 纪律闸 ──────────────────────────────────────────────────────────────────

def _walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk_strings(v)


def check_no_field_numbers(draft: dict) -> None:
    """硬纪律一：字段编号不硬编码。

    当版实证（2026-08-12 只读 `tapd_field_config`）：工作区 67600006 的 **bug 实体
    自定义字段数 = 0**，同工作区 story 实体 = 29（阳性对照，证明 0 不是查询坏了）。
    所以缺陷草稿里出现任何 `custom_field_<数字>` 都必然是错的——多半是从需求侧抄的
    （需求侧那些编号是给 story 排的——比如筛「测试人员」用的那一栏，与缺陷无关）。
    """
    hits = sorted({m for s in _walk_strings(draft) for m in FIELD_NUMBER_RE.findall(s)})
    if hits:
        raise GateError(
            "E_FIELD_NUMBER",
            "草稿里出现了写死的字段编号 " + "、".join(hits)
            + "。字段编号按工作项类型各排各的号，需求侧的编号搬到缺陷上必然写错栏；"
            "本工作区实测缺陷实体的自定义字段数为 0，本来就没有编号可填。")


def check_custom_fields_are_verified(draft: dict) -> None:
    """自定义字段：**没有经实证的写法通道就不许写**（fail-closed）。

    官方建单工具的 docstring 提到 `cus_<别名>` 会被后台转义成 `custom_field_*`，
    但那是**文档说法，本仓从未实测**；移植来源 `uiron_submit_bug.py` / `bugs/*.json`
    全量检索也从不设任何自定义字段。把一条更严的纪律建在未证实的机制上，
    比没有纪律更糟——所以这里不猜写法，直接要求停手问用户。
    """
    cf = draft.get("custom_fields") or {}
    if cf:
        raise GateError(
            "E_CUSTOM_FIELD_UNVERIFIED",
            f"草稿要写自定义字段 {sorted(cf)}，但本仓没有经实证的缺陷自定义字段写法通道。"
            "先跑 tapd_field_config(workspace_id, entity_type=\"bug\") 现场查："
            "查出来是空的就别填；非空则停手问用户该怎么写，不要照文档猜。")


def check_sections(draft: dict) -> dict:
    """硬纪律二：六段结构禁改写（缺、多、改名、调序，四种都拦）。"""
    sections = draft.get("sections")
    if not isinstance(sections, dict):
        raise GateError("E_NO_SECTIONS", "草稿缺 sections 字段。")
    got = tuple(sections.keys())
    if got != SECTIONS:
        missing = [s for s in SECTIONS if s not in got]
        extra = [s for s in got if s not in SECTIONS]
        detail = []
        if missing:
            detail.append("缺段 " + "、".join(missing))
        if extra:
            detail.append("多段 " + "、".join(extra))
        if not detail:
            detail.append("段序被改了")
        raise GateError(
            "E_SECTION_DRIFT",
            "；".join(detail) + f"。固定 6 段且顺序不可变：{' → '.join(SECTIONS)}。"
            "附件、对照日志、优先级建议都不许另立成段。")
    for name in SECTIONS:
        if not sections[name]:
            raise GateError("E_SECTION_EMPTY", f"第「{name}」段是空的，6 段都要有内容。")
    return sections


def check_single_log(sections: dict) -> str:
    """硬纪律三：每缺陷只挂 1 个主复现 log。"""
    evidence = sections["日志证据"]
    if not isinstance(evidence, dict):
        raise GateError("E_EVIDENCE_SHAPE", "「日志证据」段要是对象：{log, key_findings}。")
    log = evidence.get("log")
    if isinstance(log, (list, tuple)):
        raise GateError(
            "E_MULTI_LOG",
            f"挂了 {len(log)} 个 log。每缺陷只挂 1 个主复现 log——对照日志、设备 B "
            "这些不是本缺陷的复现证据，要说就在「初步判断」里一句话带过并指向它。")
    if not isinstance(log, str) or not log.strip():
        raise GateError("E_NO_LOG", "「日志证据」段没有主复现 log；端侧缺陷这一项是必填。")
    return log.strip()


def check_repro_verified(draft: dict) -> None:
    """复现步骤铁律：只写实操过且确实复现到的路径。"""
    if draft.get("repro_verified") is not True:
        raise GateError(
            "E_REPRO_UNVERIFIED",
            "草稿没有声明 repro_verified=true。复现步骤只能写你实际操作过、"
            "且确实复现到的那条路径；从日志反推出来的触发机制归「初步判断」段，不进步骤。")


def check_body_bans(sections: dict) -> None:
    """正文禁止项：大段 <pre>、正文写优先级建议。"""
    body_text = "\n".join(_walk_strings(sections))
    if PRE_BLOCK_RE.search(body_text):
        raise GateError("E_PRE_BLOCK", "正文里有 <pre> 大段日志块；完整 log 走附件/链接，正文只留摘录。")
    if PRIORITY_IN_BODY_RE.search(body_text):
        raise GateError("E_PRIORITY_IN_BODY", "正文写了优先级；优先级由 TAPD 字段 priority_label 承载，不进正文。")


def validate(draft: dict) -> dict:
    """全部纪律闸。返回校验过的 sections。"""
    check_no_field_numbers(draft)
    check_custom_fields_are_verified(draft)
    sections = check_sections(draft)
    check_single_log(sections)
    check_repro_verified(draft)
    check_body_bans(sections)
    return sections


# ── 渲染 ────────────────────────────────────────────────────────────────────

def load_field_defaults(path: str = FIELD_DEFAULTS_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def resolve_fields(draft: dict, defaults: dict) -> dict:
    """项目默认值 + 草稿覆盖。项目没配就报缺，不拿别的项目的前缀凑合。"""
    project = str(draft.get("project") or "").strip()
    projects = defaults.get("projects") or {}
    if project not in projects:
        raise GateError(
            "E_UNKNOWN_PROJECT",
            f"project={project!r} 不在 field-defaults.json 里。先在配置里加一条，"
            "或者问用户这条缺陷提到哪个项目——不要套用别的项目的前缀和处理人。")
    base = dict(projects[project])
    base.pop("source", None)
    over = draft.get("fields") or {}
    merged = {**base, **{k: v for k, v in over.items() if v not in (None, "")}}
    vocab = defaults.get("vocabularies") or {}
    for key in ("severity", "priority_label"):
        val = str(merged.get(key) or "").strip()
        allowed = vocab.get(key) or []
        if val and allowed and val not in allowed:
            raise GateError("E_BAD_VOCAB", f"{key}={val!r} 不在候选值 {allowed} 里。")
    merged["custom_fields"] = {k: v for k, v in (draft.get("custom_fields") or {}).items()}
    return merged


def build_title(draft: dict, fields: dict) -> str:
    title = draft.get("title") or {}
    module = str(title.get("module") or "").strip()
    summary = str(title.get("summary") or "").strip()
    if not module or not summary:
        raise GateError("E_TITLE", "标题要 {module, summary} 两段：模块/分类 + 一句话现象（不写根因）。")
    return f"{fields.get('title_prefix', '')}{module}：{summary}"


def _li(items) -> str:
    return "".join(f"<li>{x}</li>" for x in items)


def render_html(sections: dict) -> str:
    out = []
    env = sections["环境"]
    out.append('<h2 id="_1">环境</h2>')
    out.append("<ul>" + _li(env if isinstance(env, list) else [env]) + "</ul>")

    repro = sections["复现步骤"]
    out.append('<h2 id="_2">复现步骤</h2>')
    if isinstance(repro, dict):
        pre = str(repro.get("前置") or "").strip()
        if pre:
            out.append(f"<p>前置：{pre}</p>")
        steps = repro.get("steps") or []
    else:
        steps = repro if isinstance(repro, list) else [repro]
    out.append("<ol>" + _li(steps) + "</ol>")

    out.append('<h2 id="_3">期望结果</h2>')
    out.append(f"<p>{sections['期望结果']}</p>")
    out.append('<h2 id="_4">实际结果</h2>')
    out.append(f"<p>{sections['实际结果']}</p>")

    ev = sections["日志证据"]
    out.append('<h2 id="_5">日志证据</h2>')
    rows = [f"日志文件：<code>{ev['log']}</code>", "关键现象："]
    for group, lines in (ev.get("key_findings") or {}).items():
        inner = "".join(f"<li><code>{ln}</code></li>" for ln in lines)
        rows.append(f"{group}：<ul>{inner}</ul>")
    out.append("<ul>" + _li(rows) + "</ul>")

    out.append('<h2 id="_6">初步判断</h2>')
    out.append(f"<p>{sections['初步判断']}</p>")
    return "\n".join(out)


def render_markdown(title: str, fields: dict, sections: dict) -> str:
    lines = [f"**标题**：{title}", ""]
    shown = [("处理人(de)", fields.get("de") or "（未填，提交前必须问用户）"),
             ("测试(te)", fields.get("te") or ""),
             ("报告人", fields.get("reporter") or ""),
             ("严重程度", fields.get("severity") or "（未填）"),
             ("优先级", fields.get("priority_label") or "（未填）"),
             ("工作区", fields.get("workspace_id") or "（未填）")]
    lines += [f"- {k}：{v}" for k, v in shown]
    for k, v in (fields.get("custom_fields") or {}).items():
        lines.append(f"- {k}：{v}")
    lines.append("")
    for idx, name in enumerate(SECTIONS, 1):
        lines.append(f"### {idx}. {name}")
        val = sections[name]
        if name == "复现步骤":
            if isinstance(val, dict):
                if val.get("前置"):
                    lines.append(f"前置：{val['前置']}")
                steps = val.get("steps") or []
            else:
                steps = val if isinstance(val, list) else [val]
            lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
        elif name == "日志证据":
            lines.append(f"- 日志文件：`{val['log']}`")
            for group, ls in (val.get("key_findings") or {}).items():
                lines.append(f"- {group}：")
                lines += [f"    - `{ln}`" for ln in ls]
        elif isinstance(val, list):
            lines += [f"- {x}" for x in val]
        else:
            lines.append(str(val))
        lines.append("")
    return "\n".join(lines)


#: 预览尾部固定跟这一段。**它不是礼貌用语，是这条链路的当前真相**——
#: 说"已提交""已授权"就是 BB-502 那个形状（挂一个兑现不了的承诺）。
CONFIRM_FOOTER = """
---
以上是**草稿预览，尚未提交**。请确认：标题 / 处理人(de) / 严重程度 / 优先级。

当前通道的真相（不许含糊）：容器里的 TAPD 只放行两个只读工具，
`create_bug` 等 17 个写工具**一个都没放行**——写工具与确认闸同批放行是 0027
裁定二的不可让步项。所以确认之后我也**提交不了**，需要你把上面这份正文
在 TAPD 页面上建单，或者先把写工具的放行走完流程。附件同理：官方 MCP 全量
43 个工具里没有附件上传工具，log 只能以链接或正文占位路径承载。
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="缺陷草稿 → 6 段预览（只预览，不提交）")
    parser.add_argument("--draft", required=True, help="草稿 JSON 路径")
    parser.add_argument("--format", choices=("md", "html", "both"), default="both")
    parser.add_argument("--submit", action="store_true",
                        help="（不可用）本脚本没有提交路径，传了会直接报错退出")
    args = parser.parse_args(argv)

    if args.submit:
        sys.stderr.write(
            "E_WRITE_NOT_RELEASED: 本脚本没有提交路径，这是设计如此。\n"
            "写工具与确认闸同批放行（决策 0027 裁定二）；当前 TAPD 白名单只有\n"
            "get_stories_or_tasks / get_stories_fields_info 两个只读工具。\n")
        return 3

    with open(args.draft, encoding="utf-8") as fh:
        draft = json.load(fh)

    try:
        sections = validate(draft)
        fields = resolve_fields(draft, load_field_defaults())
        title = build_title(draft, fields)
    except GateError as exc:
        sys.stderr.write(f"{exc.code}: {exc.message}\n")
        return 2

    if args.format in ("md", "both"):
        sys.stdout.write(render_markdown(title, fields, sections) + "\n")
    if args.format in ("html", "both"):
        sys.stdout.write("--- description(HTML) ---\n")
        sys.stdout.write(render_html(sections) + "\n")
    sys.stdout.write(CONFIRM_FOOTER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
