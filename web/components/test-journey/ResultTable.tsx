"use client";

// [fork] 结果表（交互稿 s4）。只回答一件事：**哪些结论我可以真的信。**
//
// 三条硬约束：
//   1. 结论只有四种：通过 / 不通过 / 挂起 / 拦下。**挂起必须写清为什么挂起**——
//      一个没有理由的"挂起"等于把判断推回给用户，还假装给了结论。
//   2. **探测项单独一栏，绝不混进通过率的分母**。探测性用例是"需求正文撑不住的预期"，
//      把它算进通过率，就是把"我们其实没验"读成"验过了"。
//   3. 「断到哪层」与「证据」合成一列——两列分开时用户要自己在两处之间做关联。

import { useState } from "react";
import { useTranslation } from "react-i18next";

import TraceLauncher from "@/components/test-journey/TraceLauncher";
import {
  JourneyErrorCode,
  openJourneyTrace,
} from "@/components/test-journey/client";
import type { RunRow, VerdictRow } from "@/components/test-journey/types";
import {
  runTrack,
  selectRun,
  summarizeRun,
  verdictParts,
} from "@/components/test-journey/types";

/**
 * `label` 是 i18n 键。四种结论与聊天富卡共用同一批 `journey.verdict.*` 键——
 * 同一个结论在两个面上必须是同一个词，否则用户要在两处各学一套说法。
 */
