# -*- coding: utf-8 -*-
"""M1 Advisory A2 闭环：指纹锚本次执行的靶（设计稿 §8.2 / DoD#10 / QS-M2-4）。

M1 现场：run 打的是 `127.0.0.1:9`（不可达），evidence-bundle 里却写着
`target@127.0.0.1:8047 status=200`——一个完全没被访问过的靶的成功指纹。
根因是 `_instance_fingerprint(batch_id)` 取的是**接入期**探针，
而这一趟真正打的是 `base_url_override`。
"""
import inspect

import pytest

from server.journey import execute_run


API_PROBE = {
    "reachable": True, "status": 200, "page_title": "",
    "body_head_sha256": "a" * 64, "service_banner": "nginx",
    "content_type": "application/json",
}
UI_PROBE = dict(API_PROBE, page_title="白月SMS 管理端", content_type="text/html")


# ── 签名：指纹只能由执行期的靶产生 ─────────────────────────────────────────


def test_fingerprint_takes_the_actual_target_not_a_batch_id():
    """签名改了才算修好——收 batch_id 的版本永远只能拿到接入期那个靶。"""
    params = list(inspect.signature(execute_run._instance_fingerprint).parameters)
    assert params == ["base_url", "probe"]


def test_fingerprint_follows_the_override_target():
    fp = execute_run._instance_fingerprint("http://127.0.0.1:9", API_PROBE)
    assert "127.0.0.1:9" in fp
    assert "8047" not in fp


# ── 分轨口径 ────────────────────────────────────────────────────────────────


def test_api_track_page_title_is_none_not_empty_string():
    """**必须是 None**：空串会让「没有标题」与「标题是空」不可区分，
    也会让消费方以为这一维还在生效。"""
    identity = execute_run.build_target_identity("http://x.test", API_PROBE, "api")
    assert identity["page_title"] is None


def test_ui_track_keeps_page_title():
    identity = execute_run.build_target_identity("http://x.test", UI_PROBE, "ui")
    assert identity["page_title"] == "白月SMS 管理端"


def test_api_track_has_the_replacement_dimensions():
    """`service_banner` / `content_type` 顶替 page_title 承担判别力。
    键必须在场（值可为 None），否则"换轨后指纹静默少一维"。"""
    identity = execute_run.build_target_identity("http://x.test", API_PROBE, "api")
    for key in ("service_banner", "content_type", "track", "source"):
        assert key in identity
    assert identity["track"] == "api"


def test_unreachable_is_recorded_as_unreachable_not_stale_200():
    """靶不可达就记 unreachable——沿用接入期的 200 正是 A2 那条缺陷本身。"""
    identity = execute_run.build_target_identity(
        "http://127.0.0.1:9", {"reachable": False, "error": "refused"}, "api")
    assert identity["status"] == "unreachable"


def test_4xx_still_counts_as_probed():
    """4xx/5xx **也算探到**——服务在，只是这个路径不给好脸色。"""
    identity = execute_run.build_target_identity(
        "http://x.test", dict(API_PROBE, status=404), "api")
    assert identity["status"] == 404


# ── 轨道识别（DoD#4b 的输入）────────────────────────────────────────────────


def test_track_detected_from_ops_not_from_config():
    api_manifest = {"cases": [{"actions": [{"op": "request"}, {"op": "expect_status"}]}]}
    ui_manifest = {"cases": [{"actions": [{"op": "goto"}, {"op": "click"}]}]}
    assert execute_run.detect_track(api_manifest) == "api"
    assert execute_run.detect_track(ui_manifest) == "ui"


def test_one_ui_op_is_enough_to_mark_the_run_ui():
    """混轨也算越轨——按"只要有一个 UI op"判，不按多数决。"""
    mixed = {"cases": [
        {"actions": [{"op": "request"}]},
        {"actions": [{"op": "request"}, {"op": "expect_visible"}]},
    ]}
    assert execute_run.detect_track(mixed) == "ui"


def test_ui_op_vocabulary_is_complete():
    """漏一个 op 就漏一条越轨路径。逐个对 pw_runtime 的实现列表。"""
    assert set(execute_run.UI_TRACK_OPS) == {
        "goto", "fill", "click", "expect_visible", "expect_text",
        "expect_title_contains", "expect_url_contains"}


def test_api_track_forbidden_evidence_kinds_declared():
    assert set(execute_run.UI_ONLY_EVIDENCE) == {"playwright_trace", "screenshot"}


# ── drift：换环境跑不阻断，但必须看得见 ────────────────────────────────────


def test_drift_flag_is_false_without_an_intake_anchor():
    """没有接入期锚就不该报 drift——"不知道"不等于"漂了"。"""
    assert execute_run._drifted("b-20260811-nobatch", {"base_url_host": "x:80"}) is False
