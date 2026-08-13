"use client";

// [fork] 用例展开面板（闭环稿 A 屏 + D 屏 · 决策 0012）。
//
// 两件事合在一个组件里，因为它们看的是同一份数据：
// - 只读态：这条用例**真正会发出去的东西**（方法/地址/请求头/请求体/逐条断言）
// - 编辑态：同样这些字段，可改
//
// 一条纪律贯穿两态：**凭据只出现位置，不出现值**。请求块里的 `{{变量名}}`
// 原样显示，真值在服务端解析，界面任何地方都不显示它（判据 AC-8）。

import { useState } from "react";
import { AlertTriangle, Loader2, Pencil, Play } from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import { BASE, readError } from "@/components/test-workbench/shared";

export interface CaseRequest {
  method?: string;
  url?: string;
  headers?: { key?: string; name?: string; value?: string }[] | string[];
  body?: { mode?: string; raw?: unknown; language?: string };
  assertions?: { type?: string; path?: string; expected?: unknown }[];
}

export interface CaseRowFull {
  case_id: string;
  title: string;
  module?: string;
  priority?: string;
  preconditions?: string;
  expected?: string;
  test_data?: string;
  steps?: string[];
  request?: CaseRequest | null;
  executable: boolean;
  assertion_count: number;
  required_vars?: string[];
  origin?: string;
  broken?: boolean;
}

function headerPairs(headers: CaseRequest["headers"]): { key: string; value: string }[] {
  if (!Array.isArray(headers)) return [];
  return headers.map((h) => {
    if (typeof h === "string") {
      const i = h.indexOf(":");
      return i > 0 ? { key: h.slice(0, i).trim(), value: h.slice(i + 1).trim() } : { key: h, value: "" };
    }
    return { key: String(h.key ?? h.name ?? ""), value: String(h.value ?? "") };
  }).filter((h) => h.key);
}

function bodyText(body: CaseRequest["body"]): string {
  if (!body || body.raw === undefined || body.raw === null) return "";
  return typeof body.raw === "string" ? body.raw : JSON.stringify(body.raw, null, 2);
}

