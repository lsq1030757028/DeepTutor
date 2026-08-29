# -*- coding: utf-8 -*-
"""用户可见文案的机械闸 + 档位推荐 + 需求正文继承（2026-08-29 Round 2 复验）。

## 为什么这一轮改成机械闸

Round 1 逐处改了六条拒绝文案，Round 2 复验又抓到两条——**其中一条正是 Round 1
新加的 pending 校验路径**（`environment_status="pending"` 直接怼到用户脸上）。
逐处人工扫的修法必然漏：只要还有人往 `_err` 里写新 message，下一次就还会漏。

所以判据从"这几条改对了没有"换成"**全量的 message 里还有没有机器内容**"。

覆盖边界如实写在这里：静态扫看得到 `_err(code, <字面量 / f-string 的字面部分>)`；
第二个参数是变量或 `str(exc)` 的转发型调用扫不到（它们的文案在别处定义），
那部分由运行态用例逐条覆盖，见本文件第 3、6 组与
`test_journey_env_pending_and_human_errors.py`。
"""
import ast
import re
from pathlib import Path

import pytest

from server.journey import artifacts, ingest, tools

TRUSTED_OWNER = "unit-test-owner"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setattr(artifacts, "BATCHES_ROOT", str(tmp_path / "batches"))
    monkeypatch.setattr(artifacts, "RUNS_ROOT", str(tmp_path / "runs"))
    return artifacts


# ── 1 · 机械闸本体 ──────────────────────────────────────────────────────────

