// [fork] 旅程薄壳的唯一通道封装（设计稿 §4.2 / ADR-M2-01 第 2 条）。
//
// 浏览器只走 DT 的专用 authenticated read router。通用 plugin tool executor
// 无法绑定资源 owner，也会把任意 MCP 原语暴露给页面，因此不属于合法 Journey 通道。
//
// **禁止**在 web/ 全树出现 3789 或 host.docker.internal（ADR-M2-01 G3 有断言守着）。

// 本模块不是组件、拿不到 `useTranslation`，所以用全局实例上的 `i18n.t`——
// 本仓既有写法（`components/memory/useMemoryRun.ts`、`context/UnifiedChatContext.tsx`）。
import i18n from "i18next";

import { apiFetch, apiUrl } from "@/lib/api";

/** MCP 服务器条目名。换条目名时只改这里。 */
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

/**
 * 把 DT tool-execute 的信封解成业务对象。
 *
 * 这一层是「错误当数据」防线的前端半边（总则 7）：MCP 未连接/超时时适配器返回的是
 * **普通字符串**且 `success=true`，不判就会把一句人话当成业务数据渲染出去。
 */
async function readJourney(
  path: string,
  init: RequestInit = { method: "GET" },
): Promise<JourneyEnvelope> {
  let response: Response;
  try {
    response = await apiFetch(apiUrl(path), init);
  } catch (error) {
    // 连 DT 后端都没连上——这与「连上了但 MCP 断」是两种病，说法必须不同。
    return { ok: false, code: JourneyErrorCode.GATEWAY_DOWN,
      message: i18n.t("Cannot reach the backend: {{message}}", {
        message: (error as Error).message,
      }) };
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
  if (!payload || typeof payload !== "object") {
    return { ok: false, code: JourneyErrorCode.MCP_UNAVAILABLE,
      message: i18n.t("The returned JSON is a bare scalar, not a business payload.") };
  }
  const body = payload as Record<string, unknown>;
  return {
    ok: body.ok === true,
    code: String(body.code ?? (body.ok === true ? "OK" : "E_UNKNOWN")),
    message: typeof body.message === "string" ? body.message : undefined,
    detail: body.detail,
    data: body,
  };
}

export function listJourneyBatches(): Promise<JourneyEnvelope> {
  return readJourney("/api/v1/test-journey/batches");
}

export function getJourneyBatch(batchId: string): Promise<JourneyEnvelope> {
  return readJourney(`/api/v1/test-journey/batches/${encodeURIComponent(batchId)}`);
}

export function openJourneyTrace(
  batchId: string,
  runId: string,
  traceRel: string,
): Promise<JourneyEnvelope> {
  return readJourney(
    `/api/v1/test-journey/batches/${encodeURIComponent(batchId)}` +
      `/runs/${encodeURIComponent(runId)}/trace`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trace_rel: traceRel }),
    },
  );
}
