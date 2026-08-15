# -*- coding: utf-8 -*-
"""具名扣除机制自测（manager 2026-08-11 裁定的第三条路）。

扣除只用于已被独立基线证明的非本分支红项；能修正测试契约或测试环境时就应删除。
清单允许为零，但始终保持**机械可读**并让闸打印「本次扣除 N 条」——
扣除本身要可见，否则它就是另一种静默。

本文件盯的是这套机制自己别烂掉。
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_PATH = os.path.join(HERE, "scripts", "known-deductions.json")
TOOL = os.path.join(HERE, "scripts", "deductions.py")
GATE = os.path.join(HERE, "scripts", "regression_gate.sh")
P3_FLOW = os.path.join(HERE, "scripts", "verify_p3_flow.sh")


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
    assert out.stdout.strip() == ""


def test_grep_pattern_render():
    out = _run("--layer", "web-node-tests", "--format", "grep-pattern")
    assert out.returncode == 0
    assert out.stdout.strip() == ""


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


def test_capability_set_assertion_is_no_longer_deducted():
    """Test 已进入权威能力清单后，这条断言必须真跑，不能继续被豁免。"""
    selectors = {row["selector"] for row in _rows()}
    assert not any("test_builtin_capability_registry" in item for item in selectors)


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
    """有扣除时显示复核时间；零扣除时明确显示无，不制造空白歧义。"""
    out = _run("--print")
    if _rows():
        assert "上次核" in out.stdout
    else:
        assert "本次扣除 0 条" in out.stdout and "（无）" in out.stdout


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


def test_gate_keeps_source_and_user_data_immutable_in_docker():
    """Windows Git Bash 不能改写容器路径，也不能让回归写进工作树。"""
    with open(GATE, encoding="utf-8") as fh:
        gate = fh.read()
    assert "MSYS_NO_PATHCONV=1 docker run" in gate
    assert '-v "$WIN_ROOT:/repo:ro"' in gate
    assert '-v "$WIN_ROOT/data:/seed-data:ro"' in gate
    assert "-v /repo/data" in gate
    assert "cp -a /seed-data/. /repo/data/" in gate
    assert "DEEPTUTOR_IGNORE_PROCESS_ENV_OVERRIDES=0" in gate


def _run_bash(source_path, command):
    bash = shutil.which("bash")
    if os.name == "nt":
        git_bash = os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Git", "bin", "bash.exe",
        )
        if os.path.isfile(git_bash):
            bash = git_bash
    if not bash:
        pytest.skip("bash is not available in this test environment")
    relative = os.path.relpath(source_path, HERE).replace(os.sep, "/")
    return subprocess.run(
        [bash, "-c", f"source '{relative}'; {command}"],
        cwd=HERE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_p3_flow_exit_code_matches_failure_count_without_side_effects():
    assert _run_bash(P3_FLOW, "flow_exit_code 0").returncode == 0
    assert _run_bash(P3_FLOW, "flow_exit_code 1").returncode != 0


def test_empty_node_deduction_pattern_cannot_hide_failures():
    node_output = "ok 1 - good\nnot ok 2 - real-regression"
    out = _run_bash(
        GATE,
        "filter_unexpected_node_failures "
        f"'{node_output}' ''",
    )
    assert out.returncode == 0
    assert "not ok 2 - real-regression" in out.stdout


def test_named_node_deduction_only_filters_its_matching_failure():
    node_output = "not ok 1 - known-baseline\nnot ok 2 - real-regression"
    out = _run_bash(
        GATE,
        "filter_unexpected_node_failures "
        f"'{node_output}' 'known-baseline'",
    )
    assert out.returncode == 0
    assert "known-baseline" not in out.stdout
    assert "not ok 2 - real-regression" in out.stdout


def _tap(*rows: str) -> str:
    return "\n".join(rows)


def test_node_gate_requires_complete_consistent_tap_even_without_not_ok():
    crash = _run_bash(
        GATE, "node_test_run_is_acceptable 'internal loader crash' 1 ''")
    assert crash.returncode != 0

    truncated = _tap("ok 1 - good", "# tests 1", "# pass 1")
    result = _run_bash(
        GATE, f"node_test_run_is_acceptable '{truncated}' 0 ''")
    assert result.returncode != 0


def test_node_gate_accepts_green_or_only_named_complete_failures():
    green = _tap(
        "ok 1 - good", "# tests 1", "# pass 1", "# fail 0",
        "# cancelled 0", "# skipped 0", "# todo 0")
    assert _run_bash(
        GATE, f"node_test_run_is_acceptable '{green}' 0 ''").returncode == 0

    known = _tap(
        "not ok 1 - known-baseline", "# tests 1", "# pass 0", "# fail 1",
        "# cancelled 0", "# skipped 0", "# todo 0")
    assert _run_bash(
        GATE,
        f"node_test_run_is_acceptable '{known}' 1 'known-baseline'",
    ).returncode == 0


def test_node_gate_rejects_unknown_bailout_and_inconsistent_summary():
    mixed = _tap(
        "not ok 1 - known-baseline", "not ok 2 - real-regression",
        "# tests 2", "# pass 0", "# fail 2", "# cancelled 0",
        "# skipped 0", "# todo 0")
    assert _run_bash(
        GATE,
        f"node_test_run_is_acceptable '{mixed}' 1 'known-baseline'",
    ).returncode != 0
    bailout = _tap(
        "Bail out! loader", "# tests 0", "# pass 0", "# fail 0",
        "# cancelled 0", "# skipped 0", "# todo 0")
    assert _run_bash(
        GATE, f"node_test_run_is_acceptable '{bailout}' 1 ''").returncode != 0
    inconsistent = _tap(
        "ok 1 - good", "# tests 2", "# pass 1", "# fail 0",
        "# cancelled 0", "# skipped 0", "# todo 0")
    assert _run_bash(
        GATE,
        f"node_test_run_is_acceptable '{inconsistent}' 0 ''",
    ).returncode != 0


def test_gate_uses_one_configurable_node_runtime_for_every_web_check():
    with open(GATE, encoding="utf-8") as fh:
        gate = fh.read()
    assert 'NODE_BIN="${NODE_BIN:-node}"' in gate
    assert '"$NODE_BIN" scripts/i18n_parity.mjs' in gate
    assert '"$NODE_BIN" scripts/run-node-tests.mjs' in gate
    assert '"$NODE_BIN" node_modules/eslint/bin/eslint.js' in gate


def test_gate_blocks_high_or_critical_npm_vulnerabilities():
    with open(GATE, encoding="utf-8") as fh:
        gate = fh.read()
    assert 'NPM_BIN="${NPM_BIN:-npm}"' in gate
    assert '"$NPM_BIN" audit --audit-level=high --no-fund' in gate
    assert "high/critical 必须为 0" in gate


def test_ci_paths_cover_overlay_and_trusted_journey_bridge():
    """公共 GitHub CI 必须安全自动触发，并保持完整回归面。"""
    workflow_path = os.path.abspath(os.path.join(
        HERE, "..", "..", ".github", "workflows", "test-partner.yml"
    ))
    with open(workflow_path, encoding="utf-8") as fh:
        workflow = fh.read()
    trigger = workflow.split("permissions:", maxsplit=1)[0]
    assert "pull_request:" in trigger
    assert "push:" in trigger
    assert "branches:\n      - main" in trigger
    assert "workflow_dispatch:" in trigger
    assert "pull_request_target:" not in trigger
    assert "permissions:\n  contents: read" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "self-hosted" not in workflow.lower()
    assert "secrets." not in workflow
    assert '-k "not test_ui_track_real_browser"' in workflow
    assert "tests/test_journey_exec.py::test_ui_track_real_browser" in workflow

    host_workflow_path = os.path.abspath(os.path.join(
        HERE, "..", "..", ".github", "workflows", "tests.yml"
    ))
    with open(host_workflow_path, encoding="utf-8") as fh:
        host_workflow = fh.read()
    host_trigger = host_workflow.split("permissions:", maxsplit=1)[0]
    assert "pull_request:" in host_trigger
    assert "push:" in host_trigger
    assert "branches:\n      - main" in host_trigger
    assert "workflow_dispatch:" in host_trigger
    assert "pull_request_target:" not in host_trigger
    assert "permissions:\n  contents: read" in host_workflow
    assert "runs-on: ubuntu-latest" in host_workflow
    assert "self-hosted" not in host_workflow.lower()
    assert "secrets." not in host_workflow
    for command in (
        "python -m pytest -q tests deeptutor/learning/tests",
        "npm run test:node",
        "npm run i18n:parity",
        "npm run build",
        "npm run perf:check",
    ):
        assert command in host_workflow
