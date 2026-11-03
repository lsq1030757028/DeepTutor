# -*- coding: utf-8 -*-
"""缺陷出口的**顺序闸**：写工具不许先于确认闸落地（决策 0027 裁定二）。

0027 原文（不可让步项）：「**第二步**：提缺陷、改状态等写工具，与确认闸同批放行。
先放写权限、后补流程约束，中间那段窗口期 AI 就能绕过确认直接提单。**这个口子不能开。**」

一句提示词管不住这件事，所以这里把它做成一条**耦合断言**：

    白名单里出现任何 TAPD 写工具  ⇒  必须同时存在放行记录，
                                    且记录逐条覆盖这些工具、并给出确认闸的可达锚。

当前两边都是空的（零写工具、无放行记录），闸判 OK。谁哪天只做前一半，闸立刻红。

## 这条闸「作用到了谁」——必须自己验（本线纪律四）

白名单在本仓**有六处定义点**，漏掉任何一处，写工具就能从那处溜进去而闸不知道。
所以不是写死六个路径，而是**扫**：任何包含只读工具名的列表/元组/集合字面量都算
一处白名单定义点，再与冻结清单对拍——多一处、少一处都判红，逼人来看这段。
"""
from __future__ import annotations

import ast
import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(REPO_ROOT, "skills", "defect-filing")
RELEASE_RECORD = os.path.join(SKILL_DIR, "references", "write-release.json")

#: 当前放行的两个只读工具。它们同时是「这是一处白名单字面量」的识别标志。
READ_ONLY_TOOLS = frozenset({"get_stories_or_tasks", "get_stories_fields_info"})

#: `mcp-server-tapd==8.0.80` 的全部写工具。
#: 取数方式（无截断枚举，2026-08-12 实跑）：
#:   grep -n "^def " tapd-runtime/.venv/Lib/site-packages/mcp_server_tapd/server.py
#: 得 43 个工具函数（另有 start_mcp_server / main 两个非工具入口），其中下列 17 个是写。
#: 17 这个数与 config.py:92、register_tapd.py:50 注释里的「17 个是写操作」对上。
WRITE_TOOLS = frozenset({
    "update_story_or_task", "create_story_or_task",
    "update_bug", "create_bug",
    "create_comments", "update_comments",
    "update_iteration", "create_iteration",
    "entity_relations", "program_bind_entities",
    "create_or_update_tcases", "create_tcases_batch",
    "create_wiki", "update_wiki",
    "add_timesheets", "update_timesheets",
    "send_qiwei_message",
})

#: 冻结的白名单定义点清单（相对仓库根）。**多一处少一处都要来改这里**，
#: 顺带把新那处也纳入下面的零写工具断言。
EXPECTED_WHITELIST_SITES = frozenset({
    os.path.join("server", "gateway", "config.py"),
    os.path.join("server", "gateway", "deeptutor_client.py"),
    os.path.join("server", "gateway", "partner_setup.py"),
    os.path.join("server", "gateway", "secret_write.py"),
    os.path.join("server", "journey", "oracle.py"),
    os.path.join("tapd-runtime", "register_tapd.py"),
})

_SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "node_modules", "data",
              "tests", ".git", "inbox"}


# ── 扫描器 ──────────────────────────────────────────────────────────────────

