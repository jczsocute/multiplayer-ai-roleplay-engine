import { useEffect, useState } from "react";

const stageLabels: Record<string, string> = {
  WORLD_UPDATING: "世界更新中", VIEW_GENERATING: "视角生成中", NARRATION_GENERATING: "文段生成中",
};

export function RoundView({ round, stage }: { round: number | null; stage: string | null }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!stage) return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [stage]);
  return <div className="round-bubble">
    <span>Round {round ?? "—"}</span>
    {stage && <span className="processing"> · {stageLabels[stage] ?? stage} · {elapsed}s</span>}
  </div>;
}
