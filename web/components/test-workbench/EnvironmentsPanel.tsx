"use client";

// [fork] 测试环境与变量（设计稿第 9 屏：局部配置，跟着能力走，所以在工作台里）。
//
// 两条纪律：
// 1. 页面从后端只拿**去值投影**（键名 + 掩码）。编辑时值输入框必然是空的，
//    留空提交 = 保留原值——这条语义在服务端实现，这里如实提示。
// 2. 值只在提交那一刻从输入框流向后端，不进组件状态之外的任何地方。

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ArrowLeft, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import { BASE, readError } from "@/components/test-workbench/shared";

interface VariableRow {
  key: string;
  masked?: string;
  value: string;      // 用户新输入的值；空 = 保留原值（服务端语义）
  existing: boolean;  // true = 后端已有这个键（决定占位提示怎么写）
}

interface EnvironmentPublic {
  name: string;
  base_url: string;
  note?: string;
  variable_count: number;
  variables: { key: string; masked: string; length: number }[];
}

//: 配齐这三个保留变量名，执行时才能选「登录换新」。
const RESERVED_HINT = ["login_username", "login_password", "auth_token_path"];

interface VarUsage {
  delivery_count: number;
  case_count: number;
  deliveries: { id: string; title: string; case_ids: string[] }[];
}

