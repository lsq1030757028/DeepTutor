"""生成接口的回归测试。

这是**会花钱的那一面**，所以断言集中在三处错了会真出事的地方：
并发闸（多点几下就是几倍账单）、按用户隔离（A 拿不到 B 的结果）、
空态（没配模型不能把 provider 的 401 甩给用户）。

模型全程用替身，不发真请求。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

gen = pytest.importorskip(
    "deeptutor.api.routers.test_workbench_generate",
    reason="只在 fork 仓（有 deeptutor/ 包）里跑；归档仓里跳过",
)

from server.generate import jobs as jobs_mod  # noqa: E402
from server.generate.context import Material  # noqa: E402


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    """每条用例一个干净台账——共用会让并发闸的断言互相污染。"""
    store = jobs_mod.JobStore()
    monkeypatch.setattr(gen, "_STORE", store)
    return store


@pytest.fixture()
def as_alice(monkeypatch):
    monkeypatch.setattr(gen, "_owner", lambda: "alice")


@pytest.fixture()
def material(monkeypatch):
    m = Material(endpoints=[{"method": "POST", "path": "/api/x", "calls": 1}],
                 login=None, stats={}, notes=["少给了 3 个端点"], scrub_hits={})
    monkeypatch.setattr(gen, "_load_material", lambda draft_id: m)
    return m


def _model(monkeypatch, *, ready=True):
    monkeypatch.setattr(gen, "has_usable_model", lambda: ready)


# ── 能力探测：页面靠它决定给入口还是给配置指引 ───────────────────────────

def test_capability_reports_model_readiness(monkeypatch):
    """上游没配模型时**不报错**，页面若靠调用失败判空态，

    用户看到的会是 provider 侧的 401。所以要有这个独立探测。
    """
    _model(monkeypatch, ready=False)
    assert gen.capability()["model_ready"] is False
    _model(monkeypatch, ready=True)
    assert gen.capability()["model_ready"] is True


def test_capability_exposes_the_spend_ceilings():
    """页面要能告诉用户"最多能生成几条"，而不是让他试出来。"""
    limits = gen.capability()["limits"]
    assert limits["max_cases"] > 0
    assert limits["max_model_calls"] > 0


# ── 空态：没模型不发起任务 ───────────────────────────────────────────────

def test_starting_without_a_model_is_rejected_with_guidance(monkeypatch, as_alice, material):
    from fastapi import HTTPException
    _model(monkeypatch, ready=False)
    body = gen.GenerateRequest(draft_id="har-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gen.start_generation(body))
    assert exc.value.status_code == 409
    assert "设置" in str(exc.value.detail), "要给出去哪配的指引，不能只说失败"


# ── 并发闸：多点几下就是几倍账单 ─────────────────────────────────────────

def test_second_job_is_rejected_while_one_is_running(monkeypatch, as_alice, material, clean_store):
    """用户往往是以为"没反应"才多点的。第二次必须直说"已经在跑了"。"""
    from fastapi import HTTPException
    _model(monkeypatch, ready=True)
    job = clean_store.create("alice")
    clean_store.update(job.id, state=jobs_mod.RUNNING)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(gen.start_generation(gen.GenerateRequest(draft_id="har-1")))
    assert exc.value.status_code == 409
    assert "已经有一个" in str(exc.value.detail)


def test_a_finished_job_does_not_block_the_next_one(monkeypatch, as_alice, material, clean_store):
    _model(monkeypatch, ready=True)
    old = clean_store.create("alice")
    clean_store.update(old.id, state=jobs_mod.DONE)

    async def go():
        return await gen.start_generation(gen.GenerateRequest(draft_id="har-1"))

    payload = asyncio.run(go())
    assert payload["state"] in (jobs_mod.PENDING, jobs_mod.RUNNING)


# ── 按用户隔离：A 拿不到 B 的结果 ───────────────────────────────────────

def test_another_owner_gets_404_not_403(monkeypatch, clean_store):
    """403 会泄露"这个 id 是存在的"。对不上就当不存在。"""
    from fastapi import HTTPException
    job = clean_store.create("bob")
    monkeypatch.setattr(gen, "_owner", lambda: "alice")
    with pytest.raises(HTTPException) as exc:
        gen.get_job(job.id)
    assert exc.value.status_code == 404


def test_another_owner_cannot_cancel(monkeypatch, clean_store):
    from fastapi import HTTPException
    job = clean_store.create("bob")
    clean_store.update(job.id, state=jobs_mod.RUNNING)
    monkeypatch.setattr(gen, "_owner", lambda: "alice")
    with pytest.raises(HTTPException) as exc:
        gen.cancel_job(job.id)
    assert exc.value.status_code == 404
    assert clean_store.get(job.id, "bob").cancel_requested is False


def test_owner_can_read_and_cancel_their_own(monkeypatch, as_alice, clean_store):
    job = clean_store.create("alice")
    clean_store.update(job.id, state=jobs_mod.RUNNING)
    assert gen.get_job(job.id)["job_id"] == job.id
    assert gen.cancel_job(job.id)["state"] == jobs_mod.RUNNING
    assert clean_store.get(job.id, "alice").cancel_requested is True


def test_job_projection_never_leaks_owner(monkeypatch, as_alice, clean_store):
    job = clean_store.create("alice")
    assert "owner" not in gen.get_job(job.id)


# ── 起草场景：短，同步返回；错误要翻成对的 HTTP 语义 ─────────────────────

def test_scenario_returns_text_and_material_notes(monkeypatch, material):
    async def fake_draft(call, mat, **kw):
        return "  登录后下单  "
    monkeypatch.setattr(gen, "draft_scenario", fake_draft)
    out = asyncio.run(gen.make_scenario(gen.ScenarioRequest(draft_id="har-1")))
    assert out["scenario"] == "  登录后下单  ".strip() or out["scenario"].strip() == "登录后下单"
    assert any("少给了" in n for n in out["notes"]), "裁剪层说的话要带到页面上"


@pytest.mark.parametrize("exc_type,code", [
    ("ModelUnavailable", 409),
    ("ModelTimeout", 504),
])
def test_model_errors_map_to_meaningful_status_codes(monkeypatch, material, exc_type, code):
    """把模型侧的失败翻成 HTTP 语义，页面才知道该提示"去配置"还是"再试一次"。"""
    from fastapi import HTTPException
    from server.generate import model as model_mod

    err = getattr(model_mod, exc_type)("坏了")

    async def boom(call, mat, **kw):
        raise err
    monkeypatch.setattr(gen, "draft_scenario", boom)

    with pytest.raises(HTTPException) as got:
        asyncio.run(gen.make_scenario(gen.ScenarioRequest(draft_id="har-1")))
    assert got.value.status_code == code


# ── 用户改过的场景优先 ───────────────────────────────────────────────────

def test_user_scenario_is_passed_through_untouched(monkeypatch, as_alice, material):
    """改过的必须优先——重新起草会把用户的修改悄悄冲掉。"""
    seen = {}

    async def fake_generate(call, mat, **kw):
        seen.update(kw)
        return types.SimpleNamespace(
            scenario=kw.get("scenario") or "模型起草的", picked_endpoints=[],
            skipped_reason="", cases=[], notes=[], calls_used=1, complete=True)

    _model(monkeypatch, ready=True)
    monkeypatch.setattr(gen, "generate", fake_generate)

    async def go():
        payload = await gen.start_generation(
            gen.GenerateRequest(draft_id="har-1", scenario="我自己写的"))
        for _ in range(50):          # 让出控制权，等后台任务跑完
            await asyncio.sleep(0)
            if seen:
                break
        return payload

    asyncio.run(go())
    assert seen.get("scenario") == "我自己写的"


def test_login_request_flows_from_material_through_job_to_adopted_delivery(
        monkeypatch, as_alice, clean_store, tmp_path):
    """已脱敏登录元数据不能在生成任务与采纳批次之间断掉。"""
    login_request = {
        "method": "POST",
        "url": "https://api.example.test/login",
        "headers": {"content-type": "application/json"},
        "body": {
            "raw": '{"username":"{{login_username}}",'
                   '"password":"{{login_password}}"}',
        },
        "token_extract": {"from": "json", "path": "$.data.token"},
    }
    material = Material(
        endpoints=[{"method": "POST", "path": "/api/x", "calls": 1}],
        login=login_request,
        stats={},
        notes=[],
        scrub_hits={"username": 1, "password": 1},
    )
    monkeypatch.setattr(gen, "_load_material", lambda draft_id: material)
    _model(monkeypatch, ready=True)

    async def fake_generate(call, mat, **kw):
        assert mat.login == login_request
        return types.SimpleNamespace(
            scenario="登录后查询", picked_endpoints=[], skipped_reason="",
            cases=[{
                "id": "TC-001",
                "title": "查询",
                "request": {"method": "GET", "url": "/api/x"},
                "assertions": [{"kind": "status", "expect": 200}],
            }],
            notes=[], calls_used=1, complete=True,
        )

    monkeypatch.setattr(gen, "generate", fake_generate)

    async def finish_job():
        payload = await gen.start_generation(gen.GenerateRequest(draft_id="har-1"))
        for _ in range(50):
            await asyncio.sleep(0)
            job = clean_store.get(payload["job_id"], "alice")
            if job and job.state == jobs_mod.DONE:
                return job
        raise AssertionError("后台生成任务没有完成")

    job = asyncio.run(finish_job())
    assert job.result["login_request"] == login_request

    seen = {}
    from server import delivery as delivery_mod

    def fake_save(cases, **kw):
        seen["login_request"] = kw.get("login_request")
        return {"ok": True, "delivery_dir": str(tmp_path)}

    monkeypatch.setattr(delivery_mod, "save_delivery", fake_save)
    monkeypatch.setattr(
        "deeptutor.api.routers.test_workbench_paths.deliveries_root",
        lambda: str(tmp_path))

    gen.adopt_cases(job.id, gen.AdoptRequest(case_ids=["TC-001"]))
    assert seen["login_request"] == login_request


# ── 采纳：勾选是入库的唯一闸门，服务端也要守 ─────────────────────────────

def _done_job(store, owner="alice", n=3):
    job = store.create(owner)
    store.update(job.id, state=jobs_mod.DONE, result={
        "cases": [{"id": f"TC-{i:03d}", "title": f"用例{i}",
                   "request": {"method": "GET", "url": "/api/x"},
                   "assertions": [{"kind": "status", "expect": 200}]}
                  for i in range(1, n + 1)],
        "notes": [], "calls_used": 2, "complete": True,
    })
    return job


def test_adopt_only_writes_the_checked_cases(monkeypatch, as_alice, clean_store, tmp_path):
    """生成 ≠ 入库。页面上的复选框只是这道闸的投影——

    绕开页面直接打接口也必须越不过去。
    """
    job = _done_job(clean_store, n=3)
    seen = {}

    from server import delivery as delivery_mod

    def fake_save(cases, **kw):
        seen["cases"] = cases
        seen["out_root"] = kw.get("out_root")
        return {"ok": True, "delivery_dir": str(tmp_path)}

    monkeypatch.setattr(delivery_mod, "save_delivery", fake_save)
    monkeypatch.setattr(
        "deeptutor.api.routers.test_workbench_paths.deliveries_root",
        lambda: str(tmp_path))

    out = gen.adopt_cases(job.id, gen.AdoptRequest(case_ids=["TC-001", "TC-003"]))
    assert out["adopted"] == 2
    assert [c["id"] for c in seen["cases"]] == ["TC-001", "TC-003"]


def test_adopt_writes_into_the_current_users_directory(monkeypatch, as_alice,
                                                       clean_store, tmp_path):
    """不传 out_root 的话会落到仓库根的共享目录，0009 的隔离就是假的。"""
    job = _done_job(clean_store)
    seen = {}

    from server import delivery as delivery_mod
    monkeypatch.setattr(delivery_mod, "save_delivery",
                        lambda cases, **kw: seen.update(kw) or {"ok": True})
    monkeypatch.setattr(
        "deeptutor.api.routers.test_workbench_paths.deliveries_root",
        lambda: str(tmp_path / "alice"))

    gen.adopt_cases(job.id, gen.AdoptRequest(case_ids=["TC-001"]))
    assert seen["out_root"] == str(tmp_path / "alice")


def test_adopting_nothing_is_rejected(monkeypatch, as_alice, clean_store):
    """"一条都没勾"多半是误点，不是"我想建个空批次"。"""
    from fastapi import HTTPException
    job = _done_job(clean_store)
    with pytest.raises(HTTPException) as exc:
        gen.adopt_cases(job.id, gen.AdoptRequest(case_ids=[]))
    assert exc.value.status_code == 400


def test_adopting_an_unknown_id_is_an_error_not_a_silent_skip(monkeypatch, as_alice,
                                                              clean_store):
    """勾了不存在的编号多半是页面与结果对不上了。

    悄悄少存几条比报错更难查——用户会以为都存进去了。
    """
    from fastapi import HTTPException
    job = _done_job(clean_store, n=2)
    with pytest.raises(HTTPException) as exc:
        gen.adopt_cases(job.id, gen.AdoptRequest(case_ids=["TC-001", "TC-999"]))
    assert exc.value.status_code == 400
    assert "TC-999" in str(exc.value.detail)


def test_cannot_adopt_from_an_unfinished_job(monkeypatch, as_alice, clean_store):
    from fastapi import HTTPException
    job = clean_store.create("alice")
    clean_store.update(job.id, state=jobs_mod.RUNNING)
    with pytest.raises(HTTPException) as exc:
        gen.adopt_cases(job.id, gen.AdoptRequest(case_ids=["TC-001"]))
    assert exc.value.status_code == 409


def test_cannot_adopt_from_someone_elses_job(monkeypatch, clean_store):
    from fastapi import HTTPException
    job = _done_job(clean_store, owner="bob")
    monkeypatch.setattr(gen, "_owner", lambda: "alice")
    with pytest.raises(HTTPException) as exc:
        gen.adopt_cases(job.id, gen.AdoptRequest(case_ids=["TC-001"]))
    assert exc.value.status_code == 404
