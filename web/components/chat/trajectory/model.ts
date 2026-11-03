/**
 * The trajectory tab's own selectors over a session's stream events.
 *
 * Deliberately NOT built on `CallTracePanel`'s grouping. The conversation
 * panel drops any call group with "no substance", and its substance rule
 * (`groupHasTraceSubstance`, TracePanels.tsx:721-723) excludes
 * `trace_kind === "call_status"` — so a model call that produced only a
 * status marker, no tool call and no reasoning text does not exist as a row
 * in the conversation at all. That blindness is exactly what this tab is for,
 * so the selection starts again from the raw events (design §四之二·一).
 *
 * Everything here is a pure function of the recorded events: no request is
 * made, nothing is derived from React state, and every number below is either
 * a field the backend stamped or a difference of two recorded timestamps
 * (`StreamEvent.timestamp`, epoch seconds — core/stream.py:59).
 */

import { getTraceMeta } from "@/components/chat/home/TracePanels";
import type { StreamEvent } from "@/lib/unified-ws";

/** The metadata fields this tab reads. All optional: events are weakly typed
 *  on the wire and older turns predate every field added for this feature. */
export interface TrajectoryMeta {
  call_id?: string;
  call_kind?: string;
  call_state?: string;
  call_role?: string;
  trace_kind?: string;
  trace_group?: string;
  label?: string;
  tool_name?: string;
  turn_index?: number;
  step_index?: number;
  finish_reason?: string;
  step_outcome?: { kind?: string };
  tool_calls_requested?: string[];
  tools_offered?: number;
  step_tokens?: { prompt?: number; completion?: number; total?: number };
  header_ref?: { seq?: number; hash?: string; mode?: string };
  // channel_health (P3)
  assembly_state?: string;
  status_key?: string;
  tools_available?: number;
  degraded_stages?: string[];
  degradations?: TrajectoryDegradation[];
  mcp_servers?: TrajectoryMcpServer[];
  owner_scope_timeout_s?: number;
  // result payload (P1b)
  stop?: TrajectoryStop;
  tool_steps?: number;
  rounds?: number;
  metadata?: { cost_summary?: { total_tokens?: number } };
}

export interface TrajectoryDegradation {
  stage?: string;
  reason?: string;
  detail?: string;
  elapsed_ms?: number;
}

export interface TrajectoryMcpServer {
  name?: string;
  transport?: string;
  status?: string;
  error?: string;
}

export interface TrajectoryStop {
  kind?: string;
  code?: string;
  status?: number;
  retries?: number;
}

export interface TrajectoryAssembly {
  events: StreamEvent[];
  state: string | null;
  statusKey: string | null;
  toolsAvailable: number | null;
  degradedStages: string[];
  degradations: TrajectoryDegradation[];
  mcpServers: TrajectoryMcpServer[];
  ownerScopeTimeoutSeconds: number | null;
}

export interface TrajectoryStep {
  key: string;
  callId: string;
  label: string;
  /** Backend double numbering (P1). `null` on turns recorded before batch 1. */
  turnIndex: number | null;
  stepIndex: number | null;
  /** Own call group plus every following non-model group up to the next model
   *  call — i.e. the tool executions this round asked for. */
  events: StreamEvent[];
  finishReason: string | null;
  outcomeKind: string | null;
  toolsRequested: string[];
  toolsOffered: number | null;
  /** Absent when the provider streamed no usage frame (R17). Rendered as an
   *  em dash, never as zero (批 5a 判据 7). */
  stepTokens: { prompt?: number; completion?: number; total?: number } | null;
  evidenceMode: string | null;
  startedAt: number | null;
  endedAt: number | null;
  durationSeconds: number | null;
  /** End-to-end delay to the first streamed character: this is the gap between
   *  the round's `running` marker and its first chunk event as observed on the
   *  browser's copy of the stream, so it carries server and event-bus overhead
   *  (R15). It is not the provider-side time to first token and must never be
   *  labelled as one. */
  firstChunkSeconds: number | null;
  outputChars: number;
  charsPerSecond: number | null;
  hasError: boolean;
  anomalous: boolean;
}

