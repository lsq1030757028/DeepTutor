// [fork] 聊天富卡取数层的判据（交互稿 §6f / DoD#8d / #8e-1）。
//
// 三组：① 四张卡各自取得对 ② **普通聊天里零测试元素**（单列，不靠 ① 顺带）
// ③ 增量性与性能回归（上界相对 quiz 实测基准，不是拍脑袋的毫秒数）。

import test from "node:test";
import assert from "node:assert/strict";

import {
  TEST_CAPABILITY,
  createJourneyAccumulator,
  extractJourneyState,
  journeyToolOf,
} from "../components/test-journey/chat/extract";
import { JOURNEY_CARD_COPY } from "../components/test-journey/chat/copy";
import { extractStreamingQuizQuestions } from "../lib/quiz-types";

type AnyEvent = Record<string, unknown>;

/** MCP 面把工具名包成 `mcp_<server>_journey_x`（`mcp/manager.py:84`）。 */
const wrapped = (tool: string) => `mcp_test_partner_journey_${tool}`;

function toolResult(tool: string, payload: unknown): AnyEvent {
  return {
    type: "tool_result",
    source: "chat",
    stage: "exploring",
    content: typeof payload === "string" ? payload : JSON.stringify(payload),
    metadata: { tool: wrapped(tool), trace_kind: "tool_result" },
    timestamp: 0,
  };
}

function toolCall(tool: string): AnyEvent {
  return {
    type: "tool_call",
    source: "chat",
    stage: "exploring",
    content: wrapped(tool),
    metadata: { args: {} },
    timestamp: 0,
  };
}

function filler(i: number): AnyEvent {
  return {
    type: "content",
    source: "chat",
    stage: "responding",
    content: `chunk ${i}`,
    metadata: {},
    timestamp: 0,
  };
}

 
const push = (acc: ReturnType<typeof createJourneyAccumulator>, events: AnyEvent[]) =>
  acc.push(TEST_CAPABILITY, events as any);
 
const extract = (events: AnyEvent[], capability = TEST_CAPABILITY) =>
  extractJourneyState(events as any, capability);

const CLARIFY_OK = {
  ok: true,
  code: "OK",
  business_frame: {
    artifact: "business_frame",
    batch_id: "b-1",
    rules: [
      { rule_id: "R1", statement: "销售员只看自己的订单", source_quote: "仅可见本人订单" },
      { rule_id: "R9", statement: "三次错误锁定一小时", probing: true },
    ],
  },
};

const DRAFT_OK = {
  ok: true,
  code: "OK",
  case_draft: {
    batch_id: "b-1",
    cases: [
      { case_id: "TC-001", title: "销售员甲只看到自己的 3 笔", source_anchor: { rule_id: "R1" } },
      { case_id: "TC-007", title: "金额为负应被拒", source_anchor: { rule_id: "R9", probing: true } },
    ],
    uncovered_rules: [{ rule_id: "R5", reason: "本期不测导出" }],
  },
};

const EXECUTE_OK = {
  ok: true,
  code: "OK",
  run_id: "r-20260811-1",
  triggered_by: "fresh",
  receipt: {
    // 真收据把 batch_id 盖在信封里，不在工具返回的顶层（execute_run.py:331）。
    batch_id: "b-1",
    run_id: "r-20260811-1",
    selected_case_count: 12,
    counts: { passed: 9, failed: 2, blocked: 1 },
    target_drift: false,
  },
};

const PROJECT_OK = {
  ok: true,
  code: "OK",
  projection: { code: 0, written: 12, distribution: { PASS: 9, FAIL: 2, PENDING: 1 } },
  mechanical_check: { verdict: "PASS", blocking: [] },
};

const COVERAGE_GAP = {
  ok: true,
  code: "OK",
  coverage_ledger: {
    batch_id: "b-1",
    rules: [
      {
        rule_id: "R1",
        statement: "销售员只看自己的订单",
        status: "covered",
        gap_reason: "",
        cases: [{ case_id: "TC-001", title: "只看到自己的 3 笔", probing: false, verdict: "FAIL" }],
      },
      { rule_id: "R5", statement: "导出", status: "declared_uncovered", gap_reason: "本期不测导出", cases: [] },
      { rule_id: "R9", statement: "三次错误锁定一小时", status: "gap_unexplained", gap_reason: "", cases: [] },
    ],
    summary: { total_rules: 3, covered: 1, declared_uncovered: 1, gap_unexplained: 1 },
    done: false,
    problems: ["R9 无用例且无不覆盖声明——gap 无解释不 done"],
  },
};

