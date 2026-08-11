// [fork] 聊天富卡的取数层：从一轮的事件流里累加出四张卡要的东西。
//
// ## 数据从哪来
//
// 「测试」capability 复用主聊 pipeline，旅程的每一步都是一次 MCP 工具调用
// （`journey_ingest` / `journey_clarify` / …），所以富卡的原料就是本轮的
// `tool_call` / `tool_result` 事件。**没有另一条专供富卡的事件通道**——
// 造一条就得动 extensions 侧，那是另一条线的地盘。
//
// 工具名在事件里是**带服务器前缀的**（`mcp_<server>_journey_clarify`，
// 见 `deeptutor/services/mcp/manager.py:84` 的 `wrapped_tool_name`）。服务器名
// 是用户可配的，所以这里**按 `journey_` 之后的部分认**，不按整名匹配。
//
// ## 为什么是累加器而不是每 tick 全量重解析
//
// `msg.events` 每来一个事件就换一个新数组（`UnifiedChatContext.tsx:483`
// 的 `[...(last?.events||[]), action.event]`），全量重解析是 O(事件数²)。
// 这里存 `processed` 游标 + 上次尾事件的引用：数组是上次的延长就只解析新增的，
// 否则（重放、翻历史、编辑分支）整体重来一次。**旧事件对象身份稳定**是这条
// 优化的前提，上面那行 append-only 就是它的依据。
//
// 性能回归见 `web/tests/test-journey-cards.test.ts`：上界不是拍脑袋的毫秒数，
// 是**同机同 runtime 下 quiz 那条既有流式路径的实测基准的 2 倍**。

import type { StreamEvent } from "@/lib/unified-ws";

/** 本模式的 capability 字面量。四张卡的取数第一关都判它。 */
export const TEST_CAPABILITY = "test";

const JOURNEY_PREFIX = "journey_";

/** 九原子 + 门票 + 两个读接口。名字与 `server/journey/tools.py` 的 MCP 面一致。 */
export const JOURNEY_TOOLS = [
  "ingest",
  "clarify",
  "analyze",
  "draft_cases",
  "adopt",
  "compile",
  "execute",
  "project",
  "coverage",
  "list_batches",
  "get_batch",
  "issue_gate_token",
] as const;

export type JourneyTool = (typeof JOURNEY_TOOLS)[number];

const JOURNEY_TOOL_SET = new Set<string>(JOURNEY_TOOLS);

/**
 * 从事件里的工具名认出是哪个旅程工具，认不出返回 null。
 *
 * 用 `lastIndexOf` 而不是 `startsWith`：前缀由 MCP 服务器名拼出来，
 * 服务器叫什么是用户配置的，写死前缀等于把判断建在一个会变的东西上。
 */
export function journeyToolOf(rawName: unknown): JourneyTool | null {
  if (typeof rawName !== "string" || !rawName) return null;
  const at = rawName.lastIndexOf(JOURNEY_PREFIX);
  if (at < 0) return null;
  const name = rawName.slice(at + JOURNEY_PREFIX.length);
  return JOURNEY_TOOL_SET.has(name) ? (name as JourneyTool) : null;
}

// ── 四张卡各自要的行 ────────────────────────────────────────────────────────

export interface RuleRow {
  rule_id: string;
  statement: string;
  source_quote?: string;
  /** 探测性：需求正文撑不住的预期，**不进 PASS 判据**。 */
  probing?: boolean;
}

export interface DraftCaseRow {
  case_id: string;
  title: string;
  probing: boolean;
}

export interface CoverageRuleRow {
  rule_id: string;
  statement: string;
  /** covered / declared_uncovered / gap_unexplained */
  status: string;
  gap_reason: string;
}

export interface CoverageCaseRow {
  case_id: string;
  title: string;
  probing: boolean;
  verdict: string;
}

export interface JourneyError {
  tool: JourneyTool;
  code: string;
  message: string;
}

export interface RunState {
  run_id: string;
  /** 选了几条执行（收据的 `selected_case_count`）。 */
  selected: number;
  /** 收据里的 pytest 级 outcome 计数。 */
  counts: Record<string, number>;
  /** `journey_project` 投影出的结论分布（PASS / FAIL / PENDING / BLOCKED）。 */
  distribution: Record<string, number>;
  /** `fresh` / `regenerate-replay`。重放必须留痕，不能和第一趟长得一样。 */
  triggered_by: string;
  /** 接入期靶 ≠ 执行期靶。不阻断，但结论卡上必须看得见。 */
  target_drift: boolean;
  /** 本轮第几趟执行（同一轮里 `journey_execute` 的第几次成功返回）。 */
  attempt: number;
  /** 投影跑过没有——没跑过时不许把「0 条没过」说成「全过了」。 */
  projected: boolean;
}

