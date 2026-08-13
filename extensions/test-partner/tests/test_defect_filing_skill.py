# -*- coding: utf-8 -*-
"""`defect-filing` skill 包的机械判据（0027 缺陷提单节 / 设计稿 §7 缺陷根因出口）。

三条硬纪律各自对应一组断言，每组都配**阳性对照**——只断"当前是绿的"证明不了
判据会红，本线已经栽过一次（扫描无命中其实是匹配逻辑坏了）。

另外两类判据回答本线纪律四、五：
- **接上了没有**：拿平台真实的 `SkillService` 加载这个包（不是自己复述一遍它的规矩）；
- **挂对通道没有**：这份 skill 教模型调 `ask_user`，所以它绝不能进伙伴的技能清单
  ——伙伴通道故意没有等待器（BB-502），进去就是静默降级。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(REPO_ROOT, "skills")
SKILL_NAME = "defect-filing"
SKILL_DIR = os.path.join(SKILLS_DIR, SKILL_NAME)
REFS = os.path.join(SKILL_DIR, "references")

#: 移植自小铁 BUG_FORMAT.md §3 的固定 6 段。**这个字面量是被移植的需求本身**，
#: 所以钉在这里是对的；下面三处实现两两对拍，谁漂了都会红。
CANONICAL_SECTIONS = ("环境", "复现步骤", "期望结果", "实际结果", "日志证据", "初步判断")


def _load_renderer():
    path = os.path.join(SKILL_DIR, "scripts", "render_draft.py")
    spec = importlib.util.spec_from_file_location("defect_render_draft", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rd = _load_renderer()


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _skill_files() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(SKILL_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        out += [os.path.join(dirpath, fn) for fn in filenames]
    return out


def _example_draft() -> dict:
    return json.loads(_read(os.path.join(REFS, "example-draft.json")))


# ── 包形态：平台真的能加载它（纪律五：有消费方就要有集成判据） ──────────────

def test_package_is_loadable_by_the_real_platform_skill_service():
    """用 DeepTutor 自己的 `SkillService` 加载，而不是我们复述一遍它的规矩。

    容器/开发机上 `deeptutor` 可导入时这条真跑；导不进去时 skip 并说明——
    skip 的那次不算证据，交付时要给出真跑过的那次输出。
    """
    dt_root = os.path.dirname(os.path.dirname(REPO_ROOT))
    if dt_root not in sys.path:
        sys.path.insert(0, dt_root)
    try:
        from deeptutor.services.skill.service import SkillService, render_skills_manifest
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"本环境导不进 deeptutor（{type(exc).__name__}: {exc}）——"
                    "本条须在装了平台依赖的环境里跑，见交付证据")

    svc = SkillService(root=__import__("pathlib").Path(SKILLS_DIR), builtin_root=None)
    names = {s.name: s for s in svc.list_skills()}
    assert SKILL_NAME in names, f"平台列不出这个 skill：{sorted(names)}"
    assert names[SKILL_NAME].description.strip(), "description 空 = manifest 那行没内容 = 模型不会去读它"

    body = svc.read_skill_file(SKILL_NAME, "SKILL.md")
    assert "缺陷" in body and "ask_user" in body

    # 附属文件也要能被 read_skill 取到，否则「按需取」是空话。
    for rel in ("references/bug-format.md", "references/field-defaults.json",
                "scripts/render_draft.py", "references/example-draft.json"):
        assert svc.read_skill_file(SKILL_NAME, rel).strip()

    manifest = render_skills_manifest(svc.summary_entries())
    assert f"**{SKILL_NAME}**" in manifest, "manifest 里没有这一行 = 模型看不见它"


def test_skill_dir_name_matches_frontmatter_and_platform_name_rule():
    text = _read(os.path.join(SKILL_DIR, "SKILL.md"))
    assert text.startswith("---\n"), "缺 frontmatter，平台解析不出 description"
    fm = text.split("---\n", 2)[1]
    assert re.search(r"^name:\s*defect-filing\s*$", fm, re.M)
    assert re.search(r"^description:\s*\S", fm, re.M)
    # 平台的名字正则（deeptutor/services/skill/service.py:64）。
    assert re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", SKILL_NAME)


def test_skill_is_not_provisioned_to_the_partner_channel():
    """这份 skill 教模型调 `ask_user`；伙伴通道故意没有等待器（BB-502）。

    挂过去 = 问题被拍平成本轮最终回复、用户无感知。所以它只能待在
    「测试」capability 那条有等待器的通道上。
    """
    from server.gateway import gate_selfcheck as gs
    from server.gateway import partner_setup

    assert SKILL_NAME not in partner_setup.REQUIRED_SKILLS
    assert gs.channel_capability("partner")["has_waiter"] is False
    assert gs.channel_capability("capability_test")["has_waiter"] is True


# ── 硬纪律一：字段编号不硬编码 ──────────────────────────────────────────────

def test_no_hardcoded_field_number_anywhere_in_the_package():
    hits = []
    for path in _skill_files():
        for i, line in enumerate(_read(path).splitlines(), 1):
            if rd.FIELD_NUMBER_RE.search(line):
                hits.append(f"{os.path.relpath(path, SKILL_DIR)}:{i}: {line.strip()[:70]}")
    # 规范文里以「不许写 custom_field_<数字>」这种形态解释纪律是允许的，
    # 但那种写法带的是尖括号占位而不是真数字，所以这里期望零命中。
    assert hits == [], "包里出现了写死的字段编号：\n" + "\n".join(hits)


def test_field_number_scanner_has_a_positive_control():
    """阳性对照：造一个应命中的样本，确认匹配逻辑没坏。"""
    assert rd.FIELD_NUMBER_RE.search("cus_x custom_field_10 = 1")
    assert not rd.FIELD_NUMBER_RE.search("cus_bug等级：严重")


def test_renderer_blocks_a_draft_carrying_a_field_number():
    draft = _example_draft()
    draft["custom_fields"] = {"custom_field_10": "测试"}
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_FIELD_NUMBER"


def test_field_defaults_record_the_measured_fact_not_a_guessed_mechanism():
    """配置里记的必须是**实测事实**（bug 实体 0 栏 / story 29 栏做阳性对照），

    不是"用 cus_ 别名"这种从 docstring 推出来的写法——那条通道本仓没实测过。
    """
    cfg = json.loads(_read(os.path.join(REFS, "field-defaults.json")))
    note = "\n".join(cfg["_note"])
    assert set(cfg["vocabularies"]) == {"severity", "priority_label"}
    assert "tapd_field_config" in note and "bug 实体自定义字段数 = 0" in note
    assert "cus_" not in note, "别把未实证的别名写法写回配置里"
    for name, proj in cfg["projects"].items():
        assert set(proj) >= {"workspace_id", "title_prefix", "reporter", "te", "log_required"}, name


def test_renderer_blocks_custom_fields_because_the_write_channel_is_unverified():
    """自定义字段 fail-closed：没有经实证的写法就不许写，停手问用户。"""
    draft = _example_draft()
    assert draft["custom_fields"] == {}, "样例草稿不该演示一个没实测过的写法"
    draft["custom_fields"] = {"cus_发现阶段": "测试执行"}
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_CUSTOM_FIELD_UNVERIFIED"


# ── 硬纪律二：六段结构禁改写 ────────────────────────────────────────────────

def _sections_from_format_doc() -> tuple[str, ...]:
    """从规范文里把段序解析出来（`环境 → 复现步骤 → …` 那一行）。"""
    text = _read(os.path.join(REFS, "bug-format.md"))
    for line in text.splitlines():
        if "→" in line and "环境" in line:
            return tuple(p.strip() for p in line.strip().split("→"))
    raise AssertionError("bug-format.md 里找不到段序那一行")


def test_three_places_agree_on_the_six_sections():
    """规范文 / 渲染器 / 样例草稿三处对拍，任一处漂了都红。"""
    assert _sections_from_format_doc() == CANONICAL_SECTIONS
    assert rd.SECTIONS == CANONICAL_SECTIONS
    assert tuple(_example_draft()["sections"].keys()) == CANONICAL_SECTIONS


@pytest.mark.parametrize("mutate,label", [
    (lambda s: {**s, "附件": ["a.log"]}, "多一段"),
    (lambda s: {k: v for k, v in s.items() if k != "初步判断"}, "少一段"),
    (lambda s: {("根因" if k == "初步判断" else k): v for k, v in s.items()}, "改段名"),
    (lambda s: dict(reversed(list(s.items()))), "调段序"),
])
def test_renderer_blocks_every_shape_of_section_drift(mutate, label):
    """阳性对照四连：缺、多、改名、调序，一种都不许放过。"""
    draft = _example_draft()
    draft["sections"] = mutate(draft["sections"])
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_SECTION_DRIFT", label


def test_renderer_blocks_priority_and_pre_block_in_body():
    draft = _example_draft()
    draft["sections"]["初步判断"] += " 建议把优先级调高。"
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_PRIORITY_IN_BODY"

    draft = _example_draft()
    draft["sections"]["日志证据"]["key_findings"] = {"摘录": ["<pre>大段日志</pre>"]}
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_PRE_BLOCK"


# ── 硬纪律三：每缺陷只挂 1 个主复现 log ─────────────────────────────────────

def test_renderer_blocks_more_than_one_log():
    draft = _example_draft()
    draft["sections"]["日志证据"]["log"] = ["a.log", "b.log"]
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_MULTI_LOG"


def test_renderer_blocks_zero_log():
    draft = _example_draft()
    draft["sections"]["日志证据"]["log"] = ""
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_NO_LOG"


def test_rendered_body_has_exactly_one_log_slot():
    draft = _example_draft()
    html = rd.render_html(rd.validate(draft))
    assert html.count("日志文件：") == 1


def test_format_doc_states_the_single_log_rule_and_the_attachment_gap():
    text = _read(os.path.join(REFS, "bug-format.md"))
    assert "只挂 1 个主复现 log" in text
    # 0027 的待验证点已经用无截断枚举闭掉：官方 43 个工具里没有附件上传工具。
    assert "没有任何附件上传工具" in text


# ── 复现步骤铁律 ────────────────────────────────────────────────────────────

def test_renderer_blocks_unverified_repro():
    draft = _example_draft()
    draft["repro_verified"] = False
    with pytest.raises(rd.GateError) as e:
        rd.validate(draft)
    assert e.value.code == "E_REPRO_UNVERIFIED"


# ── 零自动提单：包里没有提交路径，写工具只在禁止语境里出现 ──────────────────

WRITE_TOOL_NAMES = ("create_bug", "update_bug", "create_comments", "update_comments",
                    "create_story_or_task", "update_story_or_task", "create_tcases_batch",
                    "create_or_update_tcases", "add_timesheets", "update_timesheets",
                    "create_wiki", "update_wiki", "send_qiwei_message",
                    "entity_relations", "program_bind_entities",
                    "create_iteration", "update_iteration")

#: 刻意**不含**「确认」二字：「确认完直接调 create_bug」正是我们要抓的那种句子，
#: 把「确认」当豁免词会让阳性对照本身变绿——这条曾在第一次跑时真的红过。
BAN_MARKERS = ("禁止", "不得", "不许", "不调", "没放行", "未放行", "白名单")


def _write_tool_mentions_without_ban(text: str) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(t in line for t in WRITE_TOOL_NAMES) and not any(m in line for m in BAN_MARKERS):
            out.append(f"{i}: {line.strip()[:70]}")
    return out


def test_write_tools_are_only_ever_mentioned_under_a_prohibition():
    bad = {}
    for path in _skill_files():
        hits = _write_tool_mentions_without_ban(_read(path))
        if hits:
            bad[os.path.relpath(path, SKILL_DIR)] = hits
    assert bad == {}, ("包里出现了不带禁止/未放行语境的写工具提及——"
                       f"这正是模型会照着调的那种句子：{bad}")


def test_prohibition_scanner_has_a_positive_control():
    assert _write_tool_mentions_without_ban("确认完直接调 create_bug 提交即可。\n")
    assert not _write_tool_mentions_without_ban("禁止调用 create_bug。\n")


def test_renderer_has_no_submit_path_at_all():
    """不是"提交函数里加了个判断"，是**根本没有提交代码**。"""
    src = _read(os.path.join(SKILL_DIR, "scripts", "render_draft.py"))
    for banned in ("import requests", "import httpx", "import urllib.request",
                   "urlopen(", "def submit", "create_bug("):
        assert banned not in src, f"渲染器里出现了 {banned!r}——这个脚本不许有对外写入能力"


def test_submit_flag_refuses_with_reason():
    err = io.StringIO()
    old, sys.stderr = sys.stderr, err
    try:
        code = rd.main(["--draft", os.path.join(REFS, "example-draft.json"), "--submit"])
    finally:
        sys.stderr = old
    assert code == 3
    assert "E_WRITE_NOT_RELEASED" in err.getvalue()


def test_skill_does_not_claim_the_ticket_was_filed():
    text = _read(os.path.join(SKILL_DIR, "SKILL.md"))
    assert "尚未提交" in _read(os.path.join(SKILL_DIR, "scripts", "render_draft.py"))
    assert "不要说" in text and "已提交" in text


# ── dry-run golden ─────────────────────────────────────────────────────────

def test_dry_run_golden_on_the_example_draft():
    out = io.StringIO()
    old, sys.stdout = sys.stdout, out
    try:
        code = rd.main(["--draft", os.path.join(REFS, "example-draft.json")])
    finally:
        sys.stdout = old
    text = out.getvalue()
    assert code == 0
    # 六段按序出现在预览里
    positions = [text.index(f"### {i}. {name}") for i, name in enumerate(CANONICAL_SECTIONS, 1)]
    assert positions == sorted(positions)
    assert '<h2 id="_1">环境</h2>' in text and '<h2 id="_6">初步判断</h2>' in text
    assert "【小铁IP机器人】" in text          # 项目前缀来自配置，不写死在渲染器里
    assert "尚未提交" in text                   # 预览尾部的真相段没被丢掉
    assert "处理人(de)" in text and "未填" in text  # de 空着就必须显式提示要问用户