// ── ① 四张卡的取数 ─────────────────────────────────────────────────────────

test("工具名认的是 journey_ 之后那截，不认死服务器前缀", () => {
  assert.equal(journeyToolOf("mcp_test_partner_journey_draft_cases"), "draft_cases");
  // 服务器名是用户可配的，换个名字照样认得出。
  assert.equal(journeyToolOf("mcp_whatever_journey_coverage"), "coverage");
  assert.equal(journeyToolOf("journey_execute"), "execute");
  // 不是旅程工具的一律不认，别把别的工具结果当旅程产物解析。
  assert.equal(journeyToolOf("mcp_tapd_get_stories"), null);
  assert.equal(journeyToolOf("journey_not_a_tool"), null);
  assert.equal(journeyToolOf(undefined), null);
});

test("批次号从产物信封里取，不只看顶层——页脚跳转全靠它", () => {
  // 只有 ingest 把 batch_id 放顶层；其余工具盖在产物信封里。只读顶层的后果是
  // 每张卡的按钮都渲染成灰的点不动。这条是渲染测试抓出来后补的单测。
  assert.equal(extract([toolResult("clarify", CLARIFY_OK)])?.batchId, "b-1");
  assert.equal(extract([toolResult("draft_cases", DRAFT_OK)])?.batchId, "b-1");
  assert.equal(extract([toolResult("execute", EXECUTE_OK)])?.batchId, "b-1");
  assert.equal(extract([toolResult("coverage", COVERAGE_GAP)])?.batchId, "b-1");
});

test("卡一：澄清结果出规则清单，探测性标出来", () => {
  const state = extract([toolResult("clarify", CLARIFY_OK)]);
  assert.ok(state);
  assert.equal(state.rules.length, 2);
  assert.equal(state.rules[0].source_quote, "仅可见本人订单");
  assert.equal(state.rules[1].probing, true);
  // 探测性规则没有原文依据是**合法的**，不该被当成缺依据。
  assert.equal(state.rules[1].source_quote, undefined);
});

test("卡二：用例草稿概览，探测项与不覆盖声明分开数", () => {
  const state = extract([toolResult("draft_cases", DRAFT_OK)]);
  assert.ok(state?.draft);
  assert.equal(state.draft.cases.length, 2);
  assert.equal(state.draft.cases[0].probing, false);
  assert.equal(state.draft.cases[1].probing, true);
  assert.equal(state.draft.uncoveredCount, 1);
});

test("卡三：执行只给计数，投影才给结论——没投影不许显示通过/没过", () => {
  const afterExecute = extract([toolResult("execute", EXECUTE_OK)]);
  assert.ok(afterExecute?.run);
  assert.equal(afterExecute.run.selected, 12);
  assert.equal(afterExecute.run.projected, false);
  // 收据里的 pytest 计数**不冒充结论分布**：两个层次的东西混起来，
  // 正是"把我们其实没验说成验过了"的那一步。
  assert.deepEqual(afterExecute.run.distribution, {});
  assert.equal(afterExecute.run.counts.passed, 9);

  const afterProject = extract([
    toolResult("execute", EXECUTE_OK),
    toolResult("project", PROJECT_OK),
  ]);
  assert.equal(afterProject?.run?.projected, true);
  assert.deepEqual(afterProject?.run?.distribution, { PASS: 9, FAIL: 2, PENDING: 1 });
});

test("卡三：重放与靶机漂移都留痕，不能和第一趟长得一样", () => {
  const replay = extract([
    toolResult("execute", {
      ...EXECUTE_OK,
      triggered_by: "regenerate-replay",
      receipt: { ...EXECUTE_OK.receipt, target_drift: true },
    }),
  ]);
  assert.equal(replay?.run?.triggered_by, "regenerate-replay");
  assert.equal(replay?.run?.target_drift, true);
});

test("卡三：同一轮跑两趟，第二趟不继承第一趟的结论", () => {
  const state = extract([
    toolResult("execute", EXECUTE_OK),
    toolResult("project", PROJECT_OK),
    toolResult("execute", { ...EXECUTE_OK, run_id: "r-2" }),
  ]);
  assert.equal(state?.run?.attempt, 2);
  assert.equal(state?.run?.run_id, "r-2");
  // 新一趟从零开始。沿用上一趟的分布 = 把旧结论贴到新执行上。
  assert.deepEqual(state?.run?.distribution, {});
  assert.equal(state?.run?.projected, false);
});

