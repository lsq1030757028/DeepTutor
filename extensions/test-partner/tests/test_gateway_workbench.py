"""工作台后端的离线测试：批次扫描、用例读取、执行台账。

**本文件不发任何真实请求**：执行器换成假件（`FakeExecutor`），它只记下自己被
拿什么参数调过，顺便按脚本回一个 `execute_cases` 形状的结果。真执行器有自己的
测试文件（`tests/test_execute.py`，那边用 MockTransport 拦在传输层）。

两条断言在这里最重要：
1. 路径穿越进不来（批次 id 只能是 `deliveries/` 下的一级目录名）；
2. 旧批次（没有 `cases.json`）**明说降级**，不反推、不假装能执行。
"""

from __future__ import annotations

import json
import os
import time

import pytest

from server import delivery
from server.gateway import workbench
from server.gateway.workbench import RunRegistry, WorkbenchError

CASES = [
    {"编号": "TC-001", "标题": "分页查询订单列表成功", "前置条件": "已登录",
     "操作步骤": ["调用 GET /api/v1/orders"], "预期结果": "200", "优先级": "高",
     "所属模块": "订单",
     "request": {"method": "GET", "url": "{{baseUrl}}/api/v1/orders",
                 "headers": [{"key": "Authorization", "value": "Bearer {{token}}"}],
                 "assertions": [{"type": "status", "expected": 200}]}},
    {"编号": "TC-002", "标题": "人工核对对账单", "前置条件": "无",
     "操作步骤": ["打开后台"], "预期结果": "数字一致", "优先级": "中"},
]


@pytest.fixture()
def root(tmp_path, monkeypatch):
    """临时 deliveries 根。落盘走真的 `save_delivery`，产物形状与线上同源。"""
    base = tmp_path / "deliveries"
    base.mkdir()
    monkeypatch.setattr(delivery, "DELIVERIES_DIR", str(base))
    return str(base)


def make_batch(root, title="订单域用例", cases=None, fmt="xlsx+postman"):
    result = delivery.save_delivery(cases if cases is not None else CASES,
                                    title=title, fmt=fmt)
    assert result["ok"] is True, result
    # 同秒落两批会撞名，`save_delivery` 自己会加后缀，这里只要保证时间戳不同
    time.sleep(0.01)
    return os.path.basename(result["delivery_dir"])


def make_legacy_batch(root, name="20260101-090000-旧批次"):
    """0.7.0 之前的批次：有 receipt.json 与产物，就是没有 cases.json。"""
    path = os.path.join(root, name)
    os.makedirs(path)
    with open(os.path.join(path, "receipt.json"), "w", encoding="utf-8") as f:
        json.dump({"title": "旧批次", "generated_at": "2026-01-01 09:00:00",
                   "case_count": 12, "format": "xlsx"}, f, ensure_ascii=False)
    with open(os.path.join(path, "cases.xlsx"), "wb") as f:
        f.write(b"not-a-real-xlsx")
    return name


# ── 批次扫描 ────────────────────────────────────────────────────────────────

def test_missing_deliveries_dir_is_an_empty_state_not_an_error(tmp_path):
    payload = workbench.scan_deliveries(str(tmp_path / "nope"))
    assert payload["ok"] is True
    assert payload["deliveries"] == []
    assert "还没有" in payload["empty_reason"]


def test_empty_deliveries_dir_says_what_to_do(root):
    payload = workbench.scan_deliveries(root)
    assert payload["deliveries"] == []
    assert "save_delivery" in payload["empty_reason"]


def test_scan_lists_batches_newest_first(root):
    old = make_legacy_batch(root)
    fresh = make_batch(root)
    ids = [d["id"] for d in workbench.scan_deliveries(root)["deliveries"]]
    assert ids == [fresh, old]                 # 2026-08-xx 排在 2026-01-01 前面


