// [fork] 触点 #9 的机械守：**「测试」模式在 picker 里选得到**。
//
// ## 这条守的是什么
//
// DT 没有意图路由——测试模式只能由用户在聊天输入框的能力 picker 里显式选中。
// 那张表（`CAPABILITIES`）写在 `web/app/(workspace)/home/[[...sessionId]]/page.tsx`
// 里，漏改的表现是**入口直接消失**：功能全在、后端注册也在，但没人点得到。
// 在本条建成之前这个触点零机械守（登记表 #9 自述"断不到"）。
//
// ## 为什么不断源码字符串
//
// "page.tsx 里出现过 test" 与 "picker 真渲染得出这一项" 之间没有蕴含关系：
// 表里有、但被 `loopEngine` 收进二级浮层，或过滤条件改了，源码文本一个字不变。
// BB-508 正是这个形状（源码里有 `AgenticChatPipeline` 字样、运行时是纯 chat）。
// 所以这里**渲染真的 `ChatComposer`**，用**真的 `CAPABILITIES`**，断 HTML。
//
// ## 两个工程细节
//
// 1. `page.tsx` 的模块图里有两个 ESM-only 叶子（react-markdown / remark-gfm），
//    本测试 harness 是 CJS，`require` 它们会 `ERR_REQUIRE_ESM`。只对**抛这个错
//    的模块**装桩，别的错照抛——不加区分地吞异常会把真故障也一起吞掉。
//    被桩掉的两个只参与消息渲染，与 picker 无关。
// 2. `CAPABILITIES` 为此加了 `export`。Next 的 page 校验是
//    `Specific extends AppPageConfig<...>`（结构性、非精确），多一个具名导出合法。
//
// ## 残余缺口（如实写，不粉饰）
//
// 本条断的是「page 导出的这张表 → 真 picker 渲染」这一段。**page 自己有没有把
// 这张表传给 composer**（`capabilities={CAPABILITIES}`）不在断言范围内——那需要
// 渲染整个 page，而 page 依赖 router/context/网络，在 node 测试里起不来。
// 真要补，得走浏览器级 e2e。

import test from "node:test";
import assert from "node:assert/strict";

type Cap = {
  value: string;
  label: string;
  description: string;
  loopEngine?: boolean;
};

/** 只桩 ESM-only 叶子，其余错误照抛。 */
async function loadHomePage(): Promise<{ CAPABILITIES: Cap[] }> {

  const Module = require("node:module");
  const original = Module._load;
  const stubbed: string[] = [];
  Module._load = function (request: string, parent: unknown, isMain: boolean) {
    try {
      return original.call(this, request, parent, isMain);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ERR_REQUIRE_ESM") throw error;
      stubbed.push(request);
      const noop = () => null;
      return new Proxy(noop, { get: () => noop, apply: () => null });
    }
  };
  try {

    return (await import(
      "../app/(workspace)/home/[[...sessionId]]/page"
    )) as unknown as { CAPABILITIES: Cap[] };
  } finally {
    Module._load = original;
  }
}

async function renderPicker(capabilities: Cap[]) {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { I18nextProvider } = await import("react-i18next");
  const { createInstance } = await import("i18next");

  const Composer = ((await import("../components/chat/home/ChatComposer")) as any)
    .default;

  // 英文原文即键的仓库，空资源表下 `t(x)` 返回 x 本身——正好让断言落在
  // 表里那份英文原文上，不必再引一层 locales。
  const i18n = createInstance();
  await i18n.init({ lng: "en", resources: { en: { translation: {} } } });


  const props: any = {
    capabilities,
    activeCap: capabilities[0],
    capMenuOpen: true, // picker 展开态：这正是要断的那一屏
    onSetCapMenuOpen: () => {},
    onSetSpaceMenuOpen: () => {},
    attachments: [],
    knowledgeBases: [],
    connectedAgents: [],
    llmOptions: [],
    selectedKnowledgeBases: [],
    selectedNotebookRecords: [],
    selectedBookReferences: [],
    selectedHistorySessions: [],
    selectedAgentSessions: [],
    selectedQuestionEntries: [],
    selectedMemoryFiles: [],
    notebookReferenceGroups: [],
    composerRef: { current: null },
    capMenuRef: { current: null },
    capBtnRef: { current: null },
    spaceMenuRef: { current: null },
    spaceBtnRef: { current: null },
    dragCounter: { current: 0 },
  };
  return renderToStaticMarkup(

    createElement(I18nextProvider, { i18n } as any, createElement(Composer, props)),
  );
}

