"use client";

/**
 * The trajectory view: every model call the session actually made, in the
 * order it happened, with the facts that decide which of {model, prompt,
 * assembly} a bad answer came from.
 *
 * Three rules this file lives under (Delta 十六 / design §四之二·十):
 *
 *  - it renders inside its own tab and writes nothing back to the conversation;
 *  - it carries no attention-getting affordance of any kind — no marker on the
 *    tab, no running tally of problems, no hint in the transcript. The user
 *    said it plainly: they will come and look when something is wrong;
 *  - it states facts and passes no verdicts. "No tool call, 17 tools in hand"
 *    is a fact; "the model should have called one" is a judgement only the
 *    reader can make.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { formatTurnDuration } from "@/lib/trace-timing";
import type { StreamEvent } from "@/lib/unified-ws";
import {
  buildTrajectory,
  hasIdleTools,
  selectAnomalousTurns,
  type TrajectorySource,
  type TrajectoryStep,
  type TrajectoryTurn,
} from "./model";

type Translate = (key: string, opts?: Record<string, unknown>) => string;

const EM_DASH = "—";

function formatSeconds(value: number | null): string {
  if (value === null) return EM_DASH;
  if (value < 10) return `${value.toFixed(2)}s`;
  return formatTurnDuration(value);
}

function formatNumber(value: number | null): string {
  return value === null ? EM_DASH : String(value);
}

function stopLabel(turn: TrajectoryTurn, t: Translate): string {
  const kind = turn.stop?.kind;
  if (!kind) return t("trajectory.stop.pending");
  if (kind === "completed") return t("trajectory.stop.completed");
  if (kind === "interrupted") return t("trajectory.stop.interrupted");
  if (kind === "error") return t("trajectory.stop.error");
  return kind;
}

function assemblyLabel(turn: TrajectoryTurn, t: Translate): string {
  const assembly = turn.assembly;
  if (!assembly || !assembly.state) return t("trajectory.assembly.unrecorded");
  if (assembly.state === "degraded") {
    return t("trajectory.assembly.degraded", {
      stages: assembly.degradedStages.join(", ") || EM_DASH,
      tools: formatNumber(assembly.toolsAvailable),
    });
  }
  return t("trajectory.assembly.ok", {
    tools: formatNumber(assembly.toolsAvailable),
  });
}

function stepTitle(step: TrajectoryStep, position: number, t: Translate) {
  if (step.turnIndex !== null && step.stepIndex !== null) {
    return t("trajectory.step.numbered", {
      turn: step.turnIndex,
      step: step.stepIndex,
    });
  }
  return t("trajectory.step.positional", { position });
}

function describeEvent(event: StreamEvent, t: Translate): string {
  const meta = (event.metadata ?? {}) as Record<string, unknown>;
  const traceKind = meta.trace_kind ? String(meta.trace_kind) : "";
  const toolName = meta.tool_name ? String(meta.tool_name) : "";
  const state = meta.call_state ? String(meta.call_state) : "";
  const parts: string[] = [event.type];
  if (traceKind && traceKind !== event.type) parts.push(traceKind);
  if (state) parts.push(state);
  if (toolName) parts.push(toolName);
  const text = (event.content ?? "").trim();
  if (text) parts.push(text.length > 80 ? `${text.slice(0, 80)}…` : text);
  if (event.type === "error" && !text) parts.push(t("trajectory.event.error"));
  return parts.join(" · ");
}

const rowBase =
  "w-full rounded-lg px-2 py-1.5 text-left text-[13px] leading-[1.5] transition-colors";
const factClass =
  "rounded-md bg-[var(--muted)]/50 px-1.5 py-0.5 text-[12px] text-[var(--muted-foreground)]";

function Fold({ open }: { open: boolean }) {
  const Glyph = open ? ChevronDown : ChevronRight;
  return (
    <Glyph
      aria-hidden="true"
      className="mt-[3px] h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)]"
    />
  );
}

export function TrajectoryPanel({ messages }: { messages: TrajectorySource[] }) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [anomaliesOnly, setAnomaliesOnly] = useState(false);
  const [turnFolds, setTurnFolds] = useState<Record<string, boolean>>({});
  const [stepFolds, setStepFolds] = useState<Record<string, boolean>>({});

  const turns = useMemo(() => buildTrajectory(messages), [messages]);
  const visibleTurns = useMemo(
    () => (anomaliesOnly ? selectAnomalousTurns(turns) : turns),
    [anomaliesOnly, turns],
  );
  const lastTurnKey = turns.length ? turns[turns.length - 1].key : null;

  const isTurnOpen = useCallback(
    (turn: TrajectoryTurn) =>
      turnFolds[turn.key] ?? (anomaliesOnly || turn.key === lastTurnKey),
    [anomaliesOnly, lastTurnKey, turnFolds],
  );
  const isStepOpen = useCallback(
    (step: TrajectoryStep) => stepFolds[step.key] ?? step.anomalous,
    [stepFolds],
  );

  const setAllTurns = useCallback(
    (open: boolean) =>
      setTurnFolds(Object.fromEntries(turns.map((turn) => [turn.key, open]))),
    [turns],
  );
  const setAllSteps = useCallback(
    (open: boolean) =>
      setStepFolds(
        Object.fromEntries(
          turns.flatMap((turn) => turn.steps.map((step) => [step.key, open])),
        ),
      ),
    [turns],
  );

  /**
   * Keyboard handling is bound to this element, never to `window` or
   * `document`: two existing listeners already fight over the same keys at
   * those scopes — the turn navigator's window-level `Alt+Arrow` (A32) and the
   * session viewer's document-level `Escape` with `stopPropagation` (A33).
   */
  const handleKeyDown = useCallback((event: React.KeyboardEvent) => {
    const root = containerRef.current;
    if (!root) return;
    const active = document.activeElement as HTMLElement | null;

    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      if (!active?.hasAttribute("data-trajectory-row")) return;
      const open = active.getAttribute("aria-expanded") === "true";
      if (open === (event.key === "ArrowRight")) return;
      event.preventDefault();
      active.click();
      return;
    }

    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const rows = Array.from(
      root.querySelectorAll<HTMLElement>("[data-trajectory-row]"),
    );
    if (!rows.length) return;
    const at = active ? rows.indexOf(active) : -1;
    const next =
      event.key === "ArrowDown"
        ? Math.min(rows.length - 1, at + 1)
        : Math.max(0, at <= 0 ? 0 : at - 1);
    event.preventDefault();
    rows[next]?.focus();
  }, []);

  return (
    <div
      ref={containerRef}
      onKeyDown={handleKeyDown}
      data-trajectory-root="true"
      className="flex w-full flex-1 min-h-0 flex-col"
    >
      <div className="mx-auto flex w-full max-w-[960px] flex-wrap items-center gap-x-4 gap-y-2 px-6 pb-2 pt-1">
        <label className="inline-flex items-center gap-2 text-[13px] text-[var(--foreground)]">
          <input
            type="checkbox"
            checked={anomaliesOnly}
            onChange={(event) => setAnomaliesOnly(event.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--foreground)]"
          />
          {t("trajectory.filter.anomaliesOnly")}
        </label>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--muted-foreground)]">
          <button type="button" onClick={() => setAllTurns(true)}>
            {t("trajectory.turns.expandAll")}
          </button>
          <button type="button" onClick={() => setAllTurns(false)}>
            {t("trajectory.turns.collapseAll")}
          </button>
          <button type="button" onClick={() => setAllSteps(true)}>
            {t("trajectory.calls.expandAll")}
          </button>
          <button type="button" onClick={() => setAllSteps(false)}>
            {t("trajectory.calls.collapseAll")}
          </button>
        </div>
      </div>

      <div className="w-full flex-1 min-h-0 overflow-y-auto [scrollbar-gutter:stable_both-edges] pb-10">
        <div className="mx-auto w-full max-w-[960px] space-y-2 px-6">
          {!turns.length ? (
            <p className="py-10 text-center text-[13px] text-[var(--muted-foreground)]">
              {t("trajectory.empty")}
            </p>
          ) : !visibleTurns.length ? (
            <p className="py-10 text-center text-[13px] text-[var(--muted-foreground)]">
              {t("trajectory.filter.noMatch")}
            </p>
          ) : (
            visibleTurns.map((turn) => {
              const open = isTurnOpen(turn);
              const position = turns.indexOf(turn) + 1;
              return (
                <section
                  key={turn.key}
                  className="rounded-xl border border-[var(--border)] bg-[var(--background)]"
                >
                  <button
                    type="button"
                    data-trajectory-row="turn"
                    aria-expanded={open}
                    onClick={() =>
                      setTurnFolds((prev) => ({ ...prev, [turn.key]: !open }))
                    }
                    className={`${rowBase} flex items-start gap-2 hover:bg-[var(--muted)]/40`}
                  >
                    <Fold open={open} />
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium text-[var(--foreground)]">
                        {t("trajectory.turn.title", { position })}
                      </span>
                      <span className={factClass}>
                        {t("trajectory.turn.calls", {
                          value: turn.steps.length,
                        })}
                      </span>
                      <span className={factClass}>
                        {t("trajectory.turn.toolCalls", {
                          value: formatNumber(turn.toolCalls),
                        })}
                      </span>
                      <span className={factClass}>
                        {t("trajectory.turn.duration", {
                          value: formatSeconds(turn.durationSeconds),
                        })}
                      </span>
                      <span className={factClass}>{stopLabel(turn, t)}</span>
                      <span className={factClass}>{assemblyLabel(turn, t)}</span>
                      <span className={factClass}>
                        {t("trajectory.turn.tokens", {
                          value: formatNumber(turn.totalTokens),
                        })}
                      </span>
                    </span>
                  </button>

                  {open ? (
                    <div className="space-y-1.5 border-t border-[var(--border)] px-2 py-2">
                      <TurnAssembly turn={turn} t={t} />
                      {turn.steps.map((step, index) => (
                        <StepRow
                          key={step.key}
                          step={step}
                          position={index + 1}
                          open={isStepOpen(step)}
                          onToggle={() =>
                            setStepFolds((prev) => ({
                              ...prev,
                              [step.key]: !isStepOpen(step),
                            }))
                          }
                          t={t}
                        />
                      ))}
                      <TurnTail turn={turn} t={t} />
                    </div>
                  ) : null}
                </section>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

function TurnAssembly({ turn, t }: { turn: TrajectoryTurn; t: Translate }) {
  const assembly = turn.assembly;
  // The turn row already states whether assembly was normal; this block only
  // exists to spell out what degraded, so a turn with no record shows nothing
  // rather than repeating "no record" twice.
  if (!assembly) return null;
  return (
    <div className="rounded-lg bg-[var(--muted)]/35 px-2.5 py-1.5 text-[12.5px] text-[var(--muted-foreground)]">
      <p className="text-[var(--foreground)]">{assemblyLabel(turn, t)}</p>
      {assembly?.degradations.map((entry, index) => (
        <p key={`${entry.stage}-${index}`} className="mt-0.5 font-mono text-[11.5px]">
          {[entry.stage, entry.reason, entry.detail]
            .filter(Boolean)
            .join(" · ")}
          {typeof entry.elapsed_ms === "number"
            ? ` · ${entry.elapsed_ms}ms`
            : ""}
        </p>
      ))}
      {assembly?.mcpServers.map((server, index) => (
        <p key={`${server.name}-${index}`} className="mt-0.5 font-mono text-[11.5px]">
          {[server.name, server.transport, server.status, server.error]
            .filter(Boolean)
            .join(" · ")}
        </p>
      ))}
      {assembly?.ownerScopeTimeoutSeconds !== null &&
      assembly?.ownerScopeTimeoutSeconds !== undefined ? (
        <p className="mt-0.5 font-mono text-[11.5px]">
          {t("trajectory.assembly.ownerScopeTimeout", {
            value: assembly.ownerScopeTimeoutSeconds,
          })}
        </p>
      ) : null}
    </div>
  );
}

function TurnTail({ turn, t }: { turn: TrajectoryTurn; t: Translate }) {
  const errors = turn.turnEvents.filter((event) => event.type === "error");
  if (!errors.length) return null;
  return (
    <div className="rounded-lg bg-[var(--destructive)]/10 px-2.5 py-1.5 text-[12.5px] text-[var(--destructive)]">
      {errors.map((event, index) => (
        <p key={index} className="font-mono text-[11.5px]">
          {event.content?.trim() || t("trajectory.event.error")}
        </p>
      ))}
    </div>
  );
}

/**
 * One model call. Every fact the round recorded sits on the row itself —
 * folding only decides whether the raw events behind it are listed, so nothing
 * a reader needs to compare across rounds is hidden behind a click.
 */
function StepRow({
  step,
  position,
  open,
  onToggle,
  t,
}: {
  step: TrajectoryStep;
  position: number;
  open: boolean;
  onToggle: () => void;
  t: Translate;
}) {
  return (
    <div className="rounded-lg border border-[var(--border)]/70">
      <button
        type="button"
        data-trajectory-row="step"
        aria-expanded={open}
        onClick={onToggle}
        title={open ? t("trajectory.events.hide") : t("trajectory.events.show")}
        className={`${rowBase} flex items-start gap-2 hover:bg-[var(--muted)]/40`}
      >
        <Fold open={open} />
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium text-[var(--foreground)]">
            {stepTitle(step, position, t)}
          </span>
          <span className={factClass}>
            {t("trajectory.step.finishReason", {
              value: step.finishReason ?? EM_DASH,
            })}
          </span>
          <span className={factClass}>
            {t("trajectory.step.outcome", {
              value: step.outcomeKind ?? EM_DASH,
            })}
          </span>
          <span className={factClass}>
            {t("trajectory.step.duration", {
              value: formatSeconds(step.durationSeconds),
            })}
          </span>
          <span className={factClass}>
            {t("trajectory.step.firstChunk", {
              value: formatSeconds(step.firstChunkSeconds),
            })}
          </span>
          <span className={factClass}>
            {t("trajectory.step.charsPerSecond", {
              value:
                step.charsPerSecond === null
                  ? EM_DASH
                  : step.charsPerSecond.toFixed(1),
            })}
          </span>
          <span className={factClass}>
            {t("trajectory.step.tokens", {
              value: formatNumber(step.stepTokens?.total ?? null),
            })}
          </span>
          {hasIdleTools(step) ? (
            <span className={factClass}>
              {t("trajectory.step.noToolCall", { tools: step.toolsOffered })}
            </span>
          ) : null}
          {step.toolsRequested.length ? (
            <span className={`${factClass} font-mono`}>
              {t("trajectory.step.toolsRequested", {
                value: step.toolsRequested.join(", "),
              })}
            </span>
          ) : null}
        </span>
      </button>

      {open ? (
        <ol className="space-y-0.5 px-3 pb-2 font-mono text-[11.5px] text-[var(--muted-foreground)]">
          {step.events.map((event, index) => (
            <li key={index} className="break-all">
              {describeEvent(event, t)}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

export default TrajectoryPanel;
