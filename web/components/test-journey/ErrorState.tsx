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

import { AlertTriangle, Info } from "lucide-react";

import { JourneyErrorCode } from "@/components/test-journey/client";

interface Explained {
  what: string;
  next: string;
  /** 不是故障、只是"本来就没有"的情形，用中性色而不是警告色。 */
  neutral?: boolean;
}

const EXPLANATIONS: Record<string, Explained> = {
  [JourneyErrorCode.GATEWAY_DOWN]: {
    what: "测试伙伴的服务进程不在。不是「连上了但出错」，是宿主机上那个常驻进程压根没起来。",
    next: "在宿主机上跑 extensions/test-partner/scripts/start_server.cmd 把它拉起来，再刷新本页。",
  },
  [JourneyErrorCode.MCP_UNAVAILABLE]: {
    what: "服务进程在，但 DeepTutor 到它的 MCP 通道断了。两件事分开说：进程活着，通道没通。",
    next: "去 MCP Services 页面把 test-partner 这条停用再启用一次，强制重连并重新拉取工具清单。",
  },
  [JourneyErrorCode.ORACLE_FETCH_FAILED]: {
    what: "TAPD 需求拉取失败——请求发出去了，没拿回可用的内容。",
    next: "确认 TAPD 那条 MCP 条目连着、令牌没过期，然后重试。需求正文没拿到之前不会建批次。",
  },
  [JourneyErrorCode.ORACLE_NOT_FOUND]: {
    what: "TAPD 里没有这条需求。需求号或工作区对不上。",
    next: "核对需求号与工作区 ID 再试一次。",
  },
  [JourneyErrorCode.ORACLE_FORBIDDEN]: {
    what: "TAPD 拒绝了这次读取——令牌对这个工作区没有权限。",
    next: "换一个有该工作区读权限的令牌，在配置页重新保存。",
  },
  [JourneyErrorCode.ORACLE_FIELD_MISSING]: {
    what: "取回的需求行里没有正文字段。**这是取数侧的问题，不是需求方没写正文**——"
      + "多半是字段白名单没带上 description，或者换通道后参数口径变了。",
    next: "这条要工程侧修，不用去催需求。把这条错误连同需求号报给负责取数的人。",
  },
  [JourneyErrorCode.ORACLE_BODY_EMPTY]: {
    what: "字段要到了，需求正文**确实是空的**。这条需求在 TAPD 里只有标题。",
    next: "找需求方补正文。空正文不能当判据来源——拿它当 oracle 会让后面所有结论建立在标题上。",
  },
  [JourneyErrorCode.ORACLE_DRIFT]: {
    what: "需求在你澄清之后被改过了。用旧快照冻结用例，会让判据指向一份已经不存在的需求。",
    next: "重新澄清一遍再采纳。这里没有「照旧继续」的选项——有那个开关，这道闸就等于不存在。",
  },
  [JourneyErrorCode.GATE_REQUIRED]: {
    what: "这个动作需要一张旅程门票，当前这一轮没有。",
    next: "回聊天选「测试」模式开一趟旅程，批次从那里创建。",
  },
  [JourneyErrorCode.NO_BATCH]: {
    what: "找不到这个批次。",
    next: "回列表页挑一个既有批次，或从「测试」模式新开一趟。",
  },
  [JourneyErrorCode.TRACE_NOT_APPLICABLE]: {
    what: "这一趟走的是接口轨，**按设计就没有 trace**——不是打不开，是本来就不产。",
    next: "接口轨的证据是 HTTP 报文与断言结果，在结果表里逐条看。",
    neutral: true,
  },
  [JourneyErrorCode.TRACE_MISSING]: {
    what: "这条用例没有留下 trace 文件。",
    next: "重跑一趟；如果重跑后仍然没有，看执行日志确认录制是否被关掉了。",
  },
  [JourneyErrorCode.TRACE_VIEWER_MISSING]: {
    what: "trace 文件在，但本机没装能打开它的查看器。这与「文件不见了」是两回事。",
    next: "用下面这条命令在本机打开，或先装 Playwright 再用按钮。",
  },
  [JourneyErrorCode.TRACE_SPAWN_FAILED]: {
    what: "查看器装了，但这次没起来。",
    next: "用下面这条命令手动打开。",
  },
};

export function explain(code: string, fallback?: string): Explained {
  return (
    EXPLANATIONS[code] ?? {
      what: fallback || "发生了一个没有归类的错误。",
      next: "把这条错误码报给工程侧：" + code,
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
  const { what, next, neutral } = explain(code, message);
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
            <p className="text-xs text-[var(--muted-foreground)]">服务端原话：{message}</p>
          ) : null}
          <p className="text-xs text-[var(--muted-foreground)]">错误码 {code}</p>
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="mt-1 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
            >
              {retryLabel || "重试"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
