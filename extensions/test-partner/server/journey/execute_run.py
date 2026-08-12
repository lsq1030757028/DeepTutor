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


def write_authorization(batch_id: str, root: str | None = None) -> dict[str, Any]:
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
    if artifacts.has_artifact(batch_id, "approved_caseset", root=root):
        caseset = artifacts.load_artifact(batch_id, "approved_caseset", root=root)
    current = {c.get("case_id"): c.get("source_case_digest")
               for c in caseset.get("cases") or []}

    authorized: set[str] = set()
    dropped: list[dict[str, str]] = []
    for e in artifacts.read_events(batch_id, root=root):
        if e.get("type") != "write_confirm":
            continue
        digests = e.get("digests") or {}
        claimed = list(e.get("case_ids") or [])
        if e.get("case_id"):
            claimed.append(e["case_id"])
        for cid in claimed:
            recorded = digests.get(cid)
            if not recorded:
                dropped.append({"case_id": cid, "at": str(e.get("at", "")),
                                "reason": "确认事件没记 digest，认不出它同意的是什么内容"})
            elif cid not in current:
                dropped.append({"case_id": cid, "at": str(e.get("at", "")),
                                "reason": "该用例已不在当前采纳集里"})
            elif current[cid] != recorded:
                dropped.append({"case_id": cid, "at": str(e.get("at", "")),
                                "reason": "用例内容已变（digest 不符）——旧确认同意的不是"
                                          "现在这条，须重新确认"})
            else:
                authorized.add(cid)
    return {"authorized": authorized, "dropped": dropped}


def _write_authorized_ids(batch_id: str) -> set[str]:
    return write_authorization(batch_id)["authorized"]


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
    write_auth = write_authorization(batch_id)
    write_ok = write_auth["authorized"]
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
        # L3 通道在**本次执行**是否可用。与 bundle 里的 capability_l3_granted
        # 是两回事：那个记的是编译时声明，这个记的是执行时现实。两者不一致
        # （声明有、执行时没有）时 db op 会 BLOCKED，收据上这两格并列摆着，
        # 让"为什么这批写用例没有数据层证据"有一处能查。
        "l3_channel_available": l3_dsn_present,
        "bundle_l3_granted": bool(manifest.get("capability_l3_granted")),
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
        # 被作废的写确认要落账：作废后的症状是 SKIP_WRITE_UNCONFIRMED，
        # 与「用户压根没确认」长得一模一样，不写出来就无从区分（0021 红线六）。
        "write_confirm_dropped": write_auth["dropped"],
        "reap": reap,
        "conclusion_count": len(evidence_bundle["conclusions"]),
    }
    with open(os.path.join(run_dir, "receipt.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=1)
    if run_id not in batch.get("run_ids", []):
        batch.setdefault("run_ids", []).append(run_id)
        artifacts.save_batch(batch)
    return {"ok": True, "run_id": run_id, "run_dir": run_dir, "receipt": receipt}
