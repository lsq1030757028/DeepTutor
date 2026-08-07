"""上下文裁剪层的回归测试。

这一层是 HAR 内容通往模型的**唯一**通道，所以两件事必须钉死：
出境闸一定被走到；裁掉了什么一定说得出来。
"""

from __future__ import annotations

import io
import json

from server.generate.context import (
    MAX_MATERIAL_CHARS,
    build_material,
)
from server.har_parse import parse_har_report


def _report():
    return parse_har_report(
        har_content=io.open("tests/fixtures/sample.har", encoding="utf-8").read()
    )


def _ep(method="GET", path="/api/x", count=1, statuses=None, body=None):
    return {"host": "h", "method": method, "path": path, "count": count,
            "statuses": statuses or {"200": count}, "domain": "d",
            "sample": {"method": method, "url": f"https://h{path}", "query_keys": [],
                       "headers": [], "body": body or {}, "response_status": 200}}


# ── 出境闸必须被走到 ──────────────────────────────────────────────────────

def test_pii_in_a_sample_body_never_reaches_the_material():
    """本层是 HAR 通往模型的唯一通道；漏一处就是 PII 出境（BB-430）。"""
    report = {"endpoints": [_ep("POST", "/api/user/register",
                                body={"mobile": "13800138000",
                                      "email": "real@corp.com"})],
              "replay": {}, "summary": {}}
    m = build_material(report)
    blob = json.dumps({"e": m.endpoints, "l": m.login}, ensure_ascii=False)
    assert "13800138000" not in blob and "real@corp.com" not in blob
    assert m.scrub_hits.get("手机号") == 1


def test_scrub_hits_are_surfaced_as_a_human_note():
    """脱敏了多少要说得出——静默处理等于没告诉用户素材被改过。"""
    report = {"endpoints": [_ep(body={"mobile": "13800138000"})],
              "replay": {}, "summary": {}}
    m = build_material(report)
    assert any("个人信息" in n for n in m.notes)


# ── 排序：业务价值高的先进 prompt ─────────────────────────────────────────

def test_write_operations_outrank_reads():
    report = {"endpoints": [_ep("GET", "/a", count=99), _ep("POST", "/b", count=1)],
              "replay": {}, "summary": {}}
    m = build_material(report, max_endpoints=1)
    assert m.endpoints[0]["path"] == "/b", "写操作改状态，比高频读更值得测"


def test_endpoints_with_real_failures_outrank_clean_ones():
    """出现过 4xx 的端点自带真实负例素材，比只有 200 的更有价值。"""
    report = {"endpoints": [_ep("POST", "/clean", count=50, statuses={"200": 50}),
                            _ep("POST", "/errs", count=2, statuses={"200": 1, "400": 1})],
              "replay": {}, "summary": {}}
    m = build_material(report, max_endpoints=1)
    assert m.endpoints[0]["path"] == "/errs"


# ── 截断：宁可少给，且必须说出来 ─────────────────────────────────────────

def test_truncation_is_reported_not_silent():
    """被静默截断的输出无法被可靠识别（model.py 防线二），所以宁可少给。

    少给了多少必须进 notes——它直接影响生成质量，用户有权知道。
    """
    report = {"endpoints": [_ep(path=f"/api/{i}") for i in range(60)],
              "replay": {}, "summary": {}}
    m = build_material(report, max_endpoints=40)
    assert len(m.endpoints) == 40
    assert any("端点太多" in n for n in m.notes)


def test_material_stays_within_the_char_budget():
    """预算闸是粗口径的，但必须真的生效。"""
    big = {"payload": "x" * 5000}
    report = {"endpoints": [_ep(path=f"/api/{i}", body=big) for i in range(40)],
              "replay": {}, "summary": {}}
    m = build_material(report)
    size = len(json.dumps({"e": m.endpoints, "l": m.login}, ensure_ascii=False))
    assert size <= MAX_MATERIAL_CHARS
    assert any("预算" in n for n in m.notes)


# ── 登录：有就带上，没有要说 ─────────────────────────────────────────────

def test_missing_login_is_called_out():
    report = {"endpoints": [_ep()], "summary": {},
              "replay": {"replayable": False, "missing": ["没抓到登录请求"]}}
    m = build_material(report)
    assert m.login is None
    assert any("登录" in n for n in m.notes)


def test_replayable_login_is_carried_through():
    report = {"endpoints": [_ep()], "summary": {},
              "replay": {"replayable": True,
                         "login_request": {"method": "POST", "path": "/api/auth/login"}}}
    m = build_material(report)
    assert m.login and m.login["path"] == "/api/auth/login"


# ── 字段名对着 har_parse 的真实产出，不是照记忆写的 ───────────────────────

def test_real_report_fields_are_read_correctly():
    """`count` / `statuses` / `sample.body` 这三个名字是实测出来的。

    一旦 har_parse 改了字段名，这条会红——那正是它的作用：
    裁剪层静默读到 None 的话，排序会全乱而没有任何报错。
    """
    m = build_material(_report())
    assert m.endpoints, "真实报告应当裁出端点"
    first = m.endpoints[0]
    assert first["method"] and first["path"]
    assert first["calls"] is not None, "calls 读的是 count 字段"
    assert any("statuses" in e for e in m.endpoints), "statuses 字段应当被带出"
    assert m.stats["endpoints"] is not None