def test_scan_reads_meta_from_the_receipt(root):
    make_batch(root, title="订单域用例")
    row = workbench.scan_deliveries(root)["deliveries"][0]
    assert row["title"] == "订单域用例"
    assert row["case_count"] == 2
    assert row["format"] == "xlsx+postman"
    assert row["validation_ok"] is True
    assert row["structured"] is True           # cases.json 在
    assert row["executed"] is False
    names = [f["name"] for f in row["files"]]
    assert "cases.json" in names and "receipt.json" in names
    assert all(os.path.isabs(f["path"]) for f in row["files"])


def test_a_broken_receipt_does_not_take_the_page_down(root):
    name = make_batch(root)
    with open(os.path.join(root, name, "receipt.json"), "w", encoding="utf-8") as f:
        f.write("{ 这不是 JSON")
    row = workbench.scan_deliveries(root)["deliveries"][0]
    assert row["receipt_broken"] is True
    assert row["title"] == name                # 退回目录名，不报错
    assert row["structured"] is True           # cases.json 还在，照样能执行


def test_a_batch_without_a_receipt_still_shows_up(root):
    os.makedirs(os.path.join(root, "20260101-000000-裸目录"))
    rows = workbench.scan_deliveries(root)["deliveries"]
    assert [r["id"] for r in rows] == ["20260101-000000-裸目录"]
    assert rows[0]["receipt_ok"] is False
    assert rows[0]["structured"] is False


def test_previous_execution_shows_on_the_batch(root):
    name = make_batch(root)
    with open(os.path.join(root, name, "execution_report.json"), "w",
              encoding="utf-8") as f:
        json.dump({"executed_at": "2026-08-05 12:00:00", "environment": "测试环境",
                   "summary": {"passed": 3, "failed": 1, "skipped": 0,
                               "verdict": "3 过 1 败"}}, f, ensure_ascii=False)
    row = workbench.scan_deliveries(root)["deliveries"][0]
    assert row["executed"] is True
    assert row["last_execution"]["verdict"] == "3 过 1 败"
    assert row["last_execution"]["environment"] == "测试环境"


# ── 批次 id 的安全闸 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "   ", "..", "../config", "..\\config", "a/b", "a\\b",
    "/etc/passwd", "C:\\Windows",
])
def test_path_traversal_is_refused(bad):
    with pytest.raises(WorkbenchError) as exc:
        workbench.safe_delivery_id(bad)
    assert exc.value.code == "BAD_DELIVERY_ID"


def test_a_normal_chinese_batch_id_passes(root):
    name = make_batch(root, title="订单域用例")
    assert workbench.safe_delivery_id(name) == name


# ── 用例读取 ────────────────────────────────────────────────────────────────

def test_read_delivery_returns_a_case_table(root):
    name = make_batch(root)
    payload = workbench.read_delivery(name, root)
    assert payload["ok"] is True and payload["structured"] is True
    assert payload["case_count"] == 2
    assert payload["executable_count"] == 1          # TC-002 没有 request 块
    assert payload["modules"] == ["订单", "未分类"] or payload["modules"] == ["订单"]
    first, second = payload["cases"]
    assert first["case_id"] == "TC-001"
    assert first["title"] == "分页查询订单列表成功"
    assert first["priority"] == "高"
    assert first["executable"] is True
    assert first["assertion_count"] == 1
    assert second["executable"] is False
    assert second["request"] is None


def test_the_credential_placeholder_is_shown_verbatim_never_resolved(root):
    """凭据"放在哪"看得见，凭据"是什么"看不见——页面上就该是 `{{token}}` 原样。"""
    name = make_batch(root)
    case = workbench.read_delivery(name, root)["cases"][0]
    header = case["request"]["headers"][0]
    assert header["value"] == "Bearer {{token}}"


def test_an_old_batch_degrades_out_loud(root):
    name = make_legacy_batch(root)
    payload = workbench.read_delivery(name, root)
    assert payload["ok"] is True
    assert payload["structured"] is False
    assert payload["cases"] == []
    assert payload["degraded"]["code"] == "NO_CASES_JSON"
    assert "cases.json" in payload["degraded"]["message"]
    # 降级归降级，产物路径照给——用户还是能拿到文件
    assert [f["name"] for f in payload["delivery"]["files"]] == \
        ["cases.xlsx", "receipt.json"]


