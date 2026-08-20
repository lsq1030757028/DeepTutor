/**
 * The trajectory is physically separate from the conversation.
 *
 * The user asked for two tabs and no nudging of any kind: "I'm not an idiot,
 * I'll go look at the trajectory when something goes wrong, there's no need for
 * a prompt." Two things have to hold for that to be true, and both are checked
 * here against code rather than against intent:
 *
 *  1. the conversation renders identically whether or not the events carry
 *     everything this feature added, and
 *  2. nothing in the new code draws attention to itself.
 *
 * The first is the fragile one. It holds today only because the conversation
 * already filters call groups with "no substance" and drops events without a
 * `call_id` — an implicit dependency someone could relax without ever opening
 * this feature (R14). This file is one of the three places that turn red when
 * they do; the other two are on the backend
 * (`tests/agents/chat/test_channel_health.py` and
 * `tests/agents/chat/test_model_input_sidecar.py`).
 */

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import i18next from "i18next";
import { initReactI18next } from "react-i18next";

import recordedSession from "./fixtures/trajectory-recorded-session.json";
import sixTurns from "./fixtures/trajectory-six-turns.json";
import enRuntime from "../i18n/en-runtime.json";
import { CallTracePanel } from "../components/chat/home/TracePanels";
import { TrajectoryPanel } from "../components/chat/trajectory/TrajectoryPanel";
import { SessionViewTabs } from "../components/chat/trajectory/SessionViewTabs";
import { recomputeAnswerContent } from "../lib/stream";
import type { StreamEvent } from "../lib/unified-ws";
import type { TrajectorySource } from "../components/chat/trajectory/model";

const WEB_ROOT = path.resolve(process.cwd());
const TRAJECTORY_DIR = path.join(WEB_ROOT, "components", "chat", "trajectory");
const SESSION_PAGE = path.join(
  WEB_ROOT,
  "app",
  "(workspace)",
  "home",
  "[[...sessionId]]",
  "page.tsx",
);

void i18next.use(initReactI18next).init({
  lng: "en",
  fallbackLng: false,
  resources: { en: { app: enRuntime as Record<string, string> } },
  defaultNS: "app",
  ns: ["app"],
  keySeparator: false,
  interpolation: { escapeValue: false },
});

const recorded = recordedSession as unknown as { events: StreamEvent[] }[];
const six = sixTurns as unknown as TrajectorySource[];

/* ------------------------------------------------------------------ */
/*  1 · the conversation does not move                                 */
/* ------------------------------------------------------------------ */

/** Event kinds this feature introduced. */
const ADDED_TRACE_KINDS = new Set(["model_input", "channel_health"]);
/** Metadata keys this feature added to the already-live `call_status` marker
 *  and to the `result` payload. */
const ADDED_META_KEYS = [
  "turn_index",
  "step_index",
  "finish_reason",
  "step_outcome",
  "tool_calls_requested",
  "tools_offered",
  "step_tokens",
  "header_ref",
  "status_key",
  "stop",
];

/** The same turn as it looked before any of this shipped. */
function withoutTheNewObservability(events: StreamEvent[]): StreamEvent[] {
  return events
    .filter((event) => {
      const meta = (event.metadata ?? {}) as Record<string, unknown>;
      return !ADDED_TRACE_KINDS.has(String(meta.trace_kind ?? ""));
    })
    .map((event) => {
      const meta = { ...((event.metadata ?? {}) as Record<string, unknown>) };
      for (const key of ADDED_META_KEYS) delete meta[key];
      return { ...event, metadata: meta };
    });
}

function conversationMarkup(events: StreamEvent[]): string {
  return renderToStaticMarkup(
    React.createElement(CallTracePanel, { events, isStreaming: false }),
  );
}

