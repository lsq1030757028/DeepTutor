"use client";

// [fork] 聊天侧四张富结果卡（交互稿 §6f）。
//
// ## 分工口径（判据不是"重不重要"，是"要不要动手"）
//
// 只读的、随生成流出来的、看一眼就够的 → 这里；
// 要逐条勾、要编辑、要筛选、要多列对照、要长期回看的 → 工作台。
// 所以这四张卡**没有一个会改状态的控件**：页脚那个按钮是跳转，不是提交。
//
// ## 三张卡共用一个骨架
//
// 标题行（状态点 + 一句话 + 右侧计数）→ 内容 → 页脚（一句边界说明 + 一个
// 去工作台的按钮）。**页脚那句边界说明是强制的**——它回答"为什么这张卡不让我
// 在这儿改"，不写用户就会去点卡里的东西然后发现点不动。
//
// ## 第四张卡（规则清单）的形态是推导的，不是稿上画的
//
// §6f 的分工表列了四个聊天产物，但只画了三张卡的样子。「规则清单（澄清结果）」
// 那张**稿未直接画**，本卡形态由上面那个共用骨架推导。manager 2026-08-11 批准
// 推导且要求留痕：**推导出来的东西和稿上画过的东西，可信度不是一回事**，
// UAT 清单里它单列一条。
//
// ## 硬红线：零测试元素
//
// 每张卡的取数第一关是 `msg.capability === "test"`，判断在
// `ChatMessages.tsx` 的分支里，本文件只在那道判断之内被渲染。
// 一旦漏挂，普通聊天里就会冒出测试元素——这是 §6c 的唯一硬红线。
// 判据在 `web/tests/test-journey-cards.test.ts`，且**"普通聊天不出现"单独一条**，
// 不靠"测试模式里出现"顺带。

import { AlertTriangle, FlaskConical, Loader2, Quote } from "lucide-react";

import { useJourneyCardCopy, type JourneyCardCopy } from "./copy";
import type { JourneyState, RunState } from "./extract";

// ── 骨架 ───────────────────────────────────────────────────────────────────

type Tone = "live" | "ok" | "bad" | "mute";

const DOT_TONE: Record<Tone, string> = {
  live: "bg-[var(--primary)]",
  ok: "bg-emerald-500",
  bad: "bg-red-500",
  mute: "bg-[var(--muted-foreground)]/50",
};

function Dot({ tone }: { tone: Tone }) {
  if (tone === "live") {
    return (
      <Loader2
        className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--primary)]"
        aria-hidden
      />
    );
  }
  return (
    <span
      aria-hidden
      className={`mt-[3px] h-2 w-2 shrink-0 rounded-full ${DOT_TONE[tone]}`}
    />
  );
}

function Chip({
  children,
  tone = "mute",
  title,
}: {
  children: React.ReactNode;
  tone?: "mute" | "ok" | "bad" | "warn";
  title?: string;
}) {
  const cls =
    tone === "ok"
      ? "text-emerald-700 dark:text-emerald-400"
      : tone === "bad"
        ? "text-red-700 dark:text-red-400"
        : tone === "warn"
          ? "text-amber-700 dark:text-amber-400"
          : "text-[var(--muted-foreground)]";
  return (
    <span
      title={title}
      className={`shrink-0 rounded bg-[var(--muted)] px-1.5 py-0.5 text-[10px] ${cls}`}
    >
      {children}
    </span>
  );
}

/**
 * 共用骨架。`boundary` 是**必填**：没有边界说明的富卡不许出现在聊天里。
 * 类型上强制，比写在注释里靠谱。
 */
function ResultCard({
  tone,
  title,
  meta,
  boundary,
  action,
  children,
}: {
  tone: Tone;
  title: string;
  meta?: React.ReactNode;
  boundary: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <section className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--card)] px-3.5 py-3 text-sm">
      <header className="flex items-start gap-2">
        <Dot tone={tone} />
        <h4 className="min-w-0 flex-1 font-medium text-[var(--foreground)]">
          {title}
        </h4>
        {meta ? <div className="flex shrink-0 items-center gap-1">{meta}</div> : null}
      </header>
      {children ? <div className="mt-2.5">{children}</div> : null}
      <footer className="mt-3 flex items-center gap-3 border-t border-[var(--border)] pt-2.5">
        <p className="min-w-0 flex-1 text-[11.5px] text-[var(--muted-foreground)]">
          {boundary}
        </p>
        {action}
      </footer>
    </section>
  );
}