test("卡四：覆盖三段与挡收口的那一类", () => {
  const state = extract([toolResult("coverage", COVERAGE_GAP)]);
  assert.ok(state?.coverage);
  assert.equal(state.coverage.done, false);
  assert.equal(state.coverage.summary.gap_unexplained, 1);
  assert.equal(state.coverage.problems.length, 1);
  assert.equal(state.coverage.cases.length, 1);
});

test("活点：工具在飞时是 live，结果回来就落地", () => {
  const acc = createJourneyAccumulator();
  const events: AnyEvent[] = [toolCall("draft_cases")];
  let state = push(acc, events);
  assert.equal(state?.pending, "draft_cases");
  events.push(toolResult("draft_cases", DRAFT_OK));
  state = push(acc, events);
  assert.equal(state?.pending, null);
  assert.equal(state?.draft?.cases.length, 2);
});

test("错误当数据：工具失败与非业务载荷都如实记，不猜不补默认值", () => {
  const rejected = extract([
    toolResult("draft_cases", { ok: false, code: "E_DRAFT_REJECTED", message: "没过校验" }),
  ]);
  assert.equal(rejected?.draft, null);
  assert.equal(rejected?.errors[0].code, "E_DRAFT_REJECTED");

  // MCP 适配器在未连接 / 超时 / 半截下返回的是**普通字符串且看起来像成功**。
  for (const raw of [
    "(MCP server 'test-partner' is not connected)",
    '{"case_draft": {"cases": [',
    "服务暂时不可用",
  ]) {
    const bad = extract([toolResult("draft_cases", raw)]);
    assert.equal(bad?.draft, null, raw);
    assert.equal(bad?.errors[0].code, "E_MCP_UNAVAILABLE", raw);
  }
});

test("工作台产物不进聊天卡：ingest/analyze/adopt/compile 一张卡都不喂", () => {
  const state = extract([
    toolResult("ingest", { ok: true, code: "OK", batch_id: "b-1" }),
    toolResult("analyze", { ok: true, code: "OK", test_analysis: { example_map: [] } }),
    toolResult("adopt", { ok: true, code: "OK", approved_caseset: { cases: [] } }),
    toolResult("compile", { ok: true, code: "OK", automation_bundle: {} }),
  ]);
  // 批次号认下来了（页脚跳转要用），但四张卡一张都不该出现。
  assert.equal(state, null);
});

// ── ② 作用域边界：普通聊天里零测试元素 ─────────────────────────────────────
//
// **单独一条，不靠上面任何一条顺带**：只验"测试模式里出得来"与"所有模式里都
// 出得来"是一致的，区分不开。这条线已经四次栽在"机制建成了但没人核它作用到了谁"。

test("作用域：非「测试」模式一律返回 null，事件里有旅程产物也一样", () => {
  const journeyEvents = [
    toolResult("clarify", CLARIFY_OK),
    toolResult("draft_cases", DRAFT_OK),
    toolResult("coverage", COVERAGE_GAP),
  ];
  for (const capability of ["", "chat", "deep_research", "deep_question", "visualize", undefined]) {
    const acc = createJourneyAccumulator();
     
    assert.equal(acc.push(capability, journeyEvents as any), null, String(capability));
  }
  // 同一批事件在「测试」模式下**是**出得来的——否则上面那圈 null 可能只是
  // 因为事件本身没内容，这条断言把那种假通过挡掉。
  assert.ok(extract(journeyEvents));
});

test("作用域：模式切走后状态清干净，切回来不接上一条流", () => {
  const acc = createJourneyAccumulator();
  const events = [toolResult("clarify", CLARIFY_OK)];
  assert.equal(push(acc, events)?.rules.length, 2);
   
  assert.equal(acc.push("chat", events as any), null);
   
  assert.equal(acc.push(TEST_CAPABILITY, [] as any), null);
});

// ── ③ 增量性与性能回归 ─────────────────────────────────────────────────────

