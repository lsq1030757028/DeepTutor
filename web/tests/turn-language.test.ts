// [fork] 本轮语种取数（BB-698）：卡片外壳按事件流里的 `turn_language` 取文案。
//
// 两条判据：① 测试轮的事件里带 turn_language 时取到它（以最新一条为准）；
// ② 普通聊天（没有这条事件）得到 null——调用方照旧跟界面语言，零变化。

import test from "node:test";
import assert from "node:assert/strict";

import { turnLanguageOf } from "../lib/turn-language";
import type { StreamEvent } from "../lib/unified-ws";

function event(
  type: StreamEvent["type"],
  metadata: Record<string, unknown>,
  content = "",
): StreamEvent {
  return {
    type,
    source: "chat",
    stage: "exploring",
    content,
    metadata,
    session_id: "session-1",
    turn_id: "turn-1",
    seq: 1,
    timestamp: 0,
  };
}

test("turnLanguageOf reads the server-announced turn language", () => {
  const events = [
    event("progress", { trace_kind: "turn_language", turn_language: "zh" }),
    event("content", {}, "帮我拉一下TAPD的需求"),
  ];
  assert.equal(turnLanguageOf(events), "zh");
});

test("turnLanguageOf takes the latest announcement", () => {
  const events = [
    event("progress", { trace_kind: "turn_language", turn_language: "en" }),
    event("progress", { trace_kind: "turn_language", turn_language: "zh" }),
  ];
  assert.equal(turnLanguageOf(events), "zh");
});

test("turnLanguageOf is null for ordinary chat turns and junk values", () => {
  assert.equal(turnLanguageOf(undefined), null);
  assert.equal(turnLanguageOf([]), null);
  assert.equal(
    turnLanguageOf([event("content", {}, "hello"), event("progress", { turn_language: "fr" })]),
    null,
  );
});