export interface CoverageState {
  rules: CoverageRuleRow[];
  cases: CoverageCaseRow[];
  summary: {
    total_rules: number;
    covered: number;
    declared_uncovered: number;
    gap_unexplained: number;
  };
  done: boolean;
  problems: string[];
}

export interface JourneyState {
  batchId: string;
  /** 卡一：规则清单（澄清结果）。 */
  rules: RuleRow[];
  /** 卡二：用例草稿概览。 */
  draft: { cases: DraftCaseRow[]; uncoveredCount: number } | null;
  /** 卡三：执行进度与结论摘要。 */
  run: RunState | null;
  /** 卡四：覆盖收口摘要。 */
  coverage: CoverageState | null;
  /** 有旅程工具在飞（`tool_call` 已出、`tool_result` 未回）→ 卡上的活点。 */
  pending: JourneyTool | null;
  /** 工具明说自己失败了，或返回的根本不是业务数据。**不猜、不补默认值。** */
  errors: JourneyError[];
}

/** 什么都还没发生。四张卡一张都不渲染。 */
export const EMPTY_JOURNEY_STATE: JourneyState = {
  batchId: "",
  rules: [],
  draft: null,
  run: null,
  coverage: null,
  pending: null,
  errors: [],
};

/**
 * 两张流式卡在「工具还在飞、结果没回」时就要出来——那正是流式的价值所在：
 * 别让人盯着空屏等。其余工具在飞不出卡（§6f 的表里它们不是流式卡）。
 */
const LIVE_CARD_TOOLS = new Set<JourneyTool>(["draft_cases", "execute", "project"]);

/** 这一轮有没有任何一张卡可渲染。空状态下整块早退，不留空壳。 */
export function hasJourneyContent(state: JourneyState): boolean {
  return (
    state.rules.length > 0 ||
    state.draft !== null ||
    state.run !== null ||
    state.coverage !== null ||
    state.errors.length > 0 ||
    (state.pending !== null && LIVE_CARD_TOOLS.has(state.pending))
  );
}

// ── 解析 ───────────────────────────────────────────────────────────────────

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function num(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function countMap(value: unknown): Record<string, number> {
  const src = asRecord(value);
  if (!src) return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(src)) {
    const n = Number(v);
    if (Number.isFinite(n)) out[k] = n;
  }
  return out;
}

/**
 * 工具返回体。**只接 JSON 对象**——DT 的 MCP 适配器在未连接 / 超时 / 半截三种
 * 失败下返回的是普通字符串且看起来像成功（`services/mcp/manager.py:416-441`），
 * 所以判不出就当通道错误，绝不返回半个业务对象。这一层与 extensions 侧的
 * `mcp_payload.parse_mcp_payload` 是同一条纪律的两端。
 */
function parseToolPayload(content: unknown): Record<string, unknown> | null {
  if (typeof content !== "string") return asRecord(content);
  const text = content.trim();
  if (!text || text.startsWith("(MCP ")) return null;
  if (text[0] !== "{" && text[0] !== "[") return null;
  try {
    return asRecord(JSON.parse(text));
  } catch {
    return null;
  }
}

interface Mutable {
  batchId: string;
  rules: RuleRow[];
  draft: JourneyState["draft"];
  run: RunState | null;
  coverage: CoverageState | null;
  errors: JourneyError[];
  inFlight: number;
  lastCall: JourneyTool | null;
  executeCount: number;
}

function applyClarify(m: Mutable, payload: Record<string, unknown>): boolean {
  const frame = asRecord(payload.business_frame);
  if (!frame) return false;
  m.rules = asArray(frame.rules).flatMap((raw) => {
    const rule = asRecord(raw);
    if (!rule) return [];
    return [
      {
        rule_id: str(rule.rule_id),
        statement: str(rule.statement),
        source_quote: str(rule.source_quote) || undefined,
        probing: Boolean(rule.probing),
      },
    ];
  });
  return true;
}

