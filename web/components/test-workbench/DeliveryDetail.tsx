"use client";

// [fork] 批次详情：用例/结果一张表 + 执行 + 导出（设计稿第 1/6/7/8 屏）。
//
// 设计定稿里不能走样的三条：
// 1. 导出与执行是平级的两个动作——谁先谁后随意、可以只做一个、可以反复做。
// 2. 用例和结果是同一张表：同一行在生命周期里换状态，不开第二张表。
// 3. 红色必须带可操作的出路——没配环境时给"去配环境"的按钮，不是只报错。
//
// 视觉：颜色只写 [var(--token)]；状态色用 tailwind 原生色 + 成对 dark:。

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Download,
  FileJson2,
  FileSpreadsheet,
  FileText,
  Loader2,
  Play,
  Table2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import { BASE, downloadBlob, readError } from "@/components/test-workbench/shared";

const POLL_MS = 1000;

interface CaseRow {
  case_id: string;
  title: string;
  module?: string;
  priority?: string;
  executable: boolean;
  assertion_count: number;
  broken?: boolean;
}

interface DeliveryInfo {
  id: string;
  title: string;
  generated_at?: string;
  files?: { name: string; bytes: number }[];
  last_execution?: {
    executed_at?: string;
    environment?: string;
    verdict?: string;
  } | null;
}

interface DetailResponse {
  ok: boolean;
  delivery: DeliveryInfo;
  cases: CaseRow[];
  structured: boolean;
  degraded?: { code: string; message: string } | null;
  auth_refresh?: { available: boolean; endpoint?: string; reason?: string };
  executable_count?: number;
}

interface CaseResult {
  case_id: string;
  passed: boolean | null;
  skipped: boolean;
  reason?: string;
  error?: string | null;
  status_code?: number | null;
  elapsed_ms?: number | null;
  assertions?: { name?: string; passed: boolean; expected?: unknown; actual?: unknown }[];
}

interface Run {
  run_id: string;
  state: "running" | "done" | "error";
  total: number;
  done: number;
  current_case_id?: string;
  current_title?: string;
  error?: string;
  result?: {
    summary?: { passed?: number; failed?: number; skipped?: number; total?: number };
    results?: CaseResult[];
  } | null;
}

interface EnvironmentRow {
  name: string;
  base_url: string;
  variable_count: number;
}

const EXPORT_KINDS = [
  { key: "xlsx", label: "Excel", icon: FileSpreadsheet, hintKey: "Columns match the TAPD case library" },
  { key: "postman", label: "Postman", icon: FileJson2, hintKey: "Import into Apifox / Postman and run" },
  { key: "markdown", label: "Markdown", icon: FileText, hintKey: "Paste into a doc or chat" },
  { key: "csv", label: "CSV", icon: Table2, hintKey: "For other systems to consume" },
] as const;

