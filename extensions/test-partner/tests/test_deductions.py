# -*- coding: utf-8 -*-
"""具名扣除机制自测（manager 2026-08-11 裁定的第三条路）。

两案都被否掉：长期红不可接受（红久了没人看，真红混进来认不出），
花第 6 行改上游断言也不必要（每次同步必撞 + 花掉最后的硬余量）。
正解是照抄圆桌线回归闸的「具名扣除」形态，并加两条硬要求：
**清单机械可读** + **闸打印「本次扣除 N 条」**——扣除本身要可见，否则它就是另一种静默。

本文件盯的是这套机制自己别烂掉。
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_PATH = os.path.join(HERE, "scripts", "known-deductions.json")
TOOL = os.path.join(HERE, "scripts", "deductions.py")
GATE = os.path.join(HERE, "scripts", "regression_gate.sh")


def _run(*args, cwd=HERE):
    # PYTHONIOENCODING 必须显式给：Windows 下子进程的 stdout 默认跟控制台代码页
    # （GBK），而清单里全是中文——不给就会在 UTF-8 解码时炸，
    # 且症状是 UnicodeDecodeError 而不是"工具坏了"，很容易误判成工具的问题。
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, TOOL, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8")


def _rows():
    with open(LIST_PATH, encoding="utf-8") as fh:
        return json.load(fh)["deductions"]


# ── 三段式：没有到期条件的扣除 = 永久豁免 ──────────────────────────────────


def test_every_deduction_answers_all_three_questions():
    for row in _rows():
        for field in ("why_red", "when_it_should_disappear", "reviewer"):
            assert str(row.get(field) or "").strip(), \
                f"{row.get('id')} 缺 {field}"


def test_why_red_is_an_attribution_not_a_label():
    """「已知问题」四个字不算归因。要求写出足够长的因果，挡住敷衍。"""
    for row in _rows():
        assert len(row["why_red"]) >= 30, f"{row['id']} 的 why_red 太短，像标签不像归因"


def test_expiry_condition_is_falsifiable():
    """到期条件必须写出**可核对的判据**，不能是「以后再说」。"""
    for row in _rows():
        text = row["when_it_should_disappear"]
        assert len(text) >= 20, f"{row['id']} 的到期条件太含糊"
        assert any(k in text for k in ("上游", "基线", "同步", "复跑", "删除", "停止")), \
            f"{row['id']} 的到期条件没有可核对的触发点"


def test_validator_rejects_a_missing_third_segment(tmp_path):
    """探测器自身的探测器：抽掉一段必须判红。"""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "1.0", "deductions": [
        {"id": "x", "layer": "upstream-tests", "selector": "a::b",
         "why_red": "y" * 40, "reviewer": "me"}]}, ensure_ascii=False),
        encoding="utf-8")
    out = _run("--list", str(bad), "--validate")
    assert out.returncode == 1
    assert "WHEN_IT_SHOULD_DISAPPEAR" in out.stderr


def test_validator_rejects_duplicate_ids(tmp_path):
    bad = tmp_path / "dup.json"
    row = {"id": "x", "layer": "upstream-tests", "selector": "a::b",
           "why_red": "y" * 40, "when_it_should_disappear": "上游修好后删除，复跑基线确认",
           "reviewer": "me"}
    bad.write_text(json.dumps({"schema_version": "1.0", "deductions": [row, dict(row)]},
                              ensure_ascii=False), encoding="utf-8")
    assert _run("--list", str(bad), "--validate").returncode == 1


# ── 渲染：闸真的用得上 ─────────────────────────────────────────────────────


def test_real_list_validates():
    out = _run("--validate")
    assert out.returncode == 0, out.stderr


def test_pytest_deselect_render():
    out = _run("--layer", "upstream-tests", "--format", "pytest-deselect")
    assert out.returncode == 0
    assert "--deselect tests/core/test_capabilities_runtime.py::" in out.stdout
    assert "--deselect tests/api/test_cors_settings.py" in out.stdout


def test_grep_pattern_render():
    out = _run("--layer", "web-node-tests", "--format", "grep-pattern")
    assert out.returncode == 0
    assert "code-block-themes" in out.stdout


def test_print_shows_the_count():
    """**扣除本身要可见**：闸每次都打「本次扣除 N 条」。"""
    out = _run("--print")
    assert out.returncode == 0
    assert "本次扣除" in out.stdout
    assert f"本次扣除 {len(_rows())} 条" in out.stdout


# ── 闸接线：别把清单建好了却没人读 ─────────────────────────────────────────


def test_gate_reads_the_list_instead_of_hardcoding():
    with open(GATE, encoding="utf-8") as fh:
        gate = fh.read()
    assert "deductions.py" in gate
    assert "--validate" in gate and "--print" in gate
    # 散落的硬编码必须已经被换掉——留着就是第二真相
    assert 'KNOWN_BROKEN_NODE_TESTS="appearance' not in gate
    assert "--deselect tests/api/test_cors_settings.py" not in gate


def test_gate_validates_the_list_before_using_it():
    """先校验再用：清单坏了要当场判红，而不是渲染出一个空 deselect 悄悄跑过去。"""
    with open(GATE, encoding="utf-8") as fh:
        gate = fh.read()
    validate_at = gate.index("--validate")
    first_use = min(gate.index("--format grep-pattern"),
                    gate.index("--format pytest-deselect"))
    assert validate_at < first_use


# ── 与 fork 注册的对应关系 ─────────────────────────────────────────────────


def test_capability_set_assertion_is_deducted_by_full_test_name():
    """按**测试全名**扣，不按文件扣——按文件扣会连带把同文件里其他真红也藏了。"""
    row = next(r for r in _rows() if r["id"] == "upstream-capability-set-assertion")
    assert row["selector"].endswith(
        "::test_builtin_capability_registry_covers_documented_capabilities")
    assert "::" in row["selector"]