export interface TrajectoryTurn {
  key: string;
  turnId: string;
  assembly: TrajectoryAssembly | null;
  steps: TrajectoryStep[];
  /** Events carrying no `call_id` — `result` (which never gets one, A34),
   *  transport errors, stage markers. They belong to the turn, not a step. */
  turnEvents: StreamEvent[];
  stop: TrajectoryStop | null;
  totalTokens: number | null;
  toolCalls: number | null;
  startedAt: number | null;
  endedAt: number | null;
  durationSeconds: number | null;
  hasError: boolean;
  anomalous: boolean;
  anomalyReasons: TrajectoryAnomalyReason[];
}

export type TrajectoryAnomalyReason =
  | "stop"
  | "assembly"
  | "outcome"
  | "error";

/** Step outcomes the loop reports when it had to end a round against its own
 *  intent (`agent_loop.py:379-419` / `:276-312`). */
const FORCED_OUTCOMES = new Set([
  "forced_budget",
  "forced_error",
  "nudge_empty",
]);

export interface TrajectorySource {
  events?: StreamEvent[];
}

function readMeta(event: StreamEvent): TrajectoryMeta {
  return getTraceMeta(event) as unknown as TrajectoryMeta;
}

function timestampOf(event: StreamEvent): number | null {
  const ts = event.timestamp;
  return typeof ts === "number" && Number.isFinite(ts) ? ts : null;
}

function span(events: StreamEvent[]): {
  startedAt: number | null;
  endedAt: number | null;
} {
  let min: number | null = null;
  let max: number | null = null;
  for (const event of events) {
    const ts = timestampOf(event);
    if (ts === null) continue;
    if (min === null || ts < min) min = ts;
    if (max === null || ts > max) max = ts;
  }
  return { startedAt: min, endedAt: max };
}

function lastTimestampOfType(
  events: StreamEvent[],
  type: StreamEvent["type"],
): number | null {
  let found: number | null = null;
  for (const event of events) {
    if (event.type !== type) continue;
    const ts = timestampOf(event);
    if (ts !== null) found = ts;
  }
  return found;
}

function elapsed(from: number | null, to: number | null): number | null {
  if (from === null || to === null) return null;
  return Math.max(0, to - from);
}

function isModelCallMeta(meta: TrajectoryMeta): boolean {
  return (
    meta.call_kind === "agent_loop_round" || typeof meta.step_index === "number"
  );
}

interface CallGroup {
  callId: string;
  events: StreamEvent[];
  kind: "model" | "assembly" | "other";
}

/**
 * Split one turn's events into ordered `call_id` groups plus the events that
 * carry no `call_id` at all. The conversation panel throws the latter away
 * (`TracePanels.tsx:1517-1518`); the trajectory keeps them, which is the only
 * reason the structured `stop` on the `result` event is readable anywhere.
 */
function splitIntoGroups(events: StreamEvent[]): {
  groups: CallGroup[];
  looseEvents: StreamEvent[];
} {
  const groups: CallGroup[] = [];
  const byId = new Map<string, number>();
  const looseEvents: StreamEvent[] = [];

  for (const event of events) {
    const meta = readMeta(event);
    const callId = String(meta.call_id || "");
    if (!callId) {
      looseEvents.push(event);
      continue;
    }
    const existing = byId.get(callId);
    if (existing === undefined) {
      byId.set(callId, groups.length);
      groups.push({
        callId,
        events: [event],
        kind:
          meta.trace_kind === "channel_health" ||
          meta.call_kind === "channel_health"
            ? "assembly"
            : isModelCallMeta(meta)
              ? "model"
              : "other",
      });
      continue;
    }
    const group = groups[existing];
    group.events.push(event);
    // A group's first event may be a bare chunk that predates its status
    // marker; promote it as soon as any member identifies it as a model call.
    if (group.kind === "other" && isModelCallMeta(meta)) group.kind = "model";
  }

  return { groups, looseEvents };
}

function buildAssembly(group: CallGroup): TrajectoryAssembly {
  let meta: TrajectoryMeta = {};
  for (const event of group.events) {
    const candidate = readMeta(event);
    if (candidate.assembly_state) meta = candidate;
  }
  return {
    events: group.events,
    state: meta.assembly_state ? String(meta.assembly_state) : null,
    statusKey: meta.status_key ? String(meta.status_key) : null,
    toolsAvailable:
      typeof meta.tools_available === "number" ? meta.tools_available : null,
    degradedStages: Array.isArray(meta.degraded_stages)
      ? meta.degraded_stages.map(String)
      : [],
    degradations: Array.isArray(meta.degradations) ? meta.degradations : [],
    mcpServers: Array.isArray(meta.mcp_servers) ? meta.mcp_servers : [],
    ownerScopeTimeoutSeconds:
      typeof meta.owner_scope_timeout_s === "number"
        ? meta.owner_scope_timeout_s
        : null,
  };
}

