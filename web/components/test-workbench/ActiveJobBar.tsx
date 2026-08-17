"use client";

// [fork] 常驻任务条（闭环稿 C 屏 · 决策 0012）。
//
// 修的是 BB-489：生成中点别处、切 TAB、刷新页面，任务就"消失"了——
// 其实任务一直在服务端跑着，只是页面把它忘了。
//
// 为什么是常驻条而不是"记住上次位置自动跳回去"：用户离开是为了干别的事，
// 不该被弹回来。这条让他**知道任务还活着**并能一键回去，同时不打断当前动作。

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import { BASE } from "@/components/test-workbench/shared";

const POLL_MS = 1500;

export interface ActiveJob {
  job_id: string;
  state: "pending" | "running" | "done" | "failed" | "cancelled";
  stage?: string;
  done?: number;
  total?: number;
}

export default function ActiveJobBar({ onOpen }: { onOpen: (jobId: string) => void }) {
  const { t } = useTranslation();
  const [job, setJob] = useState<ActiveJob | null>(null);
  //: 用户手动关掉的任务号。跑完的那条提示可以关掉，关掉之后别再冒出来。
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async () => {
    try {
      const res = await apiFetch(apiUrl(`${BASE}/generate/jobs/active`));
      if (!res.ok) return;                 // 扩展没装/接口不在，静默——这只是个提示条
      const data = await res.json();
      setJob(data?.job || null);
    } catch {
      // 网络抖动不该让提示条报错刷屏，下一轮会自己好
    }
  }, []);

  useEffect(() => {
    // 挂载即问一次：这正是"刷新后还能找回任务"的实现——任务在服务端，
    // 页面重新打开时主动认领，而不是指望页面自己记住。
    void poll();
    timer.current = setInterval(() => void poll(), POLL_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [poll]);

  const cancel = useCallback(async () => {
    if (!job) return;
    try {
      await apiFetch(apiUrl(`${BASE}/generate/jobs/${job.job_id}/cancel`), { method: "POST" });
    } finally {
      void poll();
    }
  }, [job, poll]);

  if (!job || dismissed.has(job.job_id)) return null;

  const running = job.state === "pending" || job.state === "running";
  const pct = job.total ? Math.round(((job.done || 0) / job.total) * 100) : 0;

  if (running) {
    return (
      <div className="mb-3 flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--muted)]/40 px-3.5 py-2.5">
        <Loader2 size={15} className="shrink-0 animate-spin text-[var(--primary)]" />
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Generating cases")}
            {typeof job.total === "number" && job.total > 0 && (
              <span className="font-normal text-[var(--muted-foreground)]">
                {" · "}{t("{{done}} / {{total}}", { done: job.done ?? 0, total: job.total })}
              </span>
            )}
          </div>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--muted)]">
            <div className="h-full bg-[var(--primary)] transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <button
          type="button"
          onClick={() => onOpen(job.job_id)}
          className="shrink-0 rounded-[9px] border border-[var(--border)] bg-[var(--card)] px-2.5 py-1 text-[12px] text-[var(--foreground)] hover:bg-[var(--accent)]"
        >
          {t("Back to task")}
        </button>
        <button
          type="button"
          onClick={() => void cancel()}
          className="shrink-0 rounded-[9px] border border-[var(--border)] px-2.5 py-1 text-[12px] text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
        >
          {t("Stop")}
        </button>
      </div>
    );
  }

  // 跑完了但用户还没回来看：也要提示，否则他不知道结果已经好了
  if (job.state === "done") {
    return (
      <div className="mb-3 flex items-center gap-3 rounded-xl border border-emerald-500/40 bg-emerald-500/8 px-3.5 py-2.5">
        <CheckCircle2 size={15} strokeWidth={1.8} className="shrink-0 text-emerald-600 dark:text-emerald-400" />
        <div className="min-w-0 flex-1 text-[12.5px] text-[var(--foreground)]">
          {t("Cases are ready for review")}
          <span className="text-[var(--muted-foreground)]">
            {" · "}{t("Not adopted yet — come back any time")}
          </span>
        </div>
        <button
          type="button"
          onClick={() => onOpen(job.job_id)}
          className="shrink-0 rounded-[9px] bg-[var(--primary)] px-2.5 py-1 text-[12px] font-medium text-[var(--primary-foreground)]"
        >
          {t("Review now")}
        </button>
        <button
          type="button"
          aria-label={t("Dismiss")}
          onClick={() => setDismissed((prev) => new Set(prev).add(job.job_id))}
          className="shrink-0 rounded-md p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
        >
          <X size={13} strokeWidth={1.8} />
        </button>
      </div>
    );
  }

  return null;
}
