# -*- coding: utf-8 -*-
"""tools — 九原子工具的**对外调用面**（M2 第 0 号施工项，ADR-M2-01）。

## 这个模块存在的理由

M1 把九原子工具做完了，但**没有任何对外调用面**：`@mcp.tool()` 只暴露 7 个非 journey
工具，gateway 只有 3 个只读端点 + 1 个 trace 端点，唯一驱动方是 demo 与测试的进程内直调。
即「M1 的闭环能跑，但没人能从 DT 侧调它」。M2 的三件主事——TAPD 全链、`(workspace)` 薄壳、
聊天人闸——全部压在这条缺失的面上。

本模块是那条面的**服务端半边**：把 `server/journey/*` 的纯函数包成
「统一信封 + 可信 owner + 幂等闸 + 错误码」的形态，再由 `server/main.py` 挂成 MCP 工具。

## 三条形态约束

1. **返回体首字段固定 `{ok, code, ...}`**（ADR-M2-01 第 3 条）。MCP 适配器会把
   TextContent 拼成字符串，消费方靠约定解析——所以约定必须简单到不会解错。
2. **原子，不做编排**。这里没有 `journey_run_all`。默认组合由聊天 agent 按档位决定，
   不由服务端固化（M1 §1「禁向导式流水线」）。
3. **副作用工具过幂等闸**（`adopt` / `compile` / `execute`），判断类工具不过——
   重跑一次 `analyze` 只是多花点时间，重跑一次 `execute` 会多打一遍被测系统。
"""
from __future__ import annotations

from functools import wraps
import json
import os
from typing import Any

from server.journey import (
    adopt as _adopt,
)
from server.journey import (
    analyze as _analyze,
)
from server.journey import (
    artifacts,
    gate,
    idempotency,
    oracle,
)
from server.journey import (
    clarify as _clarify,
)
from server.journey import (
    compile_bundle as _compile,
)
from server.journey import (
    coverage as _coverage,
)
from server.journey import (
    draft_cases as _draft,
)
from server.journey import (
    execute_run as _execute,
)
from server.journey import (
    ingest as _ingest,
)
from server.journey import (
    project_verdicts as _project,
)
from server.journey.digest import sha256_digest

#: 判断类工具（聊天 agent 驱动）与机械类工具（薄壳 / 链式调用）的分工，
#: 与 SSOT「动作分流」一致：判断类从聊天发起，机械类在工作台。
JUDGEMENT_TOOLS = ("ingest", "clarify", "analyze", "draft_cases", "adopt")
MECHANICAL_TOOLS = ("compile", "execute", "project", "coverage")
READ_TOOLS = ("list_batches", "get_batch", "open_trace")

#: 九原子 + 两个读接口。`main.py` 的挂载数与本元组对拍（ADR-M2-01 G2），
#: 防「加了工具忘了挂 MCP 面」与「挂了未声明的工具」两向漂移。
TOOL_NAMES: tuple[str, ...] = JUDGEMENT_TOOLS + MECHANICAL_TOOLS + READ_TOOLS

#: 人闸类：写确认落账。**不算九原子之一**——九原子是旅程的业务工序，
#: 它是人闸的服务端半边（记录用户答案）。混进 JUDGEMENT_TOOLS 会让
#: 「九原子一个都不能少」那条断言从"业务工序齐不齐"变成"工具总数对不对"，
#: 而后者随便加个工具就会红，红几次就没人看了。
#: 但它们确实挂在 MCP 面上，所以要在对拍集合里明说——否则 G2 的断言会因为
#: "多出一个没声明的工具"而红，或者被人顺手放宽成 `>=`。
GATE_TOOLS: tuple[str, ...] = ("write_confirm",)

#: MCP 面上 `journey_*` 的**完整**集合。G2 断言的对象就是它。
MCP_TOOL_NAMES: tuple[str, ...] = TOOL_NAMES + GATE_TOOLS

#: 有副作用、必须过幂等闸的三个。
SIDE_EFFECT_TOOLS = ("adopt", "compile", "execute")


