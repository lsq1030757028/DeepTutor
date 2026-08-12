# -*- coding: utf-8 -*-
"""artifacts — 批次（batch）状态对象与类型化产物存储。

组合约束（设计稿 §1）：批次是唯一状态对象，聊天与工作台是它的两个投影；
牙挂在产物上——产物写入即校验，闸不过不落盘（fail-closed）。

目录形态（M2 起加 `<owner>/` 一层，决策 0009 布局分区 / 设计稿 §11 / BB-503）::

    data/test-workbench/<owner>/batches/<batch_id>/
        batch.json               批次元信息（id/title/owner/base_url/环境引用）
        intake_profile.json      ingest 产物
        business_frame.json      clarify 产物
        test_analysis.json       analyze 产物
        case_draft.json          draft 产物
        approved_caseset.json    adopt 产物（双 digest 冻结）
        coverage_ledger.json     coverage 产物
        requirement_code_map.json  L5 需求↔代码反向对账产物
        oracle/                  TAPD 需求正文快照（ADR-M2-02）
        bundle/                  compile 产物（pytest+Playwright 工程）
        events.jsonl             批次事件流（append-only：人闸确认、写确认收据……）
    data/test-workbench/<owner>/runs/<run_id>/
        receipt.json / evidence/ / evidence-bundle.json / verdicts.jsonl / ...

生产调用由 DeepTutor 的短时签名绑定 owner；扩展验签后同时绑定 owner 与本次
workbench root。`_local` 仅保留离线兼容，不会被生产工具扫描或自动认领。

## 为什么每个路径构造函数都带 `root`（BB-503 的正解）

M1 把 `WORKBENCH_ROOT` 写成模块级常量、`batch_dir()`/`run_dir()` 不收 root，
而同期的 `gateway/workbench.py` 每个函数本来就收可选 `root`——**0009 的隔离因此
在交付件路径上成立、在批次路径上不成立**。这是 M1 引入的新缺口，不是 0009 的旧欠账。
本模块自 M2 起：**任何构造路径的公开函数都必须能被注入 root**，CI 有对拍断言守着
（`tests/test_journey_artifacts.py::test_no_path_builder_without_root`）。

产物文件公共信封字段：artifact / schema_version / batch_id / created_at。
stepper 状态由**产物存在性推导**（presence-derived），不另存进度字段——
避免第二真相（SSOT「单一状态双视图」）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from typing import Any, Iterator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认工作台根。**不要直接读它拼路径**——走 `workbench_root()`，那条路允许注入。
WORKBENCH_ROOT = os.path.join(REPO_ROOT, "data", "test-workbench")

#: 环境变量注入位（容器/多实例部署用；显式 root 参数优先级更高）。
ROOT_ENV = "TEST_PARTNER_WORKBENCH_ROOT"

#: M1 平铺布局的两个常量。**已弃用，仅为兼容既有调用与测试的 monkeypatch 保留**；
#: 新代码一律走 `batches_root()` / `runs_root()`（它们带 owner 与 root 参数）。
#: 留着它们不是为了还有人读，而是为了「删掉会让一批测试炸在 setattr 上」这种
#: 与本次修复无关的噪音不出现——真正的路径解析已经不看这两个值了。
BATCHES_ROOT = os.path.join(WORKBENCH_ROOT, "batches")
RUNS_ROOT = os.path.join(WORKBENCH_ROOT, "runs")

#: 未声明 owner 时的归属。0009 的"各看各的"在 M2 只落到布局，默认单人即 `_local`。
DEFAULT_OWNER = "_local"

# Trusted production calls bind this before entering the older pure journey
# modules.  Those modules predate multi-user routing and often omit ``owner``;
# the context makes omission resolve to the verified partition, never to a
# cross-owner search.  Low-level unit tests may still address ``_local``
# explicitly, but the MCP production facade rejects it.
_TRUSTED_OWNER: ContextVar[str | None] = ContextVar(
    "test_partner_journey_owner", default=None)
_TRUSTED_ROOT: ContextVar[str | None] = ContextVar(
    "test_partner_journey_root", default=None)

#: 产物注册表：kind -> (文件名, 展示名)。顺序即 stepper 前六格顺序（产物账本）。
#: **展示名在这里是唯一真相**——前端与调试面都只渲染服务端给的列表，不各持常量（§9 第 3 条）。
ARTIFACT_FILES: dict[str, str] = {
    "intake_profile": "intake_profile.json",
    "business_frame": "business_frame.json",
    "test_analysis": "test_analysis.json",
    "case_draft": "case_draft.json",
    "approved_caseset": "approved_caseset.json",
    "coverage_ledger": "coverage_ledger.json",
    "requirement_code_map": "requirement_code_map.json",
}

#: 九格账本的展示名（服务端单源，§9 第 3 条消灭第二真相）。
STEP_LABELS: dict[str, str] = {
    "intake_profile": "接入档案",
    "business_frame": "业务框架",
    "test_analysis": "测试分析",
    "case_draft": "用例草稿",
    "approved_caseset": "采纳用例集",
    "automation_bundle": "自动化工程",
    "run_receipt": "执行收据",
    "verdicts": "结论投影",
    "coverage_ledger": "覆盖收口",
}

#: 九格的固定顺序（DoD#6 的 CI 断言对象）。`requirement_code_map` 是 L5 附加产物，
#: **不进九格**——它是白盒增强的旁挂产物，进了就把账本变成十格，与 0021 的界面投影对不上。
STEPPER_ORDER: tuple[str, ...] = (
    "intake_profile", "business_frame", "test_analysis", "case_draft",
    "approved_caseset", "automation_bundle", "run_receipt", "verdicts",
    "coverage_ledger",
)

#: 每格的前置格（推导 `missing_prereq` 用）。
_PREREQ: dict[str, str] = {
    "business_frame": "intake_profile",
    "test_analysis": "business_frame",
    "case_draft": "test_analysis",
    "approved_caseset": "case_draft",
    "automation_bundle": "approved_caseset",
    "run_receipt": "automation_bundle",
    "verdicts": "run_receipt",
    "coverage_ledger": "test_analysis",
}

_BATCH_ID_RE = re.compile(r"^b-[0-9]{8}-[a-z0-9]{6,}$")
_RUN_ID_RE = re.compile(r"^r-[0-9]{8}-[a-z0-9]{6,}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


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


def safe_owner(owner: Any) -> str:
    """owner 同样会拼进路径，同一道闸。空值落 `_local`。"""
    text = str(owner or "").strip() or DEFAULT_OWNER
    if not _OWNER_RE.match(text):
        raise ArtifactError(f"owner 形态非法：{owner!r}")
    return text


def current_trusted_owner() -> str | None:
    return _TRUSTED_OWNER.get()


def current_trusted_root() -> str | None:
    return _TRUSTED_ROOT.get()


@contextmanager
def trusted_owner(owner: Any, *, root: str | None = None):
    text = str(owner or "").strip()
    if not text:
        raise ArtifactError("可信 owner 不能为空")
    partition = safe_owner(text)
    token = _TRUSTED_OWNER.set(partition)
    root_token = None
    if root is not None:
        root_token = _TRUSTED_ROOT.set(os.path.abspath(root))
    try:
        yield partition
    finally:
        if root_token is not None:
            _TRUSTED_ROOT.reset(root_token)
        _TRUSTED_OWNER.reset(token)


def _partition(owner: str | None) -> str:
    """Resolve an explicit or already-verified partition without scanning."""
    if owner is not None and str(owner).strip():
        return safe_owner(owner)
    return current_trusted_owner() or DEFAULT_OWNER


# ── 根与分区（**所有路径构造的唯一入口**）────────────────────────────────────


def workbench_root(root: str | None = None) -> str:
    """解析工作台根：显式参数 > 可信调用上下文 > 环境变量 > 模块常量。

    模块常量放在最后而不是被写死，是为了让调用方（gateway / 测试 / 多实例）
    都能注入，同时保留既有 monkeypatch 用法。
    """
    if root:
        return os.path.abspath(root)
    trusted = current_trusted_root()
    if trusted:
        return trusted
    env = os.environ.get(ROOT_ENV)
    if env:
        return os.path.abspath(env)
    return WORKBENCH_ROOT


def owner_root(owner: str | None = None, *, root: str | None = None) -> str:
    return os.path.join(workbench_root(root), _partition(owner))


def batches_root(owner: str | None = None, *, root: str | None = None) -> str:
    return os.path.join(owner_root(owner, root=root), "batches")


def runs_root(owner: str | None = None, *, root: str | None = None) -> str:
    return os.path.join(owner_root(owner, root=root), "runs")


def list_owners(root: str | None = None) -> list[str]:
    base = workbench_root(root)
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        if os.path.isdir(os.path.join(base, name, "batches")) or \
                os.path.isdir(os.path.join(base, name, "runs")):
            try:
                out.append(safe_owner(name))
            except ArtifactError:
                continue
    return out


def _legacy_dir(kind: str, ident: str, root: str | None) -> str:
    """M1 的平铺布局 `<root>/{batches,runs}/<id>`。只读兼容，不新建。"""
    return os.path.join(workbench_root(root), kind, ident)


def _resolve_dir(kind: str, ident: str, owner: str | None,
                 root: str | None) -> tuple[str, str]:
    """Resolve production calls in one owner; keep offline legacy reads isolated.

    A bound trusted owner is authoritative and disables every fallback.  The
    scan/flat fallback exists only for direct low-level migration and unit-test
    callers that did not enter the production tool facade.
    """
    if owner or current_trusted_owner():
        partition = _partition(owner)
        return os.path.join(workbench_root(root), partition, kind, ident), partition
    for candidate in list_owners(root):
        path = os.path.join(workbench_root(root), candidate, kind, ident)
        if os.path.isdir(path):
            return path, candidate
    legacy = _legacy_dir(kind, ident, root)
    if os.path.isdir(legacy):
        return legacy, DEFAULT_OWNER
    return os.path.join(workbench_root(root), DEFAULT_OWNER, kind, ident), DEFAULT_OWNER


def batch_dir(batch_id: str, *, create: bool = False, owner: str | None = None,
              root: str | None = None) -> str:
    ident = _safe_id(batch_id, _BATCH_ID_RE, "batch_id")
    path, _ = _resolve_dir("batches", ident, owner, root)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def run_dir(run_id: str, *, create: bool = False, owner: str | None = None,
            root: str | None = None) -> str:
    ident = _safe_id(run_id, _RUN_ID_RE, "run_id")
    path, _ = _resolve_dir("runs", ident, owner, root)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


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
    """Atomically replace one JSON file without sharing a temporary pathname.

    Every writer gets a private temporary file in the destination directory.
    A fixed ``batch.json.tmp`` lets two otherwise valid runs overwrite or move
    each other's staging file before ``os.replace``.
    """
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _assert_batch_payload_safe(data: dict[str, Any], label: str) -> None:
    """Reject unknown high-entropy values before batch artifacts persist."""
    from server.journey.gates import credential_scan
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
    report = credential_scan.scan_text_content(encoded, label=label)
    if not report["ok"]:
        raise ArtifactError(
            f"{label} 疑似包含凭据，已在落盘前拒绝；请移除或变量化后重试")


@contextmanager
def _file_lock(path: str, *, timeout_s: float = 1200.0) -> Iterator[None]:
    """Serialize one artifact mutation across threads and local processes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+b") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"等待产物锁超时：{os.path.basename(path)}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def consume_owner_decision_receipt(*, owner: str, decision_jti: str,
                                   gate: str, root: str | None = None) -> bool:
    """Atomically consume one signed user-decision receipt for an owner.

    Intake has no batch yet, so per-batch events cannot prevent the same human
    answer from creating two batches.  This owner-local ledger closes that
    pre-batch replay window without storing the signed token or its answer.
    """
    partition = _partition(owner)
    jti = str(decision_jti or "").strip()
    gate_name = str(gate or "").strip()
    if not jti or not gate_name:
        raise ArtifactError("decision receipt identity is incomplete")
    receipt_sha256 = hashlib.sha256(jti.encode("utf-8")).hexdigest()
    base = owner_root(partition, root=root)
    path = os.path.join(base, "decision-receipts.jsonl")
    with _file_lock(path + ".lock"):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and str(
                            json.loads(line).get("decision_hash") or ""
                    ) == receipt_sha256:
                        return False
        os.makedirs(base, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "decision_hash": receipt_sha256,
                "gate": gate_name,
                "at": now_iso(),
            }, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return True


