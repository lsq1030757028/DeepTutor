"use client";

// [fork] 批次详情容器（交互稿 s2/s3/s4/s5 + s7 的双向锚）。
//
// 三个页签：账本 / 规则 / 结果。接入面（s5）挂在账本页签下——它回答的是
// "接到什么程度了"，属于账本的上下文，不值得再占一个页签。
//
// s7 双向锚：工作台 →「继续对话」带批次上下文回聊天。
// M1 时这条只是文字承诺（`rg` 过 test-workbench 全是注释与 i18n，零 router.push），
// M2 把它建起来：跳 /home 并把批次号带在 query 上。

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, MessageSquare, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import ArtifactLedger from "@/components/test-journey/ArtifactLedger";
import ErrorState from "@/components/test-journey/ErrorState";
import IntakePanel from "@/components/test-journey/IntakePanel";
import ResultTable from "@/components/test-journey/ResultTable";
import RuleTable from "@/components/test-journey/RuleTable";
import { callJourney } from "@/components/test-journey/client";
import type { RunRow, StepCell } from "@/components/test-journey/types";

type Tab = "ledger" | "rules" | "results";

/** `label` 是 i18n 键，渲染时才 `t()`——模块加载时还没有语言。 */
const TABS: { id: Tab; label: string }[] = [
  { id: "ledger", label: "journey.tab.ledger" },
  { id: "rules", label: "journey.tab.rules" },
  { id: "results", label: "journey.tab.results" },
];

interface BatchPayload {
  batch?: Record<string, unknown>;
  stepper?: StepCell[];
  artifacts?: Record<string, Record<string, unknown>>;
  runs?: RunRow[];
  events?: Record<string, unknown>[];
}

