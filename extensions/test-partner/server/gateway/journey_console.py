# -*- coding: utf-8 -*-
"""journey_console — 批次工作台面（M1 SSOT「单一状态双视图」的工作台投影）。

批次是唯一状态对象（server/journey/artifacts），本面是它的工作台投影：
  - stepper：产物账本状态栏（产物存在性推导亮灭，非另存进度）
  - 结果表：run 的逐 case verdict + 证据链接
  - 覆盖图：coverage_ledger 数据（哪些测了/没测/为什么，gap 显式原因）
  - trace 打开双路径（ADR-M1-02 §2.3）：按钮调本地接口起 `playwright show-trace`；
    同行显示可复制命令作降级路径。

挂在 gateway（127.0.0.1:3789 域内，extensions/ 侧，0 上游触点）；(workspace) 路由组
的批次页（0015 拍板）以此为数据/渲染后端，前端薄壳嵌入即可。
数据 API 只读产物文件；trace-open 是唯一副作用（起本地 viewer 子进程，PID 登记回收）。
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from server.journey import artifacts
from server.journey import process_registry as preg
from server.journey.pw_harness import case_slug


# ── 数据 API（只读产物）─────────────────────────────────────────────────────

def _batch_summary(batch_id: str) -> dict[str, Any]:
    meta = artifacts.load_batch(batch_id)
    return {
        "batch_id": batch_id,
        "title": meta.get("title", ""),
        "owner": meta.get("owner", ""),
        "base_url_host": _host_only(meta.get("base_url", "")),
        "created_at": meta.get("created_at", ""),
        "run_ids": meta.get("run_ids", []),
        "stepper": artifacts.stepper(batch_id),
    }


def _host_only(base_url: str) -> str:
    from server.journey import redlines
    return redlines.host_key(base_url)


def list_batches_payload() -> dict[str, Any]:
    out = []
    for meta in artifacts.list_batches():
        bid = meta["batch_id"]
        out.append({
            "batch_id": bid, "title": meta.get("title", ""),
            "created_at": meta.get("created_at", ""),
            "run_count": len(meta.get("run_ids", [])),
            "stepper": meta.get("stepper", artifacts.stepper(bid)),
        })
    return {"ok": True, "batches": out}


def batch_detail_payload(batch_id: str) -> dict[str, Any]:
    try:
        summary = _batch_summary(batch_id)
    except artifacts.ArtifactError as exc:
        return {"ok": False, "error": str(exc)}
    coverage = None
    if artifacts.has_artifact(batch_id, "coverage_ledger"):
        coverage = artifacts.load_artifact(batch_id, "coverage_ledger")
    runs = [_run_result(rid) for rid in summary["run_ids"]]
    return {"ok": True, "batch": summary, "coverage": coverage, "runs": runs}


def _run_result(run_id: str) -> dict[str, Any]:
    rd = artifacts.run_dir(run_id)
    receipt_path = os.path.join(rd, "receipt.json")
    receipt = {}
    if os.path.isfile(receipt_path):
        with open(receipt_path, encoding="utf-8") as fh:
            receipt = json.load(fh)
    verdicts = []
    vpath = os.path.join(rd, "verdicts.jsonl")
    if os.path.isfile(vpath):
        with open(vpath, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    v = json.loads(line)
                    slug = case_slug(v["id"].split("/")[-1] if "/" in v["id"] else v["id"])
                    # trace 存在性：run 目录下该 case 的 trace.zip
                    trace_rel = None
                    for d in os.listdir(rd):
                        cand = os.path.join(rd, d, "trace.zip")
                        if os.path.isfile(cand) and d.endswith(case_slug(v["id"].split("/")[-1])):
                            trace_rel = os.path.join(d, "trace.zip")
                            break
                    verdicts.append({
                        "id": v["id"], "verdict": v["verdict"],
                        "note": v.get("note", ""),
                        "evidence": [e.get("ref") for e in v.get("evidence", [])],
                        "trace_rel": trace_rel,
                    })
    return {"run_id": run_id, "receipt": receipt, "verdicts": verdicts}


def open_trace(run_id: str, trace_rel: str) -> dict[str, Any]:
    """trace 打开（ADR-M1-02 §2.3 路径1）：起本地 playwright show-trace 子进程。
    路径2（降级）：返回可复制命令，前端始终显示。"""
    rd = artifacts.run_dir(run_id)
    abspath = os.path.realpath(os.path.join(rd, trace_rel))
    if os.path.commonpath([rd]) != os.path.commonpath([rd, abspath]):
        return {"ok": False, "error": "trace 路径越界"}
    if not os.path.isfile(abspath):
        return {"ok": False, "error": "trace 文件不存在"}
    command = f'python -m playwright show-trace "{abspath}"'
    started = False
    spawn_error = ""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "playwright", "show-trace", abspath],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:  # viewer 起不来不致命，用户走降级命令
        spawn_error = str(exc)
    else:
        # 登记必须在 Popen 之后**立刻**发生，且失败要看得见。
        # BB-501 现场教训：此处原先是一个大 `except Exception`，
        # 而 `preg.register_pid` 当时根本不存在 —— AttributeError 被吞掉，
        # 于是"起了进程但从未登记"，泄漏无从追。别把这个 except 再放宽。
        preg.register_pid(rd, proc.pid, "trace-viewer")
        started = True
    return {"ok": True, "started": started, "command": command,
            "trace_path": abspath, "spawn_error": spawn_error}


# ── 页面 ────────────────────────────────────────────────────────────────────

_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>测试伙伴 · 批次工作台</title>
<style>
 body{font:14px/1.6 system-ui,"Segoe UI",sans-serif;margin:0;background:#f6f7f9;color:#1c2430}
 header{background:#1f6feb;color:#fff;padding:12px 20px;font-size:16px;font-weight:600}
 main{max-width:1080px;margin:16px auto;padding:0 16px}
 .card{background:#fff;border:1px solid #e3e7ec;border-radius:8px;padding:14px 16px;margin:12px 0}
 .step{display:inline-block;padding:3px 9px;margin:2px;border-radius:12px;font-size:12px;border:1px solid #cbd2da;color:#8a94a0}
 .step.on{background:#e6f4ea;border-color:#3fa564;color:#256b3e}
 table{border-collapse:collapse;width:100%}
 th,td{border-bottom:1px solid #eef1f4;padding:6px 8px;text-align:left;font-size:13px;vertical-align:top}
 th{color:#5b6572;font-weight:600}
 .v{font-weight:600;padding:1px 7px;border-radius:10px;font-size:12px}
 .PASS{background:#e6f4ea;color:#256b3e}.FAIL{background:#fdecec;color:#b3261e}
 .PENDING,.BLOCK,.BLOCKED{background:#fff4e5;color:#8a5a00}
 .covered{color:#256b3e}.declared_uncovered{color:#8a5a00}.gap_unexplained{color:#b3261e;font-weight:600}
 button{font:12px system-ui;padding:3px 9px;border:1px solid #cbd2da;background:#fff;border-radius:5px;cursor:pointer}
 code{background:#f0f2f5;padding:1px 5px;border-radius:4px;font-size:12px;user-select:all}
 a{color:#1f6feb;text-decoration:none}.muted{color:#8a94a0;font-size:12px}
</style></head><body>
<header>测试伙伴 · 批次工作台 <span class="muted" style="color:#cfe0ff">SSOT 单一状态双视图 · 工作台投影</span></header>
<main id="app">加载中…</main>
<script>
// 第二真相已删（设计稿 §9 第 3 条）：格数、顺序与展示名一律由服务端 stepper 给，
// 前端不再各持一份常量。此前这里内嵌 7 项、服务端 9 项，两边永远对不上。
const app=document.getElementById('app');
const q=new URLSearchParams(location.search);
function stepper(st){return (st||[]).map(s=>{
 const why=s.present?'':(s.missing_prereq?('缺前置：'+s.missing_prereq):(s.blocked_reason||'尚未产出'));
 const anchor=s.run_id?(' @'+s.run_id):'';
 return `<span class="step ${s.present?'on':''}" title="${esc(why)}${esc(anchor)}">${esc(s.label||s.artifact)}</span>`
}).join('')}
async function jget(u){const r=await fetch(u,{headers:{'Cache-Control':'no-store'}});return r.json()}
async function listView(){
 const d=await jget('/api/journey/batches');
 app.innerHTML='<div class="card"><h3 style="margin:.2em 0">批次</h3>'+
  (d.batches.length?('<table><tr><th>批次</th><th>标题</th><th>产物账本</th><th>运行</th></tr>'+
   d.batches.map(b=>`<tr><td><a href="?batch=${b.batch_id}">${b.batch_id}</a></td><td>${esc(b.title)}</td><td>${stepper(b.stepper)}</td><td>${b.run_count}</td></tr>`).join('')+'</table>')
   :'<p class="muted">还没有批次。批次由聊天侧发起（接入工具），产物落 data/test-workbench。</p>')+'</div>'
}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function detailView(id){
 const d=await jget('/api/journey/batches/'+id);
 if(!d.ok){app.innerHTML='<div class="card">读取失败：'+esc(d.error)+'</div>';return}
 const b=d.batch;let h=`<p><a href="?">← 批次列表</a></p>`;
 h+=`<div class="card"><h3 style="margin:.2em 0">${esc(b.title)} <span class="muted">${b.batch_id}</span></h3>`+
    `<div>${stepper(b.stepper)}</div><p class="muted">靶 host: ${esc(b.base_url_host)} · owner: ${esc(b.owner)||'(未分区,M2)'} · 建于 ${esc(b.created_at)}</p></div>`;
 if(d.coverage){const s=d.coverage.summary;h+=`<div class="card"><h3 style="margin:.2em 0">覆盖图</h3>`+
   `<p>规则 ${s.total_rules} · <span class="covered">已覆盖 ${s.covered}</span> · <span class="declared_uncovered">显式不覆盖 ${s.declared_uncovered}</span> · <span class="gap_unexplained">无解释缺口 ${s.gap_unexplained}</span> · 官方结论 ${s.official_verdicts}（PASS ${s.pass}）</p>`+
   '<table><tr><th>规则</th><th>状态</th><th>用例/verdict 或 gap 原因</th></tr>'+
   d.coverage.rules.map(r=>`<tr><td>${esc(r.rule_id)}<br><span class="muted">${esc(r.statement)}</span></td><td class="${r.status}">${r.status}</td><td>${r.cases.length?r.cases.map(c=>`${esc(c.case_id)} <span class="v ${c.verdict.split(':')[0]}">${c.verdict}</span>`).join('<br>'):esc(r.gap_reason)||'—'}</td></tr>`).join('')+'</table></div>'}
 (d.runs||[]).forEach(run=>{h+=`<div class="card"><h3 style="margin:.2em 0">执行 ${run.run_id} <span class="v ${(run.receipt.verdict||'').split(':')[0]}">${run.receipt.verdict||''}</span></h3>`+
   `<p class="muted">${JSON.stringify(run.receipt.counts||{})} · 凭据零落盘扫描 ${run.receipt.credential_scan_ok?'✓':'✗'}</p>`+
   '<table><tr><th>用例</th><th>verdict</th><th>trace</th></tr>'+
   run.verdicts.map(v=>`<tr><td>${esc(v.id)}<br><span class="muted">${esc(v.note)}</span></td><td><span class="v ${v.verdict.split(':')[0]}">${v.verdict}</span></td><td>${v.trace_rel?`<button onclick="openTrace('${run.run_id}','${v.trace_rel.replace(/\\\\/g,'/')}',this)">打开 trace</button>`:'<span class="muted">API 轨(无trace)</span>'}</td></tr>`).join('')+'</table></div>'});
 app.innerHTML=h;
}
async function openTrace(rid,rel,btn){
 btn.disabled=true;const r=await (await fetch('/api/journey/runs/'+rid+'/trace-open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trace_rel:rel})})).json();
 btn.outerHTML=(r.ok?(r.started?'viewer 已弹出 · ':'')+'降级命令: <code>'+esc(r.command)+'</code>':'失败:'+esc(r.error));
}
(q.get('batch')?detailView(q.get('batch')):listView());
</script></body></html>"""


class JourneyConsole:
    """gateway 上的批次工作台端点集合（复用 gateway 的 Host guard + CSRF）。"""

    async def page(self, request: Request) -> Response:
        return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})

    async def api_batches(self, request: Request) -> Response:
        from starlette.concurrency import run_in_threadpool
        return JSONResponse(await run_in_threadpool(list_batches_payload),
                            headers={"Cache-Control": "no-store"})

    async def api_batch(self, request: Request) -> Response:
        from starlette.concurrency import run_in_threadpool
        bid = request.path_params["batch_id"]
        payload = await run_in_threadpool(batch_detail_payload, bid)
        return JSONResponse(payload, status_code=200 if payload["ok"] else 404,
                            headers={"Cache-Control": "no-store"})

    async def api_trace_open(self, request: Request) -> Response:
        from starlette.concurrency import run_in_threadpool
        run_id = request.path_params["run_id"]
        body = await request.json()
        rel = str(body.get("trace_rel", ""))
        payload = await run_in_threadpool(open_trace, run_id, rel)
        return JSONResponse(payload, status_code=200 if payload["ok"] else 400,
                            headers={"Cache-Control": "no-store"})
