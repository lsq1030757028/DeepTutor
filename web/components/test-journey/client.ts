// [fork] 旅程薄壳的唯一通道封装（设计稿 §4.2 / ADR-M2-01 第 2 条）。
//
// 交互稿 §10 写「薄壳全部只调 extensions 的接口」——**按当版机制这句话没有既有实现
// 路径**：浏览器跨源到宿主 3789 被 CORS 挡（gateway 无 CORS 中间件、POST 预检 405），
// 容器内后端到 3789 又被回环绑定挡。所以薄壳走 DT 既有的
// `POST /api/v1/plugins/tools/mcp_<server>_journey_<tool>/execute`：相对路径、
// 经 web/proxy.ts rewrite、同源、零 CORS、零新增后端路由。
// 语义上那句话仍然成立——业务逻辑确实全在 extensions 的 MCP 工具里，
// 只是换了一条到达它的合法通道。
//
// **禁止**在 web/ 全树出现 3789 或 host.docker.internal（ADR-M2-01 G3 有断言守着）。

// 本模块不是组件、拿不到 `useTranslation`，所以用全局实例上的 `i18n.t`——
// 本仓既有写法（`components/memory/useMemoryRun.ts`、`context/UnifiedChatContext.tsx`）。
import i18n from "i18next";

import { apiFetch, apiUrl } from "@/lib/api";

/** MCP 服务器条目名。换条目名时只改这里。 */
export const JOURNEY_SERVER = "test-partner";

/**
 * 互斥错误码。前端**按码取文案，不按字符串猜**——
 * 交互稿三处红线（后端不可达两种说法必须不同 / 需求拉不到三种 / trace 打不开三因）
 * 在工程侧就落成这张表。
 */
export const JourneyErrorCode = {
  /** DT ↔ test-partner 的 MCP 通道断了（容器连不上宿主的 MCP 面）。 */
  MCP_UNAVAILABLE: "E_MCP_UNAVAILABLE",
  /** test-partner 进程本身不在（宿主网关没起）。与上一条是两回事，说法必须不同。 */
  GATEWAY_DOWN: "E_GATEWAY_DOWN",
  ORACLE_FETCH_FAILED: "E_ORACLE_FETCH_FAILED",
  ORACLE_NOT_FOUND: "E_ORACLE_NOT_FOUND",
  ORACLE_FORBIDDEN: "E_ORACLE_FORBIDDEN",
  /** 字段没要到（我们的 bug）——与「正文确实为空」分开报，0021 红线六。 */
  ORACLE_FIELD_MISSING: "E_ORACLE_FIELD_MISSING",
  /** 正文确实为空（该找需求方）。 */
  ORACLE_BODY_EMPTY: "E_ORACLE_BODY_EMPTY",
  ORACLE_DRIFT: "E_ORACLE_DRIFT",
  GATE_REQUIRED: "E_GATE_REQUIRED",
  NO_BATCH: "E_NO_BATCH",
  TRACE_MISSING: "E_TRACE_MISSING",
  TRACE_VIEWER_MISSING: "E_TRACE_VIEWER_MISSING",
  TRACE_SPAWN_FAILED: "E_TRACE_SPAWN_FAILED",
  /**
   * 本趟是 API 轨，按设计就没有 trace —— **不是坏了**。
   * 把「本来就没有」说成「打不开」正是交互稿点名的错法。
   */
  TRACE_NOT_APPLICABLE: "E_TRACE_NOT_APPLICABLE",
} as const;

export type JourneyErrorCodeValue =
  (typeof JourneyErrorCode)[keyof typeof JourneyErrorCode];

export interface JourneyEnvelope<T = Record<string, unknown>> {
  ok: boolean;
  code: string;
  message?: string;
  detail?: unknown;
  data?: T;
}

/** MCP 适配器的错误串前缀。它长得像成功（success=true + 普通字符串）。 */
const MCP_ERROR_PREFIX = "(MCP ";

/**
 * 把 DT tool-execute 的信封解成业务对象。
 *
 * 这一层是「错误当数据」防线的前端半边（总则 7）：MCP 未连接/超时时适配器返回的是
 * **普通字符串**且 `success=true`，不判就会把一句人话当成业务数据渲染出去。
 */
export function parseToolEnvelope(raw: unknown): JourneyEnvelope {
  if (raw === null || raw === undefined) {
    return { ok: false, code: JourneyErrorCode.GATEWAY_DOWN,
      message: i18n.t("The backend returned no content.") };
  }
  const envelope = raw as { success?: boolean; content?: unknown; detail?: unknown };
  if (typeof envelope.detail === "string" && envelope.content === undefined) {
    return { ok: false, code: JourneyErrorCode.GATEWAY_DOWN,
      message: String(envelope.detail) };
  }
  const content = envelope.content;
  if (typeof content !== "string" || content.trim() === "") {
    return { ok: false, code: JourneyErrorCode.MCP_UNAVAILABLE,
      message: i18n.t("The MCP channel returned no business data.") };
  }
  if (content.trimStart().startsWith(MCP_ERROR_PREFIX)) {
    return { ok: false, code: JourneyErrorCode.MCP_UNAVAILABLE,
      message: content.slice(0, 200) };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return { ok: false, code: JourneyErrorCode.MCP_UNAVAILABLE,
      message: i18n.t("Response is not valid JSON: {{snippet}}", {
        snippet: content.slice(0, 120),
      }) };
  }
  if (typeof parsed !== "object" || parsed === null) {
    return { ok: false, code: JourneyErrorCode.MCP_UNAVAILABLE,
      message: i18n.t("The returned JSON is a bare scalar, not a business payload.") };
  }
  const body = parsed as Record<string, unknown>;
  return {
    ok: body.ok === true,
    code: String(body.code ?? (body.ok === true ? "OK" : "E_UNKNOWN")),
    message: typeof body.message === "string" ? body.message : undefined,
    detail: body.detail,
    data: body as Record<string, unknown>,
  };
}

/** 调一个 journey 工具。失败一律回统一信封，不抛。 */
export async function callJourney(
  tool: string,
  params: Record<string, unknown> = {},
): Promise<JourneyEnvelope> {
  const path = `/api/v1/plugins/tools/mcp_${JOURNEY_SERVER}_journey_${tool}/execute`;
  let response: Response;
  try {
    response = await apiFetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params }),
    });
  } catch (error) {
    // 连 DT 后端都没连上——这与「连上了但 MCP 断」是两种病，说法必须不同。
    return { ok: false, code: JourneyErrorCode.GATEWAY_DOWN,
      message: i18n.t("Cannot reach the backend: {{message}}", {
        message: (error as Error).message,
      }) };
  }
  if (response.status === 404) {
    return { ok: false, code: JourneyErrorCode.MCP_UNAVAILABLE,
      message: i18n.t(
        "DeepTutor cannot find the tool {{tool}}. Most likely the test-partner MCP entry is not connected — disable and re-enable it on the MCP Services page so the tool list is fetched again.",
        { tool },
      ) };
  }
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, code: JourneyErrorCode.GATEWAY_DOWN,
      message: i18n.t("The backend returned non-JSON (HTTP {{status}}).", {
        status: response.status,
      }) };
  }
  return parseToolEnvelope(payload);
}
