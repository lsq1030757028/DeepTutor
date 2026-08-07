"use client";

// [fork] 新建批次：上传 HAR → 描述场景 → AI 生成 → 审核采纳。
// 设计稿 extensions/test-partner/docs/design/workbench-full.html（决策 0010 通过）。
//
// 三条来自设计定稿、不能在实现里走样的规矩：
// 1. 挑端点是 AI 的活。用户只传文件、说一句话，看到的是 AI 挑了哪些，而不是自己去勾。
// 2. 场景描述就是一个文本框。不分字段、不填表——用户明确否掉过结构化版本。
// 3. 勾选是入库的唯一闸门。生成 ≠ 入库；不勾就不进批次（服务端同样守着这条）。
//
// 视觉：颜色只写 [var(--token)]；状态色用 tailwind 原生色 + 成对 dark:；
// 四套主题 Cream/Dark/Default/Glass 都得成立。

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Upload,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";

const BASE = "/api/v1/test-workbench";
//: 轮询间隔。生成是几十秒的活，每秒一次足够，再密只是白打接口。
const POLL_MS = 1000;

interface InspectResponse {
  draft_id: string;
  source_name?: string;
  report?: {
    summary?: {
      entry_count?: number;
      noise_entries_filtered?: number;
      endpoint_count?: number;
    };
    replay?: { replayable?: boolean };
  };
  redaction_notice?: { message?: string };
}

interface GeneratedCase {
  id?: string;
  title?: string;
  intent?: string;
  request?: { method?: string; url?: string };
  assertions?: unknown[];
}

interface JobResult {
  scenario?: string;
  picked_endpoints?: { method?: string; path?: string; why?: string }[];
  skipped_reason?: string;
  cases?: GeneratedCase[];
  notes?: string[];
  complete?: boolean;
}

interface Job {
  job_id: string;
  state: "pending" | "running" | "done" | "failed" | "cancelled";
  stage?: string;
  done?: number;
  total?: number;
  error?: string;
  result?: JobResult | null;
}

type Step = "upload" | "scenario" | "running" | "review";

async function readError(res: Response): Promise<string> {
  // 后端的 detail 有时是字符串、有时是对象（体检失败那条带 hint）。
  // 两种都要能读出人话——把 [object Object] 甩给用户等于没报错。
  try {
    const body = await res.json();
    const d = body?.detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") return d.message || d.hint || JSON.stringify(d);
    return res.statusText;
  } catch {
    return res.statusText;
  }
}

