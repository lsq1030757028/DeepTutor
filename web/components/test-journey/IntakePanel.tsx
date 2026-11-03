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
import { useTranslation } from "react-i18next";

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
  const { t } = useTranslation();
  return (
    <div className="space-y-2">
      <Section label={t("① Pull the requirement from TAPD")} state={oracle} defaultOpen>
        {oracle.present ? (
          <dl className="space-y-1 text-xs text-[var(--muted-foreground)]">
            <div>
              <dt className="inline">{t("journey.intake.requirement")}</dt>
              <dd className="inline text-[var(--foreground)]">{oracle.title || "—"}</dd>
            </div>
            <div>
              <dt className="inline">{t("journey.intake.origin")}</dt>
              <dd className="inline font-mono">
                {oracle.workspaceId}/{oracle.storyId}
              </dd>
            </div>
            <div>
              <dt className="inline">{t("journey.intake.snapshotTime")}</dt>
              <dd className="inline font-mono">{oracle.fetchedAt || "—"}</dd>
            </div>
            <div>
              <dt className="inline">{t("journey.intake.contentDigest")}</dt>
              {/* digest 是漂移闸比对的对象——需求被改一个字，采纳就会被挡下 */}
              <dd className="inline break-all font-mono">{oracle.digest || "—"}</dd>
            </div>
          </dl>
        ) : (
          <p className="text-xs text-[var(--muted-foreground)]">
            {t(
              "No requirement pulled yet. Pick the Test mode in chat and give a story ID; it will be fetched and frozen into a snapshot.",
            )}
          </p>
        )}
      </Section>

      <Section label={t("② Clarification record")} state={clarify}>
        <p className="text-xs text-[var(--muted-foreground)]">
          {clarify.present
            ? t(
                "Clarified {{rules}} rules, of which {{probing}} are probing (unsupported by the body, excluded from criteria). The rules themselves live in the Rules tab — their single authoritative view.",
                { rules: clarify.ruleCount ?? 0, probing: clarify.probingCount ?? 0 },
              )
            : t(
                "No clarification yet. The Q&A happens in chat; this screen only reports the outcome.",
              )}
        </p>
      </Section>

      <Section label={t("③ Test analysis")} state={analysis}>
        <p className="text-xs text-[var(--muted-foreground)]">
          {analysis.present
            ? t("{{count}} discriminating examples given, rule by rule.", {
                count: analysis.exampleCount ?? 0,
              })
            : t("No analysis yet.")}
        </p>
      </Section>

      {onContinueInChat ? (
        <button
          type="button"
          onClick={onContinueInChat}
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1.5 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          {t("Continue the conversation with this batch")}
          <span className="font-mono text-[10px] text-[var(--muted-foreground)]">{batchId}</span>
        </button>
      ) : null}
    </div>
  );
}