def _ok(code: str = "OK", **rest: Any) -> dict[str, Any]:
    return {"ok": True, "code": code, **rest}


def _err(code: str, message: str, **rest: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message, **rest}


def _requires_trusted_owner(fn):
    """Make every production tool call run in one server-trusted partition.

    ``owner`` is accepted only as a server-side keyword from ``main.py``.  The
    MCP wrapper overwrites model/browser identity fields after bridge
    verification; direct unit tests must pass an explicit owner as well.
    """
    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raw_owner = str(kwargs.pop("owner", "") or "").strip()
        if not raw_owner or raw_owner == artifacts.DEFAULT_OWNER:
            return _err("E_OWNER_REQUIRED", "可信 owner 缺失，拒绝访问旅程产物。")
        try:
            with artifacts.trusted_owner(raw_owner, root=kwargs.get("root")):
                return fn(*args, **kwargs)
        except artifacts.ArtifactError as exc:
            return _err("E_OWNER_REQUIRED", str(exc))
    return wrapped


def _serializes_batch_mutation(fn):
    """Keep one batch's state transitions ordered while other batches can run."""
    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        batch_id = str(kwargs.get("batch_id") or "")
        root = kwargs.get("root")
        # Preserve the tool's own E_NO_BATCH contract.  Invalid or missing
        # batches are validated by ``_guarded``; trying to derive a lock path
        # first would make the outer owner wrapper misclassify that failure.
        try:
            artifacts.load_batch(batch_id, root=root)
        except artifacts.ArtifactError:
            return fn(*args, **kwargs)
        with artifacts.batch_mutation_lock(batch_id, root=root):
            return fn(*args, **kwargs)
    return wrapped


def _log_call(batch_id: str, tool: str, caller_surface: str,
              root: str | None = None) -> None:
    """把调用面记进 events.jsonl（DoD#8e-3：**信号，不当闸**）。

    UAT 时可回看「有没有轮子在 chat 模式下摸过这些工具」。
    刻意不做成闸——调用面是调用方自报的，自报的东西当闸就是假闸。
    """
    try:
        artifacts.append_event(batch_id, {
            "type": "tool_call", "tool": f"journey_{tool}",
            "caller_surface": gate.normalize_surface(caller_surface)}, root=root)
    except artifacts.ArtifactError:
        pass


def _guarded(batch_id: str, tool: str, caller_surface: str,
             root: str | None = None) -> dict[str, Any] | None:
    """非 ingest 工具的统一前置：批次必须既存（G5 第二半）。"""
    check = gate.require_batch(batch_id, root=root)
    if not check["ok"]:
        return _err(check["code"], check["message"])
    _log_call(batch_id, tool, caller_surface, root)
    return None


def _batch_partition(batch_id: str, root: str | None) -> str:
    """从已落盘批次取幂等/执行分区，不信任调用方自报 owner。"""
    batch = artifacts.load_batch(batch_id, root=root)
    return artifacts.safe_owner(batch.get("partition") or batch.get("owner"))


def _artifact_state_digest(batch_id: str, kind: str,
                           root: str | None = None) -> str:
    return sha256_digest(artifacts.load_artifact(batch_id, kind, root=root))


def _bundle_state(batch_id: str, root: str | None = None) -> dict[str, str]:
    path = os.path.join(artifacts.batch_dir(batch_id, root=root), "bundle", "bundle.json")
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return {
        "bundle_sha256": sha256_digest(manifest),
        "caseset_id": str(manifest.get("caseset_id") or ""),
        "compiler_version": str(manifest.get("compiler_version") or ""),
    }


def _selected_authorized_write_risk(batch_id: str,
                                    case_ids: list[str] | None, *,
                                    owner: str,
                                    root: str | None = None) -> bool:
    """Whether this exact execute request may send a confirmed write."""
    try:
        caseset = artifacts.load_artifact(
            batch_id, "approved_caseset", owner=owner, root=root)
    except artifacts.ArtifactError:
        return False
    selected = set(case_ids or [
        str(case.get("case_id") or "") for case in caseset.get("cases") or []])
    authorized = _execute.write_authorization(
        batch_id, owner=owner, root=root)["authorized"]
    return any(
        str(case.get("case_id") or "") in selected
        and str(case.get("case_id") or "") in authorized
        and bool((case.get("side_effects") or {}).get("writes"))
        for case in caseset.get("cases") or [])