def scan_whitelist_sites(root: str) -> dict[str, set[str]]:
    """找出 root 下所有「TAPD 工具白名单」字面量，返回 {相对路径: 工具名集合}。

    判据：一个 list / tuple / set 字面量里出现了任一只读工具名 ⇒ 它是一处白名单。
    这样新加的定义点会自动被发现，而不是靠人记得来更新路径清单。
    """
    found: dict[str, set[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except (OSError, SyntaxError):
                continue
            names: set[str] = set()
            for node in ast.walk(tree):
                if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                    continue
                items = {e.value for e in node.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if items & READ_ONLY_TOOLS:
                    names |= items
            if names:
                found[os.path.relpath(path, root)] = names
    return found


def load_release_record(path: str = RELEASE_RECORD) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def evaluate_release(sites: dict[str, set[str]], record: dict | None,
                     anchor_root: str = REPO_ROOT) -> tuple[str, list[str]]:
    """耦合判定。返回 (verdict, problems)，verdict ∈ {OK, BLOCK}。"""
    enabled_writes = {t for tools in sites.values() for t in tools if t in WRITE_TOOLS}
    declared = set((record or {}).get("released_write_tools") or [])
    problems: list[str] = []

    uncovered = sorted(enabled_writes - declared)
    if uncovered:
        problems.append(
            "E_WRITE_BEFORE_GATE: 白名单里放行了写工具 " + "、".join(uncovered)
            + "，但放行记录没覆盖它们。0027 裁定二：写工具与确认闸同批放行——"
            "先写工具后确认闸的那段窗口期不许存在。")

    stale = sorted(declared - enabled_writes)
    if stale:
        problems.append(
            "E_RELEASE_DRIFT: 放行记录声称已放行 " + "、".join(stale)
            + "，白名单里却没有。记录与白名单必须同真同假，不然记录就是张空头支票。")

    if declared:
        anchor = str(((record or {}).get("confirm_gate") or {}).get("anchor") or "")
        anchor_file = anchor.split(":")[0].strip()
        if not anchor_file or not os.path.exists(os.path.join(anchor_root, anchor_file)):
            problems.append(
                f"E_GATE_ANCHOR_MISSING: 放行记录的确认闸锚 {anchor!r} 指不到实际文件。"
                "锚必须是当版可达的路径，不能是一句『已实现』。")

    return ("BLOCK" if problems else "OK"), problems


# ── 现状断言 ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sites() -> dict[str, set[str]]:
    return scan_whitelist_sites(REPO_ROOT)


def test_scanner_finds_every_known_whitelist_site(sites):
    """闸作用到了谁：六处定义点一处不少，也没冒出没人管的第七处。"""
    assert set(sites) == set(EXPECTED_WHITELIST_SITES), (
        "白名单定义点与冻结清单不一致。\n"
        f"多出来的：{sorted(set(sites) - set(EXPECTED_WHITELIST_SITES))}\n"
        f"不见了的：{sorted(set(EXPECTED_WHITELIST_SITES) - set(sites))}\n"
        "多一处 = 有个白名单没被本闸看住；少一处 = 白名单被挪走了，两种都要人来看。")


def test_no_write_tool_in_any_whitelist(sites):
    """六处白名单里，一个写工具都不许有。"""
    offenders = {f: sorted(t & WRITE_TOOLS) for f, t in sites.items() if t & WRITE_TOOLS}
    assert offenders == {}, f"白名单里出现写工具：{offenders}"


def test_all_sites_agree_on_the_same_two_tools(sites):
    """六处口径必须一致——不一致时"最松的那处"就是实际生效的那处。"""
    disagree = {f: sorted(t) for f, t in sites.items() if t != set(READ_ONLY_TOOLS)}
    assert disagree == {}, f"白名单口径不一致：{disagree}"


def test_release_gate_is_green_on_current_tree(sites):
    verdict, problems = evaluate_release(sites, load_release_record())
    assert verdict == "OK", "\n".join(problems)


# ── 阳性对照：证明上面几条不是"恰好绿"（本线纪律七） ──────────────────────

def test_scanner_positive_control(tmp_path):
    """造一个应命中的样本：扫描器必须找出来，并把写工具一起带出来。"""
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "leaky.py").write_text(
        'ENABLED = ["get_stories_or_tasks", "get_stories_fields_info", "create_bug"]\n',
        encoding="utf-8")
    (d / "unrelated.py").write_text('X = ["foo", "bar"]\n', encoding="utf-8")
    found = scan_whitelist_sites(str(tmp_path))
    assert list(found) == [os.path.join("pkg", "leaky.py")]
    assert "create_bug" in found[os.path.join("pkg", "leaky.py")]


def test_scanner_sees_tuple_and_default_arg_forms():
    """secret_write.py 那处是函数默认参数里的元组——扫描器不能只认列表。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "m.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('def f(tools=("get_stories_or_tasks", "update_bug")):\n    return tools\n')
        found = scan_whitelist_sites(tmp)
        assert found == {"m.py": {"get_stories_or_tasks", "update_bug"}}


def test_gate_blocks_write_tool_without_release_record():
    """先放写工具、没有放行记录 → 必须 BLOCK。这条红了才说明闸真的会拦。"""
    verdict, problems = evaluate_release(
        {"server/gateway/config.py": {"get_stories_or_tasks", "create_bug"}}, None)
    assert verdict == "BLOCK"
    assert any("E_WRITE_BEFORE_GATE" in p for p in problems)


def test_gate_passes_when_write_and_confirm_land_together():
    """同批放行（记录覆盖 + 确认闸锚可达）→ 放行。避免闸把正当放行也锁死。"""
    verdict, problems = evaluate_release(
        {"server/gateway/config.py": {"get_stories_or_tasks", "create_bug"}},
        {"released_write_tools": ["create_bug"],
         "confirm_gate": {"anchor": "skills/defect-filing/SKILL.md"}})
    assert verdict == "OK", problems


def test_gate_blocks_release_record_with_unreachable_anchor():
    """放行记录不许拿一句『已实现』当确认闸——锚指不到文件就 BLOCK。"""
    verdict, problems = evaluate_release(
        {"server/gateway/config.py": {"get_stories_or_tasks", "create_bug"}},
        {"released_write_tools": ["create_bug"],
         "confirm_gate": {"anchor": "skills/defect-filing/NOT-THERE.md"}})
    assert verdict == "BLOCK"
    assert any("E_GATE_ANCHOR_MISSING" in p for p in problems)


def test_gate_blocks_paper_release_record():
    """记录声称放行了、白名单里其实没有 → 也 BLOCK（两边必须同真同假）。"""
    verdict, problems = evaluate_release(
        {"server/gateway/config.py": set(READ_ONLY_TOOLS)},
        {"released_write_tools": ["create_bug"],
         "confirm_gate": {"anchor": "skills/defect-filing/SKILL.md"}})
    assert verdict == "BLOCK"
    assert any("E_RELEASE_DRIFT" in p for p in problems)
