# -*- coding: utf-8 -*-
"""提交前独占区护栏的回归（BB-518）。

护栏本体有 `--selftest`（给不装 pytest 的人用），这里把同两例接进回归套，
免得它变成"有个脚本但没人跑"。第三例守的是它的失败方向：**空声明必须 fail closed**。
"""
from __future__ import annotations

import importlib.util
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD_PATH = os.path.join(REPO_ROOT, "scripts", "staged_scope_guard.py")

_spec = importlib.util.spec_from_file_location("staged_scope_guard", GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

ALLOW = ["extensions/test-partner/skills/defect-filing/",
         "extensions/test-partner/tests/test_defect_"]


def test_out_of_scope_staged_set_is_aborted():
    r = guard.check(["extensions/test-partner/skills/defect-filing/SKILL.md",
                     "server/journey/defect_exit.py",
                     "web/components/test-journey/copy.ts"], ALLOW)
    assert r["ok"] is False
    assert r["offenders"] == ["server/journey/defect_exit.py",
                             "web/components/test-journey/copy.ts"]


def test_in_scope_staged_set_passes():
    r = guard.check(["extensions/test-partner/skills/defect-filing/SKILL.md",
                     "extensions/test-partner/tests/test_defect_write_gate.py"], ALLOW)
    assert r["ok"] is True and r["offenders"] == []


def test_empty_declaration_fails_closed():
    """查不到声明就放行 = 护栏在最需要它的时候消失。"""
    r = guard.check(["anything/at/all.py"], [])
    assert r["ok"] is False and r["offenders"] == ["anything/at/all.py"]


def test_windows_separators_are_normalised():
    """Windows 上拿到的路径可能带反斜杠；不归一化会让护栏在本机永远放行。"""
    r = guard.check([r"extensions\test-partner\skills\defect-filing\SKILL.md"], ALLOW)
    assert r["ok"] is True


def test_unknown_agent_exits_two(capsys):
    code = guard.main(["--agent", "no-such-line"])
    assert code == 2
    assert "E_UNKNOWN_AGENT" in capsys.readouterr().err


def test_declared_scopes_file_is_loadable_and_covers_this_line():
    scopes = guard.load_scopes()
    entry = scopes["agents"]["m2-defect"]
    assert any(a.endswith("skills/defect-filing/") for a in entry["allow"])