export default function CaseDetailPanel({ deliveryId, row, missing, onSaved, onRunOne }: {
  deliveryId: string;
  row: CaseRowFull;
  //: 这条用例缺哪些变量（由父组件按所选环境算出——切换环境即刻更新，不回后端）
  missing: string[];
  onSaved: () => void;
  onRunOne?: (caseId: string) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const req = row.request || {};
  const [method, setMethod] = useState(String(req.method || "GET"));
  const [url, setUrl] = useState(String(req.url || ""));
  const [headers, setHeaders] = useState(() => headerPairs(req.headers));
  const [body, setBody] = useState(() => bodyText(req.body));
  const [assertions, setAssertions] = useState(() =>
    (req.assertions || []).map((a) => ({
      type: String(a.type || "status"),
      path: String(a.path ?? ""),
      expected: a.expected === undefined ? "" : (typeof a.expected === "string"
        ? a.expected : JSON.stringify(a.expected)),
    })));
  const [title, setTitle] = useState(row.title);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      // expected 尽量还原成原始类型：断言写 200（数字）和 "200"（字符串）是两回事
      const parsedAssertions = assertions.map((a) => {
        let expected: unknown = a.expected;
        try {
          expected = JSON.parse(a.expected);
        } catch {
          /* 解析不了就按字符串原样送——用户写的可能本来就是字符串 */
        }
        return a.path ? { type: a.type, path: a.path, expected } : { type: a.type, expected };
      });
      let parsedBody: CaseRequest["body"];
      if (body.trim()) {
        parsedBody = { mode: "raw", raw: body, language: "json" };
      }
      const res = await apiFetch(
        apiUrl(`${BASE}/deliveries/${deliveryId}/cases/${encodeURIComponent(row.case_id)}`),
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title,
            request: {
              method, url,
              headers: headers.filter((h) => h.key).map((h) => ({ key: h.key, value: h.value })),
              ...(parsedBody ? { body: parsedBody } : {}),
              assertions: parsedAssertions,
            },
          }),
        });
      if (!res.ok) throw new Error(await readError(res));
      setEditing(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // ── 只读态 ────────────────────────────────────────────────────────────
  if (!editing) {
    const pairs = headerPairs(req.headers);
    const raw = bodyText(req.body);
    return (
      <div className="border-t border-[var(--border)]/60 bg-[var(--muted)]/25 px-3.5 py-3">
        {!row.request && (
          <div className="text-[12px] text-[var(--muted-foreground)]">
            {t("This case has no runnable request — it is meant to be executed by hand.")}
          </div>
        )}
        {row.request && (
          <>
            <dl className="grid grid-cols-[76px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12px]">
              <dt className="text-[var(--muted-foreground)]">{t("Endpoint")}</dt>
              <dd className="font-mono text-[var(--foreground)]">
                <span className="mr-1.5 rounded bg-[var(--muted)] px-1 py-0.5 text-[10.5px] font-semibold">
                  {String(req.method || "").toUpperCase()}
                </span>
                {String(req.url || "")}
              </dd>
              {pairs.length > 0 && (
                <>
                  <dt className="text-[var(--muted-foreground)]">{t("Headers")}</dt>
                  <dd className="font-mono text-[11.5px] text-[var(--foreground)]">
                    {pairs.map((h) => (
                      <div key={h.key}>{h.key}: {h.value}</div>
                    ))}
                  </dd>
                </>
              )}
            </dl>
            {raw && (
              <pre className="mt-2 overflow-x-auto rounded-lg bg-[var(--muted)]/60 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-[var(--foreground)]">
                {raw}
              </pre>
            )}
            <div className="mt-2.5 text-[11.5px] text-[var(--muted-foreground)]">{t("Assertions")}</div>
            <div className="mt-1 overflow-x-auto rounded-lg border border-[var(--border)]">
              <table className="w-full min-w-[320px] border-collapse text-[11.5px]">
                <thead>
                  <tr>
                    <th className="w-24 border-b border-[var(--border)] px-2 py-1.5 text-left font-medium text-[var(--muted-foreground)]">{t("Type")}</th>
                    <th className="border-b border-[var(--border)] px-2 py-1.5 text-left font-medium text-[var(--muted-foreground)]">{t("Path")}</th>
                    <th className="w-28 border-b border-[var(--border)] px-2 py-1.5 text-left font-medium text-[var(--muted-foreground)]">{t("Expected")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(req.assertions || []).map((a, i) => (
                    <tr key={i}>
                      <td className="border-b border-[var(--border)]/50 px-2 py-1.5 font-mono">{String(a.type || "")}</td>
                      <td className="border-b border-[var(--border)]/50 px-2 py-1.5 font-mono text-[var(--muted-foreground)]">{a.path || "—"}</td>
                      <td className="border-b border-[var(--border)]/50 px-2 py-1.5 font-mono">
                        {a.expected === undefined ? "—" : String(a.expected)}
                      </td>
                    </tr>
                  ))}
                  {(req.assertions || []).length === 0 && (
                    <tr><td colSpan={3} className="px-2 py-2 text-[var(--muted-foreground)]">
                      {t("No assertions — running it proves nothing.")}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {/* 凭据纪律：说清楚为什么这里全是占位符，而不是让用户以为数据缺了 */}
            <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
              {t("Every {{var}} above is a placeholder. Real values live in your environment and are resolved server-side at run time.")}
            </p>
          </>
        )}
        {missing.length > 0 && (
          <div className="mt-2 flex items-start gap-1.5 text-[11.5px] text-amber-700 dark:text-amber-400">
            <AlertTriangle size={13} strokeWidth={1.7} className="mt-0.5 shrink-0" />
            <span>{t("Missing variables: {{names}}", { names: missing.join("、") })}</span>
          </div>
        )}
        <div className="mt-2.5 flex items-center gap-2 border-t border-[var(--border)]/50 pt-2.5">
          <span className="flex-1" />
          {row.request && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2 py-0.5 text-[11.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
            >
              <Pencil size={11} strokeWidth={1.7} />
              {t("Edit this case")}
            </button>
          )}
          {row.executable && missing.length === 0 && onRunOne && (
            <button
              type="button"
              onClick={() => onRunOne(row.case_id)}
              className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2 py-0.5 text-[11.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
            >
              <Play size={11} strokeWidth={1.7} />
              {t("Run just this one")}
            </button>
          )}
        </div>
      </div>
    );
  }

  // ── 编辑态 ────────────────────────────────────────────────────────────
  return (
    <div className="border-t border-[var(--border)]/60 bg-[var(--muted)]/25 px-3.5 py-3">
      {error && (
        <div className="mb-2.5 flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-2.5 py-2 text-[12px] text-red-600 dark:text-red-400">
          <AlertTriangle size={13} strokeWidth={1.7} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <label className="mb-2 block">
        <span className="mb-1 block text-[11px] text-[var(--muted-foreground)]">{t("Case")}</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 text-[12.5px] text-[var(--foreground)]"
        />
      </label>
      <div className="mb-2 flex gap-2">
        <select
          value={method}
          onChange={(e) => setMethod(e.target.value)}
          className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2 py-1.5 text-[12px] text-[var(--foreground)]"
        >
          {["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="flex-1 rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)]"
        />
      </div>

      <div className="mb-1 text-[11px] text-[var(--muted-foreground)]">{t("Headers")}</div>
      {headers.map((h, i) => (
        <div key={i} className="mb-1.5 grid grid-cols-[1fr_1.6fr_28px] gap-1.5">
          <input
            value={h.key}
            onChange={(e) => setHeaders(headers.map((x, j) => j === i ? { ...x, key: e.target.value } : x))}
            className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2 py-1 font-mono text-[11.5px] text-[var(--foreground)]"
          />
          <input
            value={h.value}
            onChange={(e) => setHeaders(headers.map((x, j) => j === i ? { ...x, value: e.target.value } : x))}
            className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2 py-1 font-mono text-[11.5px] text-[var(--foreground)]"
          />
          <button
            type="button"
            aria-label={t("Remove")}
            onClick={() => setHeaders(headers.filter((_, j) => j !== i))}
            className="rounded-md text-[11px] text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
          >×</button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => setHeaders([...headers, { key: "", value: "" }])}
        className="mb-2 text-[11.5px] text-[var(--primary)]"
      >+ {t("Add header")}</button>

      <label className="mb-2 block">
        <span className="mb-1 block text-[11px] text-[var(--muted-foreground)]">{t("Body")}</span>
        <textarea
          rows={4}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          className="w-full rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 font-mono text-[11.5px] text-[var(--foreground)]"
        />
      </label>

      <div className="mb-1 text-[11px] text-[var(--muted-foreground)]">{t("Assertions")}</div>
      {assertions.map((a, i) => (
        <div key={i} className="mb-1.5 grid grid-cols-[110px_1fr_110px_28px] gap-1.5">
          <select
            value={a.type}
            onChange={(e) => setAssertions(assertions.map((x, j) => j === i ? { ...x, type: e.target.value } : x))}
            className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-1.5 py-1 text-[11.5px] text-[var(--foreground)]"
          >
            {["status", "json_path", "body_contains", "header"].map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          <input
            value={a.path}
            placeholder={a.type === "status" ? "—" : "$.data.token"}
            onChange={(e) => setAssertions(assertions.map((x, j) => j === i ? { ...x, path: e.target.value } : x))}
            className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2 py-1 font-mono text-[11.5px] text-[var(--foreground)]"
          />
          <input
            value={a.expected}
            onChange={(e) => setAssertions(assertions.map((x, j) => j === i ? { ...x, expected: e.target.value } : x))}
            className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2 py-1 font-mono text-[11.5px] text-[var(--foreground)]"
          />
          <button
            type="button"
            aria-label={t("Remove")}
            onClick={() => setAssertions(assertions.filter((_, j) => j !== i))}
            className="rounded-md text-[11px] text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
          >×</button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => setAssertions([...assertions, { type: "status", path: "", expected: "200" }])}
        className="text-[11.5px] text-[var(--primary)]"
      >+ {t("Add assertion")}</button>

      <div className="mt-3 flex items-center gap-2 border-t border-[var(--border)]/50 pt-2.5">
        <span className="text-[11px] text-[var(--muted-foreground)]">
          {t("Saving re-validates this case; it will be marked as edited by hand.")}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => setEditing(false)}
          className="rounded-[9px] border border-[var(--border)] px-2.5 py-1 text-[12px] text-[var(--foreground)] hover:bg-[var(--accent)]"
        >
          {t("Cancel")}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void save()}
          className="inline-flex items-center gap-1.5 rounded-[9px] bg-[var(--primary)] px-3 py-1 text-[12px] font-medium text-[var(--primary-foreground)] disabled:opacity-45"
        >
          {busy && <Loader2 size={12} className="animate-spin" />}
          {t("Save this case")}
        </button>
      </div>
    </div>
  );
}
