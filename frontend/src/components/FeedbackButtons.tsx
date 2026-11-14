import type { MouseEvent } from "react";
import type { ThermalVote } from "../api/client";
import { STRINGS } from "../lib/strings";

/** 버튼 순서와 Thermal_Vote 대응 (Req 13.2): 추웠어=cold, 적당했어=comfortable, 더웠어=hot. */
export const FEEDBACK_OPTIONS: ReadonlyArray<{ vote: ThermalVote; label: string; selectedClass: string }> = [
  { vote: "cold", label: STRINGS.feedback.cold, selectedClass: "bg-cold" },
  { vote: "comfortable", label: STRINGS.feedback.comfortable, selectedClass: "bg-mild" },
  { vote: "hot", label: STRINGS.feedback.hot, selectedClass: "bg-hot" },
];

interface FeedbackButtonsProps {
  /** 선택 상태로 표시할 투표. 보통 Outfit의 effective_vote (Req 13.3). */
  selected: ThermalVote | null;
  /** 저장 요청 대기 중이면 3버튼을 모두 비활성화한다 (Req 13.5). */
  pending: boolean;
  /** 대기 중에 탭한 버튼. 저장 중 표시에만 쓰고 선택 상태는 바꾸지 않는다. */
  pendingVote?: ThermalVote | null;
  onSelect: (vote: ThermalVote) => void;
}

/**
 * 체감 피드백 pill 버튼 3개.
 * 버튼 영역의 클릭은 부모(Notification_Card 본문 이동)로 전파하지 않는다 (Req 13.8).
 */
export function FeedbackButtons({ selected, pending, pendingVote = null, onSelect }: FeedbackButtonsProps) {
  const stop = (event: MouseEvent) => event.stopPropagation();

  return (
    <div
      role="group"
      aria-label={STRINGS.feedback.buttonsLabel}
      aria-busy={pending}
      onClick={stop}
      className="grid min-w-0 grid-cols-3 gap-2"
    >
      {FEEDBACK_OPTIONS.map(({ vote, label, selectedClass }) => {
        const isSelected = selected === vote;
        const isSaving = pending && pendingVote === vote;
        return (
          <button
            key={vote}
            type="button"
            data-vote={vote}
            aria-pressed={isSelected}
            disabled={pending}
            onClick={(event) => {
              event.stopPropagation();
              if (!pending) onSelect(vote);
            }}
            className={[
              "flex min-h-11 min-w-0 items-center justify-center gap-1 rounded-full px-2 text-sm font-semibold whitespace-nowrap",
              "transition-transform duration-120 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900",
              "disabled:cursor-not-allowed disabled:opacity-60",
              isSelected
                ? `${selectedClass} text-white shadow-sm`
                : "border border-white/70 bg-white/70 text-slate-900",
              isSaving ? "animate-pulse motion-reduce:animate-none" : "",
            ].join(" ")}
          >
            {isSelected ? <span aria-hidden="true">✓</span> : null}
            <span className="min-w-0 truncate">{label}</span>
          </button>
        );
      })}
    </div>
  );
}
