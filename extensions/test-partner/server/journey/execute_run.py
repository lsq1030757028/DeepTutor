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
from typing import Any

from server.journey import artifacts
from server.journey import process_registry as preg
from server.journey.gates import credential_scan
from server.journey.pw_harness import case_slug

#: assertion_layers.required_evidence 词表 → run 目录证据文件映射
EVIDENCE_FILES = {
    "playwright_trace": "trace.zip",
    "screenshot": "final.png",
    "http_transcript": "transcript.json",
    "console_log": "console.log",
    "db_snapshot": "db_snapshot.json",
}


def _write_authorized_ids(batch_id: str) -> set[str]:
    ids: set[str] = set()
    for e in artifacts.read_events(batch_id):
        if e.get("type") == "write_confirm":
            if e.get("case_id"):
                ids.add(e["case_id"])
            for c in e.get("case_ids") or []:
                ids.add(c)
    return ids


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
UI_TRACK_OPS = ("goto", "fill", "click", "expect_visible", "expect_text",
                "expect_title_contains", "expect_url_contains")

#: API 轨不可能产出的证据类型。要求了必然缺证（DoD#4b 第二半）。
UI_ONLY_EVIDENCE = ("playwright_trace", "screenshot")


def detect_track(manifest: dict[str, Any]) -> str:
    """从 bundle 反推本趟走的是哪条轨。

    判据是 op 集合而不是配置项：配置说的是"打算走哪条"，op 说的是"实际会发生什么"。
    两者不一致时，以实际为准并让 E22 去拦——**这里不负责拦，只负责如实说**。
    """
    for case in manifest.get("cases", []) or []:
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


def _intake_fingerprint_of(batch_id: str) -> dict[str, Any]:
    """接入期探到的靶。**只作对照，不作判据**——判据用 target_identity。"""
    try:
        intake = artifacts.load_artifact(batch_id, "intake_profile")
    except artifacts.ArtifactError:
        return {}
    from server.journey import redlines
    probe = intake.get("target_probe") or {}
    return {"base_url_host": redlines.host_key(intake.get("base_url", "")),
            "status": probe.get("status"), "source": "intake-probe"}


