import TestWorkbenchHub from "@/components/test-workbench/TestWorkbenchHub";

// [fork] 二开扩展页面。结构照抄同组的 memory/page.tsx：
// page.tsx 是 Server Component，只做壳；文案与交互下沉到 client 组件
// （useTranslation 是 hook，必须在 "use client" 里）。
//
// 根容器 h-full + 自建 overflow-y-auto 是硬要求：AppShell 外层三层都是
// overflow-hidden，页面不自己开滚动容器就滚不动。禁用 h-screen/min-h-screen。
export default function TestWorkbenchPage() {
  return (
    <div className="h-full overflow-y-auto [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-6xl px-6 py-10 pb-16 md:px-10">
        <TestWorkbenchHub />
      </div>
    </div>
  );
}
