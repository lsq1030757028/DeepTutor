# -*- coding: utf-8 -*-
"""环境挂起的诚实出口 + 拒绝文案的人机分离（2026-08-29 UAT 第二轮）。

## 起因（VERIFY-REPORT.md，V5 判 PARTIAL）

**第一张卡就把人卡死**：还没读需求就先问环境，推荐项是「去配置页新增一个」，
选了推荐项就停摆。**推荐路径本身是死路。**

**然后模型编了个地址去过自己的闸**：原话「I'll retry intake using a genuinely
reachable placeholder host」，用的是 `https://tbd-pending-environment.invalid`。
这不是模型爱撒谎——探活闸是接入的硬前置，而出用例根本不需要环境，于是
「环境还没好」这件该说出口的事，在闸面前只剩停摆和造假两条路。
**闸把诚实变成了死路，就等于在逼模型造假。**

处置依 2026-08-29「闸位重定标」决策（防编造闸从拦入口移到拦断言与写入出口，
`vibe-flow/runs/deeptutor-test-positioning-20260818/decisions.md`）：接入允许
显式挂起，探活闸下移到真要执行那一刻。

## 判据分两组

1. **诚实出口真的通**（挂起能建批次 / 挂起不许带地址 / 执行被拦），且
   **挂起与"忘了填"可分**——后者给的是推荐路径而不是一句"地址非法"。
2. **拒绝文案里给人的那半干净**：错误码、Python 异常、"用同一 batch_id 重新
   提交 journey_clarify" 一律不许进 `message`，整改指令进 `machine`。
"""
import pytest

from server.journey import artifacts, ingest, tools

TRUSTED_OWNER = "unit-test-owner"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


def _pending_batch(store):
    with artifacts.trusted_owner(TRUSTED_OWNER):
        return ingest.ingest("环境待定批次", "", source_kind="requirement_doc",
                             source_ref="local", requirement_text="需求正文",
                             tier="standard", tier_confirmed_via="test",
                             environment_status="pending", owner=TRUSTED_OWNER)


# ── 1 · 诚实出口 ────────────────────────────────────────────────────────────


def test_pending_environment_creates_the_batch_without_probing(store):
    """环境没好照样建批次——出用例不需要环境，这是整条修复的前提。"""
    r = _pending_batch(store)
    assert r["ok"], r
    profile = r["intake_profile"]
    assert profile["environment_status"] == "pending"
    assert profile["base_url"] == ""
    assert profile["environment_note"] == ingest.ENVIRONMENT_PENDING_NOTE


def test_pending_refuses_to_take_a_placeholder_address(store):
    """挂起就如实挂起。带着地址说自己挂起 = 编了个占位地址想两头讨好。

    这条正对着 V5 里那个 `https://tbd-pending-environment.invalid`：
    只放开挂起而不禁掉「挂起 + 占位地址」，模型完全可以两个都写。
    """
    with artifacts.trusted_owner(TRUSTED_OWNER):
        r = ingest.ingest("挂起还带地址", "https://tbd-pending.invalid",
                          source_kind="requirement_doc", source_ref="local",
                          requirement_text="需求正文", tier="standard",
                          tier_confirmed_via="test",
                          environment_status="pending", owner=TRUSTED_OWNER)
    assert r["ok"] is False
    assert "不要传 base_url" in r["error"]
    assert ".invalid" in r["error"]


def test_missing_address_points_at_the_pending_road_not_a_dead_end(store):
    """没给地址且没说挂起时，错误本身要把推荐路径说出来。

    旧行为是 `safe_target_url("")` 的「目标地址必须是 http(s) URL」——
    技术上正确，但它对读到的人（和模型）只说了"你错了"，没说"该怎么办"，
    而"该怎么办"恰恰是模型上次自己编了一个的地方。
    """
    with artifacts.trusted_owner(TRUSTED_OWNER):
        r = ingest.ingest("没给地址", "", source_kind="requirement_doc",
                          source_ref="local", requirement_text="需求正文",
                          tier="standard", tier_confirmed_via="test",
                          owner=TRUSTED_OWNER)
    assert r["ok"] is False
    assert 'environment_status="pending"' in r["error"]
    assert "不要编一个占位地址" in r["error"]


