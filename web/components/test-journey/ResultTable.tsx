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

import TraceLauncher from "@/components/test-journey/TraceLauncher";
import type { RunRow, VerdictRow } from "@/components/test-journey/types";
import { runTrack, summarizeRun } from "@/components/test-journey/types";

const VERDICT_STYLE: Record<string, { label: string; className: string }> = {
  PASS: { label: "通过", className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300" },
  FAIL: { label: "不通过", className: "bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300" },
  PENDING: { label: "挂起", className: "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" },
  BLOCK: { label: "拦下", className: "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" },
  BLOCKED: { label: "拦下", className: "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" },
};

function VerdictBadge({ verdict }: { verdict: string }) {
  const style = VERDICT_STYLE[String(verdict).toUpperCase()] ?? {
    label: verdict,
    className: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  };
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs ${style.className}`}>
      {style.label}
    </span>
  );
}

function EvidenceCell({ row }: { row: VerdictRow }) {
  const refs = (row.evidence ?? []).map((e) => e?.ref).filter(Boolean) as string[];
  return (
    <div className="text-xs text-[var(--muted-foreground)]">
      {row.layer ? (
        <span className="mr-1.5 rounded bg-[var(--muted)] px-1 py-0.5">{row.layer}</span>
      ) : null}
      {refs.length ? (
        <span className="font-mono">{refs.join("、")}</span>
      ) : (
        // 没有证据指针的"通过"是最贵的假绿，这里必须说出来
        <span className="text-amber-700 dark:text-amber-400">无证据指针</span>
      )}
    </div>
  );
}

export default function ResultTable({ runs }: { runs: RunRow[] }) {
  const [index, setIndex] = useState(runs.length - 1);
  if (runs.length === 0) {
    return (
      <p className="rounded-xl border border-[var(--border)] px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">
        这条批次还没有执行过。执行完成后，逐条结论与它凭什么会出现在这里。
      </p>
    );
  }
  const run = runs[Math.min(Math.max(index, 0), runs.length - 1)];
  const summary = summarizeRun(run.verdicts);
  const track = runTrack(run.receipt, run.verdicts);
  const probingRows = run.verdicts.filter((r) => r.probing);
  const scoredRows = run.verdicts.filter((r) => !r.probing);

  return (
    <div className="space-y-3">
      {runs.length > 1 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-[var(--muted-foreground)]">趟次</span>
          {runs.map((r, i) => (
            <button
              key={r.run_id}
              type="button"
              onClick={() => setIndex(i)}
              className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${
                i === index
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
          通过 {summary.passed}/{summary.total}
        </span>
        {summary.failed ? (
          <span className="text-red-700 dark:text-red-400">不通过 {summary.failed}</span>
        ) : null}
        {summary.pending ? (
          <span className="text-amber-700 dark:text-amber-400">挂起 {summary.pending}</span>
        ) : null}
        {summary.blocked ? (
          <span className="text-amber-700 dark:text-amber-400">拦下 {summary.blocked}</span>
        ) : null}
        {summary.probing ? (
          <span className="text-[var(--muted-foreground)]">
            探测 {summary.probing}（不计入通过率）
          </span>
        ) : null}
        {track ? (
          <span className="text-[var(--muted-foreground)]">
            轨道 {track === "api" ? "接口" : "界面"}
          </span>
        ) : null}
      </div>

      <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--muted-foreground)]">
              <th className="px-3 py-2 font-medium">编号</th>
              <th className="px-3 py-2 font-medium">结论</th>
              <th className="px-3 py-2 font-medium">凭什么</th>
              <th className="px-3 py-2 font-medium">回放</th>
            </tr>
          </thead>
          <tbody>
            {scoredRows.map((row) => (
              <tr key={row.id} className="border-b border-[var(--border)] last:border-0 align-top">
                <td className="px-3 py-2.5 font-mono text-xs text-[var(--foreground)]">{row.id}</td>
                <td className="px-3 py-2.5">
                  <VerdictBadge verdict={row.verdict} />
                  {/* 挂起/拦下没写理由 = 没给结论。这里把缺失说出来而不是留白 */}
                  {["PENDING", "BLOCK", "BLOCKED"].includes(String(row.verdict).toUpperCase()) ? (
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                      {row.note || "（未写明为什么挂起——这条要补）"}
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
                  <TraceLauncher track={track} traceRel={null} compact />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {probingRows.length ? (
        <div className="rounded-xl border border-dashed border-[var(--border)] px-4 py-3">
          <p className="text-xs text-[var(--muted-foreground)]">
            探测项 {probingRows.length} 条 —— 需求正文撑不住的预期，只作观察，
            <span className="text-[var(--foreground)]">不进通过率、不作判据</span>。
          </p>
          <ul className="mt-1.5 space-y-1">
            {probingRows.map((row) => (
              <li key={row.id} className="text-xs text-[var(--muted-foreground)]">
                <span className="font-mono">{row.id}</span>
                <span className="ml-2">{row.note || "（无观察记录）"}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