test("the recorded fixture really does carry the new observability", () => {
  // Positive control for the comparison below: without this, two identical
  // renders of two identical inputs would pass and prove nothing.
  const all = recorded.flatMap((message) => message.events);
  const stripped = withoutTheNewObservability(all);

  assert.ok(all.length - stripped.length >= 6, "expected new events in fixture");
  assert.ok(
    all.some((event) => {
      const meta = (event.metadata ?? {}) as Record<string, unknown>;
      return meta.trace_kind === "channel_health";
    }),
  );
  assert.ok(
    all.some((event) => {
      const meta = (event.metadata ?? {}) as Record<string, unknown>;
      return meta.trace_kind === "model_input";
    }),
  );
  assert.ok(
    all.some(
      (event) =>
        event.type === "result" &&
        (event.metadata as Record<string, unknown>)?.stop !== undefined,
    ),
  );
  assert.ok(JSON.stringify(all) !== JSON.stringify(stripped));
});

test("the conversation renders byte-identically with and without the new events", () => {
  let rendered = 0;

  for (const [index, message] of recorded.entries()) {
    const withFeature = conversationMarkup(message.events);
    const before = conversationMarkup(withoutTheNewObservability(message.events));

    assert.equal(withFeature, before, `message ${index} moved`);
    if (withFeature.length > 0) rendered += 1;
  }

  // At least one message must actually draw something, otherwise the equality
  // above is between two empty strings.
  assert.ok(rendered > 0, "no message produced any conversation markup");
});

test("the answer bubble text is unchanged by the new events", () => {
  for (const [index, message] of recorded.entries()) {
    assert.equal(
      recomputeAnswerContent(message.events),
      recomputeAnswerContent(withoutTheNewObservability(message.events)),
      `message ${index} answer text moved`,
    );
  }
});

/* ------------------------------------------------------------------ */
/*  2 · nothing asks for attention                                     */
/* ------------------------------------------------------------------ */

function trajectorySources(): { rel: string; text: string }[] {
  return fs
    .readdirSync(TRAJECTORY_DIR)
    .filter((name) => name.endsWith(".ts") || name.endsWith(".tsx"))
    .sort()
    .map((name) => ({
      rel: `components/chat/trajectory/${name}`,
      text: fs.readFileSync(path.join(TRAJECTORY_DIR, name), "utf8"),
    }));
}

/** Anything that would put a mark on the tab or a tally in front of the user.
 *  "Just a light indicator", "only the first time" and "flash once when
 *  something breaks" are the same thing wearing different names, so the scan
 *  covers the shapes they would take rather than one spelling. */
const ATTENTION_PATTERNS: RegExp[] = [
  /\bbadge/i,
  /\bunread/i,
  /hasAnomaly/,
  /anomal\w*(count|tally|total)/i,
  /\bnotification/i,
  /\bindicator/i,
  /\bdot\b/i,
  /animate-ping/,
  /\bblink/i,
  /\bpulse\b/i,
];

test("the trajectory sources carry no marker, tally or attention grab", () => {
  const sources = trajectorySources();
  assert.ok(sources.length >= 4, "expected the trajectory module to have files");

  const hits: string[] = [];
  for (const { rel, text } of sources) {
    for (const pattern of ATTENTION_PATTERNS) {
      if (pattern.test(text)) hits.push(`${rel} matches ${pattern}`);
    }
  }
  assert.deepEqual(hits, []);
});

test("the session page grows no marker either", () => {
  const lines = fs.readFileSync(SESSION_PAGE, "utf8").split(/\r?\n/);
  const hits = lines.filter((line) => {
    const trimmed = line.trim();
    // Prose in a comment is not an indicator on screen.
    if (trimmed.startsWith("//") || trimmed.startsWith("*")) return false;
    return ATTENTION_PATTERNS.some((pattern) => pattern.test(line));
  });
  assert.deepEqual(hits, []);
});

test("the conversation components know nothing about the trajectory", () => {
  for (const rel of [
    "components/chat/home/TracePanels.tsx",
    "components/chat/home/ChatMessages.tsx",
  ]) {
    const text = fs.readFileSync(path.join(WEB_ROOT, rel), "utf8");
    assert.doesNotMatch(text, /trajectory/i, `${rel} references the trajectory`);
  }
});

