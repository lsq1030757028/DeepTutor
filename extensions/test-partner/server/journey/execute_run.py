# -*- coding: utf-8 -*-
"""execute_run — 原子工具 7：执行。

输入：AutomationBundle + 环境引用 → 输出 `run_receipt` + evidence-bundle 素材。
牙（挂产物）：执行红线五条（bundle 内嵌 runtime 落点）+ 写确认（无 write_confirm
事件的写用例一律 SKIP_WRITE_UNCONFIRMED）+ 凭据零落盘（run 后机械扫描）。

资源纪律（ADR-M1-02）：并发 run ≤ 2（acquire_slot）；per-run 浏览器生命周期在
bundle conftest；run 结束 reap 登记 PID；results.jsonl 增量落盘支持 resume。
凭据：值只经 TP_VARS_JSON 环境变量注入子进程内存，不写任何盘面。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable

from server.journey import artifacts, pw_runtime, redlines
from server.journey import process_registry as preg
from server.journey.gates import credential_scan
from server.journey.gates import track_purity as _track_purity
from server.journey.pw_harness import case_slug

#: assertion_layers.required_evidence 词表 → run 目录证据文件映射
EVIDENCE_FILES = {
    "playwright_trace": "trace.zip",
    "screenshot": "final.png",
    "http_transcript": "transcript.json",
    "console_log": "console.log",
    "db_snapshot": "db_snapshot.json",
}
RUN_META_NAME = "run-meta.json"
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 1200


def _write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    """同目录原子发布 JSON，避免 resume 读到半截契约。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json_object(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        body = json.load(fh)
    if not isinstance(body, dict):
        raise ValueError(f"{os.path.basename(path)} 不是 JSON 对象")
    return body


def _execution_verdict(rows: list[dict[str, Any]], pytest_rc: int,
                       expected_case_ids: list[str]) -> tuple[str, dict[str, Any]]:
    """Classify the run and prove every selected case produced exactly one row."""
    counts: dict[str, int] = {}
    actual_ids: list[str] = []
    malformed_rows = 0
    for row in rows:
        outcome = str(row.get("outcome") or "")
        case_id = str(row.get("case_id") or "")
        if not outcome or not case_id:
            malformed_rows += 1
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
        actual_ids.append(case_id)
    expected = set(expected_case_ids)
    actual = set(actual_ids)
    duplicate_ids = sorted({case_id for case_id in actual if actual_ids.count(case_id) > 1})
    integrity = {
        "ok": pytest_rc == 0 and malformed_rows == 0 and not duplicate_ids
        and actual == expected,
        "pytest_returncode": pytest_rc,
        "missing_case_ids": sorted(expected - actual),
        "unexpected_case_ids": sorted(actual - expected),
        "duplicate_case_ids": duplicate_ids,
        "malformed_row_count": malformed_rows,
    }
    if counts.get("failed"):
        verdict = "FAIL"
    elif not integrity["ok"]:
        verdict = "BLOCK"
    elif counts.get("blocked") or counts.get("no_assertions"):
        verdict = "BLOCK"
    elif counts.get("passed") or counts.get("observed"):
        verdict = "PASS"
    else:
        verdict = "BLOCK"
    return verdict, {"counts": counts, **integrity}


def _resume_contract_problem(run_dir: str, expected: dict[str, str]) -> str:
    """只读核验 resume 契约；空串表示可以继续。"""
    if not os.path.isdir(run_dir):
        return "resume_run_id 在当前 owner 分区不存在"
    docs: list[tuple[str, dict[str, Any]]] = []
    for name in (RUN_META_NAME, "receipt.json"):
        path = os.path.join(run_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            docs.append((name, _read_json_object(path)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return f"{name} 无法作为可信 resume 契约读取：{exc}"
    if not docs:
        return "run 缺少 run-meta.json/receipt.json，归属与执行身份不可验证"
    for name, doc in docs:
        for field, wanted in expected.items():
            # 旧 receipt 没有 owner_partition；其 owner 已由精确分区路径证明。
            if field == "owner_partition" and name == "receipt.json" and field not in doc:
                continue
            if str(doc.get(field, "")) != str(wanted):
                return (f"{name} 的 {field} 与当前执行不匹配："
                        f"expected={wanted!r}, actual={doc.get(field)!r}")
    return ""


def write_authorization(batch_id: str, root: str | None = None, *,
                        owner: str | None = None) -> dict[str, Any]:
    """本批次当前**有效**的写确认。返回 {authorized, dropped}。

    ## 为什么一条确认会「失效」

    写确认是对**具体一批用例内容**的同意，不是对一串 id 的同意。
    用户在卡上看到「TC-005 新建一笔订单」才点的允许；如果之后 caseset 被重新采纳、
    TC-005 变成了「删除全部订单」，那张旧确认**不该继续生效**——id 一样，同意的
    东西已经不是同一个了。设计稿 §5.2 第 2 条要的「写确认不因重生成而复用上一次
    的确认」，机制落点就在这里。

    判据是 `source_case_digest` 逐条比对：确认事件里记的 digest 与当前 caseset 里
    该用例的 digest 不同 → 这条授权作废，进 `dropped`。

    ## 失效必须说出口

    `dropped` 不是内部细节：一条被作废的授权，症状是执行时
    `SKIP_WRITE_UNCONFIRMED`——与「用户压根没确认」长得一模一样。
    两者要分开说（0021 红线六「成对错误态分开说」），所以这里把作废原因带出去，
    由 run 收据落账。

    没有 `digests` 的确认事件一律**不授权**：认不出它同意的是什么内容，
    就不能拿它当同意。这个方向是刻意的——失败方向是少给。
    """
    caseset: dict[str, Any] = {}
    if artifacts.has_artifact(batch_id, "approved_caseset", owner=owner, root=root):
        caseset = artifacts.load_artifact(batch_id, "approved_caseset",
                                          owner=owner, root=root)
    current = {c.get("case_id"): c.get("source_case_digest")
               for c in caseset.get("cases") or []}

    authorized: set[str] = set()
    dropped: list[dict[str, str]] = []
    caseset_id = str(caseset.get("caseset_id") or "")
    write_case_ids = sorted(
        str(case.get("case_id") or "")
        for case in caseset.get("cases") or []
        if pw_runtime.effective_write_risk({
            "writes": bool((case.get("side_effects") or {}).get("writes")),
            "actions": list((((case.get("automation") or {}).get("recipe") or {}).get(
                "actions") or [])),
        })
    )
    all_events = [event for event in artifacts.read_events(
        batch_id, owner=owner, root=root)
        if event.get("type") == "write_confirm"]
    events = [event for event in all_events
              if str(event.get("caseset_id") or "") == caseset_id]
    if not events:
        # Legacy or hand-written rows must never authorize a write, but their
        # rejection belongs in the receipt so operators can distinguish a
        # malformed/stale decision from no confirmation at all.
        if all_events:
            event = all_events[-1]
            for cid in list(event.get("case_ids") or []):
                dropped.append({
                    "case_id": cid,
                    "at": str(event.get("at", "")),
                    "reason": "确认事件没记 digest 或 caseset_id，认不出它同意的是什么内容",
                })
        return {
            "authorized": authorized,
            "dropped": dropped,
            "decision_state": {
                "caseset_id": caseset_id,
                "decided": False,
                "authorized": [],
                "declined": write_case_ids,
            },
        }
    # Each confirmation is a complete decision for the current caseset.  The
    # latest serialized ledger row replaces the previous decision, so an empty
    # selection is a real revoke-all rather than a no-op.
    event = events[-1]
    digests = event.get("digests") or {}
    claimed = list(event.get("case_ids") or [])
    for cid in claimed:
        recorded = digests.get(cid)
        if not recorded:
            dropped.append({"case_id": cid, "at": str(event.get("at", "")),
                            "reason": "确认事件没记 digest，认不出它同意的是什么内容"})
        elif cid not in current:
            dropped.append({"case_id": cid, "at": str(event.get("at", "")),
                            "reason": "该用例已不在当前采纳集里"})
        elif current[cid] != recorded:
            dropped.append({"case_id": cid, "at": str(event.get("at", "")),
                            "reason": "用例内容已变（digest 不符）——旧确认同意的不是"
                                      "现在这条，须重新确认"})
        else:
            authorized.add(cid)
    return {
        "authorized": authorized,
        "dropped": dropped,
        # This deliberately excludes timestamps and actor prose. Repeating the
        # same complete decision is semantically idempotent, while confirm and
        # revoke produce different execution identities.
        "decision_state": {
            "caseset_id": caseset_id,
            "decided": True,
            "authorized": sorted(authorized),
            "declined": sorted(set(write_case_ids) - authorized),
            "dropped": sorted(
                (
                    str(item.get("case_id") or ""),
                    str(item.get("reason") or ""),
                )
                for item in dropped
            ),
        },
    }


def _write_authorized_ids(batch_id: str, *, owner: str | None = None,
                          root: str | None = None) -> set[str]:
    return write_authorization(batch_id, root, owner=owner)["authorized"]


def _read_results(run_dir: str) -> list[dict[str, Any]]:
    path = os.path.join(run_dir, "results.jsonl")
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _instance_fingerprint(base_url: str, probe: dict[str, Any]) -> str:
    """指纹串。**签名已改**（M1 Advisory A2）：由**执行期**的靶与探针产生。

    原版取 `intake_profile` 的接入期探针，而本次执行真正打的靶是
    `base_url_override or batch.base_url`——override 存在时**必然错锚**。
    M1 实测现场：run 打的是 `127.0.0.1:9`（不可达），bundle 里却写着
    `target@127.0.0.1:8047 status=200`——一个完全没被访问过的靶的成功指纹。
    """
    from server.journey import redlines
    host = redlines.host_key(base_url)
    # body 摘要用 sha256:<64hex> 形态（凭据扫描按 digest 放行；含冒号不粘连）
    body_sha = probe.get("body_head_sha256", "")
    return (f"target@{host} status={probe.get('status')} "
            f"title={probe.get('page_title', '')!r} "
            f"body=sha256:{body_sha}")


#: UI 轨才有的 op。bundle 里出现任何一个就说明生成侧越轨了（E22 / DoD#4b）。
#:
#: **单一真相在 `gates/track_purity.py`**，这里只是转出去。原先这是一份手抄清单，
#: 实测漏了 `wait_load` 与 `expect_hidden` 两个（两者都要 `self.page`，铁定 UI 轨）——
#: 一份只用这两个 op 的 UI 用例，反推结果会是 "api"。手抄清单与运行时之间没有闸，
#: 就一定会漂；track_purity 那边与 `pw_runtime` 的 `_op_*` 方法逐个对拍。
UI_TRACK_OPS = tuple(sorted(_track_purity.UI_OPS))

#: API 轨不可能产出的证据类型。要求了必然缺证（DoD#4b 第二半）。同样转出。
UI_ONLY_EVIDENCE = tuple(sorted(_track_purity.UI_ONLY_EVIDENCE))


def detect_track(manifest: dict[str, Any]) -> str:
    """从 bundle 反推本趟走的是哪条轨。

    判据是 op 集合而不是配置项：配置说的是"打算走哪条"，op 说的是"实际会发生什么"。
    两者不一致时，以实际为准——**这里不负责拦，只负责如实说**；
    拦在编译期，见 `gates/track_purity.py`（E22）。

    留一句给后人：本函数的 else 分支（「没命中 UI 清单就是 api」）是个**静默默认值**。
    它今天安全，只因为清单与运行时之间有了对拍闸。**别把清单改回手抄的**，
    否则运行时每新增一个 UI op，这里都会悄悄多放行一种越轨。
    """
    cases = manifest.get("cases", []) or []
    declared = {str(case.get("track") or "") for case in cases}
    if len(declared) == 1 and declared <= {"api", "ui"}:
        # compile-gate already proved declaration↔op consistency and that the
        # bundle is single-track.  Actions are intentionally absent from the
        # runtime manifest, so the frozen track field is the lossless source.
        return declared.pop()
    for case in cases:
        for action in case.get("actions", []) or []:
            if str(action.get("op", "")) in UI_TRACK_OPS:
                return "ui"
    return "api"


def build_target_identity(base_url: str, probe: dict[str, Any], track: str) -> dict[str, Any]:
    """本次执行的靶身份。**字段取值按轨道分口径**（设计稿 §8.2）。

    为什么 `page_title` 在 API 轨必须是 `None` 而不是空串：它是 HTML 概念
    （探针用正则抓 `<title>`），在 JSON/移动端后端上恒返空串——字段仍在、
    但恒等于空，等于指纹少了一维却不报警。`None` 让"没有标题"与"标题是空"
    可区分，也让消费方知道这一维在本轨不生效。
    """
    from server.journey import redlines
    reachable = bool(probe.get("reachable"))
    identity: dict[str, Any] = {
        # 主判别维：target_drift 只由它决定
        "base_url_host": redlines.host_key(base_url),
        # 4xx/5xx **也算探到**，不等于不可达
        "status": probe.get("status") if reachable else "unreachable",
        "body_sha256": probe.get("body_head_sha256") or None,
        "service_banner": probe.get("service_banner") or None,
        "content_type": probe.get("content_type") or None,
        "page_title": (probe.get("page_title") or None) if track == "ui" else None,
        "track": track,
        "source": "run-time-probe",
    }
    return identity


def _intake_fingerprint_of(batch_id: str, *, owner: str | None = None,
                           root: str | None = None) -> dict[str, Any]:
    """接入期探到的靶。**只作对照，不作判据**——判据用 target_identity。"""
    try:
        intake = artifacts.load_artifact(batch_id, "intake_profile",
                                         owner=owner, root=root)
    except artifacts.ArtifactError:
        return {}
    from server.journey import redlines
    probe = intake.get("target_probe") or {}
    return {"base_url_host": redlines.host_key(intake.get("base_url", "")),
            "status": probe.get("status"), "source": "intake-probe"}


def _drifted(batch_id: str, identity: dict[str, Any], *,
             owner: str | None = None, root: str | None = None) -> bool:
    """接入期与执行期的靶 host 不一致 = 换环境跑了。

    **不阻断**（换环境跑是合法的），但结论卡上必须可见——
    否则"这批结论是在哪个环境上得到的"就只能靠猜。
    """
    intake = _intake_fingerprint_of(batch_id, owner=owner, root=root)
    if not intake.get("base_url_host"):
        return False
    return intake["base_url_host"] != identity.get("base_url_host")


def _required_evidence_ok(case: dict[str, Any], run_dir: str, slug: str) -> list[str]:
    """按 assertion_layers.required_evidence 查证据类别在场；返回缺失类别。"""
    missing = []
    for layer in (case.get("assertion_layers") or {}).values():
        for kind in layer.get("required_evidence", []):
            fname = EVIDENCE_FILES.get(kind)
            if not fname:
                continue
            p = os.path.join(run_dir, slug, fname)
            if not (os.path.isfile(p) and os.path.getsize(p) > 0):
                missing.append(kind)
    return sorted(set(missing))


def _build_evidence_bundle(batch_id: str, run_dir: str, fingerprint: str,
                           rows: list[dict[str, Any]], *,
                           owner: str | None = None,
                           root: str | None = None) -> dict[str, Any]:
    caseset = artifacts.load_artifact(batch_id, "approved_caseset",
                                      owner=owner, root=root)
    by_id = {c["case_id"]: c for c in caseset["cases"]}
    conclusions = []
    for r in rows:
        cid = r["case_id"]
        case = by_id.get(cid, {})
        slug = case_slug(cid)
        # 每 case 派生 result.json（单文件证据，供结论引用）
        case_dir = os.path.join(run_dir, slug)
        os.makedirs(case_dir, exist_ok=True)
        with open(os.path.join(case_dir, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(r, fh, ensure_ascii=False, indent=1)
        evidence = [slug + "/result.json"]
        etypes = ["log"]
        if r.get("track") == "ui" or os.path.isfile(os.path.join(case_dir, "trace.zip")):
            if os.path.isfile(os.path.join(case_dir, "trace.zip")):
                evidence.append(slug + "/trace.zip")
                # 黑盒活靶观测：live（实例身份由 M4 降级指纹锚定；
                # dom/screenshot 类型的部署锚牙保留给 DT 自身 web 面（M2））
                etypes.append("live")
        if os.path.isfile(os.path.join(case_dir, "transcript.json")):
            evidence.append(slug + "/transcript.json")
            etypes.append("http")
        if os.path.isfile(os.path.join(case_dir, "db_snapshot.json")):
            evidence.append(slug + "/db_snapshot.json")
            etypes.append("db")
        outcome = r.get("outcome")
        if outcome == "passed":
            verdict = "pass"
        elif outcome == "failed":
            verdict = "fail"
        else:  # skipped / blocked / no_assertions / observed(probing)
            verdict = "undecidable"
        note_bits = []
        if outcome == "passed":
            missing = _required_evidence_ok(case, run_dir, slug)
            if missing:
                verdict = "undecidable"
                note_bits.append(f"required_evidence 缺类:{missing}——不得 PASS,降 undecidable")
        if r.get("probing"):
            note_bits.append("探测性:不进 PASS 判据")
        if r.get("skip_code"):
            note_bits.append(f"{r['skip_code']}:{r.get('skip_reason', '')}")
        if r.get("blocked_reason"):
            note_bits.append("BLOCKED:" + str(r["blocked_reason"]))
        claim = (f"{case.get('title', cid)}｜预期:{case.get('expected', '')}"
                 + ("｜" + ";".join(note_bits) if note_bits else ""))
        strength = ("undecidable" if verdict == "undecidable"
                    else ("hard-anchor" if len(evidence) >= 2 else "single-anchor"))
        conclusions.append({
            "id": cid, "claim": claim, "verdict": verdict, "strength": strength,
            "evidence": evidence, "evidence_types": etypes,
        })
    bundle = {
        "schema_version": "1.0",
        "agent_id": "test-partner",
        "build_fingerprint": fingerprint,
        "conclusions": conclusions,
    }
    with open(os.path.join(run_dir, "evidence-bundle.json"), "w",
              encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=1)
    return bundle


def execute(batch_id: str, *, variables: dict[str, Any] | None = None,
            case_ids: list[str] | None = None, resume_run_id: str = "",
            base_url_override: str = "", timeout_s: int = 900,
            triggered_by: str = "fresh", root: str | None = None,
            reserved_run_id: str = "",
            on_effect_boundary: Callable[[], None] | None = None) -> dict[str, Any]:
    """跑一趟。

    `triggered_by`（`fresh` / `regenerate-replay`）落进 run_receipt——
    让"为什么这个批次有两条一模一样的 run"在账本上可解释，而不是靠猜
    （设计稿 §5.2 第三层）。它**不参与幂等 key**：幂等看的是输入，
    不是谁按的按钮。
    """
    try:
        timeout_s = int(timeout_s)
    except (TypeError, ValueError):
        return {"ok": False, "error": "INVALID_TIMEOUT",
                "detail": f"timeout_s 必须是 {MIN_TIMEOUT_S}..{MAX_TIMEOUT_S} 的整数"}
    if not MIN_TIMEOUT_S <= timeout_s <= MAX_TIMEOUT_S:
        return {"ok": False, "error": "INVALID_TIMEOUT",
                "detail": f"timeout_s 必须在 {MIN_TIMEOUT_S}..{MAX_TIMEOUT_S} 秒"}
    batch = artifacts.load_batch(batch_id, root=root)
    owner = artifacts.safe_owner(batch.get("partition") or batch.get("owner"))
    # 解析到 batch 后立刻钉死 owner；后续不再走“跨 owner 扫描”兼容路径。
    batch = artifacts.load_batch(batch_id, owner=owner, root=root)
    bundle_dir = os.path.join(
        artifacts.batch_dir(batch_id, owner=owner, root=root), "bundle")
    manifest_path = os.path.join(bundle_dir, "bundle.json")
    if not os.path.isfile(manifest_path):
        return {"ok": False, "error": "NO_BUNDLE",
                "hint": "先跑 compile 产出 AutomationBundle"}
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest_case_ids = [str(item["case_id"]) for item in manifest["cases"]]
    requested_case_ids = list(case_ids or manifest_case_ids)
    unknown_case_ids = sorted(set(requested_case_ids) - set(manifest_case_ids))
    if unknown_case_ids:
        return {"ok": False, "error": "UNKNOWN_CASE_IDS",
                "detail": unknown_case_ids}
    selected_case_ids = [case_id for case_id in manifest_case_ids
                         if case_id in set(requested_case_ids)]
    test_names = [item["test_name"] for item in manifest["cases"]
                  if item["case_id"] in set(selected_case_ids)]
    if not test_names:
        return {"ok": False, "error": "NO_CASES_SELECTED"}
    raw_base_url = base_url_override or batch.get("base_url") or ""
    validated_url = redlines.safe_target_url(raw_base_url)
    if not validated_url["ok"]:
        return {"ok": False, "error": "INVALID_BASE_URL",
                "detail": validated_url["error"]}
    base_url = validated_url["url"]

    contract = {
        "batch_id": batch_id,
        "owner_partition": owner,
        "caseset_id": str(manifest["caseset_id"]),
        "caseset_sha256": str(manifest.get("caseset_sha256") or ""),
        "compiler_version": str(manifest["compiler_version"]),
        "base_url_host": redlines.host_key(base_url),
    }
    run_id = resume_run_id or reserved_run_id or artifacts.new_run_id()
    contract["run_id"] = run_id
    try:
        run_dir = artifacts.run_dir(
            run_id, create=not bool(resume_run_id), owner=owner, root=root)
    except artifacts.ArtifactError as exc:
        return {"ok": False, "error": "RESUME_RUN_INVALID", "detail": str(exc)}
    if resume_run_id:
        problem = _resume_contract_problem(run_dir, contract)
        if problem:
            return {"ok": False, "error": "RESUME_RUN_MISMATCH", "detail": problem}
    else:
        _write_json_atomic(os.path.join(run_dir, RUN_META_NAME), {
            "artifact": "run_meta",
            "schema_version": "1.0",
            **contract,
            "created_at": artifacts.now_iso(),
        })
    done_ids = [r["case_id"] for r in _read_results(run_dir)] if resume_run_id else []

    slot = preg.acquire_slot(run_id, run_dir, root=root)
    if not slot["ok"]:
        if not resume_run_id:
            artifacts.discard_fresh_run(
                run_id, owner=owner, root=root)
        return slot

    variables = dict(variables or {})
    write_auth = write_authorization(batch_id, root, owner=owner)
    write_ok = write_auth["authorized"]
    env = dict(os.environ)
    env.update({
        "TP_BASE_URL": base_url,
        "TP_RUN_DIR": run_dir,
        "TP_VARS_JSON": json.dumps(variables, ensure_ascii=False),
        "TP_WRITE_AUTHORIZED_IDS": ",".join(sorted(write_ok)),
        "TP_DONE_CASE_IDS": ",".join(done_ids),
    })
    # L3 只读 DSN：与 TP_VARS_JSON 同纪律——值只经环境变量进子进程内存，
    # 不写 bundle、不写 run 目录。这里**不塞默认值**：没配就是没有 L3，
    # 用例里的 db op 会 BLOCKED，而不是悄悄跳过（护栏 3）。
    # 注意 env 是 os.environ 的副本，宿主若已设该变量本就会传下去；
    # 这一行的作用是让"它是被有意传下去的"在代码里看得见，且能被测试断言。
    from server.journey import db_readonly as _dbro
    l3_dsn_present = _dbro.dsn_present()
    started = artifacts.now_iso()
    t0 = time.time()
    try:
        # The caller persists an execution intent before entering this
        # function.  Advancing it here, immediately before pytest can issue a
        # target request, closes the crash window without classifying earlier
        # validation/slot failures as possible external writes.
        if on_effect_boundary is not None:
            on_effect_boundary()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-c", "pytest.ini",
             "--tb=line", "-k", " or ".join(test_names), "."],
            cwd=bundle_dir, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s)
        # stdout/stderr 是不受信输入，可能回显本轮变量。业务证据已经在
        # results.jsonl 与 per-case 目录；receipt 只记“已省略”，不持久化 raw tail。
        pytest_tail_omitted = bool(proc.stdout or proc.stderr)
        pytest_tail = "(pytest output omitted by credential policy)" \
            if pytest_tail_omitted else ""
        pytest_rc = proc.returncode
    except subprocess.TimeoutExpired:
        pytest_tail = f"(超时 {timeout_s}s 被杀；pytest output omitted)"
        pytest_tail_omitted = True
        pytest_rc = -1
    finally:
        reap = preg.reap_run(run_dir)
        preg.release_slot(run_id, root=root)

    rows = _read_results(run_dir)
    expected_case_ids = list(dict.fromkeys([*done_ids, *selected_case_ids]))
    verdict, result_integrity = _execution_verdict(
        rows, pytest_rc, expected_case_ids)
    counts = result_integrity.pop("counts")

    # A2：执行期就地探针 —— 指纹锚的是**这一趟真正打的靶**，不是接入期那个。
    # 探针失败也如实记（status=unreachable），那正是类 1 故障场景要的语义。
    from server.journey.ingest import probe_target
    exec_probe = probe_target(base_url)
    track = detect_track(manifest)
    target_identity = build_target_identity(base_url, exec_probe, track)
    fingerprint = _instance_fingerprint(base_url, exec_probe)
    evidence_bundle = _build_evidence_bundle(
        batch_id, run_dir, fingerprint, rows, owner=owner, root=root)
    pre_scan_verdict = verdict
    receipt = {
        "artifact": "run_receipt",
        # 1 → 1.1：新增 target_identity / target_drift / triggered_by，
        # 且 build_fingerprint 的语义由「接入期」改为「执行期」。
        # 迁移说明见 docs/schema-changelog.md；M4 判据的消费点同步改读 target_identity。
        "schema_version": "1.1",
        "run_id": run_id,
        "batch_id": batch_id,
        "owner_partition": owner,
        "caseset_id": manifest["caseset_id"],
        "caseset_sha256": manifest.get("caseset_sha256", ""),
        "compiler_version": manifest["compiler_version"],
        "base_url_host": redlines.host_key(base_url),   # 收据只记 host，不记完整 URL
        # A2：两个指纹**并列，不互相冒充**。
        #   target_identity  = 本次执行就地探到的靶（判据用这个）
        #   intake_fingerprint = 接入期探到的靶（只作对照）
        # 换环境跑是合法的，所以 drift 不阻断——但结论卡上必须看得见。
        "target_identity": target_identity,
        # L3 通道在**本次执行**是否可用。与 bundle 里的 capability_l3_granted
        # 是两回事：那个记的是编译时声明，这个记的是执行时现实。两者不一致
        # （声明有、执行时没有）时 db op 会 BLOCKED，收据上这两格并列摆着，
        # 让"为什么这批写用例没有数据层证据"有一处能查。
        "l3_channel_available": l3_dsn_present,
        "bundle_l3_granted": bool(manifest.get("capability_l3_granted")),
        "intake_fingerprint": _intake_fingerprint_of(
            batch_id, owner=owner, root=root),
        "target_drift": _drifted(batch_id, target_identity,
                                  owner=owner, root=root),
        "triggered_by": triggered_by,
        "started_at": started,
        "finished_at": artifacts.now_iso(),
        "elapsed_s": round(time.time() - t0, 1),
        "resumed": bool(resume_run_id),
        "selected_case_count": len(test_names),
        "counts": counts,
        "result_integrity": result_integrity,
        "verdict": verdict,
        "pytest_returncode": pytest_rc,
        "pytest_tail": pytest_tail,
        "pytest_tail_omitted": pytest_tail_omitted,
        # 先落 provisional=false；最终值只由覆盖 receipt 的二次扫描决定。
        "credential_scan_ok": False,
        "credential_scan_passes": 0,
        # 被作废的写确认要落账：作废后的症状是 SKIP_WRITE_UNCONFIRMED，
        # 与「用户压根没确认」长得一模一样，不写出来就无从区分（0021 红线六）。
        "write_confirm_dropped": write_auth["dropped"],
        "reap": reap,
        "conclusion_count": len(evidence_bundle["conclusions"]),
    }
    receipt_path = os.path.join(run_dir, "receipt.json")
    _write_json_atomic(receipt_path, receipt)

    def _secret_leaves(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [leaf for item in value.values() for leaf in _secret_leaves(item)]
        if isinstance(value, (list, tuple)):
            return [leaf for item in value for leaf in _secret_leaves(item)]
        if value is None:
            return []
        if isinstance(value, bool):
            return ["true" if value else "false"]
        return [str(value)]

    known_secrets = list(dict.fromkeys(_secret_leaves(variables)))

    def _public_scan_report(scan: dict[str, Any], *, passes: int) -> dict[str, Any]:
        return {
            "ok": bool(scan["ok"]),
            "known_hits": scan["known_hits"],
            # raw entropy token 不落报告，只留定位信息与短 preview。
            "entropy_hits": [
                {k: h[k] for k in ("file", "token_preview", "length", "entropy")}
                for h in scan["entropy_hits"]
            ],
            "allowlisted_hits": scan.get("allowlisted_hits", []),
            "entropy_skipped_large_files": scan.get(
                "entropy_skipped_large_files", []),
            "archive_rejections": scan.get("archive_rejections", []),
            "scanned_files": scan["scanned_files"],
            "passes": passes,
            "note": scan["note"],
        }

    # pass 1 覆盖 provisional receipt；写受控报告与初步结果后，pass 2 再覆盖
    # receipt/credscan，堵住“扫描绿后又把 stdout 秘密写进去”的时序洞。
    scan1 = credential_scan.scan_tree(run_dir, known_secrets=known_secrets)
    receipt["credential_scan_ok"] = bool(scan1["ok"])
    receipt["credential_scan_passes"] = 1
    receipt["verdict"] = pre_scan_verdict if scan1["ok"] else "BLOCK"
    _write_json_atomic(os.path.join(run_dir, "credscan.json"),
                       _public_scan_report(scan1, passes=1))
    _write_json_atomic(receipt_path, receipt)

    scan2 = credential_scan.scan_tree(run_dir, known_secrets=known_secrets)
    final_scan_ok = bool(scan1["ok"] and scan2["ok"])
    receipt["credential_scan_ok"] = final_scan_ok
    receipt["credential_scan_passes"] = 2
    receipt["verdict"] = pre_scan_verdict if final_scan_ok else "BLOCK"
    _write_json_atomic(os.path.join(run_dir, "credscan.json"),
                       _public_scan_report(scan2, passes=2))
    _write_json_atomic(receipt_path, receipt)
    # 同一批次允许并行跑不同输入；完成登记必须在批次级锁内重新读取并追加，
    # 否则两边拿着开跑前的旧 batch 做 read-modify-write，会把其中一个 run_id 覆盖掉。
    artifacts.append_run_id(batch_id, run_id, owner=owner, root=root)
    return {"ok": True, "run_id": run_id, "run_dir": run_dir, "receipt": receipt}
