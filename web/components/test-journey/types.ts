// [fork] 旅程薄壳的类型与投影规则。
//
// **界面四段 ≠ 后端九格**，两者是投影关系不是同一个数（0021 必改 1 + 0022 核验）：
// 后端 `stepper()` 返 9 项，界面账本条显示 4 段，段点开可见段内格子。
// 段状态由段内格子**即时汇总**，不存盘、不新增状态源。
//
// 施工纪律（设计稿 §4.3 明写）：
//   · 不许为了对齐界面把后端砍回 4 项；
//   · 不许把九格搬回列表卡（0021 必改 3，列表卡上的九格微条已删）。
// 所以映射写在这里、只在这里，且**格子的定义仍由服务端给**（stepper_definition）。

/** 服务端九格中的一格。 */
export interface StepCell {
  artifact: string;
  label: string;
  present: boolean;
  /** run 级两格带 run 锚，batch 级为空串。 */
  run_id: string;
  /** 前置没产出 → 前置的 artifact 名。与 blocked_reason 互斥。 */
  missing_prereq: string;
  /** 前置齐了还灭 → 一句话说明。与 missing_prereq 互斥。 */
  blocked_reason: string;
}

export interface BatchSummary {
  batch_id: string;
  title: string;
  base_url: string;
  owner: string;
  created_at: string;
  run_count: number;
  stepper: StepCell[];
}

export type SegmentId = "intake" | "cases" | "run" | "closure";

export interface Segment {
  id: SegmentId;
  /** i18n 键，不是上屏文案。渲染方自己 `t()`。 */
  label: string;
  /** 段内格子（服务端九格的子集），顺序即服务端顺序。 */
  cells: StepCell[];
  /** 段状态由段内格子即时汇总：全亮 done / 部分亮 doing / 全灭 todo。 */
  status: "done" | "doing" | "todo";
}

/**
 * 四段各自罩住哪几格。**这是投影表，不是新的状态源。**
 * 覆盖收口独立成段是因为它回答的是"能不能收工"，与"跑没跑"是两个问题。
 *
 * `label` 存的是 **i18n 键**（英文原文即键，本仓平铺表的既有形态），
 * 不是可直接上屏的文案——本模块是纯投影层，不进 React 上下文、拿不到 `t`。
 * 渲染方（ArtifactLedger / JourneyList）负责 `t(segment.label)`。
 */
const SEGMENT_MAP: { id: SegmentId; label: string; artifacts: string[] }[] = [
  { id: "intake", label: "journey.segment.intake",
    artifacts: ["intake_profile", "business_frame", "test_analysis"] },
  { id: "cases", label: "journey.segment.cases",
    artifacts: ["case_draft", "approved_caseset", "automation_bundle"] },
  { id: "run", label: "journey.segment.run",
    artifacts: ["run_receipt", "verdicts"] },
  { id: "closure", label: "journey.segment.closure",
    artifacts: ["coverage_ledger"] },
];

/** 把服务端九格投影成界面四段。不存盘、不缓存——每次从九格现算。 */
export function projectSegments(cells: StepCell[]): Segment[] {
  const byArtifact = new Map(cells.map((c) => [c.artifact, c]));
  return SEGMENT_MAP.map(({ id, label, artifacts }) => {
    const inSegment = artifacts
      .map((a) => byArtifact.get(a))
      .filter((c): c is StepCell => Boolean(c));
    const present = inSegment.filter((c) => c.present).length;
    const status: Segment["status"] =
      inSegment.length > 0 && present === inSegment.length
        ? "done"
        : present > 0
          ? "doing"
          : "todo";
    return { id, label, cells: inSegment, status };
  });
}

/**
 * 「现在轮到我做什么」——第一个没亮的格子。
 * 这一句是详情页第一眼要回答的唯一问题（交互稿 s2）。
 */
export function nextAction(cells: StepCell[]): StepCell | null {
  return cells.find((c) => !c.present) ?? null;
}

export type Verdict = "PASS" | "FAIL" | "PENDING" | "BLOCK" | "BLOCKED";

export interface VerdictRow {
  id: string;
  verdict: Verdict | string;
  note?: string;
  evidence?: { ref?: string; kind?: string }[];
  /** 探测性用例：**单独一栏，绝不混进通过率的分母**（交互稿 s4）。 */
  probing?: boolean;
  layer?: string;
  /** Owner-bound opaque trace handle returned by the dedicated Journey API. */
  trace_rel?: string | null;
}

export interface RunRow {
  run_id: string;
  receipt: Record<string, unknown> | null;
  verdicts: VerdictRow[];
}

export function verdictParts(verdict: string): { code: string; reason: string } {
  const raw = String(verdict ?? "").trim();
  const separator = raw.indexOf(":");
  return {
    code: (separator >= 0 ? raw.slice(0, separator) : raw).toUpperCase(),
    reason: separator >= 0 ? raw.slice(separator + 1).trim() : "",
  };
}

/** Default to the newest run; preserve an explicit older selection if it still exists. */
export function selectRun(runs: RunRow[], selectedRunId: string | null): RunRow | null {
  if (selectedRunId) {
    const selected = runs.find((run) => run.run_id === selectedRunId);
    if (selected) return selected;
  }
  return runs.length ? runs[runs.length - 1] : null;
}

/**
 * 汇总一趟的结论。探测项从分母里摘出去单列——
 * 把探测性用例算进通过率，是最容易把"我们其实没验"读成"验过了"的一步。
 */
export function summarizeRun(rows: VerdictRow[]) {
  const probing = rows.filter((r) => r.probing);
  const scored = rows.filter((r) => !r.probing);
  const count = (v: string) =>
    scored.filter((r) => verdictParts(String(r.verdict)).code === v).length;
  return {
    total: scored.length,
    passed: count("PASS"),
    failed: count("FAIL"),
    pending: count("PENDING"),
    blocked: count("BLOCK") + count("BLOCKED"),
    probing: probing.length,
  };
}

/**
 * 本趟是哪条轨。API 轨没有 trace，界面得说「本来就没有」而不是「打不开」。
 *
 * 两级判据，**不许退化成"取不到就当 UI 轨"**——那会让每一行都显示
 * 「trace 打不开」，把一个正常的接口轨批次说成一批坏掉的 UI 批次
 * （施工期真的踩到过：M1 存量收据没有 target_identity，结果 9 行全报 E_TRACE_MISSING）。
 *
 * 1. `target_identity.track`：M2 起的收据自带，权威。
 * 2. 存量收据（schema 1.0，没有这个字段）：按证据形态反推——
 *    有 HTTP 报文、没有 trace，就是接口轨。反推不出来才返回空串。
 */
export function runTrack(
  receipt: Record<string, unknown> | null,
  verdicts?: VerdictRow[],
): "api" | "ui" | "" {
  const identity = (receipt?.target_identity ?? {}) as Record<string, unknown>;
  const track = identity.track;
  if (track === "api" || track === "ui") return track;

  const refs = (verdicts ?? []).flatMap((v) =>
    (v.evidence ?? []).map((e) => String(e?.ref ?? "")),
  );
  if (refs.length === 0) return "";
  if (refs.some((r) => r.endsWith("trace.zip"))) return "ui";
  if (refs.some((r) => r.endsWith("transcript.json"))) return "api";
  return "";
}