function WorkbenchLink({
  batchId,
  label,
  disabled,
  emphasis,
}: {
  batchId: string;
  label: string;
  disabled?: boolean;
  emphasis?: boolean;
}) {
  const base =
    "shrink-0 rounded-lg border px-2.5 py-1 text-[11.5px] transition-colors";
  if (disabled || !batchId) {
    return (
      <span
        className={`${base} cursor-not-allowed border-[var(--border)] text-[var(--muted-foreground)]/60`}
      >
        {label}
      </span>
    );
  }
  return (
    <a
      href={`/test-journey/${encodeURIComponent(batchId)}`}
      className={
        emphasis
          ? `${base} border-transparent bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90`
          : `${base} border-[var(--border)] text-[var(--foreground)] hover:bg-[var(--muted)]`
      }
    >
      {label}
    </a>
  );
}

function ChatLink({
  batchId,
  label,
  disabled,
  emphasis,
}: {
  batchId: string;
  label: string;
  disabled?: boolean;
  emphasis?: boolean;
}) {
  const base =
    "shrink-0 rounded-lg border px-2.5 py-1 text-[11.5px] transition-colors";
  if (disabled || !batchId) {
    return (
      <span className={`${base} cursor-not-allowed border-[var(--border)] text-[var(--muted-foreground)]/60`}>
        {label}
      </span>
    );
  }
  return (
    <a
      href={`/home?capability=test&test_batch=${encodeURIComponent(batchId)}`}
      className={
        emphasis
          ? `${base} border-transparent bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90`
          : `${base} border-[var(--border)] text-[var(--foreground)] hover:bg-[var(--muted)]`
      }
    >
      {label}
    </a>
  );
}

function CaseRow({
  id,
  title,
  badge,
}: {
  id: string;
  title: string;
  badge?: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-2 border-b border-[var(--border)] py-1.5 last:border-0">
      <span className="shrink-0 font-mono text-[11px] text-[var(--muted-foreground)]">
        {id}
      </span>
      <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--foreground)]">
        {title}
      </span>
      {badge}
    </div>
  );
}

// ── 卡一 · 规则清单（形态由共用骨架推导，稿未直接画）────────────────────────

function RulesCard({ state }: { state: JourneyState }) {
  const C = useJourneyCardCopy();
  const { rules } = state;
  if (rules.length === 0) return null;
  return (
    <ResultCard
      tone="ok"
      title={C.rules.title}
      meta={<Chip>{C.rules.count(rules.length)}</Chip>}
      boundary={C.rules.boundary}
      action={<WorkbenchLink batchId={state.batchId} label={C.rules.action} />}
    >
      <ul>
        {rules.map((rule) => (
          <li
            key={rule.rule_id}
            className="border-b border-[var(--border)] py-1.5 last:border-0"
          >
            <div className="flex items-baseline gap-2">
              <span className="shrink-0 font-mono text-[11px] text-[var(--muted-foreground)]">
                {rule.rule_id}
              </span>
              <span className="min-w-0 flex-1 text-[13px] text-[var(--foreground)]">
                {rule.statement}
              </span>
              {rule.probing ? (
                <Chip tone="warn" title={C.rules.probingHint}>
                  <FlaskConical className="mr-0.5 inline h-3 w-3" />
                  {C.rules.probing}
                </Chip>
              ) : null}
            </div>
            {rule.source_quote ? (
              <p className="mt-0.5 flex gap-1 pl-1 text-[11.5px] text-[var(--muted-foreground)]">
                <Quote className="mt-0.5 h-3 w-3 shrink-0" />
                <span className="line-clamp-2">{rule.source_quote}</span>
              </p>
            ) : rule.probing ? null : (
              <p className="mt-0.5 pl-1 text-[11.5px] text-amber-700 dark:text-amber-400">
                {C.rules.noQuote}
              </p>
            )}
          </li>
        ))}
      </ul>
    </ResultCard>
  );
}

// ── 卡二 · 用例草稿概览（流式）────────────────────────────────────────────

