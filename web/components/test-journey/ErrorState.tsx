"use client";

// [fork] 四种异常态的统一呈现（交互稿 s8）。
//
// 模子只有一个：**说清是什么坏了 + 用户还能走哪条路**。缺任何一半都不算做完。
// 所以本组件强制两个字段（what / next），没有"只报错不给出路"的调用方式。
//
// 文案按**互斥错误码**取，不按字符串猜（设计稿 §4.3）。三对最容易被合并的：
//   · 通道断（MCP_UNAVAILABLE）vs 进程不在（GATEWAY_DOWN）
//   · 字段没要到（ORACLE_FIELD_MISSING）vs 正文确实为空（ORACLE_BODY_EMPTY）
//   · trace 打不开（TRACE_MISSING/…）vs 本轨压根没有 trace（TRACE_NOT_APPLICABLE）
// 每一对合并了都违反 0021 红线六。

import type { TFunction } from "i18next";
import { AlertTriangle, Info } from "lucide-react";
import { useTranslation } from "react-i18next";

import { JourneyErrorCode } from "@/components/test-journey/client";

interface Explained {
  what: string;
  next: string;
  /** 不是故障、只是"本来就没有"的情形，用中性色而不是警告色。 */
  neutral?: boolean;
}

/**
 * 码 → 文案。**按互斥错误码取，不按字符串猜**（设计稿 §4.3）。
 * 取的时机是渲染时而不是模块加载时——加载时 `t` 还没有语言。
 */
function explanations(t: TFunction): Record<string, Explained> {
  return {
    [JourneyErrorCode.GATEWAY_DOWN]: {
      what: t(
        "The test partner service process is not running. This is not “connected but erroring” — the resident process on the host never started.",
      ),
      next: t(
        "Run extensions/test-partner/scripts/start_server.cmd on the host to bring it up, then reload this page.",
      ),
    },
    [JourneyErrorCode.MCP_UNAVAILABLE]: {
      what: t(
        "The service process is up, but DeepTutor's MCP channel to it is down. Two separate things: the process is alive, the channel is not.",
      ),
      next: t(
        "Disable and re-enable the test-partner entry on the MCP Services page to force a reconnect and re-fetch the tool list.",
      ),
    },
    [JourneyErrorCode.ORACLE_FETCH_FAILED]: {
      what: t(
        "Fetching the TAPD requirement failed — the request went out, nothing usable came back.",
      ),
      next: t(
        "Check that the TAPD MCP entry is connected and the token has not expired, then retry. No batch is created until the requirement body is in hand.",
      ),
    },
    [JourneyErrorCode.ORACLE_NOT_FOUND]: {
      what: t("TAPD has no such requirement. The story ID or the workspace does not match."),
      next: t("Check the story ID and workspace ID, then try again."),
    },
    [JourneyErrorCode.ORACLE_FORBIDDEN]: {
      what: t(
        "TAPD refused this read — the token has no permission on this workspace.",
      ),
      next: t(
        "Switch to a token with read access to that workspace and save it again on the settings page.",
      ),
    },
    [JourneyErrorCode.ORACLE_FIELD_MISSING]: {
      what: t(
        "The fetched requirement row has no body field. **This is a fetch-side problem, not a missing write-up** — most likely the field allowlist omits description, or the parameters changed when the channel changed.",
      ),
      next: t(
        "Engineering has to fix this one; do not chase the requirement author. Report this error together with the story ID to whoever owns the fetch path.",
      ),
    },
    [JourneyErrorCode.ORACLE_BODY_EMPTY]: {
      what: t(
        "The field came back and the requirement body **really is empty**. This story has nothing but a title in TAPD.",
      ),
      next: t(
        "Ask the requirement author to write the body. An empty body cannot serve as criteria — using it as the oracle would build every later verdict on a title.",
      ),
    },
    [JourneyErrorCode.ORACLE_DRIFT]: {
      what: t(
        "The requirement changed after you clarified it. Freezing cases against the old snapshot would point the criteria at a requirement that no longer exists.",
      ),
      next: t(
        "Clarify again before adopting. There is deliberately no “continue anyway” option here — with that switch, this gate would not exist.",
      ),
    },
    [JourneyErrorCode.GATE_REQUIRED]: {
      what: t("This action needs a journey ticket, and this turn does not have one."),
      next: t(
        "Go back to chat, pick the Test mode and start a journey — batches are created there.",
      ),
    },
    [JourneyErrorCode.NO_BATCH]: {
      what: t("This batch cannot be found."),
      next: t(
        "Pick an existing batch from the list page, or start a new journey from the Test mode.",
      ),
    },
    [JourneyErrorCode.TRACE_NOT_APPLICABLE]: {
      what: t(
        "This attempt ran on the API track, which **by design produces no trace** — nothing is broken, there is simply nothing to open.",
      ),
      next: t(
        "Evidence on the API track is the HTTP exchange and the assertion results; read them row by row in the result table.",
      ),
      neutral: true,
    },
    [JourneyErrorCode.TRACE_MISSING]: {
      what: t("This case left no trace file behind."),
      next: t(
        "Run it again; if there is still no trace, check the execution log to see whether recording was turned off.",
      ),
    },
    [JourneyErrorCode.TRACE_VIEWER_MISSING]: {
      what: t(
        "The trace file is there, but this machine has no viewer installed to open it. That is a different thing from “the file is gone”.",
      ),
      next: t(
        "Open it locally with the command below, or install Playwright first and then use the button.",
      ),
    },
    [JourneyErrorCode.TRACE_SPAWN_FAILED]: {
      what: t("The viewer is installed but did not start this time."),
      next: t("Open it manually with the command below."),
    },
  };
}

export function explain(t: TFunction, code: string, fallback?: string): Explained {
  return (
    explanations(t)[code] ?? {
      what: fallback || t("An unclassified error occurred."),
      next: t("Report this error code to engineering: {{code}}", { code }),
    }
  );
}

interface Props {
  code: string;
  message?: string;
  /** 补充信息（命令、路径等），原样等宽显示。 */
  detail?: string;
  onRetry?: () => void;
  retryLabel?: string;
}

export default function ErrorState({ code, message, detail, onRetry, retryLabel }: Props) {
  const { t } = useTranslation();
  const { what, next, neutral } = explain(t, code, message);
  const Icon = neutral ? Info : AlertTriangle;
  const tone = neutral
    ? "border-[var(--border)] bg-[var(--muted)]"
    : "border-amber-300 bg-amber-50 dark:border-amber-800/60 dark:bg-amber-950/30";
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <div className="flex gap-3">
        <Icon
          className={`mt-0.5 h-4 w-4 shrink-0 ${
            neutral ? "text-[var(--muted-foreground)]" : "text-amber-600 dark:text-amber-400"
          }`}
        />
        <div className="min-w-0 space-y-2">
          <p className="text-sm text-[var(--foreground)]">{what}</p>
          <p className="text-sm text-[var(--muted-foreground)]">{next}</p>
          {detail ? (
            <code className="block overflow-x-auto rounded-md bg-[var(--muted)] px-2 py-1.5 text-xs text-[var(--foreground)]">
              {detail}
            </code>
          ) : null}
          {message && message !== what ? (
            <p className="text-xs text-[var(--muted-foreground)]">
              {t("Server said: {{message}}", { message })}
            </p>
          ) : null}
          <p className="text-xs text-[var(--muted-foreground)]">
            {t("Error code {{code}}", { code })}
          </p>
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="mt-1 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
            >
              {retryLabel || t("Retry")}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