def _recover_completed_execute(batch_id: str, intent: dict[str, Any], *,
                               owner: str, root: str | None = None
                               ) -> dict[str, Any] | None:
    """Recover the post-receipt/pre-ledger crash window without re-running."""
    run_id = str(intent.get("run_id") or "")
    try:
        receipt_path = os.path.join(artifacts.run_dir(
            run_id, owner=owner, root=root), "receipt.json")
        with open(receipt_path, encoding="utf-8") as fh:
            receipt = json.load(fh)
    except (artifacts.ArtifactError, OSError, ValueError):
        return None
    if not isinstance(receipt, dict) or \
            str(receipt.get("run_id") or "") != run_id or \
            str(receipt.get("batch_id") or "") != batch_id or \
            str(receipt.get("owner_partition") or owner) != owner or \
            int(receipt.get("credential_scan_passes") or 0) < 2:
        return None
    artifacts.append_run_id(batch_id, run_id, owner=owner, root=root)
    return _ok(run_id=run_id, receipt=receipt, recovered=True)


# ── 1. ingest（唯一能创建批次；fail-closed 于可信 bridge）───────────────────


@_requires_trusted_owner
def ingest(*, title: str, base_url: str, source_kind: str = "tapd",
           source_ref: str = "",
           workspace_id: str = "", story_id: str = "",
           requirement_text: str = "", environment_ref: str = "",
           tier: str = "", tier_confirmed_via: str = "", owner: str = "",
           caller_surface: str = "unknown",
           root: str | None = None) -> dict[str, Any]:
    """接入 + 定档。可信 bridge 已在 MCP wrapper 中先于本函数验完。"""

    oracle_source: dict[str, Any] = {}
    text = requirement_text
    if workspace_id and story_id:
        oracle.warm_up()
        try:
            story = oracle.fetch_story(workspace_id, story_id)
        except oracle.OracleError as exc:
            # fail-closed：错误串绝不当需求正文快照下来（ADR-M2-02 第 5 条）
            return exc.as_payload()
        text = oracle.plain_text(str(story.get("description") or ""))
        oracle_source = {"_story": story, "_workspace_id": workspace_id,
                         "_story_id": story_id}
    elif not text:
        return _err("E_ORACLE_NOT_FOUND",
                    "既没给 TAPD 需求号（workspace_id + story_id），也没给本地需求正文。"
                    "oracle 缺失时不建批次——对空气接单是 M1 就定下的红线。")

    result = _ingest.ingest(title, base_url, source_kind=source_kind,
                            source_ref=source_ref or (
                                f"tapd:{workspace_id}/{story_id}"
                                if story_id else ""),
                            requirement_text=text,
                            environment_ref=environment_ref, tier=tier,
                            tier_confirmed_via=tier_confirmed_via, owner=owner)
    if not result.get("ok"):
        if result.get("need") == "tier_confirmation":
            return _ok("NEEDS_GATE", needs_gate="stage_tier", probe=result["probe"],
                       card=result["card"], proposed_tier=result["proposed_tier"],
                       score=result["score"], reasons=result["reasons"])
        return _err("E_INGEST_REJECTED", str(result.get("error") or "接入被拒"),
                    detail=result)

    bid = result["batch_id"]
    _log_call(bid, "ingest", caller_surface, root)

    if oracle_source:
        src = oracle.snapshot(bid, oracle_source["_story"],
                              workspace_id=oracle_source["_workspace_id"],
                              story_id=oracle_source["_story_id"], root=root)
        profile = artifacts.load_artifact(bid, "intake_profile", root=root)
        profile["oracle_source"] = src
        profile["source"] = {"kind": "tapd", "ref": f"tapd:{workspace_id}/{story_id}",
                             "content_digest": src["content_digest"]}
        artifacts.save_artifact(bid, "intake_profile", profile, root=root)
        result["intake_profile"] = profile
    return _ok(batch_id=bid, intake_profile=result["intake_profile"])


