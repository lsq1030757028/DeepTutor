# -*- coding: utf-8 -*-
"""AC27 · 自由能力面零账本（`free_surface_no_ledger`）。

AC19 说"探索段的只读查询不进账本"。AC27 把同一条语义推广到整个自由能力面：
HTTP 探测 / DB 只读 / 日志 / TAPD 只读，一律不产九格产物、不写 `events.jsonl`。
**AC19 原文不动**，两条各留各的样例。

判据分两半：
- **结构半边**：模块层面就够不着账本（不 import `artifacts`，源码里没有 events.jsonl）；
- **行为半边**：真跑一遍，产物根 diff 为空、`events.jsonl` 没被建出来，
  而观测日志里逐次调用都在。
"""
from __future__ import annotations

import ast
import email
import json
import os

import pytest

from server.journey import artifacts
from server.journey import free_surface as fs


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "workbench"
    root.mkdir()
    monkeypatch.setenv(artifacts.ROOT_ENV, str(root))
    log = tmp_path / "obs" / "log.jsonl"
    monkeypatch.setenv(fs.OBSERVATION_LOG_ENV, str(log))
    return root, log


def _tree(root) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


class _Response:
    status = 200
    code = 200

    def __init__(self):
        self.headers = email.message_from_string("Content-Type: application/json\r\n")

    def read(self, size=-1):
        return b'{"ok":true}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def open(self, request, timeout=None):
        return _Response()


def _resolver(host, port):
    return ["93.184.216.34"]


class _Conn:
    class _Cursor:
        description = [("n",)]

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return ("on",)

        def fetchmany(self, size):
            return [(1,)]

        def close(self):
            pass

    def cursor(self):
        return self._Cursor()

    def close(self):
        pass


# ── 结构半边 ──────────────────────────────────────────────────────────────
def test_the_free_surface_module_cannot_reach_the_ledger():
    tree = ast.parse(open(fs.__file__, encoding="utf-8").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}".strip(".") for a in node.names)
    assert not [m for m in imported if "artifacts" in m or m.endswith("tools")], (
        f"自由面能够到账本层：{sorted(imported)}")


# ── 行为半边 ──────────────────────────────────────────────────────────────
def test_a_full_free_surface_session_leaves_the_artifact_tree_untouched(sandbox):
    root, log = sandbox
    before = _tree(root)

    fs.probe_http(curl="curl https://api.example.com/a", resolver=_resolver, opener=_Opener())
    fs.probe_http(url="http://10.0.0.1/b", resolver=_resolver, opener=_Opener())
    fs.probe_http(curl="curl -X POST https://api.example.com/c -d '{}'",
                  resolver=_resolver, opener=_Opener())
    fs.db_query(sql="SELECT count(*) FROM orders", connector=_Conn)

    assert _tree(root) == before == set(), "自由面在产物树里留下了东西"
    assert not list(root.rglob("events.jsonl")), "无批次时被建出了账本"


def test_every_free_surface_action_is_recorded_in_the_observation_log(sandbox):
    _root, log = sandbox
    fs.probe_http(curl="curl https://api.example.com/a", resolver=_resolver, opener=_Opener())
    fs.db_query(sql="SELECT 1", connector=_Conn)

    records = [json.loads(line) for line in open(log, encoding="utf-8") if line.strip()]
    assert len(records) == 2
    tools = {r["tool"] for r in records}
    assert tools == {"journey_probe_http", "journey_db_query"}
    http = next(r for r in records if r["tool"] == "journey_probe_http")
    # AC27 点名的四样：工具名 / 目标 host / 方法 / 结果码。
    assert http["host"] == "api.example.com"
    assert http["method"] == "GET"
    assert http["code"] == "OK" and http["status"] == 200


def test_the_observation_log_is_not_an_artifact_tree(sandbox):
    """观测日志不属事件契约（spec Delta 四），也不能藏在产物根里冒充产物。"""
    root, log = sandbox
    fs.probe_http(url="https://api.example.com/a", resolver=_resolver, opener=_Opener())
    assert os.path.exists(log)
    assert not str(log).startswith(str(root))


def test_ac19_exploration_wording_is_untouched(prompt_body):
    """AC27 是推广，不是改写：AC19 那句原文必须还在。"""
    zh = "探索阶段的只读查询**不进旅程账本**" in prompt_body
    en = "do not enter the journey ledger" in prompt_body
    assert zh or en, "AC19 的探索段原文被改动了"


def test_the_free_surface_is_declared_as_leaving_no_ledger(prompt_body):
    zh = "不建批次、不进账本" in prompt_body or "用完不留账本" in prompt_body
    en = "open no batch and enter no ledger" in prompt_body or "leave no ledger" in prompt_body
    assert zh or en, "自由面零账本的纪律不在提示词里"
