# -*- coding: utf-8 -*-
"""evidence_gate 移植验收：原件 .agents/agents/test-engineer 靶场向量逐条过。

向量抄自原件 fixtures/（golden，禁改）；期望状态与关键错误码与原件
test_evidence_gate.py 一致。M1 适配点仅 expected_agent_id 参数化。
"""
import pytest

from server.journey.gates import evidence_gate


def bundle(**over):
    doc = {
        "schema_version": "1.0",
        "agent_id": "test-engineer",
        "build_fingerprint": "h5-a72c29d",
        "conclusions": [],
    }
    doc.update(over)
    return doc


@pytest.fixture()
def root(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "probe-log.txt").write_text("probe ok: 3 rules effective\n" * 3, encoding="utf-8")
    (ev / "db-check.txt").write_text("db rows consistent: 3/3\n" * 3, encoding="utf-8")
    return str(tmp_path)


def run(doc, root):
    return evidence_gate.check_bundle(doc, root, expected_agent_id="test-engineer")


def codes(result):
    return {e.split(":")[0].split("[")[0] for e in result["errors"]}


def test_normal_ready_for_judge(root):
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "不可用原因最多三条已生效", "verdict": "pass",
         "strength": "hard-anchor",
         "evidence": ["evidence/probe-log.txt", "evidence/db-check.txt"]},
        {"id": "c2", "claim": "可用时段优先级需真机才能判", "verdict": "undecidable",
         "strength": "undecidable", "evidence": []},
    ])
    r = run(doc, root)
    assert r["status"] == "ready-for-judge", r["errors"]


def test_fake_green_blocked(root):
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "样式正确", "verdict": "pass",
         "strength": "undecidable", "evidence": []}])
    r = run(doc, root)
    assert r["status"] == "blocked" and "E_FAKE_GREEN" in codes(r)


def test_missing_evidence_blocked(root):
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "接口生效", "verdict": "pass",
         "strength": "single-anchor", "evidence": ["evidence/not-there.txt"]}])
    r = run(doc, root)
    assert r["status"] == "blocked" and "E_EVIDENCE_MISSING" in codes(r)


def test_no_fingerprint_blocked(root):
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "接口生效", "verdict": "pass",
         "strength": "single-anchor", "evidence": ["evidence/probe-log.txt"]}])
    del doc["build_fingerprint"]
    r = run(doc, root)
    assert r["status"] == "blocked" and "E_BUILD_FINGERPRINT" in codes(r)


def test_evidence_escape_blocked(root):
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "接口生效", "verdict": "pass",
         "strength": "single-anchor", "evidence": ["../../../secrets.txt"]}])
    r = run(doc, root)
    assert r["status"] == "blocked" and "E_EVIDENCE_ESCAPE" in codes(r)


def test_single_as_hard_blocked(root):
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "接口生效", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["evidence/probe-log.txt"]}])
    r = run(doc, root)
    assert r["status"] == "blocked" and "E_STRENGTH_HARD" in codes(r)


# ── 补充：移植面自有的边界（不改原件判据，只证 M1 适配点）──────────────────

def test_agent_id_parameterized(root):
    doc = bundle(agent_id="test-partner", conclusions=[
        {"id": "c1", "claim": "x", "verdict": "undecidable",
         "strength": "undecidable", "evidence": []}])
    assert evidence_gate.check_bundle(doc, root)["status"] == "ready-for-judge"
    r = evidence_gate.check_bundle(bundle(conclusions=[
        {"id": "c1", "claim": "x", "verdict": "undecidable",
         "strength": "undecidable", "evidence": []}]), root)
    assert "E_AGENT_ID" in codes(r)  # 默认期望 test-partner


def test_empty_evidence_file_blocked(root, tmp_path):
    (tmp_path / "evidence" / "empty.txt").write_text("", encoding="utf-8")
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "接口生效", "verdict": "pass",
         "strength": "single-anchor", "evidence": ["evidence/empty.txt"]}])
    r = run(doc, root)
    assert r["status"] == "blocked" and "E_EVIDENCE_EMPTY" in codes(r)


def test_duplicate_ids_blocked(root):
    doc = bundle(conclusions=[
        {"id": "c1", "claim": "a", "verdict": "undecidable", "strength": "undecidable",
         "evidence": []},
        {"id": "c1", "claim": "b", "verdict": "undecidable", "strength": "undecidable",
         "evidence": []}])
    r = run(doc, root)
    assert "E_CONCLUSION_DUPLICATE" in codes(r)


def test_unknown_top_fields_blocked(root):
    doc = bundle(extra_field=1, conclusions=[
        {"id": "c1", "claim": "x", "verdict": "undecidable",
         "strength": "undecidable", "evidence": []}])
    r = run(doc, root)
    assert "E_UNKNOWN_FIELDS" in codes(r)