# ── 2–5. 判断类 ─────────────────────────────────────────────────────────────


@_requires_trusted_owner
@_serializes_batch_mutation
def clarify(*, batch_id: str, rules: list[dict[str, Any]],
            confirmed_facts_md: str, clarifications: list[dict[str, Any]] | None = None,
            caller_surface: str = "unknown",
            root: str | None = None) -> dict[str, Any]:
    blocked = _guarded(batch_id, "clarify", caller_surface, root)
    if blocked:
        return blocked
    r = _clarify.clarify(batch_id, rules=rules, clarifications=clarifications,
                         confirmed_facts_md=confirmed_facts_md)
    return _ok(business_frame=r["business_frame"]) if r.get("ok") else \
        _err("E_CLARIFY_REJECTED", "澄清产物没过牙", detail=r)


@_requires_trusted_owner
@_serializes_batch_mutation
def analyze(*, batch_id: str, example_map: list[dict[str, Any]],
            analysis_md: str, caller_surface: str = "unknown",
            root: str | None = None) -> dict[str, Any]:
    blocked = _guarded(batch_id, "analyze", caller_surface, root)
    if blocked:
        return blocked
    r = _analyze.analyze(batch_id, example_map=example_map, analysis_md=analysis_md)
    return _ok(test_analysis=r["test_analysis"]) if r.get("ok") else \
        _err("E_ANALYZE_REJECTED", "分析产物没过牙", detail=r)


@_requires_trusted_owner
@_serializes_batch_mutation
def draft_cases(*, batch_id: str, cases: list[dict[str, Any]],
                uncovered_rules: list[dict[str, Any]] | None = None,
                caller_surface: str = "unknown",
                root: str | None = None) -> dict[str, Any]:
    blocked = _guarded(batch_id, "draft_cases", caller_surface, root)
    if blocked:
        return blocked
    r = _draft.draft(batch_id, cases=cases, uncovered_rules=uncovered_rules)
    if not r.get("ok"):
        return _err("E_DRAFT_REJECTED", "用例草稿没过校验", detail=r)
    return _ok(case_draft=r["case_draft"], coverage=r.get("coverage"),
               warnings=r.get("warnings"))


@_requires_trusted_owner
@_serializes_batch_mutation
def adopt(*, batch_id: str, selected_draft_ids: list[str], caseset_slug: str = "",
          adopted_via: str = "workbench_selection", confirmed_by: str = "",
          idempotency_key: str = "", skip_drift_check: bool = False,
          caller_surface: str = "unknown", root: str | None = None) -> dict[str, Any]:
    """采纳冻结。**漂移闸在这里**（ADR-M2-02 第 6 条）。

    `skip_drift_check` 只服务单测与离线回放，**不是给调用方的绕过开关**：
    MCP 工具面不暴露这个参数（见 `main.py` 的签名），所以从 DT 侧调不到它。
    """
    blocked = _guarded(batch_id, "adopt", caller_surface, root)
    if blocked:
        return blocked
    if not skip_drift_check:
        drift = oracle.check_drift(batch_id, root=root)
        if not drift["ok"]:
            return _err(drift["code"], drift["message"], detail=drift.get("detail"))

    params = {"selected_draft_ids": sorted(selected_draft_ids or []),
              "caseset_slug": caseset_slug,
              "case_draft_sha256": _artifact_state_digest(
                  batch_id, "case_draft", root),
              "intake_profile_sha256": _artifact_state_digest(
                  batch_id, "intake_profile", root)}
    owner = _batch_partition(batch_id, root)
    with idempotency.reservation(batch_id, "adopt", params, idempotency_key,
                                 owner=owner, root=root) as (key, replay):
        if replay is not None:
            return replay
        r = _adopt.adopt(batch_id, selected_draft_ids=selected_draft_ids,
                         caseset_slug=caseset_slug, adopted_via=adopted_via,
                         confirmed_by=confirmed_by)
        if not r.get("ok"):
            return _err("E_ADOPT_REJECTED", "采纳没过 cases_gate", detail=r)
        # The bundle is a pure derivative of the approved caseset.  Keeping an
        # older bundle after re-adoption would let execute run the previous
        # cases while the UI shows the new frozen set.
        artifacts.invalidate_bundle(batch_id, owner=owner, root=root)
        out = _ok(approved_caseset=r.get("approved_caseset"),
                  cases_gate=r.get("cases_gate"), idempotency_key=key)
        idempotency.record(batch_id, key, "adopt", out, params=params,
                           owner=owner, root=root)
        return out