# ── 批次 ────────────────────────────────────────────────────────────────────


def create_batch(title: str, *, owner: str = "", base_url: str = "",
                 environment_ref: str = "", source_ref: str = "",
                 root: str | None = None) -> dict[str, Any]:
    batch_id = new_batch_id()
    partition = _partition(owner or None)
    meta = {
        "artifact": "batch",
        "schema_version": "1",
        "batch_id": batch_id,
        "title": str(title or "未命名批次"),
        # `owner` 保持 M1 语义（调用方声明的归属，可为空串）；
        # `partition` 是落盘用的目录名，空 owner 落 `_local`。两者刻意分开：
        # 把空串直接写成 `_local` 会让"没声明归属"与"归属就是本地"不可区分。
        "owner": str(owner or ""),
        "partition": partition,
        "base_url": str(base_url or ""),
        "environment_ref": str(environment_ref or ""),
        "source_ref": str(source_ref or ""),
        "created_at": now_iso(),
        "run_ids": [],
    }
    _assert_batch_payload_safe(meta, "batch.json")
    target = batch_dir(batch_id, create=True, owner=partition, root=root)
    _write_json(os.path.join(target, "batch.json"), meta)
    return meta


def load_batch(batch_id: str, *, owner: str | None = None,
               root: str | None = None) -> dict[str, Any]:
    return _read_json(os.path.join(batch_dir(batch_id, owner=owner, root=root),
                                   "batch.json"))