const VERDICT_STYLE: Record<string, { label: string; className: string }> = {
  PASS: { label: "journey.verdict.pass", className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300" },
  FAIL: { label: "journey.verdict.fail", className: "bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300" },
  PENDING: { label: "journey.verdict.pending", className: "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" },
  BLOCK: { label: "journey.verdict.blocked", className: "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" },
  BLOCKED: { label: "journey.verdict.blocked", className: "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" },
};

function VerdictBadge({ verdict }: { verdict: string }) {
  const { t } = useTranslation();
  const known = VERDICT_STYLE[verdictParts(verdict).code];
  // 认不出来的结论码原样显示——**不许翻译、不许归到四种里的任何一种**。
  const label = known ? t(known.label) : verdict;
  const className = known
    ? known.className
    : "bg-[var(--muted)] text-[var(--muted-foreground)]";
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs ${className}`}>
      {label}
    </span>
  );
}

function EvidenceCell({ row }: { row: VerdictRow }) {
  const { t } = useTranslation();
  const refs = (row.evidence ?? []).map((e) => e?.ref).filter(Boolean) as string[];
  return (
    <div className="text-xs text-[var(--muted-foreground)]">
      {row.layer ? (
        <span className="mr-1.5 rounded bg-[var(--muted)] px-1 py-0.5">{row.layer}</span>
      ) : null}
      {refs.length ? (
        <span className="font-mono">{refs.join(t("journey.listSeparator"))}</span>
      ) : (
        // 没有证据指针的"通过"是最贵的假绿，这里必须说出来
        <span className="text-amber-700 dark:text-amber-400">{t("No evidence pointer")}</span>
      )}
    </div>
  );
}

interface TraceState {
  errorCode?: string;
  command?: string;
}

export default function ResultTable({
  batchId,
  runs,
}: {
  batchId: string;
  runs: RunRow[];
}) {
  const { t } = useTranslation();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [traceStates, setTraceStates] = useState<Record<string, TraceState>>({});
  if (runs.length === 0) {
    return (
      <p className="rounded-xl border border-[var(--border)] px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">
        {t(
          "This batch has never been run. Once a run finishes, each verdict and what backs it will appear here.",
        )}
      </p>
    );
  }
  const run = selectRun(runs, selectedRunId);
  if (!run) return null;
  const summary = summarizeRun(run.verdicts);
  const track = runTrack(run.receipt, run.verdicts);
  const probingRows = run.verdicts.filter((r) => r.probing);
  const scoredRows = run.verdicts.filter((r) => !r.probing);

  const openTrace = async (row: VerdictRow) => {
    if (!row.trace_rel) return;
    const key = `${run.run_id}:${row.id}`;
    const result = await openJourneyTrace(batchId, run.run_id, row.trace_rel);
    const body = (result.data ?? {}) as Record<string, unknown>;
    let errorCode: string | undefined;
    if (!result.ok) {
      errorCode = result.code === "E_NO_TRACE"
        ? JourneyErrorCode.TRACE_MISSING
        : result.code === JourneyErrorCode.TRACE_VIEWER_MISSING
          ? JourneyErrorCode.TRACE_VIEWER_MISSING
          : JourneyErrorCode.TRACE_SPAWN_FAILED;
    } else if (body.spawn_error) {
      errorCode = JourneyErrorCode.TRACE_SPAWN_FAILED;
    }
    setTraceStates((previous) => ({
      ...previous,
      [key]: {
        errorCode,
        command: typeof body.command === "string" ? body.command : undefined,
      },
    }));
  };

  return (
    <div className="space-y-3">
      {runs.length > 1 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-[var(--muted-foreground)]">
            {t("journey.label.attempts")}
          </span>
          {runs.map((r) => (
            <button
              key={r.run_id}
              type="button"
              onClick={() => setSelectedRunId(r.run_id)}
              className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${
                r.run_id === run.run_id
                  ? "border-[var(--primary)] text-[var(--foreground)]"
                  : "border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
              }`}
            >
              {r.run_id}
            </button>
          ))}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-x-5 gap-y-1 rounded-xl border border-[var(--border)] px-4 py-3 text-sm">
        <span className="text-[var(--foreground)]">
          {t("Pass {{passed}}/{{total}}", {
            passed: summary.passed,
            total: summary.total,
          })}
        </span>
        {summary.failed ? (
          <span className="text-red-700 dark:text-red-400">
            {t("Fail {{count}}", { count: summary.failed })}
          </span>
        ) : null}
        {summary.pending ? (
          <span className="text-amber-700 dark:text-amber-400">
            {t("Pending {{count}}", { count: summary.pending })}
          </span>
        ) : null}
        {summary.blocked ? (
          <span className="text-amber-700 dark:text-amber-400">
            {t("Blocked {{count}}", { count: summary.blocked })}
          </span>
        ) : null}
        {summary.probing ? (
          <span className="text-[var(--muted-foreground)]">
            {t("Probe {{count}} (excluded from the pass rate)", {
              count: summary.probing,
            })}
          </span>
        ) : null}
        {track ? (
          <span className="text-[var(--muted-foreground)]">
            {t("Track {{track}}", {
              track: track === "api" ? t("journey.track.api") : t("journey.track.ui"),
            })}
          </span>
        ) : null}
      </div>

      <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--muted-foreground)]">
              <th className="px-3 py-2 font-medium">{t("journey.col.caseId")}</th>
              <th className="px-3 py-2 font-medium">{t("journey.col.verdict")}</th>
              <th className="px-3 py-2 font-medium">{t("journey.col.evidence")}</th>
              <th className="px-3 py-2 font-medium">{t("journey.col.trace")}</th>
            </tr>
          </thead>
          <tbody>
            {scoredRows.map((row) => {
              const traceState = traceStates[`${run.run_id}:${row.id}`] ?? {};
              const verdict = verdictParts(row.verdict);
              return (
              <tr key={row.id} className="border-b border-[var(--border)] last:border-0 align-top">
                <td className="px-3 py-2.5 font-mono text-xs text-[var(--foreground)]">{row.id}</td>
                <td className="px-3 py-2.5">
                  <VerdictBadge verdict={row.verdict} />
                  {/* 挂起/拦下没写理由 = 没给结论。这里把缺失说出来而不是留白 */}
                  {["PENDING", "BLOCK", "BLOCKED"].includes(verdict.code) ? (
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                      {row.note || verdict.reason ||
                        t("(No reason recorded for holding this one — it must be filled in)")}
                    </p>
                  ) : row.note ? (
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">{row.note}</p>
                  ) : null}
                </td>
                <td className="px-3 py-2.5">
                  <EvidenceCell row={row} />
                </td>
                <td className="px-3 py-2.5">
                  {/* 表格里用紧凑形态：一行说清即可。完整的「是什么坏了 + 还能走哪条路」
                      留给独立呈现的场合——把整块异常卡塞进每一行会把表读死。 */}
                  <TraceLauncher
                    track={track}
                    traceRel={row.trace_rel}
                    command={traceState.command}
                    errorCode={traceState.errorCode}
                    onOpen={() => void openTrace(row)}
                    compact
                  />
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {probingRows.length ? (
        <div className="rounded-xl border border-dashed border-[var(--border)] px-4 py-3">
          <p className="text-xs text-[var(--muted-foreground)]">
            {t(
              "{{count}} probing cases — expectations the requirement body cannot support, recorded as observations only: ",
              { count: probingRows.length },
            )}
            <span className="text-[var(--foreground)]">
              {t("out of the pass rate and not used as criteria")}
            </span>
          </p>
          <ul className="mt-1.5 space-y-1">
            {probingRows.map((row) => (
              <li key={row.id} className="text-xs text-[var(--muted-foreground)]">
                <span className="font-mono">{row.id}</span>
                <span className="ml-2">{row.note || t("(No observation recorded)")}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