export default function DeliveryDetail({ deliveryId, onBack, onOpenEnvironments }: {
  deliveryId: string;
  onBack: () => void;
  onOpenEnvironments: () => void;
}) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [environments, setEnvironments] = useState<EnvironmentRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [env, setEnv] = useState("");
  const [auth, setAuth] = useState<"env" | "login">("env");

  const [run, setRun] = useState<Run | null>(null);
  const [results, setResults] = useState<Map<string, CaseResult>>(new Map());

  const [showExport, setShowExport] = useState(false);
  const [formats, setFormats] = useState<Set<string>>(new Set(["xlsx", "postman"]));
  const [exporting, setExporting] = useState(false);
  const [exported, setExported] = useState<{ name: string; bytes: number }[] | null>(null);
  const [exportWarnings, setExportWarnings] = useState<string[]>([]);
  // 导出产物脱敏（BB-424）。默认开——安全默认不该要求用户先知道有这个开关。
  const [redactPii, setRedactPii] = useState(true);
  const [redactionHits, setRedactionHits] = useState<Record<string, number> | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);
  useEffect(() => stopPolling, [stopPolling]);

  const applyRunResults = useCallback((finished: Run) => {
    const rows = finished.result?.results || [];
    setResults(new Map(rows.map((r) => [String(r.case_id), r])));
  }, []);

  const pollRun = useCallback((runId: string) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const res = await apiFetch(apiUrl(`${BASE}/runs/${runId}`));
        if (!res.ok) throw new Error(await readError(res));
        const cur: Run = await res.json();
        setRun(cur);
        if (cur.state !== "running") {
          stopPolling();
          if (cur.state === "done") applyRunResults(cur);
        }
      } catch (e) {
        stopPolling();
        setError(e instanceof Error ? e.message : String(e));
      }
    }, POLL_MS);
  }, [stopPolling, applyRunResults]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d: DetailResponse = await apiFetch(apiUrl(`${BASE}/deliveries/${deliveryId}`))
        .then((r) => r.json());
      setDetail(d);
      // 默认全选可执行的：用户要做的是"去掉不要的"，不是从零勾一遍。
      setSelected(new Set((d.cases || []).filter((c) => c.executable).map((c) => c.case_id)));

      const e = await apiFetch(apiUrl(`${BASE}/environments`)).then((r) => r.json());
      const rows: EnvironmentRow[] = Array.isArray(e.environments) ? e.environments : [];
      setEnvironments(rows);
      setEnv((prev) => prev || rows[0]?.name || "");

      // 上一轮执行的逐条结果在批次目录的报告文件里；跑过就取来铺进表格——
      // 设计稿第 1 屏：跑过的批次打开默认就带着状态。
      if (d.delivery?.files?.some((f) => f.name === "execution_report.json")) {
        try {
          const rep = await apiFetch(
            apiUrl(`${BASE}/deliveries/${deliveryId}/files/execution_report.json`),
          ).then((r) => r.json());
          if (Array.isArray(rep?.results)) {
            setResults(new Map(rep.results.map((r: CaseResult) => [String(r.case_id), r])));
          }
        } catch {
          // 报告读不出来不阻断——表格照常显示，只是没有历史状态。
        }
      }

      // 页面刷新后接回还在跑的那一轮。
      const act = await apiFetch(apiUrl(`${BASE}/deliveries/${deliveryId}/runs/active`))
        .then((r) => r.json());
      if (act?.run?.state === "running") {
        setRun(act.run);
        pollRun(act.run.run_id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [deliveryId, pollRun]);

  useEffect(() => {
    void load();
  }, [load]);

  const startRun = useCallback(async () => {
    setError(null);
    setExported(null);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/deliveries/${deliveryId}/runs`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ env, case_ids: Array.from(selected), auth }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const first: Run = await res.json();
      setRun(first);
      pollRun(first.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [deliveryId, env, selected, auth, pollRun]);

  const doExport = useCallback(async () => {
    setExporting(true);
    setError(null);
    setExported(null);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/deliveries/${deliveryId}/export`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ formats: Array.from(formats), redact_pii: redactPii }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setExported(Array.isArray(data.files) ? data.files : []);
      setExportWarnings(Array.isArray(data.warnings) ? data.warnings : []);
      setRedactionHits(data.pii_redaction?.hits ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }, [deliveryId, formats, redactPii]);

  const running = run?.state === "running";
  const cases = detail?.cases || [];
  const summary = run?.result?.summary;
  const noEnvironments = environments !== null && environments.length === 0;

  const statusTag = (row: CaseRow) => {
    const r = results.get(row.case_id);
    if (running && run?.current_case_id === row.case_id) {
      return (
        <span className="rounded-full bg-[var(--primary)]/15 px-2 py-0.5 text-[10.5px] text-[var(--primary)]">
          {t("Running")}
        </span>
      );
    }
    if (!r) {
      return (
        <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[10.5px] text-[var(--muted-foreground)]">
          {row.executable ? t("Not run") : t("Manual case")}
        </span>
      );
    }
    if (r.skipped) {
      return (
        <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10.5px] text-amber-600 dark:text-amber-400">
          {t("Skipped")}
        </span>
      );
    }
    return r.passed ? (
      <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10.5px] text-emerald-600 dark:text-emerald-400">
        {t("Passed")}
      </span>
    ) : (
      <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[10.5px] text-red-600 dark:text-red-400">
        {t("Failed")}
      </span>
    );
  };

  const failureDetail = (row: CaseRow) => {
    const r = results.get(row.case_id);
    if (!r) return null;
    if (r.skipped && r.reason) {
      return (
        <div className="mt-1 text-[11px] text-[var(--muted-foreground)]">{r.reason}</div>
      );
    }
    if (r.passed === false) {
      const firstFail = (r.assertions || []).find((a) => !a.passed);
      const text = r.error
        || (firstFail
          ? `${t("expected")} ${JSON.stringify(firstFail.expected)}, ${t("actual")} ${JSON.stringify(firstFail.actual)}`
          : "");
      if (!text) return null;
      return (
        <div className="mt-1 border-l-2 border-red-500/40 pl-2 font-mono text-[11px] text-[var(--muted-foreground)]">
          {text}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--card)]">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
        >
          <ArrowLeft size={13} strokeWidth={1.7} />
          {t("Back to batches")}
        </button>
        <span className="text-[13px] font-medium text-[var(--foreground)]">
          {detail?.delivery?.title || deliveryId}
        </span>
        <span className="flex-1" />
        {detail?.delivery?.last_execution?.executed_at && !running && (
          <span className="text-[11px] text-[var(--muted-foreground)]">
            {t("Last run")} {detail.delivery.last_execution.executed_at}
            {detail.delivery.last_execution.verdict
              ? ` · ${detail.delivery.last_execution.verdict}` : ""}
          </span>
        )}
      </div>

      <div className="px-4 py-4">
        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
            <AlertTriangle size={14} strokeWidth={1.7} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {loading && (
          <div className="py-8 text-center text-[13px] text-[var(--muted-foreground)]">
            {t("Loading…")}
          </div>
        )}

        {/* 旧批次：明说降级，不假装能执行（工作台后端同一条纪律的前端面） */}
        {!loading && detail && !detail.structured && (
          <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12.5px] text-[var(--foreground)]">
            {detail.degraded?.message || t("This batch has no structured cases; only its files are available.")}
          </div>
        )}

        {!loading && detail && (
          <>
            {/* 结果摘要——跑完后同一张表上方给四个数 */}
            {summary && !running && (
              <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
                {(summary.failed ?? 0) > 0 && (
                  <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] text-red-600 dark:text-red-400">
                    {t("Failed")} {summary.failed}
                  </span>
                )}
                {(summary.skipped ?? 0) > 0 && (
                  <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-600 dark:text-amber-400">
                    {t("Skipped")} {summary.skipped}
                  </span>
                )}
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-600 dark:text-emerald-400">
                  {t("Passed")} {summary.passed ?? 0}
                </span>
                <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[11px] text-[var(--muted-foreground)]">
                  {t("All")} {summary.total ?? 0}
                </span>
              </div>
            )}

            {detail.structured && (
              <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
                <table className="w-full min-w-[460px] border-collapse text-[12.5px]">
                  <thead>
                    <tr>
                      <th className="w-8 border-b border-[var(--border)] px-2.5 py-2">
                        <input
                          type="checkbox"
                          aria-label={t("Select all")}
                          checked={selected.size > 0 && cases.filter((c) => c.executable)
                            .every((c) => selected.has(c.case_id))}
                          onChange={(e) => {
                            setSelected(e.target.checked
                              ? new Set(cases.filter((c) => c.executable).map((c) => c.case_id))
                              : new Set());
                          }}
                          className="h-3.5 w-3.5 accent-[var(--primary)]"
                        />
                      </th>
                      <th className="w-16 border-b border-[var(--border)] px-2.5 py-2 text-left text-[11px] font-medium text-[var(--muted-foreground)]">
                        {t("ID")}
                      </th>
                      <th className="border-b border-[var(--border)] px-2.5 py-2 text-left text-[11px] font-medium text-[var(--muted-foreground)]">
                        {t("Case")}
                      </th>
                      <th className="w-20 border-b border-[var(--border)] px-2.5 py-2 text-left text-[11px] font-medium text-[var(--muted-foreground)]">
                        {t("Status")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {cases.map((c) => {
                      const on = selected.has(c.case_id);
                      return (
                        <tr key={c.case_id}>
                          <td className="border-b border-[var(--border)]/60 px-2.5 py-2 align-top">
                            <input
                              type="checkbox"
                              checked={on}
                              disabled={!c.executable}
                              aria-label={c.title}
                              onChange={() => {
                                const next = new Set(selected);
                                on ? next.delete(c.case_id) : next.add(c.case_id);
                                setSelected(next);
                              }}
                              className="mt-0.5 h-3.5 w-3.5 accent-[var(--primary)] disabled:opacity-40"
                            />
                          </td>
                          <td className="border-b border-[var(--border)]/60 px-2.5 py-2 align-top font-mono text-[11.5px] text-[var(--muted-foreground)]">
                            {c.case_id}
                          </td>
                          <td className="border-b border-[var(--border)]/60 px-2.5 py-2 align-top">
                            <div className="text-[var(--foreground)]">{c.title}</div>
                            {!c.executable && !c.broken && (
                              <div className="mt-0.5 text-[11px] text-[var(--muted-foreground)]">
                                {t("No runnable request — execute manually and record the outcome.")}
                              </div>
                            )}
                            {failureDetail(c)}
                          </td>
                          <td className="border-b border-[var(--border)]/60 px-2.5 py-2 align-top">
                            {statusTag(c)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* 执行中：进度条 + 第 N/M 条（进度是真的——执行同步逐条） */}
            {running && (
              <div className="mt-3 flex items-center gap-3">
                <div className="h-1.5 min-w-[150px] flex-1 overflow-hidden rounded-full bg-[var(--muted)]">
                  <div
                    className="h-full bg-[var(--primary)] transition-all"
                    style={{ width: `${run!.total ? Math.round((run!.done / run!.total) * 100) : 0}%` }}
                  />
                </div>
                <span className="text-[11.5px] tabular-nums text-[var(--muted-foreground)]">
                  {t("Case {{done}} of {{total}}", { done: run!.done, total: run!.total })}
                </span>
              </div>
            )}
            {run?.state === "error" && (
              <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
                {run.error}
              </div>
            )}

            {/* 动作行：导出与执行平级 */}
            {detail.structured && (
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3">
                <span className="text-[12px] text-[var(--muted-foreground)]">
                  {t("{{n}} selected", { n: selected.size })} · {t("Environment")}
                </span>
                {noEnvironments ? (
                  // 红色带出路：没配环境不是死路，给"去配"的控件
                  <button
                    type="button"
                    onClick={onOpenEnvironments}
                    className="rounded-[9px] border border-amber-500/50 bg-amber-500/10 px-2.5 py-1 text-[12px] text-amber-700 dark:text-amber-400"
                  >
                    {t("No environment yet — set one up")}
                  </button>
                ) : (
                  <select
                    value={env}
                    onChange={(e) => setEnv(e.target.value)}
                    className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2 py-1 text-[12px] text-[var(--foreground)]"
                  >
                    {(environments || []).map((row) => (
                      <option key={row.name} value={row.name}>{row.name}</option>
                    ))}
                  </select>
                )}
                <span className="text-[12px] text-[var(--muted-foreground)]">{t("Auth")}</span>
                <select
                  value={auth}
                  onChange={(e) => setAuth(e.target.value as "env" | "login")}
                  className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2 py-1 text-[12px] text-[var(--foreground)]"
                >
                  <option value="env">{t("Follow environment")}</option>
                  <option value="login" disabled={!detail.auth_refresh?.available}>
                    {t("Re-login for a fresh token")}
                  </option>
                </select>
                <span className="flex-1" />
                <button
                  type="button"
                  onClick={() => setShowExport((v) => !v)}
                  className="inline-flex items-center gap-1.5 rounded-[9px] border border-[var(--border)] px-3 py-1.5 text-[12.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
                >
                  <Download size={13} strokeWidth={1.7} />
                  {t("Export…")}
                </button>
                <button
                  type="button"
                  disabled={running || selected.size === 0 || !env}
                  onClick={() => void startRun()}
                  className="inline-flex items-center gap-1.5 rounded-[9px] bg-[var(--primary)] px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--primary-foreground)] disabled:opacity-45"
                >
                  {running
                    ? <Loader2 size={13} className="animate-spin" />
                    : <Play size={13} strokeWidth={1.8} />}
                  {t("Run {{n}} selected", { n: selected.size })}
                </button>
              </div>
            )}
            {detail.structured && (
              <p className="mt-2 text-[11.5px] text-[var(--muted-foreground)]">
                {t("Export and run are peers — either order, only one, or repeatedly.")}
              </p>
            )}

            {/* 登录换新为什么不可选/选了会发生什么：把后端给的理由亮出来 */}
            {detail.auth_refresh && auth === "login" && detail.auth_refresh.available && (
              <p className="mt-1 text-[11.5px] text-[var(--muted-foreground)]">
                {detail.auth_refresh.endpoint} · {detail.auth_refresh.reason}
              </p>
            )}

            {/* ── 导出面板（第 7 屏：图标分类、可多选） ── */}
            {showExport && detail.structured && (
              <div className="mt-3 rounded-lg border border-[var(--border)] p-3">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {EXPORT_KINDS.map(({ key, label, icon: Icon, hintKey }) => {
                    const on = formats.has(key);
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          const next = new Set(formats);
                          on ? next.delete(key) : next.add(key);
                          setFormats(next);
                        }}
                        className={`flex items-start gap-2 rounded-lg border px-2.5 py-2 text-left ${
                          on
                            ? "border-[var(--primary)] bg-[var(--primary)]/8"
                            : "border-[var(--border)] hover:bg-[var(--accent)]"
                        }`}
                      >
                        <Icon size={18} strokeWidth={1.5} className="mt-0.5 shrink-0 text-[var(--muted-foreground)]" />
                        <span>
                          <span className="block text-[12.5px] font-medium text-[var(--foreground)]">{label}</span>
                          <span className="block text-[11px] text-[var(--muted-foreground)]">{t(hintKey)}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
                {/* 脱敏开关（BB-424）。默认勾上，说明文字如实标出抓不到的那一类 */}
                <label className="mt-2.5 flex cursor-pointer items-start gap-2">
                  <input
                    type="checkbox"
                    checked={redactPii}
                    onChange={(e) => setRedactPii(e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5 accent-[var(--primary)]"
                  />
                  <span>
                    <span className="block text-[12px] text-[var(--foreground)]">
                      {t("Replace personal info with placeholders in exported files")}
                    </span>
                    <span className="block text-[11px] text-[var(--muted-foreground)]">
                      {t("Covers ID numbers, phones, emails, cards. Names inside free-form text are not detected — check before sharing.")}
                    </span>
                  </span>
                </label>

                <div className="mt-2.5 flex items-center gap-2">
                  <span className="text-[11.5px] text-[var(--muted-foreground)]">
                    {t("{{n}} formats selected", { n: formats.size })}
                  </span>
                  <span className="flex-1" />
                  <button
                    type="button"
                    disabled={exporting || formats.size === 0}
                    onClick={() => void doExport()}
                    className="inline-flex items-center gap-1.5 rounded-[9px] bg-[var(--primary)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--primary-foreground)] disabled:opacity-45"
                  >
                    {exporting && <Loader2 size={13} className="animate-spin" />}
                    {t("Export")}
                  </button>
                </div>

                {exported && (
                  <div className="mt-2.5 border-t border-[var(--border)] pt-2.5">
                    {/* 替换了什么、几处，明说——静默替换会让用户以为产物里还是原值 */}
                    {redactionHits && Object.keys(redactionHits).length > 0 && (
                      <p className="mb-2 text-[11.5px] text-[var(--muted-foreground)]">
                        {t("Replaced in exports:")}{" "}
                        {Object.entries(redactionHits)
                          .map(([kind, n]) => `${kind}×${n}`)
                          .join("、")}
                      </p>
                    )}
                    <ul className="flex flex-col gap-1.5">
                      {exported.map((f) => (
                        <li key={f.name} className="flex items-center gap-2">
                          <span className="font-mono text-[11.5px] text-[var(--foreground)]">{f.name}</span>
                          <span className="text-[11px] text-[var(--muted-foreground)]">
                            {Math.max(1, Math.round(f.bytes / 1024))} KB
                          </span>
                          <span className="flex-1" />
                          <button
                            type="button"
                            onClick={() => void downloadBlob(
                              `${BASE}/deliveries/${deliveryId}/files/${encodeURIComponent(f.name)}`,
                              f.name,
                            ).catch((e) => setError(String(e?.message || e)))}
                            className="inline-flex items-center gap-1 rounded-md border border-[var(--border)] px-2 py-0.5 text-[11.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
                          >
                            <Download size={11} strokeWidth={1.7} />
                            {t("Download")}
                          </button>
                        </li>
                      ))}
                    </ul>
                    {exported.length > 1 && (
                      <button
                        type="button"
                        onClick={() => void downloadBlob(
                          `${BASE}/deliveries/${deliveryId}/archive?files=${encodeURIComponent(exported.map((f) => f.name).join(","))}`,
                          `${deliveryId}.zip`,
                        ).catch((e) => setError(String(e?.message || e)))}
                        className="mt-2 text-[11.5px] text-[var(--primary)] underline"
                      >
                        {t("Download all as one zip")}
                      </button>
                    )}
                    {exportWarnings.map((w, i) => (
                      <p key={i} className="mt-1.5 text-[11.5px] text-amber-600 dark:text-amber-400">{w}</p>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 如实声明（第 8 屏底部）：响应体里的凭证在报告里是占位，页面只出现环境名 */}
            <p className="mt-3 text-[11.5px] text-[var(--muted-foreground)]">
              {t("Credentials echoed in responses are replaced with placeholders in reports. Only the environment name appears on this page.")}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