test("增量：逐 tick 喂 N 个事件，只检查 N 次而不是 N²/2 次", () => {
  const acc = createJourneyAccumulator();
  const events: AnyEvent[] = [];
  const N = 300;
  for (let i = 0; i < N; i += 1) {
    events.push(i % 40 === 39 ? toolResult("clarify", CLARIFY_OK) : filler(i));
    push(acc, [...events]);
  }
  assert.equal(acc.processed, N);
  // 这是「O(新增事件)」的**确定性**证明，不依赖计时：全量重解析会是 45150。
  assert.equal(acc.examined, N);
});

test("增量结果与一次性解析逐字段相同（增量不能换来错答案）", () => {
  const events = [
    toolCall("clarify"),
    toolResult("clarify", CLARIFY_OK),
    filler(1),
    toolResult("draft_cases", DRAFT_OK),
    toolResult("execute", EXECUTE_OK),
    toolResult("project", PROJECT_OK),
    toolResult("coverage", COVERAGE_GAP),
  ];
  const acc = createJourneyAccumulator();
  for (let i = 1; i <= events.length; i += 1) push(acc, events.slice(0, i));
  assert.deepEqual(push(acc, events), extract(events));
});

test("流被换掉（重放 / 翻历史）时整体重算，不把两条流的状态混在一起", () => {
  const acc = createJourneyAccumulator();
  const first = [toolResult("clarify", CLARIFY_OK), toolResult("draft_cases", DRAFT_OK)];
  assert.equal(push(acc, first)?.draft?.cases.length, 2);
  // 全新数组、全新对象：不是上一条的延长。
  const second = [toolResult("clarify", CLARIFY_OK)];
  const state = push(acc, second);
  assert.equal(state?.rules.length, 2);
  assert.equal(state?.draft, null, "上一条流的草稿不该漏进来");
});

test("性能回归：不慢于 quiz 那条既有流式路径的 2 倍", () => {
  // ## 上界怎么定的
  //
  // 不写绝对毫秒——绝对值换台机器就失效，会变成又一条被扣除的红。
  // 基准取 `extractStreamingQuizQuestions`：它是上游**已被接受**的流式实现，
  // 同机同 runtime 同一次进程内实测，是天然参照物。上界 = 基准 × 2。
  //
  // 两边喂等量事件、等量 tick，各自的"有效事件"密度相同（每 8 个一条）。
  const TICKS = 400;
  const quizEvents: AnyEvent[] = [];
  const journeyEvents: AnyEvent[] = [];
  for (let i = 0; i < TICKS; i += 1) {
    if (i % 8 === 7) {
      quizEvents.push({
        type: "content",
        metadata: {
          call_kind: "quiz_question_emitted",
          question_index: i,
          qa_pair: {
            question_id: `q_${i}`,
            question: `第 ${i} 题：销售员能不能看到别人的订单？`,
            question_type: "single_choice",
            options: { A: "能", B: "不能" },
            correct_answer: "B",
            explanation: "需求写了仅可见本人订单。",
          },
        },
      });
      journeyEvents.push(toolResult("clarify", CLARIFY_OK));
    } else {
      quizEvents.push(filler(i));
      journeyEvents.push(filler(i));
    }
  }

  const timeIt = (fn: () => void) => {
    const t0 = process.hrtime.bigint();
    fn();
    return Number(process.hrtime.bigint() - t0) / 1e6;
  };

  // 预热：两边都跑一遍，免得 JIT 冷启动落在谁头上。
  for (let i = 1; i <= TICKS; i += 1) {
     
    extractStreamingQuizQuestions(quizEvents.slice(0, i) as any);
  }
  const warm = createJourneyAccumulator();
  for (let i = 1; i <= TICKS; i += 1) push(warm, journeyEvents.slice(0, i));

  const baselineMs = timeIt(() => {
    for (let i = 1; i <= TICKS; i += 1) {
       
      extractStreamingQuizQuestions(quizEvents.slice(0, i) as any);
    }
  });
  const acc = createJourneyAccumulator();
  const journeyMs = timeIt(() => {
    for (let i = 1; i <= TICKS; i += 1) push(acc, journeyEvents.slice(0, i));
  });

  const ratio = journeyMs / Math.max(baselineMs, 1e-6);
  // 测量条件与实测值随测试输出走，供跨机复核。
  console.log(
    `[perf] ticks=${TICKS} quiz_baseline=${baselineMs.toFixed(2)}ms ` +
      `journey=${journeyMs.toFixed(2)}ms ratio=${ratio.toFixed(2)}x node=${process.version}`,
  );
  assert.ok(
    ratio <= 2,
    `富卡取数 ${journeyMs.toFixed(2)}ms 超过 quiz 基准 ${baselineMs.toFixed(2)}ms 的 2 倍（${ratio.toFixed(2)}x）`,
  );
});