export default function JourneyDetail({ batchId }: { batchId: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("ledger");
  const [payload, setPayload] = useState<BatchPayload | null>(null);
  const [error, setError] = useState<{ code: string; message?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  // 同 JourneyList：`load` 在第一个 await 之前不许 setState（首次由 effect 调用，
  // 在 effect 内同步 setState 会触发级联渲染，本仓该 eslint 规则是 error 级）。
  const load = useCallback(async () => {
    const result = await callJourney("get_batch", { batch_id: batchId });
    if (!result.ok) {
      setError({ code: result.code, message: result.message });
      setPayload(null);
    } else {
      setError(null);
      setPayload(result.data as BatchPayload);
    }
    setLoading(false);
  }, [batchId]);

  const refresh = useCallback(() => {
    setLoading(true);
    void load();
  }, [load]);

  // IIFE + cancelled 旗标：把 setState 挪出 effect 的同步体，满足
  // `react-hooks/set-state-in-effect`（本仓 error 级）。写法照 MemoryPicker.tsx:51-60。
  // cancelled 顺带治了"组件已卸载还回写状态"这个老问题。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await load();
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const continueInChat = useCallback(() => {
    // 双向锚的一半：带批次上下文回聊天。挂载位选 (workspace) 的原始理由就是
    // 这一层原生带 UnifiedChatProvider（0015）。
    router.push(`/home?test_batch=${encodeURIComponent(batchId)}`);
  }, [batchId, router]);

  const artifacts = payload?.artifacts ?? {};
  const frame = artifacts.business_frame ?? {};
  const analysis = artifacts.test_analysis ?? {};
  const coverage = artifacts.coverage_ledger ?? {};
  const intake = artifacts.intake_profile ?? {};
  const oracleSource = (intake.oracle_source ?? {}) as Record<string, unknown>;

  const rules = ((frame.rules as Record<string, unknown>[]) ?? []).map((r) => ({
    rule_id: String(r.rule_id ?? ""),
    statement: String(r.statement ?? ""),
    source_quote: r.source_quote ? String(r.source_quote) : undefined,
    probing: Boolean(r.probing),
  }));
  const exampleCounts: Record<string, number> = {};
  for (const row of ((analysis.example_map as Record<string, unknown>[]) ?? [])) {
    const rid = String(row.rule_id ?? "");
    exampleCounts[rid] = ((row.examples as unknown[]) ?? []).length;
  }
  // coverage_ledger 的行在 `rules` 键下（`server/journey/coverage.py` 的
  // ledger_payload），每行的用例在 `cases[].case_id`、不覆盖理由在 `gap_reason`。
  // 这里照服务端的实际形状读，不自己猜键名——猜错的表现是"全部未收口"，
  // 而那看起来像业务结论、不像取数 bug。
  const coverageRows = ((coverage.rules as Record<string, unknown>[]) ?? []).map((c) => ({
    rule_id: String(c.rule_id ?? ""),
    status: String(c.status ?? ""),
    reason: c.gap_reason ? String(c.gap_reason) : undefined,
    case_ids: ((c.cases as Record<string, unknown>[]) ?? []).map((x) =>
      String(x.case_id ?? ""),
    ),
  }));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href="/test-journey"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("All journeys")}
        </Link>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={continueInChat}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1.5 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            {t("Continue in chat")}
          </button>
          <button
            type="button"
            onClick={refresh}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1.5 text-xs text-[var(--foreground)] hover:bg-[var(--muted)]"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            {t("Refresh")}
          </button>
        </div>
      </div>

      <div>
        <h1 className="text-xl font-semibold text-[var(--foreground)]">
          {String(payload?.batch?.title ?? batchId)}
        </h1>
        <p className="mt-1 font-mono text-xs text-[var(--muted-foreground)]">{batchId}</p>
      </div>

      {error ? (
        <ErrorState code={error.code} message={error.message} onRetry={refresh} />
      ) : null}

      {payload ? (
        <>
          <div className="flex gap-1 border-b border-[var(--border)]">
            {TABS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setTab(entry.id)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                  tab === entry.id
                    ? "border-[var(--primary)] text-[var(--foreground)]"
                    : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                }`}
              >
                {t(entry.label)}
              </button>
            ))}
          </div>

          {tab === "ledger" ? (
            <div className="space-y-5">
              <ArtifactLedger cells={payload.stepper ?? []} />
              <div>
                <h2 className="mb-2 text-sm font-medium text-[var(--foreground)]">
                  {t("Requirement intake")}
                </h2>
                <IntakePanel
                  batchId={batchId}
                  oracle={{
                    present: Boolean(oracleSource.content_digest),
                    summary: oracleSource.content_digest
                      ? t("Snapshot frozen")
                      : t("Not pulled"),
                    storyId: String(oracleSource.story_id ?? ""),
                    workspaceId: String(oracleSource.workspace_id ?? ""),
                    title: String(oracleSource.title ?? ""),
                    fetchedAt: String(oracleSource.fetched_at ?? ""),
                    digest: String(oracleSource.content_digest ?? ""),
                  }}
                  clarify={{
                    present: rules.length > 0,
                    summary: rules.length
                      ? t("{{count}} rules", { count: rules.length })
                      : t("Not clarified"),
                    ruleCount: rules.length,
                    probingCount: rules.filter((r) => r.probing).length,
                  }}
                  analysis={{
                    present: Object.keys(exampleCounts).length > 0,
                    summary: Object.keys(exampleCounts).length
                      ? t("{{count}} examples", {
                          count: Object.values(exampleCounts).reduce((a, b) => a + b, 0),
                        })
                      : t("Not analyzed"),
                    exampleCount: Object.values(exampleCounts).reduce((a, b) => a + b, 0),
                  }}
                  onContinueInChat={continueInChat}
                />
              </div>
            </div>
          ) : null}

          {tab === "rules" ? (
            <RuleTable rules={rules} exampleCounts={exampleCounts} coverage={coverageRows} />
          ) : null}

          {tab === "results" ? <ResultTable runs={payload.runs ?? []} /> : null}
        </>
      ) : null}
    </div>
  );
}