def test_environment_status_only_accepts_the_declared_values(store):
    with artifacts.trusted_owner(TRUSTED_OWNER):
        r = ingest.ingest("状态非法", "", source_kind="requirement_doc",
                          source_ref="local", requirement_text="需求正文",
                          tier="standard", tier_confirmed_via="test",
                          environment_status="maybe", owner=TRUSTED_OWNER)
    assert r["ok"] is False and "environment_status" in r["error"]


def test_execute_is_where_the_probe_gate_now_bites(store, monkeypatch):
    """闸没有被删掉,只是挪到了真要打被测系统那一刻。

    **这条是本组的命门**：只放开接入而不在执行处补闸，等于把探活闸删了——
    那才是真正的降级。所以正反两面都断：挂起批次执行被拒，且拒绝发生在
    任何幂等预约与副作用之前。
    """
    called = []
    monkeypatch.setattr(tools._execute, "write_authorization",
                        lambda *a, **k: called.append("touched") or {})
    bid = _pending_batch(store)["batch_id"]
    r = tools.execute(batch_id=bid, owner=TRUSTED_OWNER)
    assert r["ok"] is False and r["code"] == "E_ENVIRONMENT_PENDING"
    assert called == [], "执行闸必须在任何副作用之前拦下"
    assert ingest.ENVIRONMENT_PENDING_NOTE in str(r["detail"])


def test_a_ready_batch_still_reaches_execution(store, monkeypatch):
    """反向对照：正常批次不受这道闸影响,否则"拦住了"证明不了什么。"""
    monkeypatch.setattr(ingest, "probe_target",
                        lambda *a, **k: {"reachable": True, "status": 200})
    with artifacts.trusted_owner(TRUSTED_OWNER):
        r = ingest.ingest("正常批次", "https://example.invalid",
                          source_kind="requirement_doc", source_ref="local",
                          requirement_text="需求正文", tier="standard",
                          tier_confirmed_via="test", owner=TRUSTED_OWNER)
    assert r["ok"], r
    assert r["intake_profile"]["environment_status"] == "ready"
    assert tools._environment_pending(r["batch_id"], None) is None


# ── 2 · 拒绝文案的人机分离 ──────────────────────────────────────────────────


def test_probe_failure_message_is_human_and_keeps_the_raw_cause_for_the_model():
    """V5 里挂在界面上的那句 `<urlopen error [WinError 10054] …>` 的回归判据。"""
    raw = "<urlopen error [WinError 10054] 远程主机强迫关闭了一个现有的连接。>"
    human = tools._humanize_probe_reason(raw)
    assert "WinError" not in human and "urlopen" not in human
    assert human.endswith("）") or "连不上" in human


@pytest.mark.parametrize(("raw", "needle"), [
    ("GET / 重定向落点越出等价类: https://other.example", "跳转"),
    ("目标地址必须是 http(s) URL", "http/https"),
    ("目标地址格式非法", "格式不对"),
])
def test_probe_reasons_stay_distinguishable_after_humanizing(raw, needle):
    """三种原因三条不同的下一步（AC3 的本意）——人话化不许把它们糊成一句。

    「都翻译成『连不上』」能轻松通过上面那条 WinError 断言，而那正好丢掉了
    AC3 要保住的东西。
    """
    assert needle in tools._humanize_probe_reason(raw)


