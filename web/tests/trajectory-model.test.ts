/**
 * What the trajectory view says about a session, checked against events the
 * backend really emitted.
 *
 * `trajectory-recorded-session.json` is a verbatim capture of a four-turn
 * session run against the batch 1–3 image (`deeptutor:p3-uat-cd408bf4`,
 * replayed 2026-08-20): a degraded assembly on the first turn and a healthy one
 * afterwards, `model_input` and `call_status` on every round, one `ask_user`
 * tool call, and a transport-level rejection with no `turn_id` at all. Two
 * caveats about that file, so nobody mistakes them for behaviour:
 * the assistant's Chinese answer text was mangled by the recorder's own
 * encoding before it reached the file, and the `cost_summary` block is absent
 * because the session ran through the subscription bridge, which reports no
 * cost — that absence is exactly the "render an em dash, never a zero" case.
 *
 * The two synthetic fixtures cover shapes that recording could not supply on
 * demand: six turns spanning both anomaly rules plus two ordinary zero-tool
 * turns, and a deep_research pair whose two real turns the conversation merges
 * into a single bubble.
 */

import test from "node:test";
import assert from "node:assert/strict";

import recordedSession from "./fixtures/trajectory-recorded-session.json";
import sixTurns from "./fixtures/trajectory-six-turns.json";
import deepResearchMerged from "./fixtures/trajectory-deep-research-merged.json";
import {
  buildTrajectory,
  hasIdleTools,
  selectAnomalousTurns,
  type TrajectorySource,
} from "../components/chat/trajectory/model";
import {
  conversationLayerClass,
  shouldRenderTurnNavigator,
} from "../components/chat/trajectory/view-state";

const recorded = recordedSession as unknown as TrajectorySource[];
const six = sixTurns as unknown as TrajectorySource[];
const merged = deepResearchMerged as unknown as TrajectorySource[];

test("a recorded session resolves to one row per real turn, rejection included", () => {
  const turns = buildTrajectory(recorded);

  assert.equal(turns.length, 4);
  assert.deepEqual(
    turns.map((turn) => turn.steps.length),
    [1, 1, 1, 0],
  );
  // The fourth turn is the transport rejecting a message while another turn was
  // still running: one `error` event, no `turn_id`, no call of any kind. The
  // conversation panel drops it for want of a `call_id`
  // (TracePanels.tsx:1517-1518); the trajectory keeps it as its own turn.
  assert.equal(turns[3].turnId, "");
  assert.equal(turns[3].hasError, true);
  assert.deepEqual(turns[3].anomalyReasons, ["error"]);
});

test("the first turn reports the degraded assembly that cost it three MCP tools", () => {
  const [first, second] = buildTrajectory(recorded);

  assert.equal(first.assembly?.state, "degraded");
  assert.deepEqual(first.assembly?.degradedStages, ["owner_scope"]);
  assert.equal(first.assembly?.degradations[0]?.stage, "owner_scope");
  assert.equal(first.assembly?.degradations[0]?.reason, "timeout");
  assert.equal(first.assembly?.toolsAvailable, 46);

  assert.equal(second.assembly?.state, "ok");
  assert.equal(second.assembly?.toolsAvailable, 49);
});

test("a recorded round carries its numbering, its stop reason and what it was offered", () => {
  const [first] = buildTrajectory(recorded);
  const [step] = first.steps;

  assert.equal(step.turnIndex, 0);
  assert.equal(step.stepIndex, 0);
  assert.equal(step.finishReason, "stop");
  assert.equal(step.outcomeKind, "finish");
  assert.deepEqual(step.toolsRequested, []);
  assert.equal(step.toolsOffered, 17);
  assert.equal(step.evidenceMode, "recorded");
  assert.equal(hasIdleTools(step), true);
});

test("timings come from the recorded timestamps, not from a live clock", () => {
  const [first] = buildTrajectory(recorded);
  const [step] = first.steps;

  // running 1787230543.1204066 → complete 1787230566.9017959
  assert.ok(step.durationSeconds !== null);
  assert.equal(Number(step.durationSeconds!.toFixed(3)), 23.781);
  // First streamed character lands well inside the round.
  assert.ok(step.firstChunkSeconds !== null);
  assert.ok(step.firstChunkSeconds! > 0);
  assert.ok(step.firstChunkSeconds! < step.durationSeconds!);
  assert.ok(step.outputChars > 0);
  assert.ok(step.charsPerSecond !== null && step.charsPerSecond > 0);

  // The turn's own clock stops at its `result`. The recorded turn also carries
  // a `session_meta` stamped ten seconds later — the generated session title —
  // and letting the raw range absorb it would report 37.67s for a turn the run
  // log timed at 26.9s.
  assert.equal(Number(first.durationSeconds!.toFixed(2)), 26.84);
  assert.ok(first.durationSeconds! >= step.durationSeconds!);
});

