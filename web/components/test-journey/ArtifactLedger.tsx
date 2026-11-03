"use client";

// [fork] 阶段账本（交互稿 s2）。**这不是进度条，是账本。**
//
// 四段亮不亮，只看"这个东西产出来没有"，不看走到第几步——所以这里没有百分比、
// 没有当前步高亮、没有"第 3/9 步"。界面四段是后端九格的**投影**（0021 必改 1）：
// 段状态由段内格子即时汇总，不存盘、不新增状态源。
//
// 灭格点开只说一句"缺什么才能亮"，且两种理由互斥：
//   · 前置没产出 → 缺前置（去把前置做出来）
//   · 前置齐了还灭 → 本步没产出或被闸拦下（去看闸说了什么）
// 合并成一句"未完成"就把"该我做什么"这个唯一要回答的问题又丢回给用户了。

import { useState } from "react";
import { Check, ChevronRight, Circle, MinusCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Segment, StepCell } from "@/components/test-journey/types";
import { nextAction, projectSegments } from "@/components/test-journey/types";

function CellRow({ cell }: { cell: StepCell }) {
  const { t } = useTranslation();
  const why = cell.missing_prereq
    ? t("Missing prerequisite: {{artifact}}", { artifact: cell.missing_prereq })
    : cell.blocked_reason || t("Not produced yet");
  return (
    <li className="flex items-start gap-2 py-1.5">
      {cell.present ? (
        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <MinusCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)]" />
      )}
      <div className="min-w-0">
        <span className="text-sm text-[var(--foreground)]">{cell.label}</span>
        {cell.run_id ? (
          <span className="ml-2 text-xs text-[var(--muted-foreground)]">
            {/* run 级格子带 run 锚——多趟时说得清"哪一趟亮的" */}
            @{cell.run_id}
          </span>
        ) : null}
        {!cell.present ? (
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">{why}</p>
        ) : null}
      </div>
    </li>
  );
}

function SegmentBlock({ segment }: { segment: Segment }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const done = segment.cells.filter((c) => c.present).length;
  const tone =
    segment.status === "done"
      ? "text-emerald-700 dark:text-emerald-400"
      : segment.status === "doing"
        ? "text-amber-700 dark:text-amber-400"
        : "text-[var(--muted-foreground)]";
  return (
    <div className="rounded-lg border border-[var(--border)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-[var(--muted)]"
        aria-expanded={open}
      >
        <ChevronRight
          className={`h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)] transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        {segment.status === "done" ? (
          <Check className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <Circle className={`h-4 w-4 shrink-0 ${tone}`} />
        )}
        <span className="flex-1 text-sm font-medium text-[var(--foreground)]">
          {t(segment.label)}
        </span>
        <span className="text-xs text-[var(--muted-foreground)]">
          {done}/{segment.cells.length}
        </span>
      </button>
      {open ? (
        <ul className="border-t border-[var(--border)] px-3 py-1.5 pl-9">
          {segment.cells.map((cell) => (
            <CellRow key={cell.artifact} cell={cell} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export default function ArtifactLedger({ cells }: { cells: StepCell[] }) {
  const { t } = useTranslation();
  const segments = projectSegments(cells);
  const next = nextAction(cells);
  return (
    <div className="space-y-4">
      {/* 第一眼只回答一件事：这条旅程现在轮到我做什么 */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--muted)] px-4 py-3">
        <p className="text-xs text-[var(--muted-foreground)]">{t("Your next action")}</p>
        <p className="mt-1 text-sm text-[var(--foreground)]">
          {next
            ? next.missing_prereq
              ? t("{{label}} (produce “{{prereq}}” first)", {
                  label: next.label,
                  prereq: next.missing_prereq,
                })
              : next.label
            : t("All nine cells are filled — this journey can be closed out.")}
        </p>
      </div>
      <div className="space-y-2">
        {segments.map((segment) => (
          <SegmentBlock key={segment.id} segment={segment} />
        ))}
      </div>
      <p className="text-xs text-[var(--muted-foreground)]">
        {t(
          "The ledger is derived from artifact existence, not from progress — a lit cell means that artifact is really on disk.",
        )}
      </p>
    </div>
  );
}
