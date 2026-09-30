import { useEffect, useState } from "react";
import { translateUi, useLanguage } from "../i18n";

const stageLabels: Record<string, string> = {
  WORLD_UPDATING: "世界更新中", WORLD_DONE: "世界更新中", VIEW_GENERATING: "视角生成中", VIEW_DONE: "视角生成中", NARRATION_GENERATING: "文段生成中", FAILED: "生成失败，等待房主处理",
};

export function RoundView({ round, stage }: { round: number | null; stage: string | null }) {
  const { language } = useLanguage();
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!stage || stage === "FAILED") return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [stage]);
  return <div className="round-bubble">
    <span>{language === "en" ? `Round ${round ?? "—"}` : `第 ${round ?? "—"} 回合`}</span>
    {stage && <span className="processing"> · {translateUi(stageLabels[stage] ?? stage, language)}{stage === "FAILED" ? "" : ` · ${elapsed}s`}</span>}
  </div>;
}
