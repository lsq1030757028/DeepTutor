# -*- coding: utf-8 -*-
"""候选需求清单的字段口径（AC1 / AC21 / Delta 施工约束①②）。

## 这一层在防什么

「测试人」是 TAPD 的自定义字段，字段号按项目各不相同（67600006 上是
`custom_field_10`，换个项目就不成立）。把它写进提示词，等于把一个项目的偶然
事实固化成全局规则——模型会拿着它去查别的项目，然后**安静地返回零条**。
安静是最坏的那部分：用户会以为自己真没有待测需求。

所以这里的判据都围绕一件事：**没锚住的时候，它必须说出来**。
"""
import json

import pytest

from deeptutor.services.tapd_field_baseline import (
    DEFAULT_LIMIT,
    DEFAULT_ORDER,
    baseline_for,
    candidate_query_template,
    load_baselines,
    render_candidate_shape_block,
)


def _write_baseline(tmp_path, workspaces):
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps({"_schema": "tapd-field-baseline/v1", "workspaces": workspaces},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ── 随包过渡基线 ────────────────────────────────────────────────────────────


def test_shipped_baseline_carries_the_known_workspace_as_draft():
    """随包基线要能读出来，且 67600006 明确标成**未核对**。

    标成 confirmed 才是真问题：那会让模型不做交叉验证就把结果当全集报出去。
    """
    baselines = load_baselines()
    assert "67600006" in baselines, "随包过渡基线读不到——取数口断了"
    entry = baselines["67600006"]
    assert entry.tester_field == "custom_field_10"
    assert entry.status == "draft-unconfirmed"
    assert not entry.confirmed
    assert entry.usable
    assert entry.evidence, "过渡基线必须留依据，否则无从复核"


def test_unknown_workspace_is_missing_not_guessed(tmp_path):
    """查不到的项目返回 missing，**不许回落到别的项目的字段号**。"""
    path = _write_baseline(tmp_path, {"67600006": {"tester_field": "custom_field_10"}})
    other = baseline_for("99999999", path)
    assert other.status == "missing"
    assert other.tester_field == ""
    assert not other.usable


def test_unreadable_baseline_file_yields_empty_not_an_exception(tmp_path):
    """取数口坏掉时是空基线，不是崩——但也不是编一个出来。"""
    assert load_baselines(tmp_path / "does-not-exist.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_baselines(broken) == {}


# ── 查询模板 ────────────────────────────────────────────────────────────────


def test_template_defaults_match_the_decided_shape(tmp_path):
    """默认口径 = 测试人=我 + 未结束 + 创建时间新→旧（decisions「待定① 收口」）。"""
    path = _write_baseline(tmp_path, {
        "67600006": {"tester_field": "custom_field_10", "status": "confirmed"},
    })
    template = candidate_query_template("67600006", path)
    assert template.tester_field == "custom_field_10"
    assert template.order == DEFAULT_ORDER == "created desc"
    assert template.limit == DEFAULT_LIMIT
    assert template.exclude_closed is True
    assert template.anchored


def test_template_marks_unanchored_when_the_baseline_is_missing(tmp_path):
    path = _write_baseline(tmp_path, {})
    template = candidate_query_template("67600006", path)
    assert not template.anchored
    assert template.tester_field == ""


def test_ordering_and_limit_are_declared_as_caller_supplied(tmp_path):
    """施工约束①：工具 description 没声明这两项，不显式传就拿不到这个顺序。"""
    template = candidate_query_template("67600006", _write_baseline(tmp_path, {}))
    assert set(template.must_pass_explicitly) == {"order", "limit"}


# ── 注入块：三种状态三种话术 ────────────────────────────────────────────────


@pytest.mark.parametrize("language", ["zh", "en"])
def test_confirmed_baseline_block_states_the_field(tmp_path, language):
    path = _write_baseline(tmp_path, {
        "67600006": {"tester_field": "custom_field_10", "status": "confirmed",
                     "project": "AI陪伴类创新产品线"},
    })
    block = render_candidate_shape_block(
        candidate_query_template("67600006", path), language=language)
    assert "custom_field_10" in block
    assert "created desc" in block
    assert str(DEFAULT_LIMIT) in block


@pytest.mark.parametrize("language", ["zh", "en"])
def test_draft_baseline_block_demands_disclosure(tmp_path, language):
    """过渡草稿必须让模型**告诉用户这条没核对过**，否则它就成了沉默的事实。"""
    path = _write_baseline(tmp_path, {
        "67600006": {"tester_field": "custom_field_10", "status": "draft-unconfirmed",
                     "evidence": "Opus 实测 64 条"},
    })
    block = render_candidate_shape_block(
        candidate_query_template("67600006", path), language=language)
    assert "custom_field_10" in block
    disclosure = "未经核对" if language == "zh" else "not been reviewed"
    assert disclosure in block, f"草稿基线块没要求披露：{block}"
    assert "Opus 实测 64 条" in block, "依据没带出去，用户无从复核"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_unanchored_block_forbids_guessing_a_field_number(tmp_path, language):
    """没锚住时最危险的行为是猜一个字段号硬查然后返回零条。"""
    block = render_candidate_shape_block(
        candidate_query_template("67600006", _write_baseline(tmp_path, {})),
        language=language)
    forbid = "不要猜字段号" if language == "zh" else "Do not guess a field number"
    assert forbid in block
    assert "custom_field_10" not in block, "没锚住却漏了一个字段号出去"


def test_no_workspace_yields_no_block(tmp_path):
    """项目都还没选定就不要发这个块——那只是噪声。"""
    assert render_candidate_shape_block(
        candidate_query_template("", _write_baseline(tmp_path, {})), language="zh") == ""


# ── 注入块自己不许拆闸 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("status", ["confirmed", "draft-unconfirmed", "missing"])
def test_block_never_leaks_the_workspace_id(tmp_path, language, status):
    """注入块**任何状态下都不许把 workspace_id 写进文案**。

    纪律段明令不得向用户暴露 workspace_id；宿主注入块是提示词的一部分，
    模型会照着复述。第一版这里在「没锚住」分支上写了
    「这个项目（W1）……」——闸自己漏了，`tests/agents/test_tapd_business.py`
    那条既有断言当场抓住。这条测试把它钉死。
    """
    workspace = "9911223344"
    entry = {} if status == "missing" else {
        "tester_field": "custom_field_10", "status": status,
        "project": "AI陪伴类创新产品线", "evidence": "实测 64 条",
    }
    path = _write_baseline(tmp_path, {workspace: entry} if entry else {})
    block = render_candidate_shape_block(
        candidate_query_template(workspace, path), language=language)
    assert block, "块不该是空的"
    assert workspace not in block, f"注入块漏了 workspace_id：{block}"
