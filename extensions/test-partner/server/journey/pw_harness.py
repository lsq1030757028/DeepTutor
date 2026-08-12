# -*- coding: utf-8 -*-
"""pw_harness — bundle 内嵌 case 执行壳（编译器嵌入为 _harness.py）。

职责：per-case 生命周期（context+tracing→执行→证据落盘→pytest 语义映射）。
浏览器实例由 conftest 的 session fixture 提供（per-run 一实例，run 内复用，
finally 必关——ADR-M1-02 资源纪律）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import pytest

try:
    from server.journey import pw_runtime as rt
except ImportError:  # bundle 嵌入形态
    import _runtime as rt  # type: ignore

_CTX: dict[str, Any] | None = None


def ctx() -> dict[str, Any]:
    global _CTX
    if _CTX is None:
        _CTX = rt.load_context()
    return _CTX


def case_slug(case_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "__", case_id.lower()).strip("_")


def _finish(runner: rt.CaseRunner, extra: dict[str, Any]) -> dict[str, Any]:
    row = runner.result()
    row.update(extra)
    # required_evidence=db_snapshot 的真实落点。它必须来自本次执行刚得到的
    # before/after/delta，而不是投影阶段根据 PASS 反推或补写。
    if row.get("db_metrics"):
        case_dir = os.path.join(ctx()["run_dir"], str(extra["evidence_dir"]))
        os.makedirs(case_dir, exist_ok=True)
        with open(os.path.join(case_dir, "db_snapshot.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({
                "schema_version": "1.0",
                "case_id": row.get("case_id"),
                "metrics": row["db_metrics"],
            }, fh, ensure_ascii=False, indent=1)
    rt.append_result(ctx()["run_dir"], row)
    return row


def _pytest_outcome(row: dict[str, Any]) -> None:
    outcome = row["outcome"]
    if outcome in ("passed", "observed"):
        return
    if outcome == "skipped":
        pytest.skip(row.get("skip_code", "") + " " + row.get("skip_reason", ""))
    if outcome == "no_assertions":
        pytest.fail("no_assertions: 断言计数 0，不算过（红线四）", pytrace=False)
    if outcome == "blocked":
        pytest.fail("BLOCKED: " + row.get("blocked_reason", ""), pytrace=False)
    pytest.fail("; ".join(
        f"{a['kind']}: 期望 {a['expected']!r} 实得 {a['actual']!r}"
        for a in row["assertions"] if not a["passed"]) or outcome, pytrace=False)


def run_ui_case(browser: Any, meta: dict[str, Any]) -> None:
    c = ctx()
    slug = case_slug(meta["case_id"])
    case_dir = os.path.join(c["run_dir"], slug)
    os.makedirs(case_dir, exist_ok=True)
    context = browser.new_context()
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    page = context.new_page()
    row: dict[str, Any] = {}
    try:
        try:
            runner = rt.CaseRunner(c, meta, page=page)
        except rt.CaseSkip as exc:
            if exc.code != "SKIP_RESUME_DONE":  # 续跑跳过不重复记账（上轮行已在）
                rt.append_result(c["run_dir"], {
                    "case_id": meta["case_id"], "outcome": "skipped",
                    "skip_code": exc.code, "skip_reason": exc.reason,
                    "probing": bool(meta.get("probing")),
                    "source_case_digest": meta.get("source_case_digest"),
                    "oracle_digest": meta.get("oracle_digest"),
                    "assertion_count": 0, "assertions": []})
            pytest.skip(f"{exc.code} {exc.reason}")
            return
        rt.install_route_guard(page, c["base_url"], runner)
        extra: dict[str, Any] = {"evidence_dir": slug}
        try:
            for action in meta["actions"]:
                runner.act(action)
        except rt.CaseSkip as exc:
            runner.status = "skipped"
            extra.update({"skip_code": exc.code, "skip_reason": exc.reason})
        except rt.CaseBlocked as exc:
            runner.status = "blocked"
            extra["blocked_reason"] = str(exc)
        # 证据：最终 DOM 快照 + 截图（trace 在 finally 落）
        try:
            with open(os.path.join(case_dir, "final_dom.html"), "w",
                      encoding="utf-8") as fh:
                fh.write(str(runner._scrub(page.content())))
            page.screenshot(path=os.path.join(case_dir, "final.png"),
                            full_page=True)
        except Exception:  # noqa: BLE001 - 页面已崩溃时证据尽力而为
            pass
        if runner.status == "skipped":
            row = _finish(runner, {**extra, "outcome": "skipped"})
        elif runner.status == "blocked":
            row = _finish(runner, {**extra, "outcome": "blocked"})
        else:
            row = _finish(runner, extra)
    finally:
        try:
            context.tracing.stop(path=os.path.join(case_dir, "trace.zip"))
        finally:
            context.close()
    _pytest_outcome(row)


def run_api_case(meta: dict[str, Any]) -> None:
    c = ctx()
    slug = case_slug(meta["case_id"])
    case_dir = os.path.join(c["run_dir"], slug)
    os.makedirs(case_dir, exist_ok=True)
    try:
        runner = rt.CaseRunner(c, meta, page=None)
    except rt.CaseSkip as exc:
        if exc.code != "SKIP_RESUME_DONE":  # 续跑跳过不重复记账（上轮行已在）
            rt.append_result(c["run_dir"], {
                "case_id": meta["case_id"], "outcome": "skipped",
                "skip_code": exc.code, "skip_reason": exc.reason,
                "probing": bool(meta.get("probing")),
                "source_case_digest": meta.get("source_case_digest"),
                "oracle_digest": meta.get("oracle_digest"),
                "assertion_count": 0, "assertions": []})
        pytest.skip(f"{exc.code} {exc.reason}")
        return
    extra: dict[str, Any] = {"evidence_dir": slug}
    try:
        for action in meta["actions"]:
            runner.act(action)
    except rt.CaseSkip as exc:
        runner.status = "skipped"
        extra.update({"skip_code": exc.code, "skip_reason": exc.reason})
    except rt.CaseBlocked as exc:
        runner.status = "blocked"
        extra["blocked_reason"] = str(exc)
    with open(os.path.join(case_dir, "transcript.json"), "w", encoding="utf-8") as fh:
        json.dump(runner.http_transcript, fh, ensure_ascii=False, indent=1)
    if runner.status == "skipped":
        row = _finish(runner, {**extra, "outcome": "skipped"})
    elif runner.status == "blocked":
        row = _finish(runner, {**extra, "outcome": "blocked"})
    else:
        row = _finish(runner, extra)
    _pytest_outcome(row)
