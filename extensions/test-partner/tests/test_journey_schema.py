# -*- coding: utf-8 -*-
"""journey.schema 自测：ApprovedCaseSet 工作基线校验（正例必过、负例必被拦）。"""
import copy

from server.journey import digest, schema


def make_case(case_id="bysms/R1-C001", **over):
    case = {
        "case_id": case_id,
        "case_version": 1,
        "title": "管理员登录成功",
        "source_anchor": {"rule_id": "R1", "quote": "输入管理员账号即可登录"},
        "preconditions": [{"statement": "靶机已启动", "checkable": True,
                           "check_hint": "GET / 返回 200/301"}],
        "steps": ["打开登录页", "输入 byhy/正确口令", "点击登录"],
        "expected": "进入管理台首页",
        "failure_criteria": "停留在登录页或出现错误提示",
        "assertion_layers": {
            "ui": {"assertions": ["页面标题为白月销售管理系统"],
                   "required_evidence": ["playwright_trace", "screenshot"]},
        },
        "automation": {"admissible": True},
        "side_effects": {"writes": False},
        "open_items": {"unknowns": [], "assumptions": [], "manual_touchpoints": []},
    }
    case.update(over)
    return digest.stamp_case_digests(case)


def make_caseset(cases=None, **over):
    doc = {
        "schema_version": schema.SCHEMA_VERSION,
        "caseset_id": "acs-20260810-abc123",
        "created_at": "2026-08-10T20:00:00+08:00",
        "source": {"kind": "requirement_doc",
                   "ref": "https://www.byhy.net/py/django/req_1/",
                   "content_digest": digest.text_digest("需求正文")},
        "approval": {"adopted_at": "2026-08-10T20:30:00+08:00",
                     "adopted_via": "workbench_selection"},
        "cases": cases if cases is not None else [make_case()],
    }
    doc.update(over)
    return doc


def codes(result):
    return {e["code"] for e in result["errors"]}


def test_valid_caseset_passes():
    result = schema.validate_caseset(make_caseset())
    assert result["ok"], result["errors"]


def test_envelope_fields_tolerated():
    doc = make_caseset()
    doc["artifact"] = "approved_caseset"
    doc["batch_id"] = "b-20260810-abc123"
    assert schema.validate_caseset(doc)["ok"]


def test_wrong_schema_version_rejected():
    result = schema.validate_caseset(make_caseset(schema_version="9.9.9"))
    assert not result["ok"] and "S04" in codes(result)


def test_bad_caseset_id_and_missing_fields():
    result = schema.validate_caseset(make_caseset(caseset_id="whatever"))
    assert "S05" in codes(result)
    doc = make_caseset()
    del doc["approval"]
    assert "S02" in codes(schema.validate_caseset(doc))


def test_source_kind_and_content_digest_checked():
    doc = make_caseset()
    doc["source"]["kind"] = "guesswork"
    assert "S07" in codes(schema.validate_caseset(doc))
    doc2 = make_caseset()
    doc2["source"]["content_digest"] = "md5:abc"
    assert "S07" in codes(schema.validate_caseset(doc2))


def test_case_id_pattern_and_duplicates():
    bad = schema.validate_caseset(make_caseset(cases=[make_case("BYSMS_R1_1")]))
    assert "S13" in codes(bad)
    dup = schema.validate_caseset(make_caseset(cases=[make_case(), make_case()]))
    assert "S32" in codes(dup)


def test_db_only_assertion_layers_rejected():
    # anyOf 机械判：只给 db 不满足（设计返工项 R3 的防漏洞点）
    case = make_case(assertion_layers={
        "db": {"assertions": ["订单表新增一行"], "required_evidence": ["db_snapshot"]}})
    result = schema.validate_caseset(make_caseset(cases=[case]))
    assert "S25" in codes(result)


def test_empty_assertions_rejected():
    case = make_case(assertion_layers={
        "ui": {"assertions": [], "required_evidence": ["screenshot"]}})
    assert "S22" in codes(schema.validate_caseset(make_caseset(cases=[case])))


def test_evidence_vocab_enforced():
    case = make_case(assertion_layers={
        "ui": {"assertions": ["x"], "required_evidence": ["selenium_video"]}})
    assert "S24" in codes(schema.validate_caseset(make_caseset(cases=[case])))


def test_write_case_requires_cleanup_and_authorization():
    case = make_case(side_effects={"writes": True, "write_scope": "订单表"})
    result = schema.validate_caseset(make_caseset(cases=[case]))
    assert "S29" in codes(result)
    ok_case = make_case(side_effects={
        "writes": True, "write_scope": "订单表",
        "cleanup": "还原数据库基线 db-ori-1", "authorization": "events.jsonl#write_confirm"})
    assert schema.validate_caseset(make_caseset(cases=[ok_case]))["ok"]


def test_inadmissible_requires_reason():
    case = make_case(automation={"admissible": False})
    assert "S27" in codes(schema.validate_caseset(make_caseset(cases=[case])))
    ok = make_case(automation={"admissible": False, "exclusion_reason": "需要真人收短信"})
    assert schema.validate_caseset(make_caseset(cases=[ok]))["ok"]


def test_digest_format_checked():
    case = make_case()
    case["oracle_digest"] = "sha256:short"
    assert "S31" in codes(schema.validate_caseset(make_caseset(cases=[case])))


def test_unknown_case_field_rejected():
    case = make_case()
    case["compiled_code"] = "def test(): ..."  # 派生物字段反写进源结构
    result = schema.validate_caseset(make_caseset(cases=[case]))
    assert "S12" in codes(result)


def test_probing_flag_allowed():
    case = make_case(source_anchor={"rule_id": "R9", "probing": True,
                                    "quote": "文档未写明的探测预期"})
    assert schema.validate_caseset(make_caseset(cases=[case]))["ok"]


def test_deep_copy_safety():
    doc = make_caseset()
    snapshot = copy.deepcopy(doc)
    schema.validate_caseset(doc)
    assert doc == snapshot  # 校验器不改入参