/** 二级浮层容器的结构锚：`left-full` 只出现在 More 那个右侧浮层上。 */
const FLYOUT_ANCHOR = "left-full";

test("触点 #9：「测试」在 page 导出的能力表里，且 value 就是后端注册的那个键", async () => {
  const { CAPABILITIES } = await loadHomePage();
  assert.ok(Array.isArray(CAPABILITIES), "page 没有导出 CAPABILITIES");
  // 阳性对照：表本身不是空的、上游那几项也在——否则"找到了 test"可能只是
  // 因为我读到了一张退化成单项的表。
  assert.ok(CAPABILITIES.length >= 5, `能力表只有 ${CAPABILITIES.length} 项，多半读错了`);
  for (const upstream of ["", "deep_question", "deep_research", "visualize"]) {
    assert.ok(
      CAPABILITIES.some((c) => c.value === upstream),
      `上游能力 ${JSON.stringify(upstream)} 不在表里`,
    );
  }

  const testCap = CAPABILITIES.find((c) => c.value === "test");
  assert.ok(testCap, "能力表里没有 value=\"test\" —— 聊天里选不到测试模式");
  assert.ok(testCap.label.trim(), "测试模式没有 label，picker 那一行会是空的");
  assert.ok(testCap.description.trim(), "测试模式没有 description");
  // 不是 loop-engine：一旦被标上，它会被收进 More 二级浮层，
  // 表里"有"但一级菜单上"没有"——这正是断源码字符串抓不到的那种漏法。
  assert.notEqual(testCap.loopEngine, true);
});

test("触点 #9：真 picker 渲染得出「测试」这一项，且在一级菜单不在 More 浮层", async () => {
  const { CAPABILITIES } = await loadHomePage();
  const testCap = CAPABILITIES.find((c) => c.value === "test");
  assert.ok(testCap);

  const html = await renderPicker(CAPABILITIES);

  // 阳性对照：菜单确实渲染出来了（上游项在场），否则下面那条"找到了"没有意义。
  const chat = CAPABILITIES.find((c) => c.value === "");
  assert.ok(chat && html.includes(chat.description), "picker 根本没渲染出来");

  const at = html.indexOf(testCap.description);
  assert.notEqual(at, -1, "picker 里没有测试模式那一行 —— 入口消失");

  // 位置断言：一级列表整段排在 More 浮层之前。落在浮层里 = 用户要先悬停
  // 「More」才看得见，与交互稿说的"一级可选"不是一回事。
  const flyoutAt = html.indexOf(FLYOUT_ANCHOR);
  assert.notEqual(flyoutAt, -1, "没找到 More 浮层的结构锚，位置断言失效了");
  assert.ok(at < flyoutAt, "测试模式被渲染进了 More 二级浮层，不在一级菜单上");
});

test("触点 #9 的反例：把这一项从表里拿掉，上面那条必须红", async () => {
  // **建完一个测试要单独验它作用到了谁**。这条构造反例，证明前一条不是恒绿装饰：
  // 同一个渲染路径、同一个判据，只把 test 那一项摘掉。
  const { CAPABILITIES } = await loadHomePage();
  const without = CAPABILITIES.filter((c) => c.value !== "test");
  assert.equal(without.length, CAPABILITIES.length - 1);

  const html = await renderPicker(without);
  const testCap = CAPABILITIES.find((c) => c.value === "test");
  assert.ok(testCap);
  assert.equal(
    html.includes(testCap.description),
    false,
    "拿掉表项后 picker 仍渲染出测试模式 —— 说明判据没绑在这张表上，前一条是恒绿的",
  );
  // 反例只该少这一项，别的照旧——否则"红了"可能是整个渲染塌了而不是判据生效。
  const chat = CAPABILITIES.find((c) => c.value === "");
  assert.ok(chat && html.includes(chat.description), "反例把整个 picker 渲染塌了");
});
