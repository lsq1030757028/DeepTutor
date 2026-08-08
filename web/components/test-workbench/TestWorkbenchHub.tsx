"use client";

// [fork] 测试工作台。设计稿：extensions/test-partner/docs/design/workbench-full.html
// （决策 0010 通过验收）。本文件目前是骨架——先把「页面能打开、后端接得通、
// 每用户隔离生效」这条链路立住，交互按设计稿逐屏补。
//
// 视觉纪律（勘察实测，见 recon-deeptutor-extension-points.md）：
// 颜色只写 [var(--token)] 这种 arbitrary value（全仓 5274 处，语义简写 0 处）；
// 状态色用 tailwind 原生色 + 成对 dark:；四套主题 Cream/Dark/Default/Glass 都得成立。

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ClipboardCheck, Plus, RefreshCw, Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import SpaceSectionHeader from "@/components/space/SpaceSectionHeader";
import DeliveryDetail from "@/components/test-workbench/DeliveryDetail";
import EnvironmentsPanel from "@/components/test-workbench/EnvironmentsPanel";
import NewBatchFlow from "@/components/test-workbench/NewBatchFlow";

interface DeliverySummary {
  id: string;
  title?: string;
  case_count?: number;
  created_at?: string;
  generated_at?: string;
  degraded?: boolean;
  executed?: boolean;
  last_execution?: { executed_at?: string; verdict?: string } | null;
}

//: 主区在看什么：批次列表 / 一个批次的详情 / 环境配置。
type View = { kind: "list" } | { kind: "detail"; id: string } | { kind: "environments" };

interface DeliveriesResponse {
  deliveries?: DeliverySummary[];
}

interface HealthResponse {
  extension_loaded: boolean;
  extension_path?: string;
  error?: string | null;
}

const BASE = "/api/v1/test-workbench";

export default function TestWorkbenchHub() {
  const { t } = useTranslation();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [deliveries, setDeliveries] = useState<DeliverySummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState<View>({ kind: "list" });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // health 单独取：扩展没打进镜像时其余接口全是 503，
      // 光看 503 分不清「没装」还是「坏了」，这条把两者分开。
      const h: HealthResponse = await apiFetch(apiUrl(`${BASE}/health`)).then((r) => r.json());
      setHealth(h);
      if (!h.extension_loaded) {
        setDeliveries([]);
        return;
      }
      const d: DeliveriesResponse = await apiFetch(apiUrl(`${BASE}/deliveries`)).then((r) => r.json());
      setDeliveries(Array.isArray(d.deliveries) ? d.deliveries : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <SpaceSectionHeader
        icon={ClipboardCheck}
        title={t("Test Workbench")}
        description={t("Generate API test cases from a HAR capture, then run them against an environment.")}
        meta={
          deliveries && deliveries.length > 0 ? (
            <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[11px] text-[var(--muted-foreground)]">
              {deliveries.length}
            </span>
          ) : null
        }
        action={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setView({ kind: "environments" })}
              disabled={health ? !health.extension_loaded : false}
              className="inline-flex items-center gap-1.5 rounded-[9px] border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-[13px] text-[var(--foreground)] hover:bg-[var(--accent)] disabled:opacity-45"
            >
              <Settings2 size={14} strokeWidth={1.6} />
              {t("Environments")}
            </button>
            <button
              type="button"
              onClick={() => void load()}
              className="inline-flex items-center gap-1.5 rounded-[9px] border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-[13px] text-[var(--foreground)] hover:bg-[var(--accent)]"
            >
              <RefreshCw size={14} strokeWidth={1.6} />
              {t("Refresh")}
            </button>
            <button
              type="button"
              onClick={() => setCreating(true)}
              disabled={creating || (health ? !health.extension_loaded : false)}
              className="inline-flex items-center gap-1.5 rounded-[9px] bg-[var(--primary)] px-3 py-1.5 text-[13px] font-medium text-[var(--primary-foreground)] disabled:opacity-45"
            >
              <Plus size={14} strokeWidth={1.8} />
              {t("New batch")}
            </button>
          </div>
        }
      />

      {/* 详情与环境配置各占整个主区，自带返回。列表状态的刷新在返回时顺手做。 */}
      {view.kind === "environments" && (
        <EnvironmentsPanel onBack={() => setView({ kind: "list" })} />
      )}
      {view.kind === "detail" && (
        <DeliveryDetail
          deliveryId={view.id}
          onBack={() => {
            setView({ kind: "list" });
            void load();
          }}
          onOpenEnvironments={() => setView({ kind: "environments" })}
        />
      )}

      {view.kind === "list" && (
        <>


      {/* 扩展没装上——这是镜像漏 COPY extensions/ 时的样子，明说是哪条路径 */}
      {health && !health.extension_loaded && (
        <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3.5 py-3 text-[13px]">
          <AlertTriangle size={16} strokeWidth={1.7} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div>
            <div className="font-medium text-[var(--foreground)]">{t("Test extension is not loaded")}</div>
            <div className="mt-1 text-[var(--muted-foreground)]">{health.error || health.extension_path}</div>
          </div>
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3.5 py-3 text-[13px] text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      {creating && (
        <div className="mb-4">
          <NewBatchFlow
            onCancel={() => setCreating(false)}
            onDone={() => {
              // 采纳后回到列表并刷新——新批次要立刻看得见，
              // 否则用户不确定到底存进去没有。
              setCreating(false);
              void load();
            }}
          />
        </div>
      )}

      {loading && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] px-4 py-10 text-center text-[13px] text-[var(--muted-foreground)]">
          {t("Loading…")}
        </div>
      )}

      {!loading && deliveries?.length === 0 && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] px-4 py-12 text-center">
          <div className="text-[14px] font-medium text-[var(--foreground)]">{t("No test batches yet")}</div>
          <p className="mx-auto mt-1.5 max-w-md text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {t("Import a HAR capture and describe what you want to test — the cases get generated for you to review.")}
          </p>
        </div>
      )}

      {!loading && deliveries && deliveries.length > 0 && (
        <ul className="flex flex-col gap-2">
          {deliveries.map((d) => (
            <li key={d.id}>
              <button
                type="button"
                onClick={() => setView({ kind: "detail", id: d.id })}
                className="w-full rounded-xl border border-[var(--border)] bg-[var(--card)] px-3.5 py-3 text-left hover:border-[var(--primary)]/40"
              >
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium text-[var(--foreground)]">{d.title || d.id}</span>
                  <span className="flex-1" />
                  {d.last_execution?.verdict && (
                    <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[10.5px] text-[var(--muted-foreground)]">
                      {d.last_execution.verdict}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[11.5px] text-[var(--muted-foreground)]">
                  {typeof d.case_count === "number" ? `${d.case_count} ${t("cases")}` : t("No structured cases")}
                  {(d.generated_at || d.created_at) ? ` · ${d.generated_at || d.created_at}` : ""}
                  {d.last_execution?.executed_at ? ` · ${t("ran")} ${d.last_execution.executed_at}` : ` · ${t("never run")}`}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
        </>
      )}

      {/* 只有自己看得见——决策 0009。这句要一直在，不是提示是承诺。 */}
      <p className="mt-4 text-[11.5px] text-[var(--muted-foreground)]">
        {t("Batches, environments and credentials are private to your account.")}
      </p>
    </div>
  );
}
