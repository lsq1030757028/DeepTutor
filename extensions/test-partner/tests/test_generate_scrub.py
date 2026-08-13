"""出境脱敏闸的回归测试。

这道闸挡的是「HAR 里的个人信息随 prompt 出境到第三方模型服务商」。
它和 `har_parse` 的凭证脱敏不是一回事，也不替代 BB-424 的修复——
所以本文件的断言全部对着 `scrub_for_prompt` 这一个出境入口写。
"""

from __future__ import annotations

import json

import pytest

from server.generate.scrub import scrub_for_prompt, scrub_text


# ── 按值形态匹配的几类 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,label", [
    ("440305199001011234", "身份证"),
    ("11010519491231002X", "身份证"),
    ("13800138000", "手机号"),
    ("19912345678", "手机号"),
    ("real.person@corp.com", "邮箱"),
    ("qa+tag@sub.example.co", "邮箱"),
    ("6222021234567890123", "银行卡号"),
    ("192.168.30.238", "IP"),
    ("S9f3a7c21bb40x", "标识"),
])
def test_pii_value_is_replaced_by_a_typed_placeholder(raw, label):
    """真值必须消失，且换上的占位符要说明它原本是什么类型。"""
    cleaned, hits = scrub_text(f"字段值是 {raw} 结束")
    assert raw not in cleaned
    assert f"<{label}>" in cleaned
    assert hits.get(label) == 1


def test_placeholder_keeps_the_field_meaningful_for_the_model():
    """保形不是修辞：模型要能看出这是手机号，才写得出「少一位应该报错」。

    所以断言的是「换成了带类型的占位符」，不是「换成了 ***」。
    """
    cleaned, _ = scrub_text('{"mobile": "13800138000"}')
    assert cleaned == '{"mobile": "<手机号>"}'


@pytest.mark.parametrize("safe", [
    "orderId",              # 纯字母，不该被「标识」规则咬
    "12345",                # 短数字，是数量不是卡号
    "SKU-1001",             # 业务编号，长度不够
    "application/json",
    "200",
])
def test_ordinary_business_values_are_left_alone(safe):
    """误杀比漏杀更难发现——正常业务值被换成占位符会静默毁掉用例。"""
    cleaned, hits = scrub_text(f"值 {safe} 值")
    assert safe in cleaned
    assert hits == {}


def test_longer_patterns_win_over_shorter_ones():
    """身份证含连续数字段，若手机号规则先跑会把它咬成半截。"""
    cleaned, hits = scrub_text("440305199001011234")
    assert cleaned == "<身份证>"
    assert "手机号" not in hits


# ── 姓名：唯一一条借键名的启发式，连同它的局限一起钉住 ────────────────────

def test_cjk_name_under_a_name_like_key_is_replaced():
    cleaned, hits = scrub_for_prompt({"realName": "张三", "city": "深圳"})
    assert cleaned["realName"] == "<姓名>"
    assert hits.get("姓名") == 1


def test_same_value_under_a_non_name_key_is_kept():
    """「首页」也是两个汉字。没有键名线索时不动，宁可漏也不误杀业务文案。

    这条同时是**局限的证据**：键名不像姓名时，姓名抓不到。
    模块 docstring 里写明了，这里用测试钉住，免得日后被当成 bug 改坏。
    """
    cleaned, hits = scrub_for_prompt({"pageTitle": "首页"})
    assert cleaned["pageTitle"] == "首页"
    assert hits == {}


# ── 递归覆盖：报告是嵌套结构，漏一层就等于没做 ────────────────────────────

def test_scrub_reaches_into_nested_lists_and_dicts():
    payload = {"endpoints": [{"sample": {"body": {"contact":
              {"mobile": "13800138000", "email": "a.b@c.com"}}}}]}
    cleaned, hits = scrub_for_prompt(payload)
    blob = json.dumps(cleaned, ensure_ascii=False)
    assert "13800138000" not in blob and "a.b@c.com" not in blob
    assert hits == {"手机号": 1, "邮箱": 1}


def test_non_string_leaves_survive_unchanged():
    cleaned, _ = scrub_for_prompt({"count": 14, "ok": True, "ratio": 1.5, "none": None})
    assert cleaned == {"count": 14, "ok": True, "ratio": 1.5, "none": None}


# ── 端到端：拿真 HAR 走 build_report，再过闸 ──────────────────────────────