def test_a_broken_cases_json_degrades_too(root):
    name = make_batch(root)
    with open(os.path.join(root, name, "cases.json"), "w", encoding="utf-8") as f:
        f.write("[[[")
    payload = workbench.read_delivery(name, root)
    assert payload["structured"] is False
    assert payload["degraded"]["code"] == "CASES_JSON_BROKEN"


def test_reading_an_unknown_batch_is_a_named_error(root):
    with pytest.raises(WorkbenchError) as exc:
        workbench.read_delivery("20260101-000000-不存在", root)
    assert exc.value.code == "DELIVERY_NOT_FOUND"


# ── 执行台账 ────────────────────────────────────────────────────────────────

class FakeExecutor:
    """假执行器：记参数、按脚本回结果、可选地喂几帧进度。一个请求都不发。"""

    def __init__(self, result=None, progress_frames=None, raises=None, block=None):
        self.calls: list[dict] = []
        self.result = result
        self.progress_frames = progress_frames or []
        self.raises = raises
        self.block = block          # threading.Event：卡住执行，测"执行中"状态

    def __call__(self, cases, **kwargs):
        self.calls.append({"cases": cases, **kwargs})
        for frame in self.progress_frames:
            kwargs["progress"](frame)
        if self.block is not None:
            self.block.wait(5)
        if self.raises is not None:
            raise self.raises
        return self.result if self.result is not None else {
            "ok": True,
            "summary": {"total": 1, "executed": 1, "passed": 1, "failed": 0,
                        "skipped": 0, "verdict": "1 过 0 败；全部通过"},
            "results": [{"case_id": "TC-001", "name": "分页查询订单列表成功",
                         "passed": True, "skipped": False, "status_code": 200,
                         "elapsed_ms": 12, "assertions": []}],
            "environment": kwargs.get("env", ""),
            "base_url_host": "api-test.example.com:443",
            "report_dir": "/tmp/report", "files": [],
            "report_hint": "执行报告已落盘",
        }


def wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_run_goes_through_the_real_executor_contract(root):
    name = make_batch(root)
    fake = FakeExecutor()
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    run = runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"])
    # 假执行器快到可能在 start() 返回前就跑完了，所以这里不断言 "running"——
    # 断言"这一轮确实建起来了"，运行中的形态由 test_a_running_run_reports_where_it_is 盯。
    assert run["run_id"] and run["total"] == 1 and run["delivery_id"] == name
    assert wait_until(lambda: runs.get(run["run_id"])["state"] == "done")

    call = fake.calls[0]
    assert call["env"] == "测试环境"                     # 传的是环境名，不是凭据
    assert call["case_ids"] == ["TC-001"]
    assert call["delivery_dir"] == os.path.join(root, name)   # 报告并进批次目录
    assert call["title"] == "订单域用例"
    assert [c["case_id"] for c in call["cases"]] == ["TC-001", "TC-002"]
    assert "variables" not in call                       # 值从来不经过工作台

    done = runs.get(run["run_id"])
    assert done["result"]["summary"]["passed"] == 1
    assert done["finished_at"]


def test_progress_frames_land_on_the_run(root):
    name = make_batch(root)
    fake = FakeExecutor(progress_frames=[
        {"done": 0, "total": 2, "current_case_id": "TC-001", "current_title": "第一条"},
        {"done": 1, "total": 2, "current_case_id": "TC-002", "current_title": "第二条"},
    ])
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    run = runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001", "TC-002"])
    assert wait_until(lambda: runs.get(run["run_id"])["state"] == "done")
    # 收工后 done 归到 summary.total，过程帧在执行中被读走（见下一个测试）
    assert runs.get(run["run_id"])["done"] == 1


