# -*- coding: utf-8 -*-
"""verdict_projection 移植验收：原件双向量集逐条过。

- record 牙：test_agent/loop/record.js 内置 DEMO ①-⑪；
- 投影器：test_agent/kit/tools/bundle_to_verdicts.js selftest ①-⑮
  （⑯⑰ 是 JS 侧词表防漂检查，Python 侧等价保证 = mechanical_check 单点 import 断言）。
夹具逐字抄自原件（golden，禁改）。
"""
import json
import os

from server.journey.gates import verdict_projection as vp


# ── record 牙（原件 record.js DEMO 向量）──────────────────────────────────

def test_record_d1_source_only_rejected():
    r = vp.record_verdict({"id": "R1", "claim": "设备类型枚举对", "verdict": "PASS",
                           "evidence": [{"type": "source", "ref": "device.ts:16"}]})
    assert not r["ok"] and any("D1" in x for x in r["reason"])


def test_record_api_observed_pass():
    r = vp.record_verdict({"id": "R2", "claim": "接口返回 dialogue_mode", "verdict": "PASS",
                           "evidence": [{"type": "api", "ref": "GET /device sha=abc"}]})
    assert r["ok"] and r["recorded"]["verdict"] == "PASS"


def test_record_over_claim_rejected():
    r = vp.record_verdict({"id": "R3", "claim": "全部网点带开关", "verdict": "PASS",
                           "evidence": [{"type": "api", "ref": "...",
                                         "claimed": 14, "observed": 10}]})
    assert not r["ok"] and any("覆盖越证据" in x for x in r["reason"])


def test_record_highrisk_pass_capped():
    r = vp.record_verdict({"id": "R4", "claim": "切网络下发成功", "verdict": "PASS",
                           "high_risk": True,
                           "evidence": [{"type": "log", "ref": "ack ok"}]})
    assert r["ok"] and r["recorded"]["verdict"] == "PENDING:needs-f9"


def test_record_local_build_dom_capped():
    r = vp.record_verdict({"id": "R6", "claim": "双网详情页误渲染·生产可达", "verdict": "FAIL",
                           "evidence": [{"type": "dom", "surface": "local_build",
                                         "ref": "H5 fixture lane DOM"}]})
    assert r["ok"] and r["recorded"]["verdict"] == "PENDING:needs-deploy-anchor"


def test_record_deployed_dom_with_anchor_pass():
    r = vp.record_verdict({"id": "R7", "claim": "双网详情页渲染正确", "verdict": "PASS",
                           "evidence": [{"type": "dom", "surface": "deployed",
                                         "code_identity": {"origin_commit": "abc1234",
                                                           "origin_reachable": True},
                                         "ref": "体验版 DOM"}]})
    assert r["ok"] and r["recorded"]["verdict"] == "PASS"


def test_record_dom_no_surface_capped_not_rejected():
    # BB-297：未声明 surface 从「拒」改为「封顶」——诚实留痕，不许整条消失
    r = vp.record_verdict({"id": "R8", "claim": "某 UI 结论", "verdict": "FAIL",
                           "evidence": [{"type": "dom", "ref": "无 surface"}]})
    assert r["ok"] and r["recorded"]["verdict"] == "PENDING:needs-ui-surface"


def test_record_deployed_without_reachable_rejected():
    r = vp.record_verdict({"id": "R9", "claim": "某部署结论", "verdict": "FAIL",
                           "evidence": [{"type": "dom", "surface": "deployed",
                                         "code_identity": {"origin_commit": "zzz",
                                                           "origin_reachable": False},
                                         "ref": "commit 不在 origin"}]})
    assert not r["ok"] and any("部署身份未证" in x for x in r["reason"])


def test_record_unknown_type_rejected_with_vocab_message():
    r = vp.record_verdict({"id": "R10", "claim": "某 UI 结论标了 type=ui", "verdict": "PASS",
                           "evidence": [{"type": "ui", "ref": "ui-observed.json"}]})
    assert not r["ok"] and any("不在词表" in x for x in r["reason"])


def test_record_bad_surface_rejected():
    r = vp.record_verdict({"id": "R11", "claim": "某 UI 结论", "verdict": "PASS",
                           "evidence": [{"type": "dom", "surface": "staging",
                                         "ref": "x.json"}]})
    assert not r["ok"] and any("ui_surface" in x for x in r["reason"])


# ── 投影器（原件 bundle_to_verdicts.js selftest 向量）──────────────────────

def B(conclusions):
    return {"schema_version": "1.0", "agent_id": "test-engineer",
            "build_fingerprint": "app@commitX (origin-reachable)",
            "conclusions": conclusions}


def mk(root, sub, bundle):
    d = os.path.join(root, sub)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, vp.BUNDLE_NAME), "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False)