@_requires_trusted_owner
@_serializes_batch_mutation
def write_confirm(*, batch_id: str, case_ids: list[str] | None = None,
                  decided_by: str = "", confirmed_via: str = "ask_user_card",
                  caller_surface: str = "unknown",
                  root: str | None = None) -> dict[str, Any]:
    """写确认落账（人闸卡四的服务端半边，0028）。

    ## 这个工具为什么是补出来的

    `pw_runtime.py:88` 在执行时查 `events.jsonl` 里该用例的 `write_confirm`，
    查不到就 `SKIP_WRITE_UNCONFIRMED`。但生产侧**从来没有任何地方写过这种事件**
    （当时只产出 adopt_confirm / tier_confirm / tool_call 三种）。
    即：卡能弹、用户能答「4 条都允许」，**写用例照样被拦**，且用户无感知。
    与 BB-502 同形——挂载面与运行时能力面不一致，中间那段是哑的。

    ## 三条落法上的取舍

    1. **空选是合法答案，不是错误。** 交互稿卡四的默认就是一条都不勾，
       「都跳过，只跑只读的 8 条」是三个选项之一。空选要落一条**显式的**
       「什么都不授权」事件——它与「没答过」在账本上必须可分：前者是用户
       看过并拒绝了，后者是这道闸还没走。
    2. **只认写用例，且只认当前采纳集里的。** 给一个不写数据的用例授权是无意义的，
       给一个不存在的 case_id 授权是拼错——两者都判红而不是静默忽略：
       静默忽略的症状是「我明明点了允许，它还是跳过」，最难查的那一类。
    3. **逐条记 `source_case_digest`。** 授权的是**这批用例的内容**，不是一串 id。
       用例改了以后旧确认自动失效（判定在 `execute_run.write_authorization`），
       这是设计稿 §5.2 第 2 条「写确认不因重生成而复用上一次的确认」的落点。
    """
    blocked = _guarded(batch_id, "write_confirm", caller_surface, root)
    if blocked:
        return blocked
    if not artifacts.has_artifact(batch_id, "approved_caseset", root=root):
        return _err("E_NO_CASESET",
                    "还没有采纳集，写确认无从谈起——先采纳再确认写操作。")

    caseset = artifacts.load_artifact(batch_id, "approved_caseset", root=root)
    by_id = {c.get("case_id"): c for c in caseset.get("cases") or []}
    write_ids = {cid for cid, c in by_id.items()
                 if bool((c.get("side_effects") or {}).get("writes"))}

    requested = list(dict.fromkeys(case_ids or []))   # 去重且保序
    unknown = [c for c in requested if c not in by_id]
    if unknown:
        return _err("E_UNKNOWN_CASE",
                    f"这些 case_id 不在当前采纳集里：{unknown}。"
                    f"静默忽略会变成「我明明点了允许它还是跳过」，所以这里判红。")
    non_write = [c for c in requested if c not in write_ids]
    if non_write:
        return _err("E_NOT_A_WRITE_CASE",
                    f"这些用例不写数据，给它们写授权没有意义：{non_write}。"
                    f"通常是卡上勾错了行，或 side_effects.writes 标错了。")

    event = artifacts.append_event(batch_id, {
        "type": "write_confirm",
        "caseset_id": caseset.get("caseset_id", ""),
        "case_ids": requested,
        # 逐条记内容指纹：授权的是内容不是 id（见上文取舍 3）
        "digests": {c: by_id[c].get("source_case_digest", "") for c in requested},
        # 空选是显式答案：这一栏让「看过并拒绝」与「还没走这道闸」可分
        "decision": "authorized_some" if requested else "authorized_none",
        "declined": sorted(write_ids - set(requested)),
        "via": confirmed_via,
        "decided_by": decided_by,
    }, root=root)
    return _ok(event=event, authorized=requested,
               declined=sorted(write_ids - set(requested)),
               write_case_total=len(write_ids))