def save_batch(meta: dict[str, Any], *, root: str | None = None) -> None:
    _write_json(os.path.join(batch_dir(meta["batch_id"],
                                       owner=meta.get("partition") or None,
                                       root=root), "batch.json"), meta)


def append_run_id(batch_id: str, run_id: str, *, owner: str | None = None,
                  root: str | None = None) -> dict[str, Any]:
    """Append a completed run without losing a concurrent sibling run.

    The lock covers the complete read-modify-write transaction.  It is scoped
    to the resolved owner partition and batch, so different batches remain
    independent while two runs of the same batch both remain discoverable.
    """
    ident = _safe_id(batch_id, _BATCH_ID_RE, "batch_id")
    safe_run = _safe_id(run_id, _RUN_ID_RE, "run_id")
    target, partition = _resolve_dir("batches", ident, owner, root)
    path = os.path.join(target, "batch.json")
    with _file_lock(path + ".lock"):
        meta = _read_json(path)
        recorded_partition = safe_owner(
            meta.get("partition") or meta.get("owner"))
        if recorded_partition != partition:
            raise ArtifactError("batch.json 归属与所在分区不一致")
        run_ids = meta.get("run_ids")
        if not isinstance(run_ids, list):
            raise ArtifactError("batch.json 的 run_ids 必须是数组")
        if safe_run not in run_ids:
            meta["run_ids"] = [*run_ids, safe_run]
            _write_json(path, meta)
        return meta


