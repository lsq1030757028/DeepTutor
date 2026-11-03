# -*- coding: utf-8 -*-
"""mechanical_check 移植验收：原件 kit/f9/mechanical_check.js selftest 向量逐条过。

M1 范围 = M1/M3/M5 + M4 降级（实例指纹在场）。原件 M2 向量（neg2/neg3）不在
本移植范围——未蒸馏声明见 gates/mechanical_check.py 模块头（护栏 3）。
其余向量（正例/负1/负4-负10/正例2）逐条过。
"""
import copy
import json
import os

from server.journey.gates import mechanical_check as mc

OK_EV = [{"type": "db", "ref": "evidence/obs.json", "claimed": 4, "observed": 4}]
BASE = {"id": "TC-1", "claim": "运行态实测:全部 4 台设备写入快照", "verdict": "PASS",
        "evidence": OK_EV, "projected_from": "evidence-bundle.json"}


def bundle_of(rec, no_fingerprint=False):
    return {
        "schema_version": "1.0", "agent_id": "test-partner",
        "build_fingerprint": "" if no_fingerprint else "bysms v1.3 @127.0.0.1:8047 sha256:2945c4b3",
        "conclusions": [{
            "id": rec["id"], "claim": rec["claim"], "verdict": rec["verdict"].lower(),
            "strength": "single-anchor",
            "evidence": [e["ref"] for e in rec["evidence"]],
            "evidence_types": [e["type"] for e in rec["evidence"]],
        }],
    }


def mkrun(tmp_path, name, bundle_conclusions, verdict_lines, *,
          empty_evidence=False):
    d = tmp_path / name
    (d / "evidence").mkdir(parents=True)
    (d / "evidence" / "obs.json").write_text('{"n":1}', encoding="utf-8")
    if empty_evidence:
        (d / "evidence" / "empty.json").write_text("", encoding="utf-8")
    (d / "evidence-bundle.json").write_text(
        json.dumps(bundle_conclusions, ensure_ascii=False), encoding="utf-8")
    (d / "verdicts.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in verdict_lines),
        encoding="utf-8")
    return str(d)


def test_positive_all_green(tmp_path):
    d = mkrun(tmp_path, "pos", bundle_of(BASE), [BASE])
    r = mc.check_run(d)
    assert r["verdict"] == "PASS" and r["blocking_count"] == 0, r["blocking"]


def test_neg1_unresolvable_evidence(tmp_path):
    n = copy.deepcopy(BASE)
    n["evidence"][0]["ref"] = "evidence/missing.json"
    d = mkrun(tmp_path, "neg1", bundle_of(n), [n])
    r = mc.check_run(d)
    assert r["verdict"] == "BLOCK" and any("[M1]" in b for b in r["blocking"])


def test_neg4_universal_claim_without_scope_anchor(tmp_path):
    n = copy.deepcopy(BASE)
    n["evidence"] = [{"type": "db", "ref": "evidence/obs.json"}]
    d = mkrun(tmp_path, "neg4", bundle_of(n), [n])
    r = mc.check_run(d)
    assert r["verdict"] == "BLOCK" and any("[M3]" in b for b in r["blocking"])


def test_neg5_claimed_over_observed(tmp_path):
    n = copy.deepcopy(BASE)
    n["evidence"] = [{"type": "db", "ref": "evidence/obs.json",
                      "claimed": 293, "observed": 4}]
    d = mkrun(tmp_path, "neg5", bundle_of(n), [n])
    r = mc.check_run(d)
    assert r["verdict"] == "BLOCK" and any("claimed>observed" in b for b in r["blocking"])


def test_neg6_no_instance_fingerprint(tmp_path):
    d = mkrun(tmp_path, "neg6", bundle_of(BASE, no_fingerprint=True), [BASE])
    r = mc.check_run(d)
    assert r["verdict"] == "BLOCK" and any("[M4]" in b for b in r["blocking"])


