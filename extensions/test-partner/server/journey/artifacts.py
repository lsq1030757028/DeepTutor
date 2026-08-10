# -*- coding: utf-8 -*-
"""artifacts — 批次（batch）状态对象与类型化产物存储。

组合约束（设计稿 §1）：批次是唯一状态对象，聊天与工作台是它的两个投影；
牙挂在产物上——产物写入即校验，闸不过不落盘（fail-closed）。

目录形态（trace/产物落仓内 data/，拍板卡 3；`data/` 已在 .gitignore）::

    data/test-workbench/batches/<batch_id>/
        batch.json               批次元信息（id/title/owner/base_url/环境引用）
        intake_profile.json      ingest 产物
        business_frame.json      clarify 产物
        test_analysis.json       analyze 产物
        case_draft.json          draft 产物
        approved_caseset.json    adopt 产物（双 digest 冻结）
        coverage_ledger.json     coverage 产物
        bundle/                  compile 产物（pytest+Playwright 工程）
        events.jsonl             批次事件流（append-only：人闸确认、写确认收据……）
    data/test-workbench/runs/<run_id>/
        receipt.json / evidence/ / evidence-bundle.json / verdicts.jsonl / ...
        <case_id>/trace.zip      Playwright trace（viewer 双路径按 ADR-M1-02）

产物文件公共信封字段：artifact / schema_version / batch_id / created_at。
stepper 状态由**产物存在性推导**（presence-derived），不另存进度字段——
避免第二真相（SSOT「单一状态双视图」）。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKBENCH_ROOT = os.path.join(REPO_ROOT, "data", "test-workbench")
BATCHES_ROOT = os.path.join(WORKBENCH_ROOT, "batches")
RUNS_ROOT = os.path.join(WORKBENCH_ROOT, "runs")

#: 产物注册表：kind -> 文件名。顺序即 stepper 展示顺序（产物账本）。
ARTIFACT_FILES: dict[str, str] = {
    "intake_profile": "intake_profile.json",
    "business_frame": "business_frame.json",
    "test_analysis": "test_analysis.json",
    "case_draft": "case_draft.json",
    "approved_caseset": "approved_caseset.json",
    "coverage_ledger": "coverage_ledger.json",
}

_BATCH_ID_RE = re.compile(r"^b-[0-9]{8}-[a-z0-9]{6,}$")
_RUN_ID_RE = re.compile(r"^r-[0-9]{8}-[a-z0-9]{6,}$")


class ArtifactError(ValueError):
    """产物读写与校验错误（信息可直接回给调用方）。"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def new_batch_id() -> str:
    return f"b-{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}"


def new_run_id() -> str:
    return f"r-{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}"


def _safe_id(value: str, pattern: re.Pattern[str], label: str) -> str:
    """id 只认白名单形态——它会拼进文件路径，这里就是路径逃逸的闸。"""
    if not isinstance(value, str) or not pattern.match(value):
        raise ArtifactError(f"{label} 形态非法：{value!r}")
    return value


def batch_dir(batch_id: str, *, create: bool = False) -> str:
    d = os.path.join(BATCHES_ROOT, _safe_id(batch_id, _BATCH_ID_RE, "batch_id"))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def run_dir(run_id: str, *, create: bool = False) -> str:
    d = os.path.join(RUNS_ROOT, _safe_id(run_id, _RUN_ID_RE, "run_id"))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ArtifactError(f"产物不存在：{os.path.basename(path)}")
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"产物读取失败 {os.path.basename(path)}：{exc}")
    if not isinstance(data, dict):
        raise ArtifactError(f"产物必须是 JSON 对象：{os.path.basename(path)}")
    return data


def _write_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ── 批次 ────────────────────────────────────────────────────────────────────


def create_batch(title: str, *, owner: str = "", base_url: str = "",
                 environment_ref: str = "", source_ref: str = "") -> dict[str, Any]:
    batch_id = new_batch_id()
    meta = {
        "artifact": "batch",
        "schema_version": "1",
        "batch_id": batch_id,
        "title": str(title or "未命名批次"),
        "owner": str(owner or ""),          # 预留：decision 0009 数据分区（M2）
        "base_url": str(base_url or ""),
        "environment_ref": str(environment_ref or ""),
        "source_ref": str(source_ref or ""),
        "created_at": now_iso(),
        "run_ids": [],
    }
    _write_json(os.path.join(batch_dir(batch_id, create=True), "batch.json"), meta)
    return meta


def load_batch(batch_id: str) -> dict[str, Any]:
    return _read_json(os.path.join(batch_dir(batch_id), "batch.json"))


def save_batch(meta: dict[str, Any]) -> None:
    _write_json(os.path.join(batch_dir(meta["batch_id"]), "batch.json"), meta)


def list_batches() -> list[dict[str, Any]]:
    if not os.path.isdir(BATCHES_ROOT):
        return []
    out = []
    for name in sorted(os.listdir(BATCHES_ROOT), reverse=True):
        try:
            meta = load_batch(name)
        except ArtifactError:
            continue
        meta["stepper"] = stepper(name)
        out.append(meta)
    return out


# ── 类型化产物读写（写入即校验信封）─────────────────────────────────────────


def artifact_path(batch_id: str, kind: str) -> str:
    if kind not in ARTIFACT_FILES:
        raise ArtifactError(f"未知产物类型：{kind}（合法：{'/'.join(ARTIFACT_FILES)}）")
    return os.path.join(batch_dir(batch_id), ARTIFACT_FILES[kind])


def save_artifact(batch_id: str, kind: str, payload: dict[str, Any],
                  *, schema_version: str = "1") -> dict[str, Any]:
    """落盘一个类型化产物。信封字段由本函数盖章，payload 不得抢注。"""
    load_batch(batch_id)  # 批次必须先存在
    body = dict(payload)
    for reserved in ("artifact", "batch_id"):
        body.pop(reserved, None)
    body["artifact"] = kind
    body["schema_version"] = body.get("schema_version") or schema_version
    body["batch_id"] = batch_id
    body.setdefault("created_at", now_iso())
    _write_json(artifact_path(batch_id, kind), body)
    return body


def load_artifact(batch_id: str, kind: str) -> dict[str, Any]:
    data = _read_json(artifact_path(batch_id, kind))
    if data.get("artifact") != kind:
        raise ArtifactError(
            f"产物类型不符：文件声明 {data.get('artifact')!r}，期望 {kind!r}")
    if data.get("batch_id") != batch_id:
        raise ArtifactError("产物 batch_id 与所在批次不符")
    return data


def has_artifact(batch_id: str, kind: str) -> bool:
    return os.path.isfile(artifact_path(batch_id, kind))


def stepper(batch_id: str) -> list[dict[str, Any]]:
    """产物账本 stepper：状态由产物存在性推导，不另存进度。"""
    steps = []
    for kind in ARTIFACT_FILES:
        steps.append({"artifact": kind, "present": has_artifact(batch_id, kind)})
    steps.insert(5, {"artifact": "automation_bundle",
                     "present": os.path.isfile(
                         os.path.join(batch_dir(batch_id), "bundle", "bundle.json"))})
    return steps


# ── 批次事件流（append-only：人闸确认收据、写确认等）───────────────────────


def append_event(batch_id: str, event: dict[str, Any]) -> dict[str, Any]:
    row = dict(event)
    row.setdefault("at", now_iso())
    path = os.path.join(batch_dir(batch_id), "events.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_events(batch_id: str) -> list[dict[str, Any]]:
    path = os.path.join(batch_dir(batch_id), "events.jsonl")
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
