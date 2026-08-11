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
import re
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


# ── 作用域：扣得准不准，与清单能不能判红是两件事 ───────────────────────────


def test_no_pytest_deduction_is_file_level():
    """**全表**都要按 nodeid 扣，不止 capability 那一条。

    上一条只盯了一行，而 v1.0 的 cors 那条就是文件级溜进来的：实测该文件 4 例
    里只有 2 例红，另 2 例（含一条专测 partner PATCH 预检、正落在我方改动路径上的）
    被顺带豁免。**清单能判红 ≠ 清单扣得准**——判红管的是格式，扣得准管的是作用域，
    后者没有机械闸就只能靠人记得，而人不会记得。
    """
    offenders = [r["id"] for r in _rows()
                 if r.get("layer") == "upstream-tests" and "::" not in r["selector"]]
    assert not offenders, (
        f"这些扣除是文件级的，会连带藏掉同文件其他真红：{offenders}。"
        "先在基线上跑一次确认到底是哪几例，再按 nodeid 逐条扣。")


def test_every_deduction_records_a_real_verification_run():
    """第四段：**上次真跑确认**是哪天、怎么跑的。

    reviewer 回答「谁负责」，不回答「做没做」。没有这一段，「复核过」就是口头的——
    cors 那条的归因正是在文档里躺了三天没人复跑，实测才发现其中的怀疑部分是错的。
    """
    for row in _rows():
        stamp = str(row.get("verified_at") or "")
        assert re.search(r"\d{4}-\d{2}-\d{2}", stamp), \
            f"{row['id']} 的 verified_at 不是可核对的日期：{stamp!r}"
        how = str(row.get("verified_how") or "")
        assert len(how) >= 30, \
            f"{row['id']} 的 verified_how 太短，看不出跑的是什么：{how!r}"


def test_validator_rejects_a_missing_verification_stamp(tmp_path):
    """探测器自身的探测器（第四段版）：抽掉 verified_at 必须判红。"""
    bad = tmp_path / "noverify.json"
    bad.write_text(json.dumps({"schema_version": "1.1", "deductions": [
        {"id": "x", "layer": "upstream-tests", "selector": "a.py::b",
         "why_red": "y" * 40,
         "when_it_should_disappear": "上游修好后删除，复跑基线确认",
         "reviewer": "me", "verified_how": "z" * 40}]}, ensure_ascii=False),
        encoding="utf-8")
    out = _run("--list", str(bad), "--validate")
    assert out.returncode == 1
    assert "VERIFIED_AT" in out.stderr


def test_validator_rejects_a_vague_verification_date(tmp_path):
    """「最近复核过」不是日期。格式不卡死，这一段一周内就会退化成口号。"""
    bad = tmp_path / "vague.json"
    bad.write_text(json.dumps({"schema_version": "1.1", "deductions": [
        {"id": "x", "layer": "upstream-tests", "selector": "a.py::b",
         "why_red": "y" * 40,
         "when_it_should_disappear": "上游修好后删除，复跑基线确认",
         "reviewer": "me", "verified_at": "最近", "verified_how": "z" * 40}]},
        ensure_ascii=False), encoding="utf-8")
    out = _run("--list", str(bad), "--validate")
    assert out.returncode == 1
    assert "VERIFIED_AT_FORMAT" in out.stderr


def test_print_shows_the_verification_stamp():
    """闸打印里要能看到「上次核」，否则这段等于只写给校验器看。"""
    out = _run("--print")
    assert "上次核" in out.stdout


def test_gate_pins_the_interpreter_instead_of_using_ambient_python():
    """闸不许用裸 `python` —— 否则它的颜色取决于你在哪个 shell 里跑。

    实测同一份代码：扩展 venv 里 1217 passed / 3 skipped；全局 python 里
    `test_journey_call_surface` 直接 ModuleNotFoundError（全局没装 `mcp`）。
    **一个结论随环境变的闸不是闸。** 这条按闸脚本的字面量守，因为「我这儿是绿的」
    这种错误恰恰不会在跑闸的人那里暴露。
    """
    with open(GATE, encoding="utf-8") as fh:
        gate = fh.read()
    assert "$PY" in gate, "闸必须走钉死的解释器变量"
    # 第 4 层那条 `cd /repo && python -m pytest` 跑在**容器里**，那儿的 `python`
    # 就是唯一正确的解释器，不该也不能替换成宿主 venv 的路径。判据按「有没有
    # cd /repo」区分宿主行与容器行——不是按行号，行号会随编辑漂。
    bare = [ln.strip() for ln in gate.splitlines()
            if re.search(r"(?<![\w$/\"])python (-m pytest|\"\$DEDUCT\")", ln)
            and "cd /repo" not in ln]
    assert not bare, f"闸里还有跑在宿主上的裸 python 调用：{bare}"


def test_gate_fails_closed_when_the_venv_is_missing():
    """venv 不在时**判红并说怎么建**，不静默回落到 `python`。

    回落等于把上面那个不确定性请回来，而且这次还带着「闸说它跑过了」的背书——
    与「扣除不可见」同族：都是把一个未成立的前提伪装成正常结果。
    """
    with open(GATE, encoding="utf-8") as fh:
        gate = fh.read()
    assert "扩展 venv 不存在" in gate
    assert "python -m venv .venv" in gate
