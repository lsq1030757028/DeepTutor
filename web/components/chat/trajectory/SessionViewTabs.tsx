"use client";

/**
 * Two peer views of the same session: the conversation and its trajectory.
 *
 * The labels are labels and nothing else. No marker, no tally, no colour
 * change when something went wrong in the session — the user's instruction was
 * explicit, and "just a small mark" / "only the first time" / "flash once
 * on an error" are the same thing under different names, so none of them is
 * here either.
 *
 * The choice is page state on purpose: it is not written to the URL and not
 * written to session preferences. Looking at the trajectory is something the
 * user does while investigating, not a setting about how they like to work;
 * persisting it would carry an investigation posture into the next ordinary
 * session.
 */

import { useTranslation } from "react-i18next";

import type { SessionView } from "./view-state";

export function SessionViewTabs({
  view,
  onChange,
}: {
  view: SessionView;
  onChange: (next: SessionView) => void;
}) {
  const { t } = useTranslation();
  const tabs: { id: SessionView; label: string }[] = [
    { id: "conversation", label: t("trajectory.tab.conversation") },
    { id: "trajectory", label: t("trajectory.tab.trajectory") },
  ];

  return (
    <div
      role="tablist"
      aria-label={t("trajectory.tab.groupLabel")}
      className="mx-auto flex w-full max-w-[960px] items-center gap-1 border-b border-[var(--border)] px-6 pt-2"
    >
      {tabs.map((tab) => {
        const active = tab.id === view;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(tab.id)}
            className={`-mb-px rounded-t-lg border-b-2 px-3 py-1.5 text-[13.5px] transition-colors ${
              active
                ? "border-[var(--foreground)] text-[var(--foreground)]"
                : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export default SessionViewTabs;
