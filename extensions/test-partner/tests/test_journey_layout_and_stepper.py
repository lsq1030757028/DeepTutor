# -*- coding: utf-8 -*-
"""产物根注入面（BB-503 / DoD#20）、九格账本（§9 / DoD#6 的 CI 部分）、布局迁移。"""
import inspect
import os

import pytest

from server.journey import artifacts


@pytest.fixture()
def root(tmp_path):
    return str(tmp_path)


# ── BB-503：路径构造函数一律带 root ─────────────────────────────────────────


def test_no_path_builder_without_root(root):
    """CI 判据：`artifacts.py` 不存在无 root 参数的路径构造函数。

    与 `gateway/workbench.py` 的既有形态对拍——那边每个函数本来就收可选 `root`，
    正是"0009 零上游触点落地"的机制依据。M1 新建的批次路径漏了这条，
    于是隔离在交付件路径上成立、在批次路径上不成立。这条断言防它重演。
    """
    builders = ["workbench_root", "owner_root", "batches_root", "runs_root",
                "batch_dir", "run_dir", "artifact_path", "save_artifact",
                "load_artifact", "has_artifact", "create_batch", "load_batch",
                "save_batch", "list_batches", "stepper", "append_event",
                "read_events", "latest_run_id", "migrate_layout"]
    missing = [name for name in builders
               if "root" not in inspect.signature(getattr(artifacts, name)).parameters]
    assert missing == [], f"这些函数还不能被注入 root：{missing}"


def test_root_injection_actually_isolates(root, tmp_path):
    """两个 root 各建一个批次，互相看不见——布局分区是真的，不是文档里的一句话。"""
    a = str(tmp_path / "a")
    b = str(tmp_path / "b")
    m1 = artifacts.create_batch("A 的批次", root=a)
    m2 = artifacts.create_batch("B 的批次", root=b)
    assert [x["batch_id"] for x in artifacts.list_batches(root=a)] == [m1["batch_id"]]
    assert [x["batch_id"] for x in artifacts.list_batches(root=b)] == [m2["batch_id"]]


def test_owner_layer_in_path(root):
    meta = artifacts.create_batch("t", owner="alice", root=root)
    path = artifacts.batch_dir(meta["batch_id"], root=root)
    assert os.path.join("alice", "batches") in path
    assert meta["partition"] == "alice"


def test_empty_owner_falls_to_local_but_keeps_the_distinction(root):
    """空 owner 落 `_local` 目录，但 `owner` 字段仍是空串。

    把空串直接写成 `_local` 会让"没声明归属"与"归属就是本地"不可区分。
    """
    meta = artifacts.create_batch("t", root=root)
    assert meta["owner"] == "" and meta["partition"] == artifacts.DEFAULT_OWNER


def test_owner_path_escape_blocked(root):
    for bad in ("../../etc", "a/b", "..\\x", "x" * 100):
        with pytest.raises(artifacts.ArtifactError):
            artifacts.create_batch("t", owner=bad, root=root)


# ── 布局迁移可逆（DoD#20）───────────────────────────────────────────────────


def test_migration_is_reversible(root):
    """M1 平铺 → `<owner>/` → 再迁回来。只搬目录不改内容，两个方向都不丢数据。"""
    legacy = os.path.join(root, "batches", "b-20260810-abcdef")
    os.makedirs(legacy)
    with open(os.path.join(legacy, "batch.json"), "w", encoding="utf-8") as fh:
        fh.write('{"artifact":"batch","batch_id":"b-20260810-abcdef","title":"存量",'
                 '"run_ids":[],"created_at":"2026-08-10T00:00:00+0800"}')

    up = artifacts.migrate_layout(root=root)
    assert len(up["moved"]) == 1
    assert os.path.isfile(os.path.join(root, "_local", "batches",
                                       "b-20260810-abcdef", "batch.json"))
    assert not os.path.isdir(legacy)

    down = artifacts.migrate_layout(root=root, reverse=True)
    assert len(down["moved"]) == 1
    assert os.path.isfile(os.path.join(legacy, "batch.json"))


def test_dry_run_moves_nothing(root):
    legacy = os.path.join(root, "batches", "b-20260810-abcdef")
    os.makedirs(legacy)
    with open(os.path.join(legacy, "batch.json"), "w", encoding="utf-8") as fh:
        fh.write('{"artifact":"batch","batch_id":"b-20260810-abcdef","run_ids":[]}')
    plan = artifacts.migrate_layout(root=root, dry_run=True)
    assert plan["moved"] and os.path.isdir(legacy)


