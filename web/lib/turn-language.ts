// [fork] 一轮的回复语种（BB-698）。
//
// 「测试」模式在服务端按用户的话判定语种，并在事件流里放一条
// `progress` 事件：`metadata.turn_language = "zh" | "en"`
// （`deeptutor/agents/test/pipeline.py:_announce_turn_language`）。
// 卡片外壳（ask_user 的 Pick an option / Next question、四张富卡的标题与页脚）
// 按它取文案，而不是按界面语言——否则中文提问、界面 en 时，同一张卡正文中文、
// 外壳英文。
//
// 没有这条事件（普通聊天、旧会话）返回 null，调用方照旧跟界面语言：零变化。

import type { AppLanguage } from "@/i18n/init";
import type { StreamEvent } from "@/lib/unified-ws";

export function turnLanguageOf(
  events: StreamEvent[] | undefined,
): AppLanguage | null {
  if (!events || events.length === 0) return null;
  // 从后往前：同一轮里只会有一条，但重放/编辑分支可能带旧事件，以最新为准。
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const meta = (events[i]?.metadata ?? {}) as Record<string, unknown>;
    const lang = meta.turn_language;
    if (lang === "zh" || lang === "en") return lang;
  }
  return null;
}