def test_a_running_run_reports_where_it_is(root):
    import threading

    name = make_batch(root)
    gate = threading.Event()
    fake = FakeExecutor(block=gate, progress_frames=[
        {"done": 1, "total": 3, "current_case_id": "TC-002", "current_title": "第二条"}])
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    run = runs.start(delivery_id=name, env="测试环境",
                     case_ids=["TC-001", "TC-002", "TC-003"])
    assert wait_until(lambda: runs.get(run["run_id"])["done"] == 1)
    live = runs.get(run["run_id"])
    assert live["state"] == "running"
    assert live["total"] == 3
    assert live["current_case_id"] == "TC-002"
    gate.set()
    assert wait_until(lambda: runs.get(run["run_id"])["state"] == "done")


def test_a_second_run_on_the_same_batch_is_refused_while_one_is_live(root):
    import threading

    name = make_batch(root)
    gate = threading.Event()
    runs = RunRegistry(executor=FakeExecutor(block=gate), deliveries_root_dir=root)
    runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"])
    with pytest.raises(WorkbenchError) as exc:
        runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"])
    assert exc.value.code == "RUN_IN_PROGRESS"
    gate.set()


def test_an_executor_that_refuses_shows_up_as_an_error_run(root):
    name = make_batch(root)
    fake = FakeExecutor(result={"ok": False, "error": "ENV_NOT_FOUND",
                                "message": "没有名为「打错了」的测试环境。"})
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    run = runs.start(delivery_id=name, env="打错了", case_ids=["TC-001"])
    assert wait_until(lambda: runs.get(run["run_id"])["state"] == "error")
    assert "测试环境" in runs.get(run["run_id"])["error"]


def test_an_exploding_executor_does_not_hang_the_run(root):
    name = make_batch(root)
    runs = RunRegistry(executor=FakeExecutor(raises=RuntimeError("炸了")),
                       deliveries_root_dir=root)
    run = runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"])
    assert wait_until(lambda: runs.get(run["run_id"])["state"] == "error")
    assert "RuntimeError: 炸了" in runs.get(run["run_id"])["error"]


@pytest.mark.parametrize("kwargs,code", [
    ({"env": "", "case_ids": ["TC-001"]}, "ENV_REQUIRED"),
    ({"env": "测试环境", "case_ids": []}, "NO_CASE_SELECTED"),
    ({"env": "测试环境", "case_ids": ["  "]}, "NO_CASE_SELECTED"),
])
def test_the_gate_runs_before_anything_is_sent(root, kwargs, code):
    name = make_batch(root)
    fake = FakeExecutor()
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    with pytest.raises(WorkbenchError) as exc:
        runs.start(delivery_id=name, **kwargs)
    assert exc.value.code == code
    assert fake.calls == []                    # 执行器压根没被调到


def test_an_old_batch_cannot_be_executed(root):
    name = make_legacy_batch(root)
    fake = FakeExecutor()
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    with pytest.raises(WorkbenchError) as exc:
        runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"])
    assert exc.value.code == "NO_CASES_JSON"
    assert fake.calls == []


def test_unknown_runs_are_none(root):
    assert RunRegistry(executor=FakeExecutor(),
                       deliveries_root_dir=root).get("run-nope") is None


def test_old_runs_are_evicted(root):
    name = make_batch(root)
    runs = RunRegistry(executor=FakeExecutor(), deliveries_root_dir=root)
    ids = []
    for _ in range(workbench.MAX_RUNS_KEPT + 3):
        run = runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"])
        assert wait_until(lambda r=run: runs.get(r["run_id"]) is None
                          or runs.get(r["run_id"])["state"] == "done")
        ids.append(run["run_id"])
    assert runs.get(ids[-1]) is not None
    assert runs.get(ids[0]) is None


# ── 鉴权方式（0.8）：页面上的两个选项 → execute_cases 的 auth 参数 ───────────
#
# 工作台仍然一个凭据值都碰不到：登录换新要的账密在配置页的环境变量表里，
# 本模块只负责把「批次里那份登录请求描述」和「用户选的模式」递给执行层。

