import JourneyList from "@/components/test-journey/JourneyList";

// [fork] 测试旅程列表页。交互稿 s1；挂载位按 0015 选 (workspace)——
// 与 (utility) 的唯一实质差异是这一层原生带 UnifiedChatProvider，
// 而批次页要能「继续对话」带着上下文回聊天（s7 双向锚）。
//
// 根容器 h-full + 自建 overflow-y-auto 是硬要求：AppShell 外层三层都是
// overflow-hidden，页面不自己开滚动容器就滚不动。禁用 h-screen/min-h-screen。
export default function TestJourneyPage() {
  return (
    <div className="h-full overflow-y-auto [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-6xl px-6 py-10 pb-16 md:px-10">
        <JourneyList />
      </div>
    </div>
  );
}
