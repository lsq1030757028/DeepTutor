// [fork] 人闸卡外壳 + 进度徽标的 i18n 机械守（BB-698 判据 4 补修，2026-09-05）。
//
// UAT 实测（会话 unified_1788603204448_e9762dd3）：AskUserOptions 已按本轮语种取
// 文案，但 "Submit" / "Select all that apply." 两个键在 zh 包里根本不存在，取到的
// 是英文原键；TracePanels 的 "Tool Calling…/DeepTutor Exploring…/Asking you/
// Using test-partner/Done" 有 zh 文案却仍按界面语言取。两条断言各守一半：
//   ① 这三个文件里 t("X") 引用的每个 X，zh 与 en 都得有真值；
//   ② 渲染这些外壳的组件必须经 useTurnT 取 t，而不是界面语言的 useTranslation。

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const WEB = process.cwd();
const FILES = [
  "components/chat/home/AskUserOptions.tsx",
  "components/chat/home/TracePanels.tsx",
  "lib/trace-tools.ts",
];
const T_CALL = /\bt\(\s*"((?:[^"\\]|\\.)*)"/g;

function keysOf(source: string): string[] {
  const keys = new Set<string>();
  for (const m of source.matchAll(T_CALL)) keys.add(m[1]);
  return [...keys].sort();
}

function bundle(lang: string): Record<string, string> {
  return JSON.parse(
    readFileSync(path.join(WEB, "locales", lang, "app.json"), "utf8"),
  ) as Record<string, string>;
}

test("every shell string the cards and progress badges use exists in zh and en", () => {
  const zh = bundle("zh");
  const en = bundle("en");
  for (const file of FILES) {
    const src = readFileSync(path.join(WEB, file), "utf8");
    for (const key of keysOf(src)) {
      assert.ok(
        typeof zh[key] === "string" && zh[key].trim().length > 0,
        `${file}: zh missing "${key}"`,
      );
      assert.ok(
        typeof en[key] === "string" && en[key].trim().length > 0,
        `${file}: en missing "${key}"`,
      );
    }
  }
});

test("the UAT-observed shell strings have Chinese copy", () => {
  const zh = bundle("zh");
  for (const key of [
    "Submit",
    "Select all that apply.",
    "Tool Calling…",
    "{{name}} Exploring…",
    "Asking you",
    "Using {{service}}",
    "DeepTutor responded.",
  ]) {
    assert.ok(/[一-鿿]/.test(zh[key] ?? ""), `zh["${key}"] is not Chinese: ${zh[key]}`);
  }
});

function functionBody(source: string, name: string): string {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `function ${name} not found`);
  const next = source.indexOf("\nfunction ", start + 1);
  const nextExport = source.indexOf("\nexport function ", start + 1);
  const ends = [next, nextExport].filter((i) => i > 0);
  return source.slice(start, ends.length ? Math.min(...ends) : undefined);
}

test("progress badges and trace rows take their t from the turn language", () => {
  const src = readFileSync(path.join(WEB, "components/chat/home/TracePanels.tsx"), "utf8");
  for (const name of ["StreamingStatus", "CallTracePanel", "TraceRowItem"]) {
    const body = functionBody(src, name);
    assert.ok(body.includes("useTurnT("), `${name} does not use useTurnT`);
    assert.ok(!body.includes("useTranslation()"), `${name} still binds t to the UI language`);
  }
  const ask = readFileSync(path.join(WEB, "components/chat/home/AskUserOptions.tsx"), "utf8");
  assert.ok(ask.includes("useTurnT("));
});
