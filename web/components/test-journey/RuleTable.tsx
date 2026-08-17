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
import { useTranslation } from "react-i18next";

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

/** `label` 是 i18n 键，覆盖三态与富卡那边共用同一批键（同一概念同一个词）。 */
const STATUS_TEXT: Record<string, { label: string; className: string }> = {
  covered: {
    label: "journey.coverage.covered",
    className: "text-emerald-700 dark:text-emerald-400",
  },
  declared_uncovered: {
    label: "journey.coverage.declared",
    className: "text-amber-700 dark:text-amber-400",
  },
  gap_unexplained: {
    // 这一类是唯一会挡收口的：没测、也没说为什么不测
    label: "journey.coverage.gap",
    className: "font-semibold text-red-700 dark:text-red-400",
  },
};

export default function RuleTable({ rules, exampleCounts, coverage }: Props) {
  const { t } = useTranslation();
  const coverageById = new Map((coverage ?? []).map((c) => [c.rule_id, c]));
  if (rules.length === 0) {
    return (
      <p className="rounded-xl border border-[var(--border)] px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">
        {t(
          "No rules clarified yet. Rules come from the requirement body — run one clarification in chat and they will show up here.",
        )}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
      <table className="w-full min-w-[720px] text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--muted-foreground)]">
            <th className="px-3 py-2 font-medium">{t("journey.col.rule")}</th>
            <th className="px-3 py-2 font-medium">{t("journey.col.source")}</th>
            <th className="px-3 py-2 font-medium">{t("journey.col.examples")}</th>
            <th className="px-3 py-2 font-medium">{t("journey.col.coverage")}</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => {
            const cov = coverageById.get(rule.rule_id);
            const status = STATUS_TEXT[cov?.status ?? ""] ?? {
              label: "journey.coverage.notClosed",
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
                        title={t(
                          "Probing: an expectation the requirement body cannot support. Excluded from PASS criteria.",
                        )}
                        className="inline-flex items-center gap-0.5 rounded bg-[var(--muted)] px-1 py-0.5 text-[10px] text-[var(--muted-foreground)]"
                      >
                        <FlaskConical className="h-3 w-3" />
                        {t("journey.badge.probe")}
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
                    <span>{t("No source quote (marked probing)")}</span>
                  ) : (
                    <span className="flex gap-1 text-amber-700 dark:text-amber-400">
                      <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                      {t("Missing source")}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
                  {exampleCounts?.[rule.rule_id] ?? 0}
                </td>
                <td className="px-3 py-2.5 text-xs">
                  <span className={status.className}>{t(status.label)}</span>
                  {cov?.reason ? (
                    <p className="mt-0.5 text-[var(--muted-foreground)]">{cov.reason}</p>
                  ) : null}
                  {cov?.case_ids?.length ? (
                    <p className="mt-0.5 font-mono text-[10px] text-[var(--muted-foreground)]">
                      {cov.case_ids.join(t("journey.listSeparator"))}
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