function DraftCard({ state }: { state: JourneyState }) {
  const C = useJourneyCardCopy();
  const draft = state.draft;
  const live = state.pending === "draft_cases";
  if (!draft && !live) return null;
  const cases = draft?.cases ?? [];
  return (
    <ResultCard
      tone={live ? "live" : "ok"}
      title={live ? C.draft.titleLive : C.draft.titleDone}
      meta={cases.length > 0 ? <Chip>{C.draft.count(cases.length)}</Chip> : null}
      boundary={C.draft.boundary}
      action={
        <ChatLink
          batchId={state.batchId}
          label={live ? C.draft.actionPending : C.draft.action}
          disabled={live}
        />
      }
    >
      {cases.length > 0 ? (
        <>
          <div>
            {cases.map((c) => (
              <CaseRow
                key={c.draft_id}
                id={c.draft_id}
                title={c.title}
                badge={
                  <Chip tone={c.probing ? "warn" : "mute"}>
                    {c.probing ? C.draft.probing : C.draft.business}
                  </Chip>
                }
              />
            ))}
          </div>
          {draft && draft.uncoveredCount > 0 ? (
            <p className="mt-2 text-[11.5px] text-[var(--muted-foreground)]">
              {C.draft.uncovered(draft.uncoveredCount)}
            </p>
          ) : null}
        </>
      ) : null}
    </ResultCard>
  );
}

// ── 卡三 · 执行进度与结论摘要（流式）──────────────────────────────────────

function verdictMeta(run: RunState, C: JourneyCardCopy) {
  const d = run.distribution;
  const out: React.ReactNode[] = [];
  if (d.PASS) out.push(<Chip key="p" tone="ok">{`${C.run.pass} ${d.PASS}`}</Chip>);
  if (d.FAIL) out.push(<Chip key="f" tone="bad">{`${C.run.fail} ${d.FAIL}`}</Chip>);
  if (d.PENDING)
    out.push(<Chip key="n" tone="warn">{`${C.run.pending} ${d.PENDING}`}</Chip>);
  if (d.BLOCKED)
    out.push(<Chip key="b" tone="warn">{`${C.run.blocked} ${d.BLOCKED}`}</Chip>);
  return out;
}