export default function EnvironmentsPanel({ onBack, onOpenDelivery }: {
  onBack: () => void;
  onOpenDelivery?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<EnvironmentPublic[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  //: 变量反查表：这个变量被哪些批次的哪几条用例用着（闭环稿 B2 屏）。
  //: 用户在这一页问的是"我配这个有什么用"，这张表就是答案。
  const [usage, setUsage] = useState<Record<string, VarUsage>>({});
  //: 保存后的变化回执：「N 条用例从待配置变为可执行」——不说这句，
  //: 用户填完只看到表单收起来，不知道自己刚才那一下产生了什么效果。
  const [savedNote, setSavedNote] = useState<{ text: string; delivery?: string } | null>(null);

  // 编辑态。null = 列表；否则是表单。
  const [editing, setEditing] = useState<{
    originalName: string;
    name: string;
    baseUrl: string;
    note: string;
    variables: VariableRow[];
  } | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/environments`));
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setRows(Array.isArray(data.environments) ? data.environments : []);
      // 反查表单独取：它要扫全部批次，比环境列表慢，失败也不该拖垮这一页
      try {
        const u = await apiFetch(apiUrl(`${BASE}/environments/usage`)).then((r) => r.json());
        setUsage(u?.usage || {});
      } catch {
        setUsage({});
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const startNew = () => setEditing({
    originalName: "", name: "", baseUrl: "", note: "",
    variables: [{ key: "", value: "", existing: false }],
  });

  const startEdit = (env: EnvironmentPublic) => setEditing({
    originalName: env.name,
    name: env.name,
    baseUrl: env.base_url,
    note: env.note || "",
    variables: [
      ...env.variables.map((v) => ({ key: v.key, masked: v.masked, value: "", existing: true })),
      { key: "", value: "", existing: false },
    ],
  });

  const save = useCallback(async () => {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/environments`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: editing.name,
          base_url: editing.baseUrl,
          note: editing.note,
          original_name: editing.originalName,
          variables: editing.variables
            .filter((v) => v.key.trim())
            .map((v) => ({ key: v.key.trim(), value: v.value })),
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setRows(Array.isArray(data.environments) ? data.environments : []);

      // 变化回执：这次新填的变量各自解锁了几条用例（闭环稿 B2）
      const before = new Set(
        (rows || []).find((r) => r.name === (editing.originalName || editing.name))
          ?.variables.map((v) => v.key) || [],
      );
      const added = editing.variables
        .filter((v) => v.key.trim() && v.value && !before.has(v.key.trim()))
        .map((v) => v.key.trim());
      const unlocked = added.reduce((n, k) => n + (usage[k]?.case_count || 0), 0);
      const firstDelivery = added.map((k) => usage[k]?.deliveries?.[0]?.id).find(Boolean);
      setSavedNote(unlocked > 0
        ? { text: t("Saved. {{n}} case(s) went from waiting-on-config to runnable.", { n: unlocked }),
            delivery: firstDelivery }
        : { text: t("Saved.") });
      setEditing(null);
      void load();                       // 重新取反查表：新变量也要出现在「谁在用」里
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [editing, rows, usage, t, load]);

  const remove = useCallback(async (name: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(apiUrl(`${BASE}/environments/delete`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setRows(Array.isArray(data.environments) ? data.environments : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const setVar = (index: number, patch: Partial<VariableRow>) => {
    if (!editing) return;
    const variables = editing.variables.map((v, i) => (i === index ? { ...v, ...patch } : v));
    // 最后一行开始填了就自动补一行空的，省一次"加一行"点击
    if (index === variables.length - 1 && (patch.key || patch.value)) {
      variables.push({ key: "", value: "", existing: false });
    }
    setEditing({ ...editing, variables });
  };

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--card)]">
      <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
        >
          <ArrowLeft size={13} strokeWidth={1.7} />
          {t("Back to batches")}
        </button>
        <span className="text-[13px] font-medium text-[var(--foreground)]">
          {t("Environments & variables")}
        </span>
        <span className="flex-1" />
        {!editing && (
          <button
            type="button"
            onClick={startNew}
            className="inline-flex items-center gap-1.5 rounded-[9px] bg-[var(--primary)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--primary-foreground)]"
          >
            <Plus size={13} strokeWidth={1.8} />
            {t("New environment")}
          </button>
        )}
      </div>

      <div className="px-4 py-4">
        {/* 只有你自己看得到——决策 0009。这句要一直在。 */}
        <div className="mb-3 rounded-lg border border-emerald-500/35 bg-emerald-500/8 px-3 py-2 text-[12px] text-[var(--foreground)]">
          {t("Only you can see these. Environments, variables and credentials are isolated per account.")}
        </div>

        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
            <AlertTriangle size={14} strokeWidth={1.7} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* 保存回执：告诉用户刚才那一下产生了什么变化，并给回到现场的路 */}
        {savedNote && (
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/8 px-3 py-2 text-[12.5px] text-[var(--foreground)]">
            <span>{savedNote.text}</span>
            {savedNote.delivery && onOpenDelivery && (
              <button
                type="button"
                onClick={() => onOpenDelivery(savedNote.delivery!)}
                className="text-[var(--primary)] underline"
              >
                {t("Back to that batch")}
              </button>
            )}
            <span className="flex-1" />
            <button
              type="button"
              aria-label={t("Dismiss")}
              onClick={() => setSavedNote(null)}
              className="text-[var(--muted-foreground)]"
            >×</button>
          </div>
        )}

        {/* ── 列表 ── */}
        {!editing && rows && rows.length === 0 && (
          <div className="rounded-lg border border-[var(--border)] px-4 py-8 text-center">
            <div className="text-[13.5px] font-medium text-[var(--foreground)]">
              {t("No environments yet")}
            </div>
            <p className="mx-auto mt-1 max-w-md text-[12.5px] text-[var(--muted-foreground)]">
              {t("An environment is a name plus a base URL plus variables — running cases references it by name, so credential values never pass through chat.")}
            </p>
          </div>
        )}

        {!editing && rows && rows.map((env) => (
          <div key={env.name} className="mb-2 rounded-lg border border-[var(--border)] px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] font-medium text-[var(--foreground)]">{env.name}</span>
              <span className="font-mono text-[11.5px] text-[var(--muted-foreground)]">{env.base_url}</span>
              <span className="flex-1" />
              <button
                type="button"
                onClick={() => startEdit(env)}
                className="rounded-md border border-[var(--border)] px-2 py-0.5 text-[11.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
              >
                {t("Edit")}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  // 删环境删的是凭据值，误删不可恢复——要一次显式确认
                  if (window.confirm(t("Delete environment \"{{name}}\" and its variable values? This cannot be undone.", { name: env.name }))) {
                    void remove(env.name);
                  }
                }}
                className="rounded-md border border-red-500/40 px-2 py-0.5 text-[11.5px] text-red-600 hover:bg-red-500/10 dark:text-red-400"
              >
                <Trash2 size={11} strokeWidth={1.7} className="inline" />
              </button>
            </div>
            {env.variables.length > 0 && (
              <div className="mt-2 overflow-x-auto rounded-lg border border-[var(--border)]">
                <table className="w-full min-w-[380px] border-collapse text-[11.5px]">
                  <thead>
                    <tr>
                      <th className="w-36 border-b border-[var(--border)] px-2 py-1.5 text-left font-medium text-[var(--muted-foreground)]">{t("Variable name")}</th>
                      <th className="w-24 border-b border-[var(--border)] px-2 py-1.5 text-left font-medium text-[var(--muted-foreground)]">{t("Value")}</th>
                      <th className="border-b border-[var(--border)] px-2 py-1.5 text-left font-medium text-[var(--muted-foreground)]">{t("Used by")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {env.variables.map((v) => {
                      const u = usage[v.key];
                      return (
                        <tr key={v.key}>
                          <td className="border-b border-[var(--border)]/50 px-2 py-1.5 font-mono text-[var(--foreground)]">
                            {v.key}
                            {RESERVED_HINT.includes(v.key) && (
                              <span className="ml-1.5 rounded-full bg-emerald-500/12 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
                                {t("reserved")}
                              </span>
                            )}
                          </td>
                          <td className="border-b border-[var(--border)]/50 px-2 py-1.5 font-mono text-[var(--muted-foreground)]">
                            {v.masked || "*"}
                          </td>
                          <td className="border-b border-[var(--border)]/50 px-2 py-1.5">
                            {!u ? (
                              <span className="text-[var(--muted-foreground)]">{t("no case references it yet")}</span>
                            ) : (
                              <span className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[var(--foreground)]">
                                  {t("{{d}} batch(es) · {{c}} case(s)", { d: u.delivery_count, c: u.case_count })}
                                </span>
                                {onOpenDelivery && u.deliveries[0] && (
                                  <button
                                    type="button"
                                    onClick={() => onOpenDelivery(u.deliveries[0].id)}
                                    className="text-[var(--primary)] underline"
                                  >
                                    {u.deliveries[0].title}
                                  </button>
                                )}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {env.note && (
              <div className="mt-1 text-[11.5px] text-[var(--muted-foreground)]">{env.note}</div>
            )}
          </div>
        ))}

        {/* ── 表单 ── */}
        {editing && (
          <div>
            <div className="grid gap-2.5 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-[11.5px] text-[var(--muted-foreground)]">{t("Environment name")}</span>
                <input
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  placeholder={t("e.g. staging")}
                  className="w-full rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 text-[12.5px] text-[var(--foreground)]"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11.5px] text-[var(--muted-foreground)]">Base URL</span>
                <input
                  value={editing.baseUrl}
                  onChange={(e) => setEditing({ ...editing, baseUrl: e.target.value })}
                  placeholder="https://api-test.example.com"
                  className="w-full rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)]"
                />
              </label>
            </div>
            <label className="mt-2.5 block">
              <span className="mb-1 block text-[11.5px] text-[var(--muted-foreground)]">{t("Note (optional)")}</span>
              <input
                value={editing.note}
                onChange={(e) => setEditing({ ...editing, note: e.target.value })}
                className="w-full rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 text-[12.5px] text-[var(--foreground)]"
              />
            </label>

            <div className="mt-3">
              <div className="mb-1 grid grid-cols-[1fr_1fr_28px] gap-1.5 text-[11px] text-[var(--muted-foreground)]">
                <span>{t("Variable name")}</span>
                <span>{t("Value")}</span>
                <span />
              </div>
              {editing.variables.map((v, i) => (
                <div key={i} className="mb-1.5 grid grid-cols-[1fr_1fr_28px] gap-1.5">
                  <input
                    value={v.key}
                    onChange={(e) => setVar(i, { key: e.target.value })}
                    placeholder={i === editing.variables.length - 1 ? t("add a variable…") : ""}
                    className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)]"
                  />
                  <input
                    type="password"
                    value={v.value}
                    onChange={(e) => setVar(i, { value: e.target.value })}
                    placeholder={v.existing
                      ? t("{{masked}} — leave blank to keep", { masked: v.masked || "······" })
                      : ""}
                    autoComplete="new-password"
                    className="rounded-lg border border-[var(--input)] bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)]"
                  />
                  <button
                    type="button"
                    aria-label={t("Remove variable")}
                    onClick={() => setEditing({
                      ...editing,
                      variables: editing.variables.filter((_, j) => j !== i),
                    })}
                    className="rounded-md text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
                  >
                    <Trash2 size={12} strokeWidth={1.7} className="mx-auto" />
                  </button>
                </div>
              ))}
              <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                {t("Reserved names {{names}}: set all three to enable \"re-login for a fresh token\" when running.", { names: RESERVED_HINT.join(" / ") })}
              </p>
            </div>

            <div className="mt-3 flex items-center gap-2 border-t border-[var(--border)] pt-3">
              <button
                type="button"
                onClick={() => setEditing(null)}
                className="rounded-[9px] border border-[var(--border)] px-3 py-1.5 text-[12.5px] text-[var(--foreground)] hover:bg-[var(--accent)]"
              >
                {t("Cancel")}
              </button>
              <span className="flex-1" />
              <button
                type="button"
                disabled={busy || !editing.name.trim() || !editing.baseUrl.trim()}
                onClick={() => void save()}
                className="rounded-[9px] bg-[var(--primary)] px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--primary-foreground)] disabled:opacity-45"
              >
                {t("Save environment")}
              </button>
            </div>
          </div>
        )}

        <p className="mt-3 text-[11.5px] text-[var(--muted-foreground)]">
          {t("Runs reference an environment by name; values are resolved server-side and never enter chat or results.")}
        </p>
      </div>
    </div>
  );
}