test("the recorded tool call hangs off the round that asked for it", () => {
  const turns = buildTrajectory(recorded);
  const third = turns[2];
  const [step] = third.steps;

  assert.equal(step.outcomeKind, "narration");
  assert.deepEqual(step.toolsRequested, ["ask_user"]);
  assert.equal(third.toolCalls, 1);
  assert.ok(step.events.some((event) => event.type === "tool_call"));
  assert.ok(step.events.some((event) => event.type === "tool_result"));
});

test("a turn whose backend reported no cost keeps the field empty rather than zero", () => {
  // Subscription runs report no `cost_summary` at all. `null` is what makes the
  // view render an em dash; a `0` here would be a number nobody measured.
  for (const turn of buildTrajectory(recorded)) {
    assert.equal(turn.totalTokens, null);
  }
});

test("a round the provider sent no usage frame for reports no step tokens", () => {
  const [first] = buildTrajectory(recorded);
  assert.equal(first.steps[0].stepTokens, null);

  const withUsage = buildTrajectory(six)[0].steps[0];
  assert.deepEqual(withUsage.stepTokens, {
    prompt: 900,
    completion: 40,
    total: 940,
  });
});

test("anomalies only keeps the two broken turns and neither zero-tool answer", () => {
  const turns = buildTrajectory(six);
  assert.equal(turns.length, 6);

  const anomalies = selectAnomalousTurns(turns);

  assert.equal(anomalies.length, 2);
  assert.deepEqual(
    anomalies.map((turn) => turn.key),
    ["turn_stop_error", "turn_assembly_degraded"],
  );
  assert.deepEqual(anomalies[0].anomalyReasons, ["stop"]);
  assert.deepEqual(anomalies[1].anomalyReasons, ["assembly"]);
});

test("having tools in hand and calling none is stated as a fact, never as an anomaly", () => {
  // All six turns have a round that was offered tools and asked for none — the
  // two straight answers, and the closing round of the turns that did use a
  // tool earlier. That is what an ordinary conversation looks like, so
  // promoting the shape to an anomaly selects every single turn and leaves the
  // filter with nothing to discriminate. If anyone adds it back to the rule,
  // the count above turns red and this one says why.
  const turns = buildTrajectory(six);
  const idle = turns.filter((turn) => turn.steps.some(hasIdleTools));

  assert.equal(idle.length, turns.length);
  for (const key of ["turn_plain_a", "turn_plain_b"]) {
    const turn = turns.find((candidate) => candidate.key === key);
    assert.equal(turn?.anomalous, false, key);
  }
  // The degraded turn is selected, but for its assembly — not for idle tools.
  const degraded = turns.find((turn) => turn.key === "turn_assembly_degraded");
  assert.deepEqual(degraded?.anomalyReasons, ["assembly"]);
});

test("evidence the trace file is gone does not by itself make a turn anomalous", () => {
  // With the retention window set to zero every turn reports
  // `header_ref.mode === "unavailable"`, so treating it as an anomaly fails the
  // same way: the filter selects everything.
  const turns = buildTrajectory(six);
  const unavailable = turns.find((turn) => turn.key === "turn_plain_b");

  assert.equal(unavailable?.steps[0].evidenceMode, "unavailable");
  assert.equal(unavailable?.anomalous, false);
});

test("a deep_research pair splits into the two turns it really was", () => {
  // The conversation splices the follow-up's events onto the parent so the two
  // render as one bubble (ChatMessages.tsx:1268-1290). Telling real rounds
  // apart is the whole job here, so the split follows `turn_id`.
  assert.equal(merged.length, 1);

  const turns = buildTrajectory(merged);

  assert.equal(turns.length, 2);
  assert.deepEqual(
    turns.map((turn) => turn.turnId),
    ["turn_dr_parent", "turn_dr_followup"],
  );
});

test("switching to the trajectory hides the transcript without unmounting it", () => {
  // `display: none` reports `scrollHeight === 0`, and the autoscroll pin writes
  // that straight into `scrollTop` (hooks/useChatAutoScroll.ts:69) — a turn
  // streaming behind the trajectory would rewind the transcript to the top.
  const hidden = conversationLayerClass("trajectory");

  assert.match(hidden, /\binvisible\b/);
  assert.doesNotMatch(hidden, /\bhidden\b/);
  assert.match(hidden, /\babsolute inset-0\b/);
  assert.equal(conversationLayerClass("conversation"), "");
});

test("the turn navigator stops rendering while the trajectory is open", () => {
  // It grabs Alt+ArrowUp/Down on `window` and only steps aside for input
  // controls (TurnNavigator.tsx:201-222), so an invisible one would still eat
  // the keys the trajectory rows need.
  assert.equal(shouldRenderTurnNavigator("conversation"), true);
  assert.equal(shouldRenderTurnNavigator("trajectory"), false);
});
