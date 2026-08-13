"use client";

// [fork] trace 打开（交互稿 s4 的「回放」列 / s8 的第四种异常态）。
//
// **四种状态，一种都不许合并**：
//   1. 有 trace 且能开 → 按钮 + 降级命令（命令始终显示，ADR-M1-02 §2.3 路径 2）
//   2. E_TRACE_MISSING        这条用例没留下 trace
//   3. E_TRACE_VIEWER_MISSING 文件在，本机没装查看器
//   4. E_TRACE_SPAWN_FAILED   装了但这次没起来
//   5. E_TRACE_NOT_APPLICABLE **本趟是接口轨，按设计就没有 trace**
//
// 第 5 种是 M2 加的，也是最容易被合并掉的一种：接口轨的 `run_api_case` 不起浏览器、
// 不开 tracing，所以根本不产 trace.zip。把"本来就没有"显示成"打不开"，
// 正是交互稿点名的错法（「没装」与「装了但坏了」要分开说，同理）。

import { PlayCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import ErrorState, { explain } from "@/components/test-journey/ErrorState";
import { JourneyErrorCode } from "@/components/test-journey/client";

interface Props {
  /** 本趟的轨道。API 轨直接进第 5 种状态，连试都不该试。 */
  track: "api" | "ui" | "";
  traceRel?: string | null;
  command?: string;
  /** 已知的失败码（点过按钮之后才有）。 */
  errorCode?: string;
  onOpen?: () => void;
  /** 表格单元格里用：只出一行字，不出整块异常卡。 */
  compact?: boolean;
}

export default function TraceLauncher({
  track,
  traceRel,
  command,
  errorCode,
  onOpen,
  compact,
}: Props) {
  const { t } = useTranslation();
  const fallback = (code: string, short: string) =>
    compact ? (
      <span
        title={explain(t, code).next}
        className="text-xs text-[var(--muted-foreground)]"
      >
        {short}
      </span>
    ) : (
      <ErrorState code={code} detail={command} />
    );

  // 接口轨：先于一切判断。不产 trace 不是故障，别让用户去点一个注定失败的按钮。
  if (track === "api") {
    return fallback(
      JourneyErrorCode.TRACE_NOT_APPLICABLE,
      t("journey.trace.notApplicable"),
    );
  }
  if (errorCode) {
    return fallback(errorCode, t("journey.trace.cannotOpen"));
  }
  if (!traceRel) {
    // 轨道判不出来时也走这条：说「没有留下回放」而不是断言它坏了
    return fallback(JourneyErrorCode.TRACE_MISSING, t("journey.trace.noFile"));
  }
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onOpen}
        className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
      >
        <PlayCircle className="h-3.5 w-3.5" />
        {t("Open trace")}
      </button>
      {command ? (
        // 降级路径始终在场：按钮依赖本机装了查看器，命令谁都能复制
        <code className="block overflow-x-auto rounded-md bg-[var(--muted)] px-2 py-1 text-[11px] text-[var(--muted-foreground)]">
          {command}
        </code>
      ) : null}
    </div>
  );
}
