"""用例生成的接口（工作台第 3-5 屏）。

## 与 `test_workbench.py` 的分工

那边是只读面（批次、草稿、体检）；这边是**会花钱的面**。分开是为了让
"哪些接口会调模型"一眼可见——审成本时只需要读这一个文件加适配器。

## 为什么用 `asyncio.create_task` 而不是线程

生成要几十秒，必须异步。但**当前用户是通过 ContextVar 传递的**
（`deeptutor/multi_user/context.py`），而 `threading.Thread` **不继承 ContextVar**——
在线程里 `get_current_user_or_none()` 会返回 None，落盘就静默写进 admin 工作区。
隔离形态下（决策 0009）那不是"不整洁"，是串数据。

`asyncio.create_task` 会复制当前上下文，用户身份跟着任务走。这是选它的硬理由。

## 一个用户同时只跑一个

生成是花钱的动作。不限并发的话，页面上多点几下就是几倍账单，
而用户往往以为"没反应"才多点的。所以第二次发起直接拒，并告诉他已经在跑了。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.test_workbench_paths import (
    drafts_root as _drafts_root,
    require_extension as _require_extension,
)
from deeptutor.api.routers.test_workbench_model import call_model, has_usable_model
from deeptutor.multi_user.context import get_current_user_or_none

logger = logging.getLogger(__name__)

from server.generate import jobs as _jobs  # type: ignore[import-not-found]  # noqa: E402
from server.generate.context import build_material  # type: ignore[import-not-found]  # noqa: E402
from server.generate.model import (  # type: ignore[import-not-found]  # noqa: E402
    MAX_CALLS_PER_JOB,
    ModelTimeout,
    ModelUnavailable,
)
from server.generate.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_BATCH,
    DEFAULT_CASES,
    MAX_CASES,
    BudgetExhausted,
    Cancelled,
    draft_scenario,
    generate,
)

router = APIRouter()

#: 进程内台账。重启即清空——生成结果的归宿是交付批次，不是这里。
_STORE = _jobs.JobStore()


def _owner() -> str:
    """当前用户的 id；没有当前用户时用 admin 兜底。

    与 `test_workbench._deliveries_root()` 同一套口径：不自己拼用户 id，
    免得和平台的迁移逻辑各说各话。
    """
    user = get_current_user_or_none()
    return str(getattr(user, "id", "") or "local-admin")


def _load_material(draft_id: str):
    """按草稿 id 取回体检报告并裁成素材。

    素材只在内存里过一手，不落盘——HAR 派生数据已经在草稿里了，
    再存一份裁剪结果只是多一处可能泄漏的副本。
    """
    wb = _require_extension()
    try:
        safe = wb.safe_delivery_id(draft_id)
    except wb.WorkbenchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    path = _drafts_root() / f"{safe}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"体检草稿 {safe} 不存在，请重新上传 HAR。")

    payload = json.loads(path.read_text(encoding="utf-8"))
    report = payload.get("report") or {}
    return build_material(report)


# ── 能力探测 ──────────────────────────────────────────────────────────────

@router.get("/generate/capability")
def capability() -> dict[str, Any]:
    """页面据此决定是给生成入口，还是给"去配模型"的指引。

    单独一个端点而不是让页面去猜：上游在没配模型时**不报错**，
    页面若靠调用失败来判空态，用户看到的会是一个 provider 侧的 401。
    """
    return {
        "model_ready": has_usable_model(),
        "limits": {
            "max_cases": MAX_CASES,
            "default_cases": DEFAULT_CASES,
            "default_batch": DEFAULT_BATCH,
            "max_model_calls": MAX_CALLS_PER_JOB,
        },
    }


# ── 第一段：起草场景（短，同步返回） ─────────────────────────────────────

class ScenarioRequest(BaseModel):
    draft_id: str = Field(..., min_length=1)


@router.post("/generate/scenario")
async def make_scenario(body: ScenarioRequest) -> dict[str, Any]:
    """让模型用一段大白话描述这份抓包在做什么。

    同步返回：这一段只有几百 token，等一下就出来，为它开个异步任务
    反而让页面多两次轮询。
    """
    material = _load_material(body.draft_id)
    try:
        text = await draft_scenario(call_model, material)
    except ModelUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    return {"scenario": text, "notes": material.notes}


# ── 第二三段：生成用例（长，异步任务） ───────────────────────────────────

class GenerateRequest(BaseModel):
    draft_id: str = Field(..., min_length=1)
    #: 用户改过的场景描述。**留空才让模型重新起草**——
    #: 用户改过的必须优先，否则他的修改会被悄悄冲掉。
    scenario: str = ""
    max_cases: int = DEFAULT_CASES
    batch_size: int = DEFAULT_BATCH


@router.post("/generate/jobs")
async def start_generation(body: GenerateRequest) -> dict[str, Any]:
    owner = _owner()
    if _STORE.running_count(owner) > 0:
        raise HTTPException(
            status_code=409,
            detail="你已经有一个生成任务在跑了。等它结束或先取消，再发起新的。",
        )
    if not has_usable_model():
        raise HTTPException(
            status_code=409,
            detail="还没有可用的模型。请先在 设置 → 模型 里配置一个，或让管理员授权给你。",
        )

    material = _load_material(body.draft_id)
    job = _STORE.create(owner)

    def on_progress(p) -> None:
        _STORE.update(job.id, state=_jobs.RUNNING, stage=p.stage,
                      done=p.done, total=p.total, note=p.note)

    def should_cancel() -> bool:
        current = _STORE.get(job.id, owner)
        return bool(current and current.cancel_requested)

    async def run() -> None:
        _STORE.update(job.id, state=_jobs.RUNNING, stage="scenario")
        try:
            result = await generate(
                call_model, material,
                scenario=body.scenario,
                max_cases=body.max_cases,
                batch_size=body.batch_size,
                should_cancel=should_cancel,
                on_progress=on_progress,
            )
        except Cancelled:
            _STORE.update(job.id, state=_jobs.CANCELLED, note="已取消")
        except (ModelUnavailable, ModelTimeout, BudgetExhausted) as exc:
            _STORE.update(job.id, state=_jobs.FAILED, error=str(exc))
        except Exception as exc:  # pragma: no cover - 兜底，不让任务无声消失
            logger.exception("生成任务 %s 失败", job.id)
            _STORE.update(job.id, state=_jobs.FAILED, error=f"生成失败：{exc}")
        else:
            _STORE.update(job.id, state=_jobs.DONE, result={
                "scenario": result.scenario,
                "picked_endpoints": result.picked_endpoints,
                "skipped_reason": result.skipped_reason,
                "cases": result.cases,
                "notes": result.notes,
                "calls_used": result.calls_used,
                "complete": result.complete,
            })

    # create_task 复制当前上下文，用户身份跟着任务走。线程不行，见模块 docstring。
    asyncio.create_task(run())
    return job.public()


@router.get("/generate/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _STORE.get(job_id, _owner())
    if job is None:
        # owner 对不上也走 404：403 会泄露"这个 id 存在"。
        raise HTTPException(status_code=404, detail="任务不存在。")
    return job.public()


@router.post("/generate/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = _STORE.request_cancel(job_id, _owner())
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return job.public()