function applyDraft(m: Mutable, payload: Record<string, unknown>): boolean {
  const draft = asRecord(payload.case_draft);
  if (!draft) return false;
  const cases = asArray(draft.cases).flatMap((raw) => {
    const c = asRecord(raw);
    if (!c) return [];
    const anchor = asRecord(c.source_anchor) ?? {};
    return [
      {
        case_id: str(c.case_id),
        title: str(c.title),
        probing: Boolean(anchor.probing ?? c.probing),
      },
    ];
  });
  m.draft = {
    cases,
    uncoveredCount: asArray(draft.uncovered_rules).length,
  };
  return true;
}

function applyExecute(m: Mutable, payload: Record<string, unknown>): boolean {
  const receipt = asRecord(payload.receipt);
  const runId = str(payload.run_id);
  if (!runId && !receipt) return false;
  m.executeCount += 1;
  m.run = {
    run_id: runId || str(receipt?.run_id),
    selected: num(receipt?.selected_case_count),
    counts: countMap(receipt?.counts),
    // 新一趟从零开始：上一趟的投影不属于这一趟。
    distribution: {},
    triggered_by: str(payload.triggered_by ?? receipt?.triggered_by) || "fresh",
    target_drift: Boolean(receipt?.target_drift),
    attempt: m.executeCount,
    projected: false,
  };
  return true;
}

function applyProject(m: Mutable, payload: Record<string, unknown>): boolean {
  const projection = asRecord(payload.projection);
  if (!projection) return false;
  const distribution = countMap(projection.distribution);
  if (!m.run) {
    // 只投影没执行（翻历史、或执行在上一轮）：也要能出卡，
    // 但 run_id 与收据字段如实为空，不拿默认值冒充。
    m.executeCount += 1;
    m.run = {
      run_id: "",
      selected: 0,
      counts: {},
      distribution,
      triggered_by: "",
      target_drift: false,
      attempt: m.executeCount,
      projected: true,
    };
    return true;
  }
  m.run = { ...m.run, distribution, projected: true };
  return true;
}

function applyCoverage(m: Mutable, payload: Record<string, unknown>): boolean {
  const ledger = asRecord(payload.coverage_ledger);
  if (!ledger) return false;
  const rules: CoverageRuleRow[] = [];
  const cases: CoverageCaseRow[] = [];
  for (const raw of asArray(ledger.rules)) {
    const row = asRecord(raw);
    if (!row) continue;
    rules.push({
      rule_id: str(row.rule_id),
      statement: str(row.statement),
      status: str(row.status),
      gap_reason: str(row.gap_reason),
    });
    for (const rawCase of asArray(row.cases)) {
      const c = asRecord(rawCase);
      if (!c) continue;
      cases.push({
        case_id: str(c.case_id),
        title: str(c.title),
        probing: Boolean(c.probing),
        verdict: str(c.verdict),
      });
    }
  }
  const summary = asRecord(ledger.summary) ?? {};
  m.coverage = {
    rules,
    cases,
    summary: {
      total_rules: num(summary.total_rules) || rules.length,
      covered: num(summary.covered),
      declared_uncovered: num(summary.declared_uncovered),
      gap_unexplained: num(summary.gap_unexplained),
    },
    done: Boolean(ledger.done),
    problems: asArray(ledger.problems).map(str),
  };
  return true;
}

/** 一个 `tool_result` 事件 → 状态变更。返回是否真的改了东西。 */
function applyResult(m: Mutable, tool: JourneyTool, content: unknown): boolean {
  const payload = parseToolPayload(content);
  if (!payload) {
    m.errors = [
      ...m.errors,
      {
        tool,
        code: "E_MCP_UNAVAILABLE",
        message: str(content).slice(0, 200),
      },
    ];
    return true;
  }
  const batchId = str(payload.batch_id);
  let changed = false;
  if (batchId && batchId !== m.batchId) {
    m.batchId = batchId;
    changed = true;
  }
  const code = str(payload.code);
  if (payload.ok === false || (code && code !== "OK")) {
    m.errors = [
      ...m.errors,
      { tool, code: code || "E_UNKNOWN", message: str(payload.message) },
    ];
    return true;
  }
  switch (tool) {
    case "clarify":
      return applyClarify(m, payload) || changed;
    case "draft_cases":
      return applyDraft(m, payload) || changed;
    case "execute":
      return applyExecute(m, payload) || changed;
    case "project":
      return applyProject(m, payload) || changed;
    case "coverage":
      return applyCoverage(m, payload) || changed;
    default:
      // ingest / analyze / adopt / compile / 读接口不喂任何一张卡：
      // 它们的产物属于工作台（§6f 的分工表）。这里刻意什么都不做。
      return changed;
  }
}

