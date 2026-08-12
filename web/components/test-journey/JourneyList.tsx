"use client";

// [fork] 批次列表（交互稿 s1）。侧栏点进来的第一屏。
//
// 目标：让用户**三秒内挑出「该我管的那一条」**，而不是逐条点进去看。
// 所以卡上只放三样：走到哪（四段，不是九格）/ 结论如何 / 靶是谁。
//
// **列表卡上不放九格微条**（0021 必改 3）。九格是后端的产物账本，
// 摊在列表卡上只会让人在十几张卡上数格子——那正是上一版被判不通过的病因。

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import ErrorState from "@/components/test-journey/ErrorState";
import { callJourney } from "@/components/test-journey/client";
import type { BatchSummary } from "@/components/test-journey/types";
import { projectSegments } from "@/components/test-journey/types";

function SegmentBar({ batch }: { batch: BatchSummary }) {
  const { t } = useTranslation();
  const segments = projectSegments(batch.stepper ?? []);
  return (
    <div className="flex flex-wrap gap-1">
      {segments.map((segment) => (
        <span
          key={segment.id}
          title={t("{{label}}: {{done}}/{{total}}", {
            label: t(segment.label),
            done: segment.cells.filter((c) => c.present).length,
            total: segment.cells.length,
          })}
          className={`rounded px-1.5 py-0.5 text-[11px] ${
            segment.status === "done"
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300"
              : segment.status === "doing"
                ? "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300"
                : "bg-[var(--muted)] text-[var(--muted-foreground)]"
          }`}
        >
          {t(segment.label)}
        </span>
      ))}
    </div>
  );
}

export default function JourneyList() {
  const { t } = useTranslation();
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [error, setError] = useState<{ code: string; message?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  // 注意：`load` 里**第一个 await 之前不许 setState**。首次加载是从 effect 里调的，
  // 在 effect 内同步 setState 会触发级联渲染（eslint 该规则在本仓是 error 级）。
  // 所以 loading 的初值就是 true，转菊花由按钮那条路自己置。
  const load = useCallback(async () => {
    const result = await callJourney("list_batches");
    if (!result.ok) {
      setError({ code: result.code, message: result.message });
      setBatches([]);
    } else {
      setError(null);
      setBatches((result.data?.batches as BatchSummary[]) ?? []);
    }
    setLoading(false);
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    void load();
  }, [load]);

  // IIFE + cancelled 旗标：把 setState 挪出 effect 的同步体，满足
  // `react-hooks/set-state-in-effect`（本仓 error 级）。写法照 MemoryPicker.tsx:51-60。
  // cancelled 顺带治了"组件已卸载还回写状态"这个老问题。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await load();
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--foreground)]">
            {t("Test journeys")}
          </h1>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {t(
              "The whole path of one requirement, from intake to closure. One card per journey — open it for the ledger, the rules and the verdicts.",
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1.5 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          {t("Refresh")}
        </button>
      </div>

      {error ? <ErrorState code={error.code} message={error.message} onRetry={refresh} /> : null}

      {!error && !loading && batches.length === 0 ? (
        <div className="rounded-xl border border-[var(--border)] px-4 py-8 text-center">
          <p className="text-sm text-[var(--foreground)]">{t("No test journeys yet.")}</p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {t(
              "Pick the Test mode in chat and give a TAPD story ID to start one. Batches can only be created there — this screen is another window onto them, not a second entry point.",
            )}
          </p>
        </div>
      ) : null}

      <ul className="space-y-2">
        {batches.map((batch) => (
          <li key={batch.batch_id}>
            <Link
              href={`/test-journey/${batch.batch_id}`}
              className="block rounded-xl border border-[var(--border)] px-4 py-3 hover:bg-[var(--muted)]"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-[var(--foreground)]">
                  {batch.title || t("Untitled batch")}
                </span>
                <span className="font-mono text-[11px] text-[var(--muted-foreground)]">
                  {batch.batch_id}
                </span>
              </div>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                {batch.base_url || t("No target specified")} ·{" "}
                {t("{{count}} attempts run", { count: batch.run_count })} ·{" "}
                {batch.created_at}
              </p>
              <div className="mt-2">
                <SegmentBar batch={batch} />
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
