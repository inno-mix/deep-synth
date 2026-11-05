import { Suspense } from "react";
import { RunView } from "@/components/RunView";

export default function RunPage({ params }: PageProps<"/runs/[id]">) {
  return (
    <Suspense fallback={<p className="pt-16 text-ink-faint">Loading run…</p>}>
      <RunView params={params} />
    </Suspense>
  );
}
