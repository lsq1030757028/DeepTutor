"use client";

// [fork] 聊天富卡的**全部**文案，集中一处。
//
// ## 键的组织方式
//
// 跟本仓既有习惯走：**英文原文即键**（`locales/{en,zh}/app.json` 是平铺表，
// 3000+ 条里绝大多数都是这个形态，M1 测试工作台那批 fork 键也是）。
// 只有两种情况改用 `journey.*` 点分键——这也是本仓既有的第二形态
// （`settingsTour.*` / `research.stage.*` / `codex.oauth.*`）：
//   1. 英文短词与既有键**撞车且语义不同**（Pending 已被"待应用"占了、
//      Trace 已被"追踪"占了、Source 已被"来源"占了、Running 已被"运行中"占了）；
//   2. 单独拿出来看不出说的是哪件事的短标签（表头、状态词）。
// 撞车的键直接复用会让别处的中文变错——这不是风格洁癖，是真会错的地方。
//
// ## 施工纪律
//
// 卡片组件里**不许出现裸字面量**，一律从这里取。集中放置的目的没变：
// 文案要改时是一个文件的机械动作，不是满仓找串。
//
// ## 为什么是工厂函数不是常量
//
// `t` 只能从 React 上下文里拿。导出两个入口：
//   · `journeyCardCopy(t)` —— 纯工厂，测试可以喂任意 `t`（含 `i18n.t`）；
//   · `useJourneyCardCopy()` —— 组件用的 hook 包装。
// 纯工厂在场，渲染测试就不必为了拿一句文案去起 React 上下文。

import type { TFunction } from "i18next";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

/** 页脚那句边界说明是强制的（交互稿 §6f）。 */
export function journeyCardCopy(t: TFunction) {
  return {
    // ── 卡一 · 规则清单（澄清结果）─────────────────────────────────────────
    rules: {
      title: t("Clarified rules"),
      count: (n: number) => t("{{count}} rules", { count: n }),
      probing: t("journey.badge.probe"),
      probingHint: t(
        "Probing: an expectation the requirement body cannot support. Excluded from PASS criteria.",
      ),
      noQuote: t("No source quote"),
      boundary: t(
        "Read-only. Per-rule reasons and out-of-scope declarations are written in the workbench.",
      ),
      action: t("Open rule table in workbench"),
    },

    // ── 卡二 · 用例草稿概览（流式）────────────────────────────────────────
    draft: {
      titleLive: t("Drafting test cases"),
      titleDone: t("Case draft is ready"),
      count: (n: number) => t("{{count}} cases", { count: n }),
      probing: t("journey.badge.probe"),
      business: t("journey.badge.business"),
      uncovered: (n: number) =>
        t("{{count}} further rules are declared out of scope", { count: n }),
      boundary: t("Read-only here. Return to Test chat to explicitly select draft IDs; nothing is pre-selected."),
      action: t("Select cases in Test chat"),
      actionPending: t("Select cases in Test chat (available once drafting finishes)"),
    },

    // ── 卡三 · 执行进度与结论摘要（流式）──────────────────────────────────
    run: {
      titleLive: t("journey.run.titleLive"),
      titleDone: t("Run finished"),
      attempt: (n: number) => t("Attempt {{n}}", { n }),
      replay: t("Replay"),
      replayHint: t(
        "This attempt is a replay triggered by editing and resending, not a new attempt",
      ),
      drift: t("Target is not the machine recorded at intake"),
      driftHint: t(
        "Running against another environment is legitimate, but read the verdicts against the target this attempt actually hit",
      ),
      pass: t("journey.verdict.pass"),
      fail: t("journey.verdict.fail"),
      pending: t("journey.verdict.pending"),
      blocked: t("journey.verdict.blocked"),
      notProjected: t(
        "Verdicts are not projected yet — this attempt has execution counts only, no trustworthy pass/fail",
      ),
      selected: (n: number) => t("{{count}} cases selected", { count: n }),
      boundary: t(
        "Summary level. Evidence, assertion layer and traces are all in the workbench.",
      ),
      action: t("Open full result table"),
    },

    // ── 卡四 · 覆盖收口摘要 ───────────────────────────────────────────────
    coverage: {
      titleDone: t("Coverage is ready to close out"),
      titleGap: t("Coverage is not ready to close out yet"),
      ruleCount: (n: number) => t("{{count}} rules", { count: n }),
      covered: t("journey.coverage.covered"),
      declared: t("journey.coverage.declared"),
      gap: t("journey.coverage.gap"),
      boundary: t("Read-only here. Resolve unexplained gaps in Test chat; the closure stays in the workbench."),
      action: t("Resolve the gaps"),
      actionDone: t("Open closure in workbench"),
    },

    // ── 通用 ───────────────────────────────────────────────────────────────
    common: {
      /** 工具明说自己失败了，或返回的不是业务数据。**不猜、不补默认值。** */
      errorTitle: t("This step did not succeed"),
      errorUnavailable: t(
        "The tool channel returned no business data (not connected / timed out / truncated all look like this)",
      ),
      batchPrefix: t("journey.label.batch"),
      openWorkbench: t("Open this journey in the workbench"),
    },
  };
}

export type JourneyCardCopy = ReturnType<typeof journeyCardCopy>;

/** 组件侧入口。`t` 变了（切语言）才重算。 */
export function useJourneyCardCopy(): JourneyCardCopy {
  const { t } = useTranslation();
  return useMemo(() => journeyCardCopy(t), [t]);
}
