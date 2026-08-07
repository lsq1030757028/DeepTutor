"""生成任务台账。**零 DeepTutor import，也不碰 asyncio。**

## 为什么台账和异步分开

生成要几十秒，页面得能查进度、能取消。异步任务的调度归宿主侧（它有事件循环、
有请求上下文），但**"这个任务现在是什么状态"是纯数据**——放在这里就能脱离
事件循环单测：状态机转换、取消语义、按用户隔离，全都不需要起一个 loop 去验。

## 按用户隔离（决策 0009）

台账按 ``owner`` 分区。这不是整洁问题：不隔离的话 A 能用 B 的 job_id
查到 B 的生成结果——那结果里带着 B 那份 HAR 的业务数据。

隔离在**取号**和**查询**两处都做。只在查询处过滤是不够的——
job_id 若是全局递增，A 猜号就能试探出 B 跑过几个任务。所以 id 用随机串。

## 终态不可变

任务一旦进入 ``done`` / ``failed`` / ``cancelled``，就不再接受任何写入。
没有这条，一个跑完才返回的慢协程会把已取消的任务改回 ``done``，
用户看到的是"我明明取消了它却还是跑完了"。
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

#: 每个用户保留的历史任务数。超出后丢最旧的——台账是给"看当前这次"用的，
#: 不是归档；真正的产物落在交付批次里，不靠这里留存。
MAX_JOBS_PER_OWNER = 20

PENDING, RUNNING, DONE, FAILED, CANCELLED = (
    "pending", "running", "done", "failed", "cancelled")
_TERMINAL = {DONE, FAILED, CANCELLED}


@dataclass
class Job:
    id: str
    owner: str
    state: str = PENDING
    stage: str = ""
    done: int = 0
    total: int = 0
    note: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    #: 取消是**请求**不是命令——协程要在下一个检查点自己看见它才停。
    cancel_requested: bool = False

    @property
    def finished(self) -> bool:
        return self.state in _TERMINAL

    def public(self) -> dict[str, Any]:
        """给页面的投影。不含 owner——它是内部字段，回显没有价值只有风险。"""
        return {
            "job_id": self.id,
            "state": self.state,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "note": self.note,
            "result": self.result,
            "error": self.error,
        }


class JobStore:
    """内存台账。进程重启即清空——这是有意的，见模块 docstring。"""

    def __init__(self, max_per_owner: int = MAX_JOBS_PER_OWNER) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max = max_per_owner

    # ── 写 ────────────────────────────────────────────────────────────────

    def create(self, owner: str) -> Job:
        """开一个任务。id 用随机串，不用递增——递增的话别人猜号就能试探。"""
        job = Job(id=secrets.token_urlsafe(12), owner=owner)
        with self._lock:
            self._jobs[job.id] = job
            self._evict(owner)
        return job

    def _evict(self, owner: str) -> None:
        """只淘汰**已结束**的旧任务。在跑的不能因为超额被丢掉。"""
        mine = [j for j in self._jobs.values() if j.owner == owner]
        if len(mine) <= self._max:
            return
        finished = [j for j in mine if j.finished]
        for job in finished[: len(mine) - self._max]:
            self._jobs.pop(job.id, None)

    def update(self, job_id: str, **fields: Any) -> Job | None:
        """写状态。**终态之后一律拒写**，理由见模块 docstring。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.finished:
                return job
            for key, value in fields.items():
                setattr(job, key, value)
            return job

    def request_cancel(self, job_id: str, owner: str) -> Job | None:
        """标记取消。已结束的任务不再改状态——它已经发生过了。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner != owner:
                return None
            if not job.finished:
                job.cancel_requested = True
            return job

    # ── 读 ────────────────────────────────────────────────────────────────

    def get(self, job_id: str, owner: str) -> Job | None:
        """取任务。**owner 对不上一律当不存在**——返回 403 会泄露"这个 id 存在"。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner != owner:
                return None
            return job

    def list_for(self, owner: str) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.owner == owner]

    def running_count(self, owner: str) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values()
                       if j.owner == owner and j.state in (PENDING, RUNNING))