# ── 6–9. 机械类 ─────────────────────────────────────────────────────────────


@_requires_trusted_owner
@_serializes_batch_mutation
def compile_bundle(*, batch_id: str, idempotency_key: str = "",
                   caller_surface: str = "unknown",
                   root: str | None = None) -> dict[str, Any]:
    blocked = _guarded(batch_id, "compile", caller_surface, root)
    if blocked:
        return blocked
    owner = _batch_partition(batch_id, root)
    params = {"approved_caseset_sha256": _artifact_state_digest(
        batch_id, "approved_caseset", root)}
    with idempotency.reservation(batch_id, "compile", params, idempotency_key,
                                 owner=owner, root=root) as (key, replay):
        if replay is not None:
            return replay
        r = _compile.compile_bundle(batch_id)
        if not r.get("ok"):
            return _err("E_COMPILE_REJECTED", "编译没过 compile-gate", detail=r)
        out = _ok(bundle=r, idempotency_key=key)
        idempotency.record(batch_id, key, "compile", out, params=params,
                           owner=owner, root=root)
        return out


@_requires_trusted_owner
@_serializes_batch_mutation
def execute(*, batch_id: str, variables: dict[str, Any] | None = None,
            case_ids: list[str] | None = None, base_url_override: str = "",
            resume_run_id: str = "", timeout_s: int = 900,
            idempotency_key: str = "", triggered_by: str = "fresh",
            caller_surface: str = "unknown", root: str | None = None) -> dict[str, Any]:
    """执行。副作用最重的一个，幂等闸的主战场。

    `variables` 只把不可逆摘要纳入 key；既避免凭据落台账，也避免不同执行输入被
    错误重放。timeout 与执行范围同样属于 key，triggered_by 仍只作审计说明。
    """
    try:
        normalized_timeout = int(timeout_s)
    except (TypeError, ValueError):
        return _err("E_INVALID_TIMEOUT",
                    f"timeout_s 必须是 {_execute.MIN_TIMEOUT_S}.."
                    f"{_execute.MAX_TIMEOUT_S} 的整数")
    if not _execute.MIN_TIMEOUT_S <= normalized_timeout <= _execute.MAX_TIMEOUT_S:
        return _err("E_INVALID_TIMEOUT",
                    f"timeout_s 必须在 {_execute.MIN_TIMEOUT_S}.."
                    f"{_execute.MAX_TIMEOUT_S} 秒")
    blocked = _guarded(batch_id, "execute", caller_surface, root)
    if blocked:
        return blocked
    params = {"case_ids": sorted(case_ids or []),
              "base_url_override": base_url_override,
              "resume_run_id": resume_run_id,
              "timeout_s": normalized_timeout,
              "variables_sha256": sha256_digest(variables or {}),
              **_bundle_state(batch_id, root)}
    owner = _batch_partition(batch_id, root)
    with idempotency.reservation(batch_id, "execute", params, idempotency_key,
                                 owner=owner, root=root) as (key, replay):
        if replay is not None:
            return replay
        request_digest = sha256_digest(params)
        run_id = resume_run_id or artifacts.new_run_id()
        intent, created = idempotency.begin_execution_intent(
            batch_id, key, request_digest, run_id,
            write_risk=_selected_authorized_write_risk(
                batch_id, case_ids, owner=owner, root=root),
            owner=owner, root=root)
        if str(intent.get("request_digest") or "") != request_digest:
            return _err(
                "E_IDEMPOTENCY_CONFLICT",
                "这个执行 key 已绑定另一组输入；本次没有执行。",
                idempotency_key=key)
        recovered = _recover_completed_execute(
            batch_id, intent, owner=owner, root=root)
        if recovered is None and intent.get("status") == "complete":
            candidate = intent.get("result")
            if isinstance(candidate, dict):
                recovered = dict(candidate)
                recovered["recovered"] = True
        if recovered is not None:
            recovered["idempotency_key"] = key
            recovered["triggered_by"] = triggered_by
            idempotency.update_execution_intent(
                batch_id, key, status="complete", result=recovered,
                owner=owner, root=root)
            idempotency.record(batch_id, key, "execute", recovered, params=params,
                               owner=owner, root=root)
            return recovered
        if intent.get("status") in {"effect_started", "corrupt"} \
                and bool(intent.get("write_risk")):
            return _err(
                "E_EXECUTION_RECONCILIATION_REQUIRED",
                "上次执行可能已经向被测系统发送写请求，但没有留下完整收据。"
                "为避免自动重复写入，本次已停止；请先人工核对目标系统后再决定。",
                run_id=intent.get("run_id"), idempotency_key=key)
        retry_resume = ""
        reserved_run_id = str(intent.get("run_id") or run_id)
        if not created and intent.get("status") == "effect_started":
            retry_resume = reserved_run_id

        def mark_effect_boundary() -> None:
            idempotency.update_execution_intent(
                batch_id, key, status="effect_started",
                owner=owner, root=root)

        r = _execute.execute(batch_id, variables=variables, case_ids=case_ids,
                             resume_run_id=resume_run_id or retry_resume,
                             base_url_override=base_url_override,
                             timeout_s=normalized_timeout,
                             triggered_by=triggered_by, root=root,
                             reserved_run_id=reserved_run_id,
                             on_effect_boundary=mark_effect_boundary)
        if not r.get("ok"):
            return _err("E_EXECUTE_FAILED", str(r.get("error") or "执行失败"), detail=r)
        out = _ok(run_id=r.get("run_id"), receipt=r.get("receipt"),
                  idempotency_key=key, triggered_by=triggered_by)
        idempotency.update_execution_intent(
            batch_id, key, status="complete", result=out,
            owner=owner, root=root)
        idempotency.record(batch_id, key, "execute", out, params=params,
                           owner=owner, root=root)
        return out