LOGIN_SPEC = {
    "method": "POST",
    "url": "{{baseUrl}}/api/v1/auth/login",
    "path": "/api/v1/auth/login",
    "body": {"mode": "raw", "language": "json",
             "raw": '{"username": "{{login_username}}", '
                    '"password": "{{login_password}}"}'},
    "token_extract": {"source": "json_body", "path": "$.data.token"},
}


def make_login_batch(root, title="带登录的批次"):
    result = delivery.save_delivery(CASES, title=title, fmt="csv",
                                    login_request=LOGIN_SPEC)
    assert result["ok"] is True, result
    time.sleep(0.01)
    return os.path.basename(result["delivery_dir"])


def test_detail_offers_login_refresh_when_the_batch_carries_a_login_request(root):
    name = make_login_batch(root)
    offer = workbench.read_delivery(name, root)["auth_refresh"]
    assert offer["available"] is True
    assert offer["endpoint"] == "POST /api/v1/auth/login"
    assert "login_username" in offer["reason"]


def test_detail_says_why_login_refresh_is_unavailable(root):
    name = make_batch(root)                       # 落盘时没带 login_request
    offer = workbench.read_delivery(name, root)["auth_refresh"]
    assert offer["available"] is False
    assert "没带登录请求描述" in offer["reason"]


def test_the_login_request_itself_does_not_go_to_the_page(root):
    """页面只拿到"能不能选 + 端点"，那份描述留在网关侧。"""
    name = make_login_batch(root)
    payload = workbench.read_delivery(name, root)
    assert "login_request" not in payload
    blob = json.dumps(payload, ensure_ascii=False)
    assert "token_extract" not in blob
    assert LOGIN_SPEC["body"]["raw"] not in blob
    # 保留变量名当然要说给用户听（那是"去哪儿配"），只是描述本身不下发
    assert "login_username" in payload["auth_refresh"]["reason"]


def test_default_auth_mode_is_env(root):
    name = make_batch(root)
    fake = FakeExecutor()
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    run = runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"])
    assert wait_until(lambda: runs.get(run["run_id"])["state"] == "done")
    assert fake.calls[0]["auth"] == "env"
    assert fake.calls[0]["login_request"] is None
    assert run["auth"] == "env"


def test_login_mode_hands_the_batch_login_request_to_the_executor(root):
    name = make_login_batch(root)
    fake = FakeExecutor()
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    run = runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"],
                     auth="login")
    assert wait_until(lambda: runs.get(run["run_id"])["state"] == "done")
    call = fake.calls[0]
    assert call["auth"] == "login"
    assert call["login_request"] == LOGIN_SPEC
    assert call["env"] == "测试环境"
    assert "variables" not in call                 # 账密仍然不经过工作台
    assert run["auth"] == "login"


def test_login_mode_on_a_batch_without_a_login_request_refuses_to_start(root):
    """点了没反应最糟：这里直接不给起跑，并说清缺什么、怎么补。"""
    name = make_batch(root)
    fake = FakeExecutor()
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    with pytest.raises(WorkbenchError) as exc:
        runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"], auth="login")
    assert exc.value.code == "NO_LOGIN_REQUEST"
    assert "跟随环境" in str(exc.value)
    assert fake.calls == []                        # 一轮都没建，线程也没起


def test_an_unknown_auth_mode_is_refused_at_the_endpoint(root):
    """页面上只有两个选项，但端点不能依赖页面——curl 也打得进来。"""
    name = make_login_batch(root)
    fake = FakeExecutor()
    runs = RunRegistry(executor=fake, deliveries_root_dir=root)
    with pytest.raises(WorkbenchError) as exc:
        runs.start(delivery_id=name, env="测试环境", case_ids=["TC-001"], auth="none")
    assert exc.value.code == "BAD_AUTH_MODE"
    assert fake.calls == []
