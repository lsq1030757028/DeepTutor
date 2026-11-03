import JourneyDetail from "@/components/test-journey/JourneyDetail";

// [fork] 批次详情页。交互稿 s2/s3/s4/s5。
// Server Component 只做壳，把 batchId 传给 client 组件（useState/useEffect 要在
// "use client" 里）。滚动容器同列表页。
export default async function TestJourneyDetailPage({
  params,
}: {
  params: Promise<{ batchId: string }>;
}) {
  const { batchId } = await params;
  return (
    <div className="h-full overflow-y-auto [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-6xl px-6 py-10 pb-16 md:px-10">
        <JourneyDetail batchId={batchId} />
      </div>
    </div>
  );
}
