# -*- coding: utf-8 -*-
"""BB-502 闭环双条件（DoD#8 / DoD#24 / ADR-M2-03 G1 / QS-M2-6）。

两半都要：
① 治本次——partner 的 `builtin_tools` 不含 `ask_user`，**且**两份 SKILL.md 不再教模型调它。
   只做前者会留下「工具收窄了但剧本还在教」的二次半接通。
② 治复发——挂载面与运行时能力面的一致性自检（bank 记的根因就是"两个面无校验"）。
"""
import os
import re

from server.gateway import gate_selfcheck as gs
from server.gateway import partner_setup


SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


# ── ① 治本次：两个条件都要 ─────────────────────────────────────────────────


def test_partner_builtin_whitelist_excludes_ask_user():
    assert "ask_user" not in partner_setup.PARTNER_BUILTIN_TOOLS


def test_partner_whitelist_is_not_empty():
    """空名单 = 把伙伴的内置面全关了，那是另一种坏法，不是修复。"""
    assert len(partner_setup.PARTNER_BUILTIN_TOOLS) >= 10


def test_create_payload_sends_explicit_builtin_tools():
    """发 None 或不发 = 上游按"不设限"处理 = ask_user 照挂。必须显式发。"""
    import inspect
    src = inspect.getsource(partner_setup._create)
    assert '"builtin_tools"' in src
    assert "PARTNER_BUILTIN_TOOLS" in src


def test_repair_path_also_narrows_existing_partners():
    """只在新建路径收窄 = 把隐患留给所有已经建出来的伙伴。"""
    import inspect
    src = inspect.getsource(partner_setup._repair)
    assert "builtin_tools" in src and "HUMAN_GATE_TOOL" in src


def test_skill_docs_no_longer_instruct_calling_ask_user():
    """`rg` 计数式判据：两份 SKILL.md 中「用 ask_user 问」类指令串命中数 == 0。

    刻意只禁**指令**形态（"用 `ask_user` 问"/"调用 ask_user"），
    不禁解释性提及——文档里说明"为什么这条通道不能用问答卡"是我们要的，
    一刀切成"不许出现这个词"会逼人把解释也删掉。
    """
    pattern = re.compile(r"(用\s*`?ask_user`?\s*问|调用\s*`?ask_user`?)")
    hits = []
    for name in ("req-testing", "har-testing"):
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if pattern.search(line):
                    hits.append(f"{name}/SKILL.md:{i}: {line.strip()[:60]}")
    assert hits == [], "SKILL.md 还在教模型调问答卡：\n" + "\n".join(hits)


def test_skill_docs_explain_why_instead_of_just_deleting():
    """删掉指令但不说为什么，下一个人会原样加回来。"""
    for name in ("req-testing", "har-testing"):
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert "BB-502" in text
        assert "等待器" in text or "wait_for_user_reply" in text


# ── ② 治复发：挂载面 vs 运行时能力面 ───────────────────────────────────────


def test_mismatch_is_detected_and_tool_is_removed():
    """挂了人闸但通道没 waiter → 判 MISMATCH，并把工具从建议挂载面摘掉。"""
    r = gs.check_mount_surface("partner", ["web_fetch", "ask_user"])
    assert r["ok"] is False and r["code"] == "E_HUMAN_GATE_MISMATCH"
    assert "ask_user" not in r["safe_tools"]
    assert r["warning"] is True
    assert r["anchor"]          # 判断必须带锚，不能是拍脑袋


def test_matched_channel_passes():
    r = gs.check_mount_surface("main_chat", ["ask_user"])
    assert r["ok"] is True and r["code"] == "OK"


def test_capability_channel_may_mount_the_gate():
    """「测试」capability 跑在主聊 turn 上，人闸合法可用。"""
    r = gs.check_mount_surface("capability_test", ["ask_user"])
    assert r["ok"] is True


def test_unknown_channel_is_fail_closed():
    """未知通道按"没有 waiter"处理——反过来默认会让新接的通道自动获得挂人闸资格，
    那正是 BB-502 的复发路径。"""
    r = gs.check_mount_surface("some_new_im_bridge", ["ask_user"])
    assert r["ok"] is False and r["code"] == "E_HUMAN_GATE_MISMATCH"
    assert r["channel_unknown"] is True


def test_capable_but_unused_is_not_an_error():
    """有能力而不用是合法选择，别报成告警。"""
    r = gs.check_mount_surface("main_chat", ["web_fetch"])
    assert r["ok"] is True and r["code"] == "GATE_AVAILABLE_UNUSED"
    assert r["warning"] is False


def test_startup_report_is_green_after_the_fix():
    report = gs.startup_report()
    assert report["ok"] is True, report["message"]
    assert report["results"]["partner"]["code"] == "OK"


def test_startup_report_catches_a_regression():
    """把 ask_user 加回 partner 面 → 自检必须红。这条是"复发探测器"本身的探测器。"""
    report = gs.startup_report({"partner": ["web_fetch", "ask_user"]})
    assert report["ok"] is False and report["problem_count"] == 1


def test_known_channels_each_carry_an_anchor():
    """表里每条都要带锚——凭印象填 has_waiter=True 是这张表唯一的坏法。"""
    for name, row in gs.KNOWN_CHANNELS.items():
        assert row.get("anchor"), f"{name} 没有证据锚"
