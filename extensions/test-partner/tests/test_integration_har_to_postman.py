"""集成测试：sample.har 走一遍 parse → 填 request 块 → validate → save 双产物。

这条链路就是 `skills/har-testing/SKILL.md` 的工序，只是把「模型写用例」换成
「测试代码按体检报告的样例机械填充」——校验器、导出器、脱敏三道闸照常全跑。
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from server import case_validate, delivery, har_parse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SAMPLE_HAR = os.path.join(FIXTURES, "sample.har")

#: 与 test_har_parse.SECRETS 同一份——产物里一个字都不许出现
SECRETS = (
    "eyJhbGciOiJIUzI1NiJ9.SUPERSECRETTOKENVALUE0123456789.sigsigsig",
    "SUPERSECRETTOKENVALUE0123456789",
    "P@ssw0rd-secret-12345",
    "tk-9f8e7d6c5b4a39281706",
    "ABCDEF0123456789SESSIONVAL",
    "aG9sZFRoaXNTZWNyZXRUaWNrZXQxMjM0NQ",
)

#: 挑三个端点写用例（模块名取体检报告的业务域）
PICKED = [
    ("POST", "/api/v1/auth/login", "auth", "用户名口令登录成功换取令牌", "高",
     [{"type": "status", "expected": 200},
      {"type": "json_path", "path": "$.code", "expected": 0}]),
    ("GET", "/api/v1/orders", "orders", "已登录用户分页查询订单列表成功", "高",
     [{"type": "status", "expected": 200},
      {"type": "body_contains", "expected": "\"code\":0"}]),
    ("POST", "/api/v1/orders/:id/refund", "orders", "订单状态不允许退款时退款被拒", "中",
     [{"type": "status", "expected": 400},
      {"type": "json_path", "path": "$.code", "expected": 40001}]),
]


def _endpoint(report, method, path):
    return next(ep for ep in report["endpoints"]
                if ep["method"] == method and ep["path"] == path)


def _case_from_sample(index, method, path, module, title, priority, assertions, report):
    """按体检报告的样例填 request 块——只用样例里出现过的字段，不编造。"""
    ep = _endpoint(report, method, path)
    sample = ep["sample"]
    headers = [{"key": h["key"],
                # 凭证头的值在样例里是 <redacted>，落用例时换成变量占位
                "value": "{{token}}" if h["value"] == har_parse.REDACTED else h["value"]}
               for h in sample["headers"]]
    url = "{{baseUrl}}" + ep["path"]
    if sample["query_keys"]:
        url += "?" + "&".join(f"{k}=1" for k in sample["query_keys"])
    request = {"method": sample["method"], "url": url, "headers": headers,
               "assertions": assertions}
    if sample["body"]["mode"] == "raw":
        request["body"] = {"mode": "raw", "language": sample["body"]["language"],
                           "raw": sample["body"]["raw"]}
    return {
        "编号": f"TC-{index:03d}",
        "标题": title,
        "前置条件": "无" if module == "auth" else "已登录并持有效 token",
        "操作步骤": [f"调用 {method} {ep['path']}", "检查响应状态码与业务码"],
        "预期结果": "；".join(
            f"状态码 {a['expected']}" if a["type"] == "status"
            else f"{a.get('path', '响应体')} 为 {a['expected']}" for a in assertions),
        "优先级": priority,
        "所属模块": module,
        "用例类型": "接口测试",
        "测试数据": ", ".join(f"{k}=1" for k in sample["query_keys"]) or "无",
        "关联端点": [f"{method} {ep['path']}"],
        "request": request,
    }


@pytest.fixture(scope="module")
def pipeline():
    """跑完整条链路一次，各断言共用产物。"""
    har, source = har_parse.load_har(har_path=SAMPLE_HAR)
    report = har_parse.build_report(har, source)
    cases = [_case_from_sample(i, *picked, report)
             for i, picked in enumerate(PICKED, 1)]
    validation = case_validate.validate_cases(
        cases, report["endpoints"],
        sensitive_values=har_parse.collect_sensitive_values(har))
    return {"report": report, "cases": cases, "validation": validation,
            "fingerprint": source["fingerprint"]}


@pytest.fixture
def saved(pipeline, tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "DELIVERIES_DIR", str(tmp_path / "deliveries"))
    return delivery.save_delivery(pipeline["cases"], title="订单域接口用例",
                                  source_fingerprint=pipeline["fingerprint"])


# ── 体检 → 用例 ─────────────────────────────────────────────────────────────

def test_report_gives_enough_material(pipeline):
    report = pipeline["report"]
    assert report["ok"] is True
    assert report["replay"]["grade"] == "A"
    for method, path, *_ in PICKED:
        assert _endpoint(report, method, path)["sample"]["method"] == method


def test_cases_validate_clean(pipeline):
    validation = pipeline["validation"]
    assert validation["ok"] is True, validation["errors"]
    assert validation["summary"]["cases_with_request"] == 3
    assert validation["summary"]["warning_count"] == len(
        [w for w in validation["warnings"] if w["code"] == "W04_ENDPOINT_UNCOVERED"])


def test_coverage_matches_picked_endpoints(pipeline):
    covered = set(pipeline["validation"]["coverage"]["covered"])
    assert {"POST /api/v1/auth/login", "GET /api/v1/orders",
            "POST /api/v1/orders/:id/refund"} <= covered


def test_login_case_body_came_from_sample_and_is_masked(pipeline):
    login = pipeline["cases"][0]["request"]
    payload = json.loads(login["body"]["raw"])
    assert payload["username"] == "alice"                  # 样例里的真实字段名
    assert payload["password"] == har_parse.REDACTED       # 口令值不回填
    assert login["headers"] == [{"key": "Content-Type", "value": "application/json"}]


# ── 落盘双产物 ──────────────────────────────────────────────────────────────

def test_delivery_writes_both_products(saved):
    assert saved["ok"] is True and saved["format"] == "xlsx+postman"
    names = [os.path.basename(p) for p in saved["files"]]
    assert names == ["cases.xlsx", "订单域接口用例.postman_collection.json",
                     "cases.json", "receipt.json"]
    assert saved["validation_ok"] is True
    assert saved["case_count"] == 3


def test_receipt_records_both_artifacts_with_hashes(saved):
    with open(os.path.join(saved["delivery_dir"], "receipt.json"), encoding="utf-8") as f:
        receipt = json.load(f)
    assert receipt["source_fingerprint"].startswith("sha256:")
    assert len(receipt["artifacts"]) == 2
    for art in receipt["artifacts"]:
        blob = open(os.path.join(saved["delivery_dir"], art["file"]), "rb").read()
        assert art["sha256"] == hashlib.sha256(blob).hexdigest()
    assert receipt["postman"]["item_count"] == 3
    assert receipt["postman"]["placeholder_count"] == 0
    assert receipt["postman"]["assertion_count"] == 6


def test_collection_is_importable_shape(saved):
    with open(saved["postman_file"], encoding="utf-8") as f:
        collection = json.load(f)
    assert collection["info"]["schema"].endswith("v2.1.0/collection.json")
    folders = {f["name"]: f["item"] for f in collection["item"]}
    assert set(folders) == {"auth", "orders"}
    assert len(folders["orders"]) == 2
    login = folders["auth"][0]
    assert login["request"]["method"] == "POST"
    assert login["request"]["url"]["path"] == ["api", "v1", "auth", "login"]
    assert login["request"]["body"]["options"]["raw"]["language"] == "json"
    script = "\n".join(login["event"][0]["script"]["exec"])
    assert "pm.response.to.have.status(200);" in script
    assert 'pm.expect(jsonData["code"]).to.eql(0);' in script


def test_no_secret_reaches_any_text_artifact(saved):
    """脱敏硬约束贯穿到交付物：collection 与收据里一个凭证值都不许有。"""
    for name in ("订单域接口用例.postman_collection.json", "receipt.json"):
        text = open(os.path.join(saved["delivery_dir"], name), encoding="utf-8").read()
        for secret in SECRETS:
            assert secret not in text, f"{name} 泄漏了 {secret[:12]}..."