function buildStep(group: CallGroup): TrajectoryStep {
  let running: StreamEvent | null = null;
  let terminal: StreamEvent | null = null;
  let terminalMeta: TrajectoryMeta = {};
  let label = "";
  let turnIndex: number | null = null;
  let stepIndex: number | null = null;
  let firstChunkAt: number | null = null;
  let outputChars = 0;

  for (const event of group.events) {
    const meta = readMeta(event);
    if (!label && meta.label) label = String(meta.label);
    if (typeof meta.turn_index === "number") turnIndex = meta.turn_index;
    if (typeof meta.step_index === "number") stepIndex = meta.step_index;
    if (meta.trace_kind === "call_status") {
      if (meta.call_state === "running" && !running) running = event;
      if (meta.call_state === "complete" || meta.call_state === "error") {
        terminal = event;
        terminalMeta = meta;
      }
    }
    if (meta.trace_kind === "llm_chunk" || event.type === "content") {
      const text = typeof event.content === "string" ? event.content : "";
      if (text.length > 0) {
        outputChars += text.length;
        if (firstChunkAt === null) firstChunkAt = timestampOf(event);
      }
    }
  }

  const bounds = span(group.events);
  const startedAt = running ? timestampOf(running) : bounds.startedAt;
  const endedAt = terminal ? timestampOf(terminal) : bounds.endedAt;
  const durationSeconds = elapsed(startedAt, endedAt);
  const toolsRequested = Array.isArray(terminalMeta.tool_calls_requested)
    ? terminalMeta.tool_calls_requested.map(String)
    : [];
  const outcomeKind = terminalMeta.step_outcome?.kind
    ? String(terminalMeta.step_outcome.kind)
    : null;
  const hasError = group.events.some((event) => event.type === "error");

  return {
    key: group.callId,
    callId: group.callId,
    label,
    turnIndex,
    stepIndex,
    events: group.events,
    finishReason: terminalMeta.finish_reason
      ? String(terminalMeta.finish_reason)
      : null,
    outcomeKind,
    toolsRequested,
    toolsOffered:
      typeof terminalMeta.tools_offered === "number"
        ? terminalMeta.tools_offered
        : null,
    stepTokens: terminalMeta.step_tokens ?? null,
    evidenceMode: terminalMeta.header_ref?.mode
      ? String(terminalMeta.header_ref.mode)
      : null,
    startedAt,
    endedAt,
    durationSeconds,
    firstChunkSeconds: elapsed(startedAt, firstChunkAt),
    outputChars,
    charsPerSecond:
      durationSeconds && durationSeconds > 0
        ? outputChars / durationSeconds
        : null,
    hasError,
    anomalous:
      hasError || (outcomeKind !== null && FORCED_OUTCOMES.has(outcomeKind)),
  };
}

/**
 * Group a session's events into turns.
 *
 * Grouping is on the event's own `turn_id` (core/stream.py:57), never on the
 * conversation's merged bubble: deep_research splices a follow-up message's
 * events onto its parent so the two render as one bubble
 * (`ChatMessages.tsx:1268-1290`), and collapsing two real turns into one row
 * would defeat the whole point of the view (A35).
 *
 * Events that carry no `turn_id` — the transport-level rejection an already
 * busy session answers with, for instance — attach to the last turn seen in
 * the same message, or open a message-scoped turn of their own when there is
 * none. They are never silently dropped.
 */
export function buildTrajectory(messages: TrajectorySource[]): TrajectoryTurn[] {
  const order: string[] = [];
  const buckets = new Map<string, StreamEvent[]>();

  messages.forEach((message, messageIndex) => {
    const events = message.events ?? [];
    let lastKey: string | null = null;
    for (const event of events) {
      const turnId = typeof event.turn_id === "string" ? event.turn_id : "";
      const key: string = turnId || lastKey || `message:${messageIndex}`;
      if (!buckets.has(key)) {
        buckets.set(key, []);
        order.push(key);
      }
      buckets.get(key)!.push(event);
      lastKey = key;
    }
  });

  return order.map((key) => buildTurn(key, buckets.get(key) ?? []));
}