def test_neg7_claim_tampered_after_projection(tmp_path):
    n = copy.deepcopy(BASE)
    n["claim"] = "运行态实测:全部 293 台设备写入快照"
    d = mkrun(tmp_path, "neg7", bundle_of(BASE), [n])
    r = mc.check_run(d)
    assert r["verdict"] == "BLOCK" and any("[M5]" in b and "不一致" in b for b in r["blocking"])


def test_neg8_handwritten_no_projected_from(tmp_path):
    n = copy.deepcopy(BASE)
    del n["projected_from"]
    d = mkrun(tmp_path, "neg8", bundle_of(BASE), [n])
    r = mc.check_run(d)
    assert r["verdict"] == "BLOCK" and any("projected_from" in b for b in r["blocking"])


def test_neg9_empty_evidence_file(tmp_path):
    n = copy.deepcopy(BASE)
    n["evidence"] = [{"type": "db", "ref": "evidence/empty.json",
                      "claimed": 1, "observed": 1}]
    d = mkrun(tmp_path, "neg9", bundle_of(n), [n], empty_evidence=True)
    r = mc.check_run(d)
    assert r["verdict"] == "BLOCK" and any("为空" in b for b in r["blocking"])


def test_neg10_no_verdicts_ledger(tmp_path):
    d = tmp_path / "neg10"
    d.mkdir()
    r = mc.check_run(str(d))
    assert r["verdict"] == "BLOCK"


def test_pos2_pending_not_hurt_by_m3(tmp_path):
    # 原件正例2 的 M1 化：PENDING 条目不受 M3 约束（诚实降级不被机械误伤）。
    # 原件夹具 claim 带【源码级】触发的是 M2（不在 M1 范围），此处保留其 M3/M5 面：
    # PENDING + 全称量词 + 无广度锚 → 不阻断。
    p2 = {"id": "ENV-1", "claim": "通道快照全部留存,只取证不判决", "verdict": "PENDING",
          "evidence": [{"type": "file", "ref": "evidence/obs.json"}],
          "projected_from": "evidence-bundle.json"}
    b = {"schema_version": "1.0", "agent_id": "test-partner",
         "build_fingerprint": "bysms v1.3 @127.0.0.1:8047",
         "conclusions": [{"id": "ENV-1", "claim": p2["claim"], "verdict": "undecidable",
                          "strength": "undecidable", "evidence": ["evidence/obs.json"],
                          "evidence_types": ["file"]}]}
    d = mkrun(tmp_path, "pos2", b, [p2])
    r = mc.check_run(d)
    assert r["verdict"] == "PASS", r["blocking"]


def test_multi_surface_evidence_resolved_relative_to_bundle(tmp_path):
    # 证据 ref 相对面目录也算可引开（原件 resolveEvidence 双候选语义）
    d = tmp_path / "multi"
    (d / "api" / "evidence").mkdir(parents=True)
    (d / "api" / "evidence" / "obs.json").write_text('{"n":1}', encoding="utf-8")
    rec = {"id": "api/TC-1", "claim": "运行态实测:写入快照", "verdict": "PASS",
           "evidence": [{"type": "db", "ref": "evidence/obs.json"}],
           "projected_from": "api/evidence-bundle.json"}
    (d / "api" / "evidence-bundle.json").write_text(json.dumps({
        "schema_version": "1.0", "agent_id": "test-partner",
        "build_fingerprint": "bysms v1.3",
        "conclusions": [{"id": "TC-1", "claim": rec["claim"], "verdict": "pass",
                         "strength": "single-anchor", "evidence": ["evidence/obs.json"],
                         "evidence_types": ["db"]}]}, ensure_ascii=False),
        encoding="utf-8")
    (d / "verdicts.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    r = mc.check_run(str(d))
    assert r["verdict"] == "PASS", r["blocking"]


def test_write_output_file(tmp_path):
    d = mkrun(tmp_path, "out", bundle_of(BASE), [BASE])
    r = mc.check_and_write(d)
    assert os.path.isfile(r["out_path"])