@contextmanager
def batch_mutation_lock(batch_id: str, *, owner: str | None = None,
                        root: str | None = None) -> Iterator[None]:
    """Serialize state-changing tools for one owner-bound batch."""
    target = batch_dir(batch_id, owner=owner, root=root)
    with _file_lock(os.path.join(target, ".mutation.lock")):
        yield


def invalidate_bundle(batch_id: str, *, owner: str | None = None,
                      root: str | None = None) -> bool:
    """Remove the derived bundle when its approved caseset is superseded."""
    parent = os.path.realpath(batch_dir(
        batch_id, owner=owner, root=root))
    target = os.path.realpath(os.path.join(parent, "bundle"))
    if os.path.commonpath([parent, target]) != parent:
        raise ArtifactError("bundle 路径越出批次目录")
    if not os.path.isdir(target):
        return False
    shutil.rmtree(target)
    return True


def discard_fresh_run(run_id: str, *, owner: str | None = None,
                      root: str | None = None) -> bool:
    """Remove only a validated run directory created by the current attempt."""
    parent = os.path.realpath(runs_root(owner, root=root))
    target = os.path.realpath(run_dir(run_id, owner=owner, root=root))
    if os.path.commonpath([parent, target]) != parent:
        raise ArtifactError("run 路径越出 owner 的 runs 目录")
    if not os.path.isdir(target):
        return False
    shutil.rmtree(target)
    return True


