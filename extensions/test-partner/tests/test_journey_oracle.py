# -*- coding: utf-8 -*-
"""oracle 自测：fields 白名单、两种空正文的分开报错、快照冻结、漂移闸。

对应判据：ADR-M2-02 G3（错误串 fail-closed）/ G4（漂移闸）/ G6（靶锁定）；
DoD#5（改需求一字后 adopt 被阻断且 caseset 未变）。
"""
import json
import os

import pytest

from server.journey import artifacts, oracle


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


STORY = {
    "id": "1167600006001046281",
    "name": "【常规需求】【QIQI】Queenie App 新建自定义角色支持韩语语音交互及男女音色",
    "description": "<div>【需求内容】</div><div>新增“한국어（韩语）”选项。</div>",
    "workspace_id": "67600006",
    "status": "status_21",
}


def _fake_call(payload):
    def _call(tool, params, **kwargs):
        _call.seen.append((tool, params))
        return payload
    _call.seen = []
    return _call


# ── fields 白名单：description 必须在内 ─────────────────────────────────────


def test_default_fields_include_description():
    """施工期实测：不传 fields 时 TAPD 返回 54 栏但 description 恒为空串。

    白名单少了这一项，快照就是个只有标题的壳，而 digest 照算不误、全链无人报警。
    """
    assert "description" in oracle.DEFAULT_FIELDS


def test_fetch_story_sends_fields_whitelist(monkeypatch):
    monkeypatch.setattr(oracle, "call_read_tool",
                        _fake_call({"data": [{"Story": STORY}]}))
    oracle.fetch_story("67600006", "1167600006001046281")
    _tool, params = oracle.call_read_tool.seen[0]
    assert "description" in params["options"]["fields"]
    assert params["workspace_id"] == "67600006"


# ── 两种"没有正文"必须分开报（0021 红线六）──────────────────────────────────


def test_missing_description_key_is_our_bug_not_the_requirement(monkeypatch):
    """键根本不在 → `E_ORACLE_FIELD_MISSING`：是取数侧的问题。"""
    row = {k: v for k, v in STORY.items() if k != "description"}
    monkeypatch.setattr(oracle, "call_read_tool", _fake_call({"data": [{"Story": row}]}))
    with pytest.raises(oracle.OracleError) as exc:
        oracle.fetch_story("67600006", "1")
    assert exc.value.code == oracle.E_ORACLE_FIELD_MISSING
    assert "不是需求没写正文" in str(exc.value)


def test_empty_description_value_is_the_requirement_being_empty(monkeypatch):
    """键在、值为空 → `E_ORACLE_BODY_EMPTY`：需求方确实没写。"""
    row = dict(STORY, description="   ")
    monkeypatch.setattr(oracle, "call_read_tool", _fake_call({"data": [{"Story": row}]}))
    with pytest.raises(oracle.OracleError) as exc:
        oracle.fetch_story("67600006", "1")
    assert exc.value.code == oracle.E_ORACLE_BODY_EMPTY


def test_two_empty_shapes_do_not_share_a_code():
    """两个码不许合并——合并了就没法回答"该找需求方还是该查我们的取数"。"""
    assert oracle.E_ORACLE_FIELD_MISSING != oracle.E_ORACLE_BODY_EMPTY


def test_not_found_when_no_rows(monkeypatch):
    monkeypatch.setattr(oracle, "call_read_tool", _fake_call({"data": []}))
    with pytest.raises(oracle.OracleError) as exc:
        oracle.fetch_story("67600006", "1")
    assert exc.value.code == oracle.E_ORACLE_NOT_FOUND


def test_write_tools_are_not_reachable_through_oracle():
    """只读白名单：写工具连拼名字这一步都过不去（ADR-M2-02 第 2 条）。"""
    ch = dict(oracle.DEFAULT_CHANNEL)
    for tool in ("create_story", "update_story", "add_bug"):
        with pytest.raises(oracle.OracleError) as exc:
            oracle._dt_tool_name(ch, tool)
        assert exc.value.code == oracle.E_ORACLE_FORBIDDEN


# ── 通道可切换（2026-08-11 manager 裁定）─────────────────────────────────────


def test_channel_is_config_driven_not_hardcoded():
    """端点、条目名、传输方式都从配置读——TAPD 通道形态已被现实推翻两次，不写死。"""
    for key in ("transport", "server_name", "endpoint", "bearer_secret_key"):
        assert key in oracle.DEFAULT_CHANNEL


