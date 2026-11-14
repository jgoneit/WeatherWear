import type { RecommendationItem } from "../api/client";
import { STRINGS } from "../lib/strings";

type ConfidenceLevel = RecommendationItem["confidence_level"];

const LEVEL_CLASS: Record<ConfidenceLevel, string> = {
  high: "bg-mild text-white",
  medium: "bg-warm text-white",
  low: "bg-slate-500 text-white",
};

/** 추천 신뢰도 배지 (Req 8.5). */
export function ConfidenceBadge({ level }: { level: ConfidenceLevel }) {
  return (
    <span
      data-level={level}
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${LEVEL_CLASS[level]}`}
    >
      {STRINGS.confidence[level]}
    </span>
  );
}