def test_real_report_pii_does_not_survive_the_egress_gate():
    """这条是本文件的主张：**同一份报告，过闸前 PII 在、过闸后不在。**

    过闸前那半是有意断言的——它记录了 2026-08-07 实测的事实
    （har_parse 的凭证词表不含 PII，BB-424），也让这条闸的必要性不靠口头解释。
    若哪天 BB-424 修好、上游自己就不吐 PII 了，这一半会转红，
    那时应当改的是断言而不是删掉这条闸。
    """
    from server.har_parse import build_report

    body = json.dumps({"idCard": "440305199001011234", "mobile": "13800138000",
                       "email": "real.person@corp.com", "realName": "张三",
                       "password": "hunter2"}, ensure_ascii=False)
    har = {"log": {"entries": [{
        "startedDateTime": "2026-08-04T09:00:00.000Z", "time": 10,
        "request": {"method": "POST", "url": "https://api.x.com/api/user/register",
                    "headers": [{"name": "Authorization",
                                 "value": "Bearer eyJSECRETTOKEN0123456789abc"}],
                    "queryString": [],
                    "postData": {"mimeType": "application/json", "text": body}},
        "response": {"status": 200,
                     "content": {"mimeType": "application/json", "text": "{}"}},
    }]}}

    report = build_report(har, {"kind": "inline"})
    before = json.dumps(report, ensure_ascii=False)
    # 过闸前：凭证已被 har_parse 掩掉，PII 没有
    assert "hunter2" not in before, "凭证脱敏是 har_parse 的既有契约，不该退化"
    assert "440305199001011234" in before, "PII 仍在——这正是本闸存在的理由（BB-424）"

    cleaned, hits = scrub_for_prompt(report)
    after = json.dumps(cleaned, ensure_ascii=False)
    for real in ("440305199001011234", "13800138000", "real.person@corp.com"):
        assert real not in after, f"{real} 出境了"
    assert hits, "命中数要能说得出，不能静默处理"


# ── 序列化 JSON body（BB-465）────────────────────────────────────────────────
#
# 上面那条 test_real_report_pii_does_not_survive_the_egress_gate 断言了身份证/
# 手机号/邮箱，**独独没断言姓名**——漏洞就藏在这个缺口里：姓名规则靠键名触发，
# 而 HAR 把 body 存成序列化字符串，里头没有 dict 键，规则永远不触发。
# 手机号那几类有形态特征所以照样被挡住，问题因此被掩盖了很久。

def test_name_inside_serialized_json_body_is_scrubbed():
    """HAR 的 postData.text 形态：整份 JSON 是一个字符串。"""
    body = json.dumps({"realName": "张三", "city": "深圳"}, ensure_ascii=False)
    cleaned, hits = scrub_for_prompt({"text": body})
    assert "张三" not in json.dumps(cleaned, ensure_ascii=False)
    assert hits.get("姓名") == 1
    assert "深圳" in json.dumps(cleaned, ensure_ascii=False), "非姓名字段不该被误伤"


def test_serialized_body_nested_one_more_level_is_still_scrubbed():
    """网关转发原文时出现过 body 套 body。"""
    inner = json.dumps({"receiver": "李四"}, ensure_ascii=False)
    cleaned, _ = scrub_for_prompt({"text": json.dumps({"inner": inner}, ensure_ascii=False)})
    assert "李四" not in json.dumps(cleaned, ensure_ascii=False)


def test_a_string_that_is_json_scalar_keeps_its_type():
    """`"123"` 是合法 JSON 但解出来是 int——换回去就把字符串变成了数字。"""
    cleaned, _ = scrub_for_prompt({"text": "123"})
    assert cleaned["text"] == "123" and isinstance(cleaned["text"], str)


def test_broken_json_falls_back_to_text_rules_without_losing_content():
    """解不开就退回文本规则，内容一个字节都不能丢。"""
    broken = '{"mobile":"13800138000" 这里坏了'
    cleaned, hits = scrub_for_prompt({"text": broken})
    assert "13800138000" not in cleaned["text"]
    assert "这里坏了" in cleaned["text"]
    assert hits.get("手机号") == 1


def test_the_real_har_path_scrubs_the_name_too():
    """把上面那条测试漏掉的断言补上——同一份 HAR，这次连姓名一起验。"""
    body = json.dumps({"realName": "张三", "mobile": "13800138000"}, ensure_ascii=False)
    har = {"log": {"entries": [{
        "startedDateTime": "2026-08-04T09:00:00.000Z", "time": 10,
        "request": {"method": "POST", "url": "https://api.x.com/api/user/register",
                    "headers": [], "queryString": [],
                    "postData": {"mimeType": "application/json", "text": body}},
        "response": {"status": 200,
                     "content": {"mimeType": "application/json", "text": "{}"}},
    }]}}
    from server.har_parse import build_report

    cleaned, _ = scrub_for_prompt(build_report(har, {"kind": "inline"}))
    after = json.dumps(cleaned, ensure_ascii=False)
    assert "张三" not in after, "BB-465：真实 HAR 形态下姓名曾原样出境"
    assert "13800138000" not in after


def test_a_clean_json_body_is_returned_byte_for_byte():
    """没命中就一个字节都不能改。

    重新序列化会规范化空格与键序，而请求体的字节形态不是无所谓的：
    HMAC 签名类接口对 body 逐字节取摘要，把 `{"a":1}` 写成 `{"a": 1}` 就验签失败。
    这条不变量比"顺手统一格式"重要得多，所以单独立一条测试钉住它。
    """
    compact = '{"skuId":"SKU-1","qty":1,"nested":{"a":[1,2]}}'
    cleaned, hits = scrub_for_prompt({"raw": compact})
    assert cleaned["raw"] == compact, "无 PII 的 body 被改写了字节形态"
    assert not hits
