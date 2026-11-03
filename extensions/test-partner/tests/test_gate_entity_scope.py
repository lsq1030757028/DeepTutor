# -*- coding: utf-8 -*-
"""实体溯源闸判据（0030；作用域没人核的第七例）。"""
from __future__ import annotations

from server.journey.gates import entity_scope as es


def _case(cid, path, method="POST", writes=True):
    return {"case_id": cid,
            "side_effects": {"writes": writes},
            "automation": {"recipe": {"track": "api", "actions": [
                {"op": "request", "method": method, "path": path}]}}}


def test_actual_entity_comes_from_the_recipe_not_a_declaration():
    """Y 取配方不取声明——取声明的话这道闸就退化成"我们说我们测的是它"。"""
    assert es.written_entities(_case("a", "/admin/v1/characters/recommended")) \
        == ["recommended_character"]
    assert es.written_entities(_case("a", "/api/v1/characters/custom")) \
        == ["custom_character"]


def test_the_real_m2_miss_is_caught():
    """回归本轮真栽的那次：需求指自定义角色，实际写推荐角色。"""
    r = es.check_case(_case("q/R5-C001", "/admin/v1/characters/recommended"),
                      "custom_character")
    assert r["verdict"] == es.MISMATCH
    assert "recommended_character" in r["problem"]


def test_matching_entity_passes():
    r = es.check_case(_case("q/R5-C001", "/api/v1/characters/custom"),
                      "custom_character")
    assert r["verdict"] == es.MATCH


def test_read_only_case_is_not_judged():
    r = es.check_case(_case("q/R1-C001", "/admin/v1/voice-presets",
                            method="GET", writes=False), "custom_character")
    assert r["verdict"] == es.MATCH


def test_missing_requirement_entity_is_unknown_not_pass():
    """X 缺失判 unknown 并要求补——缺省放行等于这道闸不存在。"""
    r = es.check_case(_case("q/R5-C001", "/api/v1/characters/custom"), "")
    assert r["verdict"] == es.UNKNOWN
    assert "缺省不放行" in r["problem"]


def test_unmapped_write_path_is_unknown_not_pass():
    """反推不出实体的写路径判 unknown——默认放行会让新写入路径自动豁免。"""
    r = es.check_case(_case("q/X", "/admin/v1/some-new-thing"), "custom_character")
    assert r["verdict"] == es.UNKNOWN


def test_ui_write_without_explicit_scope_is_unknown_not_match():
    case = {
        "case_id": "q/R5-C001",
        "side_effects": {"writes": True},
        "automation": {
            "recipe": {
                "track": "ui",
                "actions": [{"op": "click", "selector": "#save"}],
            }
        },
    }
    result = es.check_case(case, "custom_character")
    assert result["verdict"] == es.UNKNOWN
    assert "不得进入业务 PASS" in result["problem"]


def test_ui_write_cannot_self_attest_entity_scope():
    case = {
        "case_id": "q/R5-C001",
        "side_effects": {"writes": True, "write_scope": "custom_character"},
        "automation": {
            "recipe": {
                "track": "ui",
                "actions": [{"op": "click", "selector": "#save"}],
            }
        },
    }
    matched_claim = es.check_case(case, "custom_character")
    mismatched_claim = es.check_case(case, "recommended_character")
    assert matched_claim["verdict"] == es.UNKNOWN
    assert mismatched_claim["verdict"] == es.UNKNOWN
    assert "不能自证" in matched_claim["problem"]


def test_banner_states_the_real_path_was_not_reached():
    out = es.check_caseset(
        {"cases": [_case("q/R5-C001", "/admin/v1/characters/recommended")]},
        "custom_character")
    assert not out["ok"]
    assert "未触达需求所指的真实用户路径" in out["banner"]


def test_gate_only_judges_write_paths_known_limitation():
    """**已知边界，写在测试里而不是只写在文档里**：本闸只判写路径。

    一条只读用例断言在另一个实体上（"同能力面顶替"的只读版），本闸抓不到。
    R1 那条就是这种形态，本轮靠人判降级。谁要扩这道闸，从这条测试改起。
    """
    r = es.check_case(_case("q/R1-C001", "/admin/v1/voice-presets",
                            method="GET", writes=False), "custom_character")
    assert r["verdict"] == es.MATCH, "只读用例当前不判——这是边界不是 bug"
