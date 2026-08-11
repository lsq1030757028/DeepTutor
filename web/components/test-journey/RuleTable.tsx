"use client";

// [fork] 规则表 —— **规则的唯一权威视图**（交互稿 s3，0021 必改 2 合并出来的）。
//
// 上一版同一批规则被投影了三次：接入面的澄清表（管"依据"）、分析面板（管"例子"）、
// 覆盖页签（管"覆盖状态"）。同一个实体在三处各认一遍，既是"信息量大"体感的主要来源，
// 也与地基「批次是唯一状态对象」相悖——对象唯一，视图就该唯一。
//
// 所以这张表一行一条规则，把三件事并进同一行：依据（原文引句）/ 例子数 / 覆盖状态。
// **不要**再在别处做第二张规则视图。

import { AlertCircle, FlaskConical, Quote } from "lucide-react";

interface Rule {
  rule_id: string;
  statement: string;
  source_quote?: string;
  /** 探测性：需求正文撑不住的预期。**不进 PASS 判据**，这里也要一眼看出来。 */
  probing?: boolean;
}

interface CoverageRow {
  rule_id: string;
  /** covered / declared_uncovered / gap_unexplained */
  status: string;
  reason?: string;
  case_ids?: string[];
}

interface Props {
  rules: Rule[];
  exampleCounts?: Record<string, number>;
  coverage?: CoverageRow[];
}

const STATUS_TEXT: Record<string, { label: string; className: string }> = {
  covered: {
    label: "有用例",
    className: "text-emerald-700 dark:text-emerald-400",
  },
  declared_uncovered: {
    label: "说明了不测",
    className: "text-amber-700 dark:text-amber-400",
  },
  gap_unexplained: {
    // 这一类是唯一会挡收口的：没测、也没说为什么不测
    label: "没测也没说",
    className: "font-semibold text-red-700 dark:text-red-400",
  },
};

export default function RuleTable({ rules, exampleCounts, coverage }: Props) {
  const coverageById = new Map((coverage ?? []).map((c) => [c.rule_id, c]));
  if (rules.length === 0) {
    return (
      <p className="rounded-xl border border-[var(--border)] px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">
        还没有澄清出规则。规则来自需求正文，在聊天里走一次澄清就会出现在这里。
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
      <table className="w-full min-w-[720px] text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--muted-foreground)]">
            <th className="px-3 py-2 font-medium">规则</th>
            <th className="px-3 py-2 font-medium">依据</th>
            <th className="px-3 py-2 font-medium">例子</th>
            <th className="px-3 py-2 font-medium">覆盖</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => {
            const cov = coverageById.get(rule.rule_id);
            const status = STATUS_TEXT[cov?.status ?? ""] ?? {
              label: "未收口",
              className: "text-[var(--muted-foreground)]",
            };
            return (
              <tr
                key={rule.rule_id}
                className="border-b border-[var(--border)] last:border-0 align-top"
              >
                <td className="px-3 py-2.5">
                  <div className="flex items-start gap-1.5">
                    <span className="font-mono text-xs text-[var(--muted-foreground)]">
                      {rule.rule_id}
                    </span>
                    {rule.probing ? (
                      <span
                        title="探测性：需求正文撑不住的预期，不进 PASS 判据"
                        className="inline-flex items-center gap-0.5 rounded bg-[var(--muted)] px-1 py-0.5 text-[10px] text-[var(--muted-foreground)]"
                      >
                        <FlaskConical className="h-3 w-3" />
                        探测
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 text-[var(--foreground)]">{rule.statement}</p>
                </td>
                <td className="px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
                  {rule.source_quote ? (
                    <span className="flex gap-1">
                      <Quote className="mt-0.5 h-3 w-3 shrink-0" />
                      <span className="line-clamp-3">{rule.source_quote}</span>
                    </span>
                  ) : rule.probing ? (
                    <span>无原文依据（已标探测性）</span>
                  ) : (
                    <span className="flex gap-1 text-amber-700 dark:text-amber-400">
                      <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                      缺依据
                    </span>
                  )}
                </td>
                <td className="px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
                  {exampleCounts?.[rule.rule_id] ?? 0}
                </td>
                <td className="px-3 py-2.5 text-xs">
                  <span className={status.className}>{status.label}</span>
                  {cov?.reason ? (
                    <p className="mt-0.5 text-[var(--muted-foreground)]">{cov.reason}</p>
                  ) : null}
                  {cov?.case_ids?.length ? (
                    <p className="mt-0.5 font-mono text-[10px] text-[var(--muted-foreground)]">
                      {cov.case_ids.join("、")}
                    </p>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
