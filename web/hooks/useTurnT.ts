"use client";

// [fork] 按「本轮语种」取文案的 t（BB-698）。
//
// `useTranslation().t` 绑的是界面语言。传入 `language` 时改用
// `i18n.getFixedT(language)`——前提是该语言的资源包已装载（zh 是懒加载的，
// `ensureLanguage` 负责），装载完成前先用界面语言的 t 顶着，装好再换。
// 不传 / 传 null 时与 `useTranslation().t` 完全等价，调用方零变化。

import { useEffect, useMemo, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import { ensureLanguage, type AppLanguage } from "@/i18n/init";

export function useTurnT(language?: AppLanguage | null): TFunction {
  const { t, i18n } = useTranslation();
  const [ready, setReady] = useState<AppLanguage | null>(() =>
    language && i18n.hasResourceBundle(language, "app") ? language : null,
  );

  useEffect(() => {
    if (!language) return;
    if (i18n.hasResourceBundle(language, "app")) {
      setReady(language);
      return;
    }
    let cancelled = false;
    void ensureLanguage(language).then(() => {
      if (!cancelled) setReady(language);
    });
    return () => {
      cancelled = true;
    };
  }, [language, i18n]);

  return useMemo(() => {
    if (language && ready === language) {
      return i18n.getFixedT(language) as TFunction;
    }
    return t;
  }, [language, ready, i18n, t]);
}