def test_legacy_flat_batches_still_readable_before_migration(root):
    """迁移前后都不瞎——存量平铺批次照样列得出来。"""
    legacy = os.path.join(root, "batches", "b-20260810-abcdef")
    os.makedirs(legacy)
    with open(os.path.join(legacy, "batch.json"), "w", encoding="utf-8") as fh:
        fh.write('{"artifact":"batch","batch_id":"b-20260810-abcdef","title":"存量",'
                 '"run_ids":[],"created_at":"2026-08-10T00:00:00+0800"}')
    assert [b["batch_id"] for b in artifacts.list_batches(root=root)] == \
        ["b-20260810-abcdef"]


# ── 九格账本（§9 / DoD#6）───────────────────────────────────────────────────


def test_stepper_has_exactly_nine_cells_in_fixed_order(root):
    meta = artifacts.create_batch("t", root=root)
    steps = artifacts.stepper(meta["batch_id"], root=root)
    assert len(steps) == 9
    assert [s["artifact"] for s in steps] == [
        "intake_profile", "business_frame", "test_analysis", "case_draft",
        "approved_caseset", "automation_bundle", "run_receipt", "verdicts",
        "coverage_ledger"]


def test_stepper_cells_carry_label_from_server(root):
    """展示名由服务端给——前端不再各持常量（消灭第二真相）。"""
    meta = artifacts.create_batch("t", root=root)
    steps = artifacts.stepper(meta["batch_id"], root=root)
    assert all(s["label"] for s in steps)


def test_console_page_has_no_hardcoded_stepper_constant():
    """`journey_console.py` 内嵌的 7 项前端常量 S 必须已删（§9 机械判据）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "server", "gateway", "journey_console.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    assert "const S=[" not in source and "const S = [" not in source


def test_run_level_cells_carry_run_anchor(root):
    """第 7/8 格带 run 锚——多 run 时说得清"哪个 run 亮的"。"""
    meta = artifacts.create_batch("t", root=root)
    bid = meta["batch_id"]
    rid = artifacts.new_run_id()
    rd = artifacts.run_dir(rid, create=True, root=root)
    with open(os.path.join(rd, "receipt.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    with open(os.path.join(rd, "verdicts.jsonl"), "w", encoding="utf-8") as fh:
        fh.write('{"id":"x","verdict":"PASS"}\n')
    meta["run_ids"] = [rid]
    artifacts.save_batch(meta, root=root)

    steps = {s["artifact"]: s for s in artifacts.stepper(bid, root=root)}
    assert steps["run_receipt"]["present"] and steps["run_receipt"]["run_id"] == rid
    assert steps["verdicts"]["present"] and steps["verdicts"]["run_id"] == rid
    # batch 级格子不带 run 锚——带了就是把两个层级混成一个
    assert steps["intake_profile"]["run_id"] == ""


def test_empty_verdicts_file_does_not_light_the_cell(root):
    """verdicts.jsonl 在场但为空 ≠ 有结论。空文件点亮格子是最廉价的假绿。"""
    meta = artifacts.create_batch("t", root=root)
    rid = artifacts.new_run_id()
    rd = artifacts.run_dir(rid, create=True, root=root)
    open(os.path.join(rd, "verdicts.jsonl"), "w").close()
    meta["run_ids"] = [rid]
    artifacts.save_batch(meta, root=root)
    steps = {s["artifact"]: s for s in artifacts.stepper(meta["batch_id"], root=root)}
    assert steps["verdicts"]["present"] is False


def test_dark_cell_explains_itself_and_reasons_are_exclusive(root):
    """灭格点开要给"为什么没有"：前置没产出 → missing_prereq；
    前置齐了还灭 → blocked_reason。两者互斥，不许同时给。"""
    meta = artifacts.create_batch("t", root=root)
    bid = meta["batch_id"]
    steps = {s["artifact"]: s for s in artifacts.stepper(bid, root=root)}
    assert steps["business_frame"]["missing_prereq"] == "intake_profile"
    assert steps["business_frame"]["blocked_reason"] == ""

    artifacts.save_artifact(bid, "intake_profile", {"tier": "standard"}, root=root)
    steps = {s["artifact"]: s for s in artifacts.stepper(bid, root=root)}
    assert steps["business_frame"]["missing_prereq"] == ""
    assert steps["business_frame"]["blocked_reason"]

    for s in artifacts.stepper(bid, root=root):
        assert not (s["missing_prereq"] and s["blocked_reason"]), \
            f"{s['artifact']} 同时给了两种理由——它们是互斥的"


def test_requirement_code_map_is_not_a_tenth_cell():
    """L5 产物是旁挂的，不进九格——进了就把账本变成十格，与界面四段投影对不上。"""
    assert "requirement_code_map" in artifacts.ARTIFACT_FILES
    assert "requirement_code_map" not in artifacts.STEPPER_ORDER
    assert len(artifacts.STEPPER_ORDER) == 9