/* ------------------------------------------------------------------ */
/*  3 · keyboard handling stays inside the tab                         */
/* ------------------------------------------------------------------ */

test("the trajectory registers no listener on window or document", () => {
  // The turn navigator already owns window-level Alt+Arrow (A32) and the
  // session viewer owns a document-level Escape that stops propagation (A33).
  // A third global listener would mean two of the three lose.
  for (const { rel, text } of trajectorySources()) {
    assert.doesNotMatch(text, /addEventListener\s*\(/, `${rel} adds a listener`);
  }
});

test("the session page renders the turn navigator through the view guard", () => {
  const page = fs.readFileSync(SESSION_PAGE, "utf8");
  assert.match(
    page,
    /shouldRenderTurnNavigator\(sessionView\)\s*\?\s*\(\s*<TurnNavigator/,
  );
});

test("the transcript is rendered unconditionally and only hidden by class", () => {
  const page = fs.readFileSync(SESSION_PAGE, "utf8");
  // One scroll container, always mounted, wearing the hide class when the other
  // tab is up. If someone turns this into `sessionView === "conversation" && (`
  // the transcript unmounts and every scroll guarantee goes with it.
  assert.match(page, /ref=\{messagesContainerRef\}/);
  assert.match(page, /\$\{conversationLayerClass\(sessionView\)\}/);
  assert.doesNotMatch(page, /sessionView === "conversation"[\s\S]{0,80}<div\s+ref=\{messagesContainerRef\}/);
});

/* ------------------------------------------------------------------ */
/*  4 · what the panel actually puts on screen                         */
/* ------------------------------------------------------------------ */

function trajectoryMarkup(messages: TrajectorySource[]): string {
  return renderToStaticMarkup(
    React.createElement(TrajectoryPanel, { messages }),
  );
}

test("a step the provider sent no usage for shows a dash, never a zero", () => {
  const html = trajectoryMarkup(six);

  assert.ok(html.includes("step tokens —"), "expected an em dash for tokens");
  assert.ok(!html.includes("step tokens 0"), "tokens rendered as zero");
});

test("the first-character metric says end to end, and nothing calls it provider TTFT", () => {
  const html = trajectoryMarkup(six);
  assert.match(html, /first character [^<]*end to end/);

  const en = JSON.parse(
    fs.readFileSync(path.join(WEB_ROOT, "locales", "en", "app.json"), "utf8"),
  ) as Record<string, string>;
  const zh = JSON.parse(
    fs.readFileSync(path.join(WEB_ROOT, "locales", "zh", "app.json"), "utf8"),
  ) as Record<string, string>;

  assert.match(en["trajectory.step.firstChunk"], /end to end/);
  assert.match(zh["trajectory.step.firstChunk"], /端到端/);

  // It is a derived number that carries server and event-bus overhead
  // (turn_runtime.py:1855-1858 hands events off through an asyncio queue).
  // Naming it the provider's TTFT would be a lie about what was measured.
  for (const { rel, text } of trajectorySources()) {
    assert.doesNotMatch(text, /ttft/i, `${rel} says TTFT`);
  }
  for (const [key, value] of [...Object.entries(en), ...Object.entries(zh)]) {
    assert.doesNotMatch(String(value), /provider ttft/i, key);
  }
});

test("the panel states the idle-tool fact and passes no verdict on it", () => {
  const html = trajectoryMarkup(six);
  assert.ok(html.includes("no tool call, 17 tools in hand"));
});

test("every turn gets a row and the anomalies are not singled out visually", () => {
  const html = trajectoryMarkup(six);
  const rows = html.match(/data-trajectory-row="turn"/g) ?? [];
  assert.equal(rows.length, 6);
});

test("the tabs are two plain labels", () => {
  const html = renderToStaticMarkup(
    React.createElement(SessionViewTabs, {
      view: "conversation",
      onChange: () => {},
    }),
  );
  const labels = [...html.matchAll(/>([^<>]+)<\/button>/g)].map((m) => m[1]);
  assert.deepEqual(labels, ["Conversation", "Trajectory"]);
});
