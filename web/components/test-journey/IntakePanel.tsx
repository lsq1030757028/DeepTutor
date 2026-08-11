"use client";

// [fork] 需求接入面（交互稿 s5，M2 新增）。
//
// **三段折叠，不是三步向导**——任一段做完停下都算数（地基：任意前缀是合法交付）。
// 每段只给"该段产出了什么 + 入口"，**不在这里第二次呈现规则本身**：
// 规则的唯一权威视图是规则表（0021 必改 2）。在这里再列一遍规则，
// 就是把刚合并掉的三重投影又加回来一重。
//
// 澄清的问答发生在聊天里，这一屏只报"澄清出了几条规则"。

import { ChevronRight, ExternalLink } from "lucide-react";
import { useState } from "react";

interface SectionState {
  present: boolean;
  summary: string;
}

interface Props {
  batchId: string;
  oracle: SectionState & {
    storyId?: string;
    workspaceId?: string;
    title?: string;
    fetchedAt?: string;
    digest?: string;
  };
  clarify: SectionState & { ruleCount?: number; probingCount?: number };
  analysis: SectionState & { exampleCount?: number };
  onContinueInChat?: () => void;
}

function Section({
  label,
  state,
  children,
  defaultOpen,
}: {
  label: string;
  state: SectionState;
  children?: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  return (
    <div className="rounded-lg border border-[var(--border)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-[var(--muted)]"
        aria-expanded={open}
      >
        <ChevronRight
          className={`h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)] transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${
            state.present ? "bg-emerald-500" : "bg-[var(--border)]"
          }`}
          aria-hidden
        />
        <span className="flex-1 text-sm font-medium text-[var(--foreground)]">{label}</span>
        <span className="text-xs text-[var(--muted-foreground)]">{state.summary}</span>
      </button>
      {open && children ? (
        <div className="border-t border-[var(--border)] px-3 py-2.5 pl-9">{children}</div>
      ) : null}
    </div>
  );
}

export default function IntakePanel({
  batchId,
  oracle,
  clarify,
  analysis,
  onContinueInChat,
}: Props) {
  return (
    <div className="space-y-2">
      <Section label="① 从 TAPD 拉需求" state={oracle} defaultOpen>
        {oracle.present ? (
          <dl className="space-y-1 text-xs text-[var(--muted-foreground)]">
            <div>
              <dt className="inline">需求：</dt>
              <dd className="inline text-[var(--foreground)]">{oracle.title || "—"}</dd>
            </div>
            <div>
              <dt className="inline">出处：</dt>
              <dd className="inline font-mono">
                {oracle.workspaceId}/{oracle.storyId}
              </dd>
            </div>
            <div>
              <dt className="inline">快照时间：</dt>
              <dd className="inline font-mono">{oracle.fetchedAt || "—"}</dd>
            </div>
            <div>
              <dt className="inline">内容摘要：</dt>
              {/* digest 是漂移闸比对的对象——需求被改一个字，采纳就会被挡下 */}
              <dd className="inline break-all font-mono">{oracle.digest || "—"}</dd>
            </div>
          </dl>
        ) : (
          <p className="text-xs text-[var(--muted-foreground)]">
            还没有拉过需求。在聊天里选「测试」模式，给一个需求号就会拉取并冻结一份快照。
          </p>
        )}
      </Section>

      <Section label="② 澄清记录" state={clarify}>
        <p className="text-xs text-[var(--muted-foreground)]">
          {clarify.present
            ? `澄清出 ${clarify.ruleCount ?? 0} 条规则，其中 ${
                clarify.probingCount ?? 0
              } 条是探测性（正文撑不住，不进判据）。规则本身在「规则」页签看——那是它的唯一权威视图。`
            : "还没有澄清。问答发生在聊天里，这一屏只报结果。"}
        </p>
      </Section>

      <Section label="③ 测试分析" state={analysis}>
        <p className="text-xs text-[var(--muted-foreground)]">
          {analysis.present
            ? `逐规则给出了 ${analysis.exampleCount ?? 0} 个判别 Example。`
            : "还没有分析。"}
        </p>
      </Section>

      {onContinueInChat ? (
        <button
          type="button"
          onClick={onContinueInChat}
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1.5 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          带着这个批次继续对话
          <span className="font-mono text-[10px] text-[var(--muted-foreground)]">{batchId}</span>
        </button>
      ) : null}
    </div>
  );
}