function snapshot(m: Mutable): JourneyState {
  return {
    batchId: m.batchId,
    rules: m.rules,
    draft: m.draft,
    run: m.run,
    coverage: m.coverage,
    pending: m.inFlight > 0 ? m.lastCall : null,
    errors: m.errors,
  };
}

export interface JourneyAccumulator {
  /**
   * 喂进本轮的 capability 与当前完整事件数组，拿回要渲染的状态。
   *
   * **capability 闸在这里，不在组件里**（§6c 的唯一硬红线：没选模式时零测试
   * 元素）。放在这一层是为了它可被机械断言——写在组件的 JSX 分支里就只能靠人
   * 看，而"建完没单独验作用域"正是这条线已经栽过四次的地方。
   *
   * 返回 `null` 有两种情形，对用户是同一种体验（什么都不出现）：
   * 不是「测试」模式，或这一轮还没有任何卡可渲染。
   */
  push(
    capability: string | undefined | null,
    events: readonly StreamEvent[] | undefined,
  ): JourneyState | null;
  /** 已解析到第几个事件。 */
  readonly processed: number;
  /** 累计**检查过**多少个事件。增量性的可核验面：喂 N 次应当接近 N 而不是 N²/2。 */
  readonly examined: number;
}

export function createJourneyAccumulator(): JourneyAccumulator {
  let m: Mutable = freshMutable();
  let processed = 0;
  let examined = 0;
  let tail: StreamEvent | null = null;
  let state: JourneyState = EMPTY_JOURNEY_STATE;

  function freshMutable(): Mutable {
    return {
      batchId: "",
      rules: [],
      draft: null,
      run: null,
      coverage: null,
      errors: [],
      inFlight: 0,
      lastCall: null,
      executeCount: 0,
    };
  }

  return {
    get processed() {
      return processed;
    },
    get examined() {
      return examined;
    },
    push(capability, events) {
      if (capability !== TEST_CAPABILITY) {
        // 模式换走了就把状态清干净：留着等于下次切回来接上一条别的流。
        if (processed !== 0) {
          m = freshMutable();
          processed = 0;
          tail = null;
          state = EMPTY_JOURNEY_STATE;
        }
        return null;
      }
      const list = events ?? [];
      // 上次那条尾事件还在原位 = 这是同一个数组的延长，接着往下解析。
      // 对不上（重放 / 翻历史 / 编辑分支换了整条流）就从头来，
      // 宁可慢一次也不要把两条流的状态混在一起。
      const continues =
        processed === 0 || (processed <= list.length && list[processed - 1] === tail);
      if (!continues) {
        m = freshMutable();
        processed = 0;
        state = EMPTY_JOURNEY_STATE;
      }
      if (processed === list.length) {
        return hasJourneyContent(state) ? state : null;
      }

      let changed = false;
      for (let i = processed; i < list.length; i += 1) {
        examined += 1;
        const event = list[i];
        if (!event) continue;
        if (event.type === "tool_call") {
          const tool = journeyToolOf(event.content) ?? journeyToolOf(
            (event.metadata as Record<string, unknown> | undefined)?.tool,
          );
          if (tool) {
            m.lastCall = tool;
            m.inFlight += 1;
            changed = true;
          }
          continue;
        }
        if (event.type !== "tool_result") continue;
        const tool = journeyToolOf(
          (event.metadata as Record<string, unknown> | undefined)?.tool,
        );
        if (!tool) continue;
        if (m.inFlight > 0) m.inFlight -= 1;
        // 结果落地至少会改活点状态，所以这里恒为真；`applyResult` 的返回值
        // 仍单独取，是为了它自己那几条「解析不出就记错误、不猜」的分支保持可读。
        applyResult(m, tool, event.content);
        changed = true;
      }
      processed = list.length;
      tail = processed > 0 ? list[processed - 1] : null;
      if (changed) state = snapshot(m);
      return hasJourneyContent(state) ? state : null;
    },
  };
}

/**
 * 一次性解析（不做增量）。给测试与非 React 调用方用；组件走累加器。
 */
export function extractJourneyState(
  events: readonly StreamEvent[] | undefined,
  capability: string = TEST_CAPABILITY,
): JourneyState | null {
  return createJourneyAccumulator().push(capability, events);
}