@_requires_trusted_owner
def project(*, run_id: str, caller_surface: str = "unknown",
            root: str | None = None) -> dict[str, Any]:
    r = _project.project(run_id)
    if not r.get("ok"):
        return _err("E_PROJECT_REJECTED", "投影没过 evidence_gate/mechanical_check",
                    detail=r)
    return _ok(**{k: v for k, v in r.items() if k != "ok"})


def _trace_handle(run_dir: str, verdict: dict[str, Any]) -> str | None:
    """Return a relative, existing trace handle; never leak an absolute path."""
    from server.journey.pw_harness import case_slug
    ident = str(verdict.get("id") or "").split("/")[-1]
    suffix = case_slug(ident)
    try:
        names = os.listdir(run_dir)
    except OSError:
        return None
    for name in names:
        if not name.endswith(suffix):
            continue
        candidate = os.path.join(run_dir, name, "trace.zip")
        if os.path.isfile(candidate):
            return os.path.join(name, "trace.zip").replace("\\", "/")
    return None


@_requires_trusted_owner
@_serializes_batch_mutation
def coverage(*, batch_id: str, run_id: str = "", caller_surface: str = "unknown",
             root: str | None = None) -> dict[str, Any]:
    blocked = _guarded(batch_id, "coverage", caller_surface, root)
    if blocked:
        return blocked
    r = _coverage.build_coverage(batch_id, run_id)
    if not r.get("ok"):
        return _err("E_COVERAGE_NOT_DONE", "覆盖收口没过（有 gap 未解释）", detail=r)
    return _ok(**{k: v for k, v in r.items() if k != "ok"})


# ── 读接口（薄壳的数据来源）─────────────────────────────────────────────────