def test_ingest_rejection_splits_human_message_from_machine_instruction(
        store, monkeypatch):
    monkeypatch.setattr(ingest, "probe_target", lambda *a, **k: {
        "reachable": False,
        "error": "<urlopen error [WinError 10054] 远程主机强迫关闭了一个现有的连接。>"})
    with artifacts.trusted_owner(TRUSTED_OWNER):
        r = tools.ingest(title="不可达", base_url="https://unreachable.example",
                         source_kind="requirement_doc", source_ref="local",
                         requirement_text="需求正文", tier="standard",
                         tier_confirmed_via="test", owner=TRUSTED_OWNER)
    assert r["ok"] is False and r["code"] == "E_INGEST_REJECTED"
    for leak in ("WinError", "urlopen", "E_INGEST", "base_url"):
        assert leak not in r["message"], f"message 泄漏 {leak}：{r['message']!r}"
    assert "WinError" in r["machine"], "原始归因必须留给模型,不能丢"
    assert 'environment_status="pending"' in r["machine"], "机器指令要给出正路"


def test_empty_machine_field_stays_out_of_the_envelope():
    """没有机器指令的拒绝不该多一个空字段——空字段会诱导消费方去读它。"""
    assert "machine" not in tools._err("E_X", "出了点问题。")
    assert tools._err("E_X", "出了点问题。", machine="  ").get("machine") is None


# ── 3 · 契约进窗口（clarify / analyze 的可照抄骨架真的合法）────────────────


def test_the_confirmed_facts_example_passes_the_real_sot_gate():
    """示例自身过真实闸。教错格式的示例比没有示例更糟（BB-688 的原话）。"""
    from server.journey import clarify as clarify_mod
    from server.journey.gates import sot_gate

    verdict = sot_gate.check(clarify_mod.CONFIRMED_FACTS_EXAMPLE)
    assert verdict["verdict"] == "PASS", verdict["problems"]


def test_the_analysis_example_passes_the_real_downstream_gate():
    from server.journey import analyze as analyze_mod
    from server.journey.gates import downstream_gate

    verdict = downstream_gate.check(analyze_mod.ANALYSIS_MD_EXAMPLE)
    assert verdict["verdict"] == "PASS", verdict["problems"]


def test_the_clarification_example_is_a_legal_assumed_entry(store, monkeypatch):
    """assumed 的 answer 必须逐字等于 recommended——示例自己得先守住这条。"""
    from server.journey import clarify as clarify_mod

    monkeypatch.setattr(ingest, "probe_target",
                        lambda *a, **k: {"reachable": True, "status": 200})
    with artifacts.trusted_owner(TRUSTED_OWNER):
        b = ingest.ingest("澄清示例自验", "https://example.invalid",
                          source_kind="requirement_doc", source_ref="local",
                          requirement_text="需求正文", tier="standard",
                          tier_confirmed_via="test", owner=TRUSTED_OWNER)
        out = clarify_mod.clarify(
            b["batch_id"],
            rules=[{"rule_id": "R1", "statement": "规则", "source_quote": "引句"}],
            clarifications=[dict(clarify_mod.CLARIFICATION_EXAMPLE)],
            confirmed_facts_md=clarify_mod.CONFIRMED_FACTS_EXAMPLE)
    assert out["ok"], out.get("problems")


def test_tool_descriptions_carry_the_two_contracts_that_kept_getting_rejected():
    """clarify 的 confirmed_facts_md 与 analyze 的盘点骨架必须在模型窗口里。

    锚取**闸真正卡住的那几个词**，不取段落标题：V5 里 clarify 被拒 3 次、
    analyze 被拒 5 次，卡的就是这两处形状。
    """
    import server.main as main_mod

    clarify_doc = main_mod.journey_clarify.__doc__ or ""
    for token in ("被测构建", "doc×code 对齐", "零access降级声明",
                  "question_id", "recommended", "assumed"):
        assert token in clarify_doc, f"clarify 工具描述缺契约要点：{token}"

    analyze_doc = main_mod.journey_analyze.__doc__ or ""
    for token in ("下游消费面盘点", "同类共存", "不覆盖声明", "记账对账"):
        assert token in analyze_doc, f"analyze 工具描述缺契约要点：{token}"

    ingest_doc = main_mod.journey_ingest.__doc__ or ""
    assert 'environment_status="pending"' in ingest_doc
    assert "绝不要编一个占位地址" in ingest_doc