def list_batches(*, owner: str | None = None,
                 root: str | None = None) -> list[dict[str, Any]]:
    """只列当前 owner 的批次；旧平铺与其他 owner 默认不可见。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    trusted = current_trusted_owner()
    scopes = [_partition(owner)] if (owner or trusted) else list_owners(root)
    dirs = [batches_root(o, root=root) for o in scopes]
    if not owner and not trusted:
        dirs.append(_legacy_dir("batches", "", root).rstrip(os.sep))
    for index, base in enumerate(dirs):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base), reverse=True):
            if name in seen or not _BATCH_ID_RE.match(name):
                continue
            try:
                scope = scopes[index] if index < len(scopes) else None
                meta = load_batch(name, owner=scope, root=root)
            except ArtifactError:
                continue
            seen.add(name)
            meta["stepper"] = stepper(name, owner=scope, root=root)
            out.append(meta)
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return out


# ── 类型化产物读写（写入即校验信封）─────────────────────────────────────────


def artifact_path(batch_id: str, kind: str, *, owner: str | None = None,
                  root: str | None = None) -> str:
    if kind not in ARTIFACT_FILES:
        raise ArtifactError(f"未知产物类型：{kind}（合法：{'/'.join(ARTIFACT_FILES)}）")
    return os.path.join(batch_dir(batch_id, owner=owner, root=root),
                        ARTIFACT_FILES[kind])


def save_artifact(batch_id: str, kind: str, payload: dict[str, Any],
                  *, schema_version: str = "1", owner: str | None = None,
                  root: str | None = None) -> dict[str, Any]:
    """落盘一个类型化产物。信封字段由本函数盖章，payload 不得抢注。"""
    if kind not in ARTIFACT_FILES:
        raise ArtifactError(
            f"未知产物类型：{kind}（合法：{'/'.join(ARTIFACT_FILES)}）")
    load_batch(batch_id, owner=owner, root=root)  # 批次必须先存在
    body = dict(payload)
    for reserved in ("artifact", "batch_id"):
        body.pop(reserved, None)
    body["artifact"] = kind
    body["schema_version"] = body.get("schema_version") or schema_version
    body["batch_id"] = batch_id
    body.setdefault("created_at", now_iso())
    _assert_batch_payload_safe(body, ARTIFACT_FILES[kind])
    _write_json(artifact_path(batch_id, kind, owner=owner, root=root), body)
    return body


def load_artifact(batch_id: str, kind: str, *, owner: str | None = None,
                  root: str | None = None) -> dict[str, Any]:
    data = _read_json(artifact_path(batch_id, kind, owner=owner, root=root))
    if data.get("artifact") != kind:
        raise ArtifactError(
            f"产物类型不符：文件声明 {data.get('artifact')!r}，期望 {kind!r}")
    if data.get("batch_id") != batch_id:
        raise ArtifactError("产物 batch_id 与所在批次不符")
    return data


def has_artifact(batch_id: str, kind: str, *, owner: str | None = None,
                 root: str | None = None) -> bool:
    return os.path.isfile(artifact_path(batch_id, kind, owner=owner, root=root))


def _bundle_present(batch_id: str, owner: str | None, root: str | None) -> bool:
    return os.path.isfile(os.path.join(
        batch_dir(batch_id, owner=owner, root=root), "bundle", "bundle.json"))


def latest_run_id(batch_id: str, *, owner: str | None = None,
                  root: str | None = None) -> str:
    try:
        meta = load_batch(batch_id, owner=owner, root=root)
    except ArtifactError:
        return ""
    runs = meta.get("run_ids") or []
    return str(runs[-1]) if runs else ""


def stepper(batch_id: str, *, owner: str | None = None,
            root: str | None = None) -> list[dict[str, Any]]:
    """九格产物账本：状态由产物存在性推导，不另存进度。

    每格：`{artifact, label, present, run_id, missing_prereq, blocked_reason}`。
    - **run 级两格带 run 锚**（第 7/8 格）——多 run 时说得清"哪个 run 亮的"（§9 第 2 条）。
    - `missing_prereq` 与 `blocked_reason` **互斥**：前置没产出就是前置的事，
      前置齐了还灭才轮得到"闸拦住了"（§9 第 4 条，灭格点开要给"为什么没有"）。

    界面上的四段账本是本九格的**投影**（0021 必改 1），不是同一个数——
    段状态由段内格子即时汇总，不存盘。**不许为了对齐界面把这里砍成 4 项。**
    """
    rid = latest_run_id(batch_id, owner=owner, root=root)
    rd = run_dir(rid, owner=owner, root=root) if rid else ""
    present_map: dict[str, bool] = {}
    for kind in STEPPER_ORDER:
        if kind == "automation_bundle":
            present_map[kind] = _bundle_present(batch_id, owner, root)
        elif kind == "run_receipt":
            present_map[kind] = bool(rd) and os.path.isfile(
                os.path.join(rd, "receipt.json"))
        elif kind == "verdicts":
            path = os.path.join(rd, "verdicts.jsonl") if rd else ""
            present_map[kind] = bool(path) and os.path.isfile(path) and \
                os.path.getsize(path) > 0
        else:
            present_map[kind] = has_artifact(batch_id, kind, owner=owner, root=root)

    steps: list[dict[str, Any]] = []
    for kind in STEPPER_ORDER:
        prereq = _PREREQ.get(kind, "")
        missing = prereq if (not present_map[kind] and prereq
                             and not present_map.get(prereq, False)) else ""
        blocked = ""
        if not present_map[kind] and prereq and present_map.get(prereq, False):
            blocked = f"前置「{STEP_LABELS.get(prereq, prereq)}」已就绪，本步尚未产出或被闸拦下"
        steps.append({
            "artifact": kind,
            "label": STEP_LABELS.get(kind, kind),
            "present": present_map[kind],
            "run_id": rid if kind in ("run_receipt", "verdicts") else "",
            "missing_prereq": missing,
            "blocked_reason": blocked,
        })
    return steps


# ── 批次事件流（append-only：人闸确认收据、写确认等）───────────────────────


def append_event(batch_id: str, event: dict[str, Any], *, owner: str | None = None,
                 root: str | None = None) -> dict[str, Any]:
    row = dict(event)
    row.setdefault("at", now_iso())
    path = os.path.join(batch_dir(batch_id, owner=owner, root=root), "events.jsonl")
    with _file_lock(path + ".lock"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return row


def read_events(batch_id: str, *, owner: str | None = None,
                root: str | None = None) -> list[dict[str, Any]]:
    path = os.path.join(batch_dir(batch_id, owner=owner, root=root), "events.jsonl")
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ── 存量迁移（平铺 → `<owner>/`，可逆）──────────────────────────────────────


def migrate_layout(*, owner: str = DEFAULT_OWNER, root: str | None = None,
                   dry_run: bool = False, reverse: bool = False) -> dict[str, Any]:
    """把 M1 平铺的 `<root>/{batches,runs}` 归入 `<root>/<owner>/`。

    `reverse=True` 走回头路（`<owner>/` → 平铺），这就是"可逆"的实现而不是口号。
    只搬目录、不改文件内容，故两个方向都不损失数据。
    """
    import shutil
    base = workbench_root(root)
    o = safe_owner(owner)
    moved: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for kind in ("batches", "runs"):
        src = os.path.join(base, kind) if not reverse else os.path.join(base, o, kind)
        dst = os.path.join(base, o, kind) if not reverse else os.path.join(base, kind)
        if not os.path.isdir(src):
            continue
        for name in sorted(os.listdir(src)):
            src_path = os.path.join(src, name)
            if not os.path.isdir(src_path):
                continue
            dst_path = os.path.join(dst, name)
            if os.path.exists(dst_path):
                skipped.append({"kind": kind, "id": name, "reason": "目标已存在"})
                continue
            moved.append({"kind": kind, "id": name, "from": src_path, "to": dst_path})
            if not dry_run:
                os.makedirs(dst, exist_ok=True)
                shutil.move(src_path, dst_path)
    return {"ok": True, "root": base, "owner": o, "reverse": reverse,
            "dry_run": dry_run, "moved": moved, "skipped": skipped}