@_requires_trusted_owner
def list_batches(*, owner: str = "", caller_surface: str = "unknown",
                 root: str | None = None) -> dict[str, Any]:
    rows = artifacts.list_batches(owner=owner or None, root=root)
    return _ok(batches=[{
        "batch_id": b["batch_id"], "title": b.get("title", ""),
        "base_url": b.get("base_url", ""), "owner": b.get("owner", ""),
        "created_at": b.get("created_at", ""),
        "run_count": len(b.get("run_ids") or []),
        "stepper": b.get("stepper", []),
    } for b in rows], stepper_definition=stepper_definition())


def stepper_definition() -> list[dict[str, str]]:
    """九格的**完整定义**（kind + 展示名 + 顺序）。

    前端与调试面都只渲染服务端给的这份，不再各持常量——这就是 §9 第 3 条
    「消灭第二真相」的落点。界面上的四段是它的投影，不是另一个数。
    """
    return [{"artifact": k, "label": artifacts.STEP_LABELS.get(k, k)}
            for k in artifacts.STEPPER_ORDER]


@_requires_trusted_owner
def get_batch(*, batch_id: str, caller_surface: str = "unknown",
              root: str | None = None) -> dict[str, Any]:
    check = gate.require_batch(batch_id, root=root)
    if not check["ok"]:
        return _err(check["code"], check["message"])
    meta = artifacts.load_batch(batch_id, root=root)
    steps = artifacts.stepper(batch_id, root=root)
    present = {s["artifact"] for s in steps if s["present"]}
    payload: dict[str, Any] = {}
    for kind in ("intake_profile", "business_frame", "test_analysis", "case_draft",
                 "approved_caseset", "coverage_ledger"):
        if kind in present:
            payload[kind] = artifacts.load_artifact(batch_id, kind, root=root)
    runs = []
    for rid in (meta.get("run_ids") or []):
        rd = artifacts.run_dir(rid, root=root)
        receipt_path = os.path.join(rd, "receipt.json")
        row: dict[str, Any] = {"run_id": rid, "receipt": None, "verdicts": []}
        if os.path.isfile(receipt_path):
            import json
            with open(receipt_path, encoding="utf-8") as fh:
                row["receipt"] = json.load(fh)
        row["verdicts"] = _project.read_verdicts(rid)
        for verdict in row["verdicts"]:
            verdict["trace_rel"] = _trace_handle(rd, verdict)
        runs.append(row)
    return _ok(batch=meta, stepper=steps, stepper_definition=stepper_definition(),
               artifacts=payload, runs=runs,
               events=artifacts.read_events(batch_id, root=root))


@_requires_trusted_owner
def open_trace(*, batch_id: str, run_id: str, trace_rel: str,
               caller_surface: str = "unknown",
               root: str | None = None) -> dict[str, Any]:
    """Open one owner-bound trace after batch/run/path membership checks."""
    check = gate.require_batch(batch_id, root=root)
    if not check["ok"]:
        return _err(check["code"], check["message"])
    batch = artifacts.load_batch(batch_id, root=root)
    if run_id not in (batch.get("run_ids") or []):
        return _err("E_NO_TRACE", "trace 不属于该批次。")
    rd = artifacts.run_dir(run_id, root=root)
    candidate = os.path.realpath(os.path.join(rd, str(trace_rel or "")))
    try:
        inside = os.path.commonpath([os.path.realpath(rd), candidate]) == \
            os.path.realpath(rd)
    except ValueError:
        inside = False
    if not inside or os.path.basename(candidate) != "trace.zip" or \
            not os.path.isfile(candidate):
        return _err("E_NO_TRACE", "trace 不存在或路径非法。")
    from server.gateway import journey_console
    result = journey_console.open_trace(run_id, trace_rel)
    if not result.get("ok"):
        return _err(str(result.get("code") or "E_TRACE_SPAWN_FAILED"),
                    str(result.get("error") or "trace 打开失败"))
    # MCP/browser payload never returns the server's absolute path.
    # The host command contains an absolute artifact path. It is useful on the
    # host-only console but must not cross the authenticated browser/MCP bridge.
    return _ok(started=bool(result.get("started")),
               spawn_error=str(result.get("spawn_error") or ""))