def make_fixture(tmp_path):
    root = str(tmp_path)
    mk(root, "api", B([
        {"id": "T1-observed-pass", "claim": "接口返回新字段", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["e/a.json", "e/b.json"],
         "evidence_types": ["api", "db"]},
        {"id": "T2-source-only", "claim": "线上行为正确", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["src/x.ts", "src/y.ts"],
         "evidence_types": ["source", "code"]},
        {"id": "T3-undeclared", "claim": "线上行为正确", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["e/a.json", "e/b.json"]},
        {"id": "T4-undecidable", "claim": "第三方链路测不了", "verdict": "undecidable",
         "strength": "undecidable", "evidence": []},
        {"id": "T5-over-claim", "claim": "全部 14 个网点都带开关", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["e/a.json"], "evidence_types": ["api"],
         "claimed": 14, "observed": 10},
        {"id": "T6-highrisk", "claim": "切网下发成功", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["e/l.log"], "evidence_types": ["log"],
         "high_risk": True},
    ]))
    mk(root, "ui", B([
        {"id": "T7-local-ui", "claim": "详情页渲染正确(生产可达)", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["e/s.png"],
         "evidence_types": ["screenshot"], "ui_surface": "local_build"},
        {"id": "T8-deployed-ui", "claim": "详情页渲染正确", "verdict": "pass",
         "strength": "hard-anchor", "evidence": ["e/s.png"],
         "evidence_types": ["screenshot"], "ui_surface": "deployed",
         "code_identity": {"origin_commit": "abc1234", "origin_reachable": True}},
        {"id": "T1-observed-pass", "claim": "同名 id 换个面", "verdict": "fail",
         "strength": "single-anchor", "evidence": ["e/dom.html"],
         "evidence_types": ["dom"], "ui_surface": "deployed",
         "code_identity": {"origin_commit": "abc1234", "origin_reachable": True}},
        {"id": "T9-ui-no-surface", "claim": "登录页渲染正确", "verdict": "pass",
         "strength": "single-anchor", "evidence": ["e/dom.html"],
         "evidence_types": ["dom"]},
    ]))
    mk(root, "badtype", B([
        {"id": "T10-unknown-type", "claim": "某 UI 结论", "verdict": "pass",
         "strength": "single-anchor", "evidence": ["e/ui.json"],
         "evidence_types": ["ui"]},
    ]))
    mk(root, "badsurface", B([
        {"id": "T11-bad-surface", "claim": "某 UI 结论", "verdict": "pass",
         "strength": "single-anchor", "evidence": ["e/dom.html"],
         "evidence_types": ["dom"], "ui_surface": "staging"},
    ]))
    return root


def test_projection_original_vectors(tmp_path):
    root = make_fixture(tmp_path)
    r = vp.project(root)
    by_id = {l["id"]: l for l in r["lines"]}
    rej = {x["id"] for x in r["rejected"]}
    # ① 声明观测证据的 pass → PASS 落账
    assert by_id["api/T1-observed-pass"]["verdict"] == "PASS"
    # ② 只有源码证据(D1) → 闸2 拒，不落账
    assert "api/T2-source-only" in rej and "api/T2-source-only" not in by_id
    # ③ 未声明 evidence_types → 封顶 PENDING
    assert by_id["api/T3-undeclared"]["verdict"] == "PENDING"
    assert "needs-evidence-type" in by_id["api/T3-undeclared"]["note"]
    # ④ undecidable → PENDING
    assert by_id["api/T4-undecidable"]["verdict"] == "PENDING"
    # ⑤ 覆盖越证据 → 拒
    assert "api/T5-over-claim" in rej
    # ⑥ 高风险 pass 未过 F9 → PENDING:needs-f9
    assert by_id["api/T6-highrisk"]["verdict"] == "PENDING:needs-f9"
    # ⑦ UI 仅本地构建 → PENDING:needs-deploy-anchor
    assert by_id["ui/T7-local-ui"]["verdict"] == "PENDING:needs-deploy-anchor"
    # ⑧ UI 带 origin 可达部署锚 → PASS
    assert by_id["ui/T8-deployed-ui"]["verdict"] == "PASS"
    # ⑨ 多面同名 id 靠面前缀不撞
    assert "api/T1-observed-pass" in by_id and "ui/T1-observed-pass" in by_id
    assert not any("id 重复" in e for e in r["errs"])
    # ⑩ 投影带 projected_from 溯源
    assert all(l["projected_from"].endswith(vp.BUNDLE_NAME) for l in r["lines"])
    # ⑬ BB-297：UI 缺 ui_surface → 封顶落账，不许整条消失
    assert by_id["ui/T9-ui-no-surface"]["verdict"] == "PENDING:needs-ui-surface"
    assert "ui/T9-ui-no-surface" not in rej
    # ⑭ BB-297 负例①：词表外类别 → 结构错，不是 D1 误诊
    assert any("T10-unknown-type" in e and "词表外类别" in e for e in r["errs"])
    assert "badtype/T10-unknown-type" not in by_id
    # ⑮ BB-297 负例②：ui_surface 取值非法 → 结构错拦下，不落账
    assert any("T11-bad-surface" in e and "ui_surface" in e for e in r["errs"])
    assert "badsurface/T11-bad-surface" not in by_id


def test_no_ledger_fail_closed(tmp_path):
    # ⑪ 账本不在场 → fail-closed（code=4），不许拿 0 蒙混
    r = vp.project_and_write(str(tmp_path))
    assert r["no_ledger"] and r["code"] == 4
    # ⑫ 且不许落一个空 verdicts.jsonl
    assert not os.path.exists(os.path.join(str(tmp_path), "verdicts.jsonl"))


def test_write_projection_deterministic(tmp_path):
    root = make_fixture(tmp_path)
    r1 = vp.project_and_write(root)
    assert r1["code"] == 2  # badtype/badsurface 的结构错在场
    with open(r1["out_file"], encoding="utf-8") as fh:
        n1 = len(fh.read().splitlines())
    r2 = vp.project_and_write(root)
    with open(r2["out_file"], encoding="utf-8") as fh:
        n2 = len(fh.read().splitlines())
    assert n1 == n2 == len(r1["lines"])  # 确定性重写，不追加
    assert os.path.exists(os.path.join(root, "verdicts.rejected.json"))


def test_vocab_single_source():
    # ⑯⑰ 等价：词表单点定义，mechanical_check 只 import 不复制
    from server.journey.gates import mechanical_check as mc
    assert mc.OBSERVED is vp.OBSERVED
    import inspect
    src = inspect.getsource(mc)
    assert "'api', 'http'" not in src and '"api", "http"' not in src