def test_tool_name_follows_configured_server_name():
    """换通道 = 改条目名，不改代码。"""
    ch = dict(oracle.DEFAULT_CHANNEL, server_name="tapd-v2")
    assert oracle._dt_tool_name(ch, "get_stories_or_tasks") == \
        "mcp_tapd-v2_get_stories_or_tasks"


# ── 快照冻结 ────────────────────────────────────────────────────────────────


def test_snapshot_freezes_server_text_and_records_provenance(store):
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    src = oracle.snapshot(bid, STORY, workspace_id="67600006",
                          story_id="1167600006001046281")
    assert src["provider"] == "tapd"
    assert src["story_id"] == "1167600006001046281"
    # 与 caseset 同一套 digest 规则（`sha256:` 前缀 + 64 位十六进制）
    assert src["content_digest"].startswith("sha256:")
    assert len(src["content_digest"]) == len("sha256:") + 64
    path = os.path.join(oracle.oracle_dir(bid), os.path.basename(src["snapshot_rel"]))
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    # 落盘的是**服务器原文行**，不是我们规整过的文本——规整规则一改，
    # 历史 digest 就全废了，那正是 digest 纪律要防的
    assert saved["story"]["description"] == STORY["description"]


def test_plain_text_not_used_for_digest():
    """digest 算在原文上；plain_text 只服务展示与定档启发式。"""
    a = oracle.content_digest_of(STORY)
    b = oracle.content_digest_of(dict(STORY, description=STORY["description"] + " "))
    assert a != b       # 原文差一个空格就是不同的 oracle


def test_load_snapshot_reads_latest(store):
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    oracle.snapshot(bid, STORY, workspace_id="67600006", story_id="1")
    oracle.snapshot(bid, dict(STORY, description="改过的正文"),
                    workspace_id="67600006", story_id="1")
    assert oracle.load_snapshot(bid)["story"]["description"] == "改过的正文"


# ── 漂移闸（DoD#5 / G4）─────────────────────────────────────────────────────


def _batch_with_oracle(store, monkeypatch, story=STORY):
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    src = oracle.snapshot(bid, story, workspace_id="67600006",
                          story_id="1167600006001046281")
    artifacts.save_artifact(bid, "intake_profile",
                            {"tier": "standard", "oracle_source": src})
    return bid


def test_drift_gate_passes_when_unchanged(store, monkeypatch):
    bid = _batch_with_oracle(store, monkeypatch)
    monkeypatch.setattr(oracle, "call_read_tool",
                        _fake_call({"data": [{"Story": STORY}]}))
    assert oracle.check_drift(bid)["ok"] is True


def test_drift_gate_blocks_on_one_character_change(store, monkeypatch):
    """改需求一个字 → adopt 被阻断（DoD#5 的机械判据）。"""
    bid = _batch_with_oracle(store, monkeypatch)
    changed = dict(STORY, description=STORY["description"] + "。")
    monkeypatch.setattr(oracle, "call_read_tool",
                        _fake_call({"data": [{"Story": changed}]}))
    r = oracle.check_drift(bid)
    assert r["ok"] is False and r["code"] == oracle.E_ORACLE_DRIFT
    assert "需求已变更" in r["message"]


def test_drift_gate_reports_fetch_failure_not_pass(store, monkeypatch):
    """重取失败**不算通过**——把"没查成"读成"没变化"是最典型的假绿。"""
    bid = _batch_with_oracle(store, monkeypatch)

    def _boom(*a, **k):
        raise oracle.OracleError("通道断了", code=oracle.E_ORACLE_FETCH_FAILED)
    monkeypatch.setattr(oracle, "call_read_tool", _boom)
    r = oracle.check_drift(bid)
    assert r["ok"] is False and r["code"] == oracle.E_ORACLE_FETCH_FAILED


def test_drift_gate_has_no_bypass_flag():
    """ADR-M2-02 第 6 条判否：不引入"以快照为准强制采纳"的旁路。

    旁路一旦存在，漂移闸等于不存在——所以这条断言盯的是**签名**，
    防有人日后"顺手"加一个 force 参数。
    """
    import inspect
    params = set(inspect.signature(oracle.check_drift).parameters)
    assert not (params & {"force", "bypass", "skip", "ignore_drift"})


def test_non_tapd_oracle_says_skipped_not_passed(store):
    """本地文档 oracle 不适用漂移闸——如实说"不适用"，不假装检查过。"""
    meta = artifacts.create_batch("t")
    bid = meta["batch_id"]
    artifacts.save_artifact(bid, "intake_profile",
                            {"tier": "standard",
                             "oracle_source": {"provider": "local_doc"}})
    r = oracle.check_drift(bid)
    assert r["ok"] is True and r["code"] == "SKIPPED_NON_TAPD"