#: 只有模型用得上的形态。三类各对着一次真实泄漏：
#: snake_case 参数名（`environment_status="pending"`）、错误码（`E_INGEST_REJECTED`）、
#: 内部闸名（`evidence_gate` / `mechanical_check`）。
_LEAK_PATTERNS = (
    (re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"), "snake_case 标识符"),
    (re.compile(r"\bE_[A-Z][A-Z0-9_]{2,}\b"), "错误码"),
    (re.compile(r"[a-z_]+\s*=\s*[\"'][^\"']*[\"']"), "参数赋值写法"),
)

#: 允许出现在人话里的例外。只有一类：用户自己也会说出口的名字。
#: 加一条就要能说清"用户为什么读得懂它"。
_LEAK_ALLOWED = ("https", "http", "tapd.cn")


def user_facing_leak(text: str) -> str:
    """返回第一处泄漏的描述；干净则返回空串。"""
    body = str(text or "")
    for allowed in _LEAK_ALLOWED:
        body = body.replace(allowed, "")
    for pattern, label in _LEAK_PATTERNS:
        hit = pattern.search(body)
        if hit:
            return f"{label}:{hit.group(0)!r}"
    return ""


def _literal_of(node):
    """字面量，或 f-string 里的字面部分。拿不到返回 None（转发型调用）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values
                 if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts) if parts else None
    return None


def _err_messages(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") != "_err" or len(node.args) < 2:
            continue
        text = _literal_of(node.args[1])
        if text is not None:
            yield node.lineno, text


def _tools_path() -> Path:
    return Path(__file__).resolve().parents[1] / "server" / "journey" / "tools.py"


def test_no_rejection_message_leaks_machine_only_content():
    """全量扫 `_err` 的 message 字面量：参数名 / 错误码 / 赋值写法一个都不许有。

    这条替代不了人读文案通不通顺，但它替代得了"这次又漏了哪一处"。
    """
    leaks = [(lineno, leak, text[:70])
             for lineno, text in _err_messages(_tools_path())
             if (leak := user_facing_leak(text))]
    assert leaks == [], "message 里混进了只给模型看的内容：" + repr(leaks)


def test_the_scan_actually_sees_a_meaningful_number_of_messages():
    """空扫也能让上一条全绿。先证明扫到了东西，再谈扫得干不干净。"""
    found = list(_err_messages(_tools_path()))
    assert len(found) >= 15, f"只扫到 {len(found)} 条 message，扫描器多半坏了"


def test_the_leak_detector_catches_the_three_real_leaks():
    """探测器的探测器。

    上面全绿有两种可能：真没泄漏，或者**正则根本抓不到东西**。
    这里把三次真实泄漏的原文喂进去，一条都不许漏。
    """
    real_leaks = (
        '没有被测环境地址。环境还没好就传 environment_status="pending" 先出用例。',
        "journey_ingest E_INGEST_REJECTED 接入终点不可达。",
        "投影没过 evidence_gate/mechanical_check",
    )
    for text in real_leaks:
        assert user_facing_leak(text), f"探测器漏掉了真实泄漏：{text!r}"


def test_the_leak_detector_does_not_flag_ordinary_sentences():
    """反向：正常人话不许误报。会误报的闸迟早会被调松到失效。"""
    for clean in (
        "这个地址连不上（网络不通，或者服务没起来），所以还没开始接入。",
        "澄清材料还有几处没达标，我按提示改一版再提交，你不用操作。",
        "还没有测试环境地址。环境没好也能先把用例出出来，我按这条走。",
        "这个批次的测试环境地址还没定，所以不能真跑。用例都在，补上地址就能执行。",
    ):
        assert not user_facing_leak(clean), f"人话被误判为泄漏：{clean!r}"


def test_the_pending_path_message_is_clean_end_to_end(store):
    """Round 2 那条漏网路径单独钉一次（静态扫之外的运行态确认）。"""
    with artifacts.trusted_owner(TRUSTED_OWNER):
        r = ingest.ingest("没给地址", "", source_kind="requirement_doc",
                          source_ref="local", requirement_text="需求正文",
                          tier="standard", tier_confirmed_via="test",
                          owner=TRUSTED_OWNER)
    assert r["ok"] is False
    assert not user_facing_leak(r["error"]), r["error"]
    assert 'environment_status="pending"' in r["machine"], "正路仍要留给模型"


# ── 2 · 档位推荐不许把"没检测到"说成"没有" ──────────────────────────────────

_DEVICE_REQUIREMENT = (
    "用户可以通过语音把设备切换到自由对话模式，也可以在设置里调整屏幕亮度，"
    "长时间无操作后设备进入待机状态，再次唤醒后恢复上一次的对话模式。"
    "管理员可以远程下发配置并重置设备。"
)


def test_device_control_requirement_is_not_recommended_checklist():
    """Round 2 卡点 A：13 条写设备状态的需求被判成「0 write verbs」→ checklist。

    checklist 明说不建用例库，用户按推荐走就永远到不了采纳卡——
    **推荐项本身是条死路**，而他还以为这就是"正式测"的全部。
    """
    result = ingest.propose_tier(_DEVICE_REQUIREMENT)
    assert result["proposed_tier"] != "checklist"
    assert result["write_intent"] == "detected"


def test_unknown_write_intent_floors_the_recommendation_at_standard():
    """词表没命中 ≠ 没有写操作。够长的正文零命中时推荐下限抬到 standard。"""
    text = "这一段描述里刻意不含任何本词表覆盖的动作词。" * 12
    result = ingest.propose_tier(text)
    assert result["write_intent"] == "unknown"
    assert result["proposed_tier"] == "standard"
    assert any("写意图未确认" in reason for reason in result["reasons"])


def test_checklist_still_reachable_when_the_caller_declares_no_writes():
    """反向对照：显式声明没有写操作时 checklist 仍推荐得出来。

    没有这条，上面两条可以靠"永远不推荐 checklist"通过，那等于把一个档位废掉。
    """
    result = ingest.propose_tier("很短的说明。", writes_expected=False)
    assert result["proposed_tier"] == "checklist"
    assert result["write_intent"] == "declared_none"


def test_the_reason_line_never_claims_zero_write_operations():
    """理由栏是模型对用户说话的原料。它只能说"没检测到"，不能说"没有"。"""
    joined = " ".join(ingest.propose_tier("一段没有动作词的描述。")["reasons"])
    assert "未检测到写动词" in joined
    assert "写操作动词 0 处" not in joined


def test_the_checklist_option_says_where_that_road_leads():
    """选项文案要自带"这条路通向哪里"，否则选了才发现是死路。"""
    card = ingest.propose_tier(_DEVICE_REQUIREMENT)["card"]
    checklist = next(o for o in card["options"] if o["value"] == "checklist")
    assert "不建用例库" in checklist["label"]
    assert "standard" in checklist["label"], "死路必须同时给出活路"


# ── 3 · 需求正文继承 ────────────────────────────────────────────────────────


def test_a_prior_batch_hands_over_its_requirement_text(store, monkeypatch):
    """Round 2 卡点 B：同一会话里先按原文列了规则，升档时却要用户手工粘贴。"""
    monkeypatch.setattr(ingest, "probe_target",
                        lambda *a, **k: {"reachable": True, "status": 200})
    with artifacts.trusted_owner(TRUSTED_OWNER):
        first = ingest.ingest("原批次", "https://example.invalid",
                              source_kind="requirement_doc", source_ref="local",
                              requirement_text="需求正文：切换对话模式并持久化。",
                              tier="checklist", tier_confirmed_via="test",
                              owner=TRUSTED_OWNER)
        assert first["ok"], first
        inherited = tools._inherited_requirement(first["batch_id"], None)
    assert inherited["ok"]
    assert "切换对话模式" in inherited["requirement_text"]
    assert inherited["source_kind"] == "requirement_doc"


def test_inheriting_from_a_batch_without_text_says_so_instead_of_inventing(store):
    """接不到就如实说接不到。编一段"继承来的正文"比要用户粘贴坏得多。"""
    assert tools._inherited_requirement("b-does-not-exist", None) == {"ok": False}


def test_asking_the_user_to_paste_is_the_last_resort_not_the_first(store):
    """拿不到正文时，机器指令要先指向历史批次，而不是直接要用户粘贴。"""
    with artifacts.trusted_owner(TRUSTED_OWNER):
        r = tools.ingest(title="没有 oracle", base_url="https://example.invalid",
                         source_kind="requirement_doc", source_ref="local",
                         tier="standard", tier_confirmed_via="test",
                         owner=TRUSTED_OWNER)
    assert r["ok"] is False and r["code"] == "E_ORACLE_NOT_FOUND"
    assert not user_facing_leak(r["message"]), r["message"]
    assert "journey_get_batch" in r["machine"]
    assert "别让用户手工粘贴" in r["machine"]


# ── 4 · 采纳卡不挂 sha256 ───────────────────────────────────────────────────


def _write_case(draft_id: str, title: str, expected: str = "生效"):
    return {"draft_id": draft_id, "title": title, "expected": expected,
            "steps": ["操作一步"],
            "side_effects": {"writes": True, "cleanup": "复原",
                             "authorization": "用户已授权"}}


def test_the_adoption_card_description_is_human_readable():
    """Round 2 第 13 条：每条写用例前挂一整行 64 位十六进制。

    两头都断：卡面没有摘要，**且**内容绑定还在。只断前者的话，
    把摘要整个弄丢也能通过——那是把一个缺陷换成一个更坏的。
    """
    from server.journey import decision_auth

    case = _write_case("d1", "语音触发切换至自由对话模式并持久化")
    card = decision_auth.expected_adopt_question("b-1", {"cases": [case]}, ["d1"])
    description = card["options"][0]["description"]
    assert "sha256:" not in description, f"卡面仍挂着摘要：{description!r}"
    assert description == case["title"]

    changed = decision_auth.expected_adopt_question(
        "b-1", {"cases": [_write_case("d1", case["title"], expected="改过了")]}, ["d1"])
    assert changed["id"] != card["id"], "内容变了卡 id 必须跟着变，否则绑定丢了"


def test_the_write_case_digest_still_reaches_the_model():
    """摘要不是删掉，是搬家——搬到模型读得到、用户读不到的地方。"""
    from server.journey import decision_auth

    case = _write_case("d1", "语音触发切换至自由对话模式并持久化")
    rows = tools._adopt_write_disclosure({"cases": [case]}, ["d1"])
    assert rows and rows[0]["draft_id"] == "d1"
    assert rows[0]["content_digest"] == decision_auth.adopt_draft_digest(case)