function RunCard({ state }: { state: JourneyState }) {
  const C = useJourneyCardCopy();
  const run = state.run;
  const live = state.pending === "execute" || state.pending === "project";
  if (!run && !live) return null;

  const title = live ? C.run.titleLive : C.run.titleDone;
  const attempt = run ? C.run.attempt(run.attempt) : "";
  const meta: React.ReactNode[] = [];
  if (run?.triggered_by === "regenerate-replay") {
    meta.push(
      <Chip key="replay" tone="warn" title={C.run.replayHint}>
        {C.run.replay}
      </Chip>,
    );
  }
  if (run) meta.push(...verdictMeta(run, C));

  // 没跑投影就没有可信的通过/没过。这里**不拿收据里的 pytest 计数冒充结论**——
  // 那是两个层次的东西，混起来正是"把我们其实没验说成验过了"的那一步。
  const failedCases =
    state.coverage?.cases.filter(
      (c) => c.verdict && c.verdict !== "PASS" && c.verdict !== "NOT_EXECUTED",
    ) ?? [];

  return (
    <ResultCard
      tone={live ? "live" : run && run.distribution.FAIL ? "bad" : "ok"}
      title={attempt ? `${title} · ${attempt}` : title}
      meta={meta.length > 0 ? meta : null}
      boundary={C.run.boundary}
      action={
        <WorkbenchLink batchId={state.batchId} label={C.run.action} disabled={live} />
      }
    >
      {run ? (
        <>
          {run.selected > 0 ? (
            <p className="text-[11.5px] text-[var(--muted-foreground)]">
              {C.run.selected(run.selected)}
              {run.run_id ? (
                <span className="ml-2 font-mono text-[11px]">{run.run_id}</span>
              ) : null}
            </p>
          ) : null}
          {!run.projected ? (
            <p className="mt-1 text-[11.5px] text-amber-700 dark:text-amber-400">
              {C.run.notProjected}
            </p>
          ) : null}
          {run.target_drift ? (
            <p
              className="mt-1 flex items-start gap-1 text-[11.5px] text-amber-700 dark:text-amber-400"
              title={C.run.driftHint}
            >
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              {C.run.drift}
            </p>
          ) : null}
          {failedCases.length > 0 ? (
            <div className="mt-2">
              {failedCases.map((c) => (
                <CaseRow
                  key={c.case_id}
                  id={c.case_id}
                  title={c.title}
                  badge={
                    <Chip tone={c.verdict === "FAIL" ? "bad" : "warn"}>{c.verdict}</Chip>
                  }
                />
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </ResultCard>
  );
}

// ── 卡四 · 覆盖收口摘要 ───────────────────────────────────────────────────

function CoverageCard({ state }: { state: JourneyState }) {
  const C = useJourneyCardCopy();
  const cov = state.coverage;
  if (!cov) return null;
  const s = cov.summary;
  const total = Math.max(s.total_rules, 1);
  const bars: { key: string; n: number; cls: string }[] = [
    { key: "covered", n: s.covered, cls: "bg-emerald-500" },
    { key: "declared", n: s.declared_uncovered, cls: "bg-amber-500" },
    { key: "gap", n: s.gap_unexplained, cls: "bg-red-500" },
  ];
  return (
    <ResultCard
      tone={cov.done ? "ok" : "bad"}
      title={cov.done ? C.coverage.titleDone : C.coverage.titleGap}
      meta={<Chip>{C.coverage.ruleCount(s.total_rules)}</Chip>}
      boundary={C.coverage.boundary}
      action={
        cov.done ? (
          <WorkbenchLink batchId={state.batchId} label={C.coverage.actionDone} />
        ) : (
          <ChatLink batchId={state.batchId} label={C.coverage.action} emphasis />
        )
      }
    >
      <div className="flex h-2 overflow-hidden rounded-full bg-[var(--muted)]">
        {bars.map((bar) =>
          bar.n > 0 ? (
            <span
              key={bar.key}
              className={bar.cls}
              style={{ width: `${(bar.n / total) * 100}%` }}
            />
          ) : null,
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-3 text-[11px] text-[var(--muted-foreground)]">
        <span>
          <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
          {C.coverage.covered} {s.covered}
        </span>
        <span>
          <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />
          {C.coverage.declared} {s.declared_uncovered}
        </span>
        <span>
          <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-red-500" />
          {C.coverage.gap} {s.gap_unexplained}
        </span>
      </div>
      {cov.problems.length > 0 ? (
        <ul className="mt-2">
          {cov.problems.slice(0, 4).map((p) => (
            <li
              key={p}
              className="py-0.5 text-[11.5px] text-red-700 dark:text-red-400"
            >
              {p}
            </li>
          ))}
        </ul>
      ) : null}
    </ResultCard>
  );
}

// ── 错误：工具明说自己失败了，或返回的不是业务数据 ─────────────────────────

function ErrorCard({ state }: { state: JourneyState }) {
  const C = useJourneyCardCopy();
  if (state.errors.length === 0) return null;
  return (
    <section className="mt-3 rounded-xl border border-red-500/40 bg-red-500/5 px-3.5 py-3 text-sm">
      <header className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600 dark:text-red-400" />
        <h4 className="font-medium text-[var(--foreground)]">{C.common.errorTitle}</h4>
      </header>
      <ul className="mt-1.5">
        {state.errors.map((err, index) => (
          <li
            key={`${err.tool}-${err.code}-${index}`}
            className="py-0.5 text-[12px] text-[var(--muted-foreground)]"
          >
            <span className="font-mono text-[11px]">{`journey_${err.tool}`}</span>{" "}
            <span className="font-mono text-[11px]">{err.code}</span>{" "}
            {err.message || C.common.errorUnavailable}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ── 入口 ───────────────────────────────────────────────────────────────────

/**
 * 四张卡按旅程顺序排。**空状态返回 null**，不留空壳——
 * "选了模式但还没开始"和"这一轮什么都没发生"，用户看到的应当一样。
 */
export default function TestJourneyCards({ state }: { state: JourneyState }) {
  return (
    <div data-testid="test-journey-cards">
      <RulesCard state={state} />
      <DraftCard state={state} />
      <RunCard state={state} />
      <CoverageCard state={state} />
      <ErrorCard state={state} />
    </div>
  );
}
