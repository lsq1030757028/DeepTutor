# -*- coding: utf-8 -*-
"""journey.digest 自测：RFC 8785 测试向量（准入闸）+ digest 字段包含/排除规则。

RFC 8785 向量抄自规范附录（golden，禁改）；digest 规则对 schema 草案 digestRules。
"""
import pytest

from server.journey import digest


# ── RFC 8785 Appendix B 综合向量（golden）──────────────────────────────────

def test_rfc8785_appendix_composite_vector():
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3,
                    0.000000000000000000000000001],
        "string": "€$\x0f\nA'B\"\\\\\"/",
        "literals": [None, True, False],
    }
    expected = (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert digest.canonicalize(value) == expected


def test_rfc8785_key_sorting_utf16_code_units():
    # RFC 8785 §3.2.3 排序向量：😀(代理对)按 UTF-16 码元排在 דּ(U+FB33) 之前
    value = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\x80": "Control",
        "\xf6": "Latin Small Letter O With Diaeresis",
    }
    expected = (
        '{"\\r":"Carriage Return","1":"One","\x80":"Control",'
        '"\xf6":"Latin Small Letter O With Diaeresis","€":"Euro Sign",'
        '"\U0001f600":"Emoji: Grinning Face",'
        '"דּ":"Hebrew Letter Dalet With Dagesh"}'
    )
    assert digest.canonicalize(value) == expected


@pytest.mark.parametrize("value,expected", [
    (0.0, "0"),
    (-0.0, "0"),
    (1, "1"),
    (4.5, "4.5"),
    (2e-3, "0.002"),
    (1e21, "1e+21"),
    (1e20, "100000000000000000000"),
    (1e-6, "0.000001"),
    (1e-7, "1e-7"),
    (5e-324, "5e-324"),
    (9.999999999999997e22, "9.999999999999997e+22"),
    (333333333.33333329, "333333333.3333333"),
    (True, "true"),
    (None, "null"),
])
def test_es_number_and_literal_vectors(value, expected):
    assert digest.canonicalize(value) == expected


def test_nan_inf_rejected():
    with pytest.raises(ValueError):
        digest.canonicalize(float("nan"))
    with pytest.raises(ValueError):
        digest.canonicalize(float("inf"))


def test_non_string_key_rejected():
    with pytest.raises(TypeError):
        digest.canonicalize({1: "x"})


def test_no_whitespace_and_deterministic():
    v = {"b": [1, 2], "a": {"y": "z"}}
    s = digest.canonicalize(v)
    assert s == '{"a":{"y":"z"},"b":[1,2]}'
    assert digest.sha256_digest(v) == digest.sha256_digest({"a": {"y": "z"}, "b": [1, 2]})


# ── digest 字段包含/排除规则（schema 草案 digestRules）─────────────────────

CASE = {
    "case_id": "bysms/R1-C001",
    "case_version": 1,
    "title": "登录成功",
    "expected": "进入管理台",
    "failure_criteria": "停留在登录页或报错",
    "assertion_layers": {"ui": {"assertions": ["标题为管理台"],
                               "required_evidence": ["screenshot"]}},
    "steps": ["打开登录页", "输入正确账密", "提交"],
}


def test_oracle_digest_only_three_fields():
    base = digest.oracle_digest(CASE)
    changed_title = dict(CASE, title="改标题不影响 oracle")
    assert digest.oracle_digest(changed_title) == base
    changed_expected = dict(CASE, expected="别的预期")
    assert digest.oracle_digest(changed_expected) != base


def test_source_digest_excludes_digest_fields_only():
    stamped = digest.stamp_case_digests(CASE)
    # 重算稳定：对已盖章 case 再算 source digest，排除掉 digest 字段后应与首次一致
    assert digest.source_case_digest(stamped) == stamped["source_case_digest"]
    # 改任何业务字段（含 title）都会变 source digest
    tampered = dict(stamped, title="偷偷改标题")
    assert digest.source_case_digest(tampered) != stamped["source_case_digest"]


def test_verify_case_digests_catches_tamper():
    stamped = digest.stamp_case_digests(CASE)
    assert digest.verify_case_digests(stamped) == []
    tampered = dict(stamped, expected="业务预期被反写")
    errs = digest.verify_case_digests(tampered)
    # oracle 与 source 两个摘要都必须报不一致（expected 同时进两者）
    assert len(errs) == 2


def test_text_digest_normalizes_newlines():
    assert digest.text_digest("a\r\nb") == digest.text_digest("a\nb")
    assert digest.text_digest("a\nb").startswith("sha256:")