function buildTurn(key: string, events: StreamEvent[]): TrajectoryTurn {
  const { groups, looseEvents } = splitIntoGroups(events);

  let assembly: TrajectoryAssembly | null = null;
  const steps: TrajectoryStep[] = [];
  const strayEvents: StreamEvent[] = [];

  for (const group of groups) {
    if (group.kind === "assembly") {
      if (!assembly) assembly = buildAssembly(group);
      else strayEvents.push(...group.events);
      continue;
    }
    if (group.kind === "model") {
      steps.push(buildStep(group));
      continue;
    }
    // Tool executions and any other call-scoped work belong to the round that
    // asked for them: they are emitted after that round's `complete` marker
    // and before the next round opens.
    const current = steps[steps.length - 1];
    if (current) current.events.push(...group.events);
    else strayEvents.push(...group.events);
  }

  const turnEvents = [...looseEvents, ...strayEvents];
  const resultMeta = looseEvents
    .filter((event) => event.type === "result")
    .map(readMeta)
    .pop();

  const stop = resultMeta?.stop ?? null;
  const totalTokens =
    typeof resultMeta?.metadata?.cost_summary?.total_tokens === "number"
      ? resultMeta.metadata.cost_summary.total_tokens
      : null;
  const toolCalls =
    typeof resultMeta?.tool_steps === "number"
      ? resultMeta.tool_steps
      : events.filter((event) => event.type === "tool_call").length;

  const bounds = span(events);
  // The turn's clock stops when the turn reports its result. Some events are
  // stamped with the turn's id after that — the generated session title arrives
  // on `session_meta` ten seconds later in the recorded fixture — and letting
  // the raw range absorb them would report a turn as half again as long as it
  // took. Turns that never reported (a tool left waiting for a human, say) keep
  // the raw upper bound.
  const closedAt =
    lastTimestampOfType(events, "result") ??
    lastTimestampOfType(events, "done") ??
    bounds.endedAt;
  const hasError =
    events.some((event) => event.type === "error") ||
    steps.some((step) => step.hasError);

  const anomalyReasons: TrajectoryAnomalyReason[] = [];
  if (stop && stop.kind && stop.kind !== "completed") anomalyReasons.push("stop");
  if (assembly?.state === "degraded") anomalyReasons.push("assembly");
  if (steps.some((step) => FORCED_OUTCOMES.has(String(step.outcomeKind))))
    anomalyReasons.push("outcome");
  if (hasError) anomalyReasons.push("error");

  const turnId = events.find((event) => event.turn_id)?.turn_id ?? "";

  return {
    key,
    turnId: String(turnId),
    assembly,
    steps,
    turnEvents,
    stop,
    totalTokens,
    toolCalls,
    startedAt: bounds.startedAt,
    endedAt: closedAt,
    durationSeconds: elapsed(bounds.startedAt, closedAt),
    hasError,
    anomalous: anomalyReasons.length > 0,
    anomalyReasons,
  };
}

/**
 * The "anomalies only" filter.
 *
 * Two candidates are deliberately absent, and both absences are load-bearing
 * (design §四之二·六):
 *
 *  - **"had tools in hand and called none"** is the ordinary shape of a normal
 *    question answered directly. Treating it as an anomaly selects nearly every
 *    turn and the filter stops discriminating. Whether a given question needed
 *    a tool is a judgement only the reader can make, so the step row states the
 *    fact ("no tool call, N tools in hand") and passes no verdict.
 *  - **`header_ref.mode === "unavailable"`** is what every turn looks like once
 *    a deployment sets the retention window to zero — same failure mode.
 *
 * `web/tests/trajectory-model.test.ts` pins both exclusions against a fixture
 * that contains two zero-tool normal turns.
 */
export function selectAnomalousTurns(
  turns: TrajectoryTurn[],
): TrajectoryTurn[] {
  return turns.filter((turn) => turn.anomalous);
}

/** True when the step states a fact worth a standing label: the model was
 *  offered tools and asked for none. A fact, not a verdict — see above. */
export function hasIdleTools(step: TrajectoryStep): boolean {
  return (
    step.toolsRequested.length === 0 &&
    typeof step.toolsOffered === "number" &&
    step.toolsOffered > 0
  );
}