def _drifted(batch_id: str, identity: dict[str, Any]) -> bool:
    """接入期与执行期的靶 host 不一致 = 换环境跑了。

    **不阻断**（换环境跑是合法的），但结论卡上必须可见——
    否则"这批结论是在哪个环境上得到的"就只能靠猜。
    """
    intake = _intake_fingerprint_of(batch_id)
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
                           rows: list[dict[str, Any]]) -> dict[str, Any]:
    caseset = artifacts.load_artifact(batch_id, "approved_caseset")
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
            triggered_by: str = "fresh") -> dict[str, Any]:
    """跑一趟。

    `triggered_by`（`fresh` / `regenerate-replay`）落进 run_receipt——
    让"为什么这个批次有两条一模一样的 run"在账本上可解释，而不是靠猜
    （设计稿 §5.2 第三层）。它**不参与幂等 key**：幂等看的是输入，
    不是谁按的按钮。
    """
    batch = artifacts.load_batch(batch_id)
    bundle_dir = os.path.join(artifacts.batch_dir(batch_id), "bundle")
    manifest_path = os.path.join(bundle_dir, "bundle.json")
    if not os.path.isfile(manifest_path):
        return {"ok": False, "error": "NO_BUNDLE",
                "hint": "先跑 compile 产出 AutomationBundle"}
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    base_url = (base_url_override or batch.get("base_url") or "").rstrip("/")
    if not base_url:
        return {"ok": False, "error": "NO_BASE_URL"}

    run_id = resume_run_id or artifacts.new_run_id()
    run_dir = artifacts.run_dir(run_id, create=True)
    done_ids = [r["case_id"] for r in _read_results(run_dir)] if resume_run_id else []

    slot = preg.acquire_slot(run_id, run_dir)
    if not slot["ok"]:
        return slot

    variables = dict(variables or {})
    write_ok = _write_authorized_ids(batch_id)
    selected = case_ids or [m["case_id"] for m in manifest["cases"]]
    test_names = [m["test_name"] for m in manifest["cases"]
                  if m["case_id"] in selected]
    if not test_names:
        preg.release_slot(run_id)
        return {"ok": False, "error": "NO_CASES_SELECTED"}

    env = dict(os.environ)
    env.update({
        "TP_BASE_URL": base_url,
        "TP_RUN_DIR": run_dir,
        "TP_VARS_JSON": json.dumps(variables, ensure_ascii=False),
        "TP_WRITE_AUTHORIZED_IDS": ",".join(sorted(write_ok)),
        "TP_DONE_CASE_IDS": ",".join(done_ids),
    })
    started = artifacts.now_iso()
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-c", "pytest.ini",
             "--tb=line", "-k", " or ".join(test_names), "."],
            cwd=bundle_dir, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s)
        pytest_tail = (proc.stdout or "")[-2000:]
        pytest_rc = proc.returncode
    except subprocess.TimeoutExpired:
        pytest_tail = f"(超时 {timeout_s}s 被杀)"
        pytest_rc = -1
    finally:
        reap = preg.reap_run(run_dir)
        preg.release_slot(run_id)

    rows = _read_results(run_dir)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    if counts.get("failed"):
        verdict = "FAIL"
    elif counts.get("blocked") or counts.get("no_assertions") or pytest_rc == -1:
        verdict = "BLOCK"
    elif counts.get("passed") or counts.get("observed"):
        verdict = "PASS"
    else:
        verdict = "BLOCK"

    # A2：执行期就地探针 —— 指纹锚的是**这一趟真正打的靶**，不是接入期那个。
    # 探针失败也如实记（status=unreachable），那正是类 1 故障场景要的语义。
    from server.journey.ingest import probe_target
    exec_probe = probe_target(base_url)
    track = detect_track(manifest)
    target_identity = build_target_identity(base_url, exec_probe, track)
    fingerprint = _instance_fingerprint(base_url, exec_probe)
    evidence_bundle = _build_evidence_bundle(batch_id, run_dir, fingerprint, rows)

    # 红线 3 收尾自证：凭据零落盘机械扫描（已知值 = 本轮全部变量值）
    scan = credential_scan.scan_tree(
        run_dir, known_secrets=[str(v) for v in variables.values()])
    scan_report = {"ok": scan["ok"], "known_hits": scan["known_hits"],
                   "entropy_hits": [
                       {k: h[k] for k in ("file", "token_preview", "length", "entropy")}
                       for h in scan["entropy_hits"]],
                   "scanned_files": scan["scanned_files"], "note": scan["note"]}
    with open(os.path.join(run_dir, "credscan.json"), "w", encoding="utf-8") as fh:
        json.dump(scan_report, fh, ensure_ascii=False, indent=1)

    from server.journey import redlines
    receipt = {
        "artifact": "run_receipt",
        # 1 → 1.1：新增 target_identity / target_drift / triggered_by，
        # 且 build_fingerprint 的语义由「接入期」改为「执行期」。
        # 迁移说明见 docs/schema-changelog.md；M4 判据的消费点同步改读 target_identity。
        "schema_version": "1.1",
        "run_id": run_id,
        "batch_id": batch_id,
        "caseset_id": manifest["caseset_id"],
        "compiler_version": manifest["compiler_version"],
        "base_url_host": redlines.host_key(base_url),   # 收据只记 host，不记完整 URL
        # A2：两个指纹**并列，不互相冒充**。
        #   target_identity  = 本次执行就地探到的靶（判据用这个）
        #   intake_fingerprint = 接入期探到的靶（只作对照）
        # 换环境跑是合法的，所以 drift 不阻断——但结论卡上必须看得见。
        "target_identity": target_identity,
        "intake_fingerprint": _intake_fingerprint_of(batch_id),
        "target_drift": _drifted(batch_id, target_identity),
        "triggered_by": triggered_by,
        "started_at": started,
        "finished_at": artifacts.now_iso(),
        "elapsed_s": round(time.time() - t0, 1),
        "resumed": bool(resume_run_id),
        "selected_case_count": len(test_names),
        "counts": counts,
        "verdict": verdict,
        "pytest_returncode": pytest_rc,
        "pytest_tail": pytest_tail,
        "credential_scan_ok": scan["ok"],
        "reap": reap,
        "conclusion_count": len(evidence_bundle["conclusions"]),
    }
    with open(os.path.join(run_dir, "receipt.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=1)
    if run_id not in batch.get("run_ids", []):
        batch.setdefault("run_ids", []).append(run_id)
        artifacts.save_batch(batch)
    return {"ok": True, "run_id": run_id, "run_dir": run_dir, "receipt": receipt}