export default function NewBatchFlow({ onDone, onCancel }: {
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>("upload");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState<InspectResponse | null>(null);
  const [scenario, setScenario] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);
  // 组件卸载必须停轮询，否则用户关掉面板后它还在后台打接口。
  useEffect(() => stopPolling, [stopPolling]);

  // ── 第 1 步：上传 + 本地体检（不调模型，免费且立即出结果） ──────────────
  const upload = useCallback(async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiFetch(apiUrl(`${BASE}/har/inspect`), {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error(await readError(res));
      const data: InspectResponse = await res.json();
      setDraft(data);
      setStep("scenario");
      // 上传完立刻让 AI 起草，用户进到第 2 步就有东西可改，不用再点一次。
      void drafting(data.draft_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  // ── 第 2 步：AI 起草场景（用户可改；改过的优先） ────────────────────────
  const [drafted, setDrafted] = useState(false);
  const drafting = useCallback(async (draftId: string) => {
    setDrafted(false);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/generate/scenario`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_id: draftId }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      // 只在用户还没动过的时候填，免得把他正在写的东西冲掉。
      setScenario((prev) => (prev.trim() ? prev : String(data.scenario || "")));
    } catch (e) {
      // 起草失败不阻断：用户自己写也能往下走。
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafted(true);
    }
  }, []);

  // ── 第 3 步：发起生成并轮询 ─────────────────────────────────────────────
  const start = useCallback(async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/generate/jobs`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_id: draft.draft_id, scenario }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const first: Job = await res.json();
      setJob(first);
      setStep("running");

      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const r = await apiFetch(apiUrl(`${BASE}/generate/jobs/${first.job_id}`));
          if (!r.ok) throw new Error(await readError(r));
          const cur: Job = await r.json();
          setJob(cur);
          if (cur.state === "done" || cur.state === "failed" || cur.state === "cancelled") {
            stopPolling();
            if (cur.state === "done") {
              // 默认全选：模型产出的都可采纳，用户要做的是"去掉不要的"，
              // 而不是从零勾一遍——后者在十几条时是纯苦力。
              setPicked(new Set((cur.result?.cases || []).map((c) => String(c.id))));
              setStep("review");
            }
          }
        } catch (e) {
          stopPolling();
          setError(e instanceof Error ? e.message : String(e));
        }
      }, POLL_MS);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [draft, scenario, stopPolling]);

  const cancelJob = useCallback(async () => {
    if (!job) return;
    stopPolling();
    try {
      await apiFetch(apiUrl(`${BASE}/generate/jobs/${job.job_id}/cancel`), { method: "POST" });
    } finally {
      onCancel();
    }
  }, [job, onCancel, stopPolling]);

  // ── 第 4 步：采纳 ───────────────────────────────────────────────────────
  const adopt = useCallback(async () => {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/generate/jobs/${job.job_id}/adopt`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ids: Array.from(picked) }),
      });
      if (!res.ok) throw new Error(await readError(res));
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [job, picked, onDone]);

  const summary = draft?.report?.summary;
  const cases = job?.result?.cases || [];

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--card)]">
      <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
        <span className="text-[11.5px] text-[var(--muted-foreground)]">
          {step === "upload" && t("New batch · step 1 of 3")}
          {step === "scenario" && t("New batch · step 2 of 3")}
          {(step === "running" || step === "review") && t("New batch · step 3 of 3")}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onCancel}
          aria-label={t("Close")}
          className="rounded-md p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
        >
          <X size={14} strokeWidth={1.8} />
        </button>
      </div>

      <div className="px-4 py-4">
        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
            <AlertTriangle size={14} strokeWidth={1.7} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* ── 1 上传 ── */}
        {step === "upload" && (
          <div>
            <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed border-[var(--border)] bg-[var(--muted)]/40 px-4 py-8 text-center">
              <Upload size={20} strokeWidth={1.5} className="text-[var(--muted-foreground)]" />
              <span className="text-[13px] text-[var(--foreground)]">{t("Choose a .har file")}</span>
              <span className="text-[11.5px] text-[var(--muted-foreground)]">
                {t("Browser DevTools → Network → right click → Save all as HAR")}
              </span>
              <input
                type="file"
                accept=".har,application/json"
                className="hidden"
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void upload(f);
                }}
              />
            </label>
            {busy && (
              <p className="mt-3 flex items-center gap-2 text-[12.5px] text-[var(--muted-foreground)]">
                <Loader2 size={14} className="animate-spin" />
                {t("Inspecting locally — no model is called in this step.")}
              </p>
            )}
          </div>
        )}

        {/* ── 2 场景描述 ── */}
        {step === "scenario" && (
          <div>
            {summary && (
              <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  [summary.entry_count, t("requests")],
                  [summary.noise_entries_filtered, t("noise filtered")],
                  [summary.endpoint_count, t("endpoints")],
                  [draft?.report?.replay?.replayable ? 1 : 0, t("login found")],
                ].map(([n, label]) => (
                  <div key={String(label)} className="rounded-lg border border-[var(--border)] px-2.5 py-2">
                    <div className="text-[17px] font-semibold tabular-nums text-[var(--foreground)]">{String(n ?? "—")}</div>
                    <div className="text-[11px] text-[var(--muted-foreground)]">{label}</div>
                  </div>
                ))}
              </div>
            )}

            {/* 如实声明，不写"已全部脱敏"——凭证换成了占位，PII 不在范围内（BB-424） */}
            {draft?.redaction_notice?.message && (
              <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px] text-[var(--foreground)]">
                {draft.redaction_notice.message}
              </div>
            )}

            <label className="mb-1.5 block text-[13px] font-medium text-[var(--foreground)]">
              {t("Say what you want to test, in one paragraph")}
            </label>
            <textarea
              rows={4}
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              placeholder={drafted ? t("e.g. logging in with a password, opening home, switching device, logging out") : t("AI is drafting…")}
              className="w-full rounded-lg border border-[var(--input)] bg-[var(--card)] px-3 py-2 text-[13px] leading-relaxed text-[var(--foreground)]"
            />
            <p className="mt-1.5 text-[11.5px] text-[var(--muted-foreground)]">
              {t("Describe it like you would to a teammate. AI picks which endpoints to cover.")}
            </p>

            <div className="mt-4 flex items-center gap-2 border-t border-[var(--border)] pt-3">
              <button
                type="button"
                onClick={() => setStep("upload")}
                className="inline-flex items-center gap-1.5 rounded-[9px] border border-[var(--border)] px-3 py-1.5 text-[12.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
              >
                <ArrowLeft size={13} strokeWidth={1.7} />
                {t("Back")}
              </button>
              <span className="flex-1" />
              <button
                type="button"
                disabled={busy || !scenario.trim()}
                onClick={() => void start()}
                className="rounded-[9px] bg-[var(--primary)] px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--primary-foreground)] disabled:opacity-45"
              >
                {t("Generate cases")}
              </button>
            </div>
          </div>
        )}

        {/* ── 3 生成中 ── */}
        {step === "running" && (
          <div>
            <div className="flex items-center gap-2 text-[13px] text-[var(--foreground)]">
              <Loader2 size={15} className="animate-spin text-[var(--primary)]" />
              {job?.stage === "scenario" && t("Reading your description…")}
              {job?.stage === "outline" && t("Picking endpoints and listing cases…")}
              {job?.stage === "detail" && t("Writing requests and assertions…")}
              {!job?.stage && t("Starting…")}
            </div>
            {typeof job?.total === "number" && job.total > 0 && (
              <div className="mt-3">
                <div className="h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
                  <div
                    className="h-full bg-[var(--primary)] transition-all"
                    style={{ width: `${Math.round(((job.done || 0) / job.total) * 100)}%` }}
                  />
                </div>
                <div className="mt-1 text-[11.5px] tabular-nums text-[var(--muted-foreground)]">
                  {job.done} / {job.total}
                </div>
              </div>
            )}
            {job?.state === "failed" && (
              <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
                {job.error}
              </div>
            )}
            <div className="mt-4 border-t border-[var(--border)] pt-3">
              <button
                type="button"
                onClick={() => void cancelJob()}
                className="rounded-[9px] border border-[var(--border)] px-3 py-1.5 text-[12.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
              >
                {t("Stop")}
              </button>
            </div>
          </div>
        )}

        {/* ── 4 审核采纳 ── */}
        {step === "review" && (
          <div>
            {/* AI 挑了哪些端点：给你看，可以不同意，但不用你动手挑 */}
            {(job?.result?.picked_endpoints?.length ?? 0) > 0 && (
              <div className="mb-3 rounded-lg border border-[var(--border)] px-3 py-2 text-[12px]">
                <div className="text-[var(--muted-foreground)]">{t("AI covered these endpoints")}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {job!.result!.picked_endpoints!.map((e, i) => (
                    <span key={i} className="rounded border border-[var(--border)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--foreground)]">
                      {e.method} {e.path}
                    </span>
                  ))}
                </div>
                {job?.result?.skipped_reason && (
                  <div className="mt-1.5 text-[11.5px] text-[var(--muted-foreground)]">
                    {job.result.skipped_reason}
                  </div>
                )}
              </div>
            )}

            {/* 裁剪层与生成层说的"少给了什么/哪几条没成"，一条不藏 */}
            {(job?.result?.notes?.length ?? 0) > 0 && (
              <ul className="mb-3 flex flex-col gap-1">
                {job!.result!.notes!.map((n, i) => (
                  <li key={i} className="text-[11.5px] text-[var(--muted-foreground)]">· {n}</li>
                ))}
              </ul>
            )}

            <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
              <table className="w-full min-w-[420px] border-collapse text-[12.5px]">
                <thead>
                  <tr>
                    <th className="w-8 border-b border-[var(--border)] px-2.5 py-2" />
                    <th className="w-16 border-b border-[var(--border)] px-2.5 py-2 text-left text-[11px] font-medium text-[var(--muted-foreground)]">
                      {t("ID")}
                    </th>
                    <th className="border-b border-[var(--border)] px-2.5 py-2 text-left text-[11px] font-medium text-[var(--muted-foreground)]">
                      {t("Case")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => {
                    const id = String(c.id);
                    const on = picked.has(id);
                    const thin = !c.assertions || c.assertions.length <= 1;
                    return (
                      <tr key={id}>
                        <td className="border-b border-[var(--border)]/60 px-2.5 py-2 align-top">
                          <input
                            type="checkbox"
                            checked={on}
                            aria-label={c.title || id}
                            onChange={() => {
                              const next = new Set(picked);
                              on ? next.delete(id) : next.add(id);
                              setPicked(next);
                            }}
                            className="mt-0.5 h-3.5 w-3.5 accent-[var(--primary)]"
                          />
                        </td>
                        <td className="border-b border-[var(--border)]/60 px-2.5 py-2 align-top font-mono text-[11.5px] text-[var(--muted-foreground)]">
                          {id}
                        </td>
                        <td className="border-b border-[var(--border)]/60 px-2.5 py-2 align-top">
                          <div className="text-[var(--foreground)]">{c.title}</div>
                          <div className="mt-0.5 font-mono text-[11px] text-[var(--muted-foreground)]">
                            {c.request?.method} {c.request?.url}
                          </div>
                          {thin && (
                            <div className="mt-1 text-[11.5px] text-amber-600 dark:text-amber-400">
                              {t("Only asserts the status code — running it just proves the endpoint is alive.")}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3">
              <span className="text-[12px] text-[var(--muted-foreground)]">
                {t("{{n}} selected", { n: picked.size })}
              </span>
              <span className="flex-1" />
              <button
                type="button"
                disabled={busy || picked.size === 0}
                onClick={() => void adopt()}
                className="inline-flex items-center gap-1.5 rounded-[9px] bg-[var(--primary)] px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--primary-foreground)] disabled:opacity-45"
              >
                <CheckCircle2 size={14} strokeWidth={1.8} />
                {t("Adopt selected")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