// ── ④ 真渲染：取数对了 ≠ 卡片会出现 ────────────────────────────────────────
//
// 上面三组全绿也只证明"数据取得对"。卡片会不会真长出来、页脚那句强制的边界
// 说明在不在、探测项标没标——只有把组件渲染出来才知道。这里用
// `react-dom/server` 渲成静态 HTML 断文本，不进浏览器也能守住这一层。
//
// 注意用 `createElement` 不写 JSX：本文件是 .ts，写 JSX 要改测试编译配置，
// 那是为了测试改产线配置，不划算。

test("真渲染：四张卡出得来，页脚的边界说明每张都在", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { default: TestJourneyCards } = await import(
    "../components/test-journey/chat/TestJourneyCards"
  );

  const state = extract([
    toolResult("clarify", CLARIFY_OK),
    toolResult("draft_cases", DRAFT_OK),
    toolResult("execute", EXECUTE_OK),
    toolResult("project", PROJECT_OK),
    toolResult("coverage", COVERAGE_GAP),
  ]);
  assert.ok(state);
  const html = renderToStaticMarkup(createElement(TestJourneyCards, { state }));

  // 四张卡的标题各出现一次。
  assert.match(html, /已澄清的规则/);
  assert.match(html, /用例草稿写好了/);
  assert.match(html, /执行完成/);
  assert.match(html, /覆盖还差一点才能收口/);

  // **页脚那句边界说明是强制的**——它回答"为什么这张卡不让我在这儿改"。
  for (const boundary of [
    JOURNEY_CARD_COPY.rules.boundary,
    JOURNEY_CARD_COPY.draft.boundary,
    JOURNEY_CARD_COPY.run.boundary,
    JOURNEY_CARD_COPY.coverage.boundary,
  ]) {
    assert.ok(html.includes(boundary), `缺边界说明：${boundary}`);
  }

  // 页脚按钮指向这条旅程的工作台页，不是空 href。
  assert.match(html, /href="\/test-journey\/b-1"/);
  // 探测项标出来了，且规则行带原文依据。
  assert.match(html, /探测/);
  assert.match(html, /仅可见本人订单/);
  // 结论分布上了卡面。
  assert.match(html, /没过 2/);
});

test("真渲染：只有一步走完时只出那一张卡，不出空壳", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { default: TestJourneyCards } = await import(
    "../components/test-journey/chat/TestJourneyCards"
  );

  const state = extract([toolResult("clarify", CLARIFY_OK)]);
  assert.ok(state);
  const html = renderToStaticMarkup(createElement(TestJourneyCards, { state }));
  assert.match(html, /已澄清的规则/);
  // 断的是**卡标题**，不是随手挑的词：像"覆盖"这种词也出现在规则卡的边界说明
  // （"不覆盖声明"）里，拿它当"覆盖卡没出现"的判据会假红。
  for (const absent of [
    JOURNEY_CARD_COPY.draft.titleDone,
    JOURNEY_CARD_COPY.draft.titleLive,
    JOURNEY_CARD_COPY.run.titleDone,
    JOURNEY_CARD_COPY.coverage.titleGap,
    JOURNEY_CARD_COPY.coverage.titleDone,
  ]) {
    assert.ok(!html.includes(absent), `不该出现：${absent}`);
  }
});

test("真渲染：流式卡在工具还在飞时就出来，且采纳按钮点不动", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { default: TestJourneyCards } = await import(
    "../components/test-journey/chat/TestJourneyCards"
  );

  const state = extract([toolCall("draft_cases")]);
  assert.ok(state, "工具在飞时就该有卡——这正是流式的价值：别让人盯着空屏等");
  const html = renderToStaticMarkup(createElement(TestJourneyCards, { state }));
  assert.match(html, /正在生成用例/);
  // 生成完才能采纳：这时候按钮是死的，不是一个点了没反应的链接。
  assert.ok(!html.includes('href="/test-journey/'), "生成中不该给可点的采纳链接");
  assert.match(html, /生成完才能点/);
});
