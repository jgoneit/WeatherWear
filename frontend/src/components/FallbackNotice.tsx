import { formatTemp } from "../lib/format";
import { STRINGS } from "../lib/strings";

interface FallbackNoticeProps {
  /** 실제로 적용된 허용 오차(°C). 응답의 `applied_tolerance_c`. */
  appliedToleranceC?: number | null;
}

/** 허용 오차를 넓혀 추천했을 때 추천 영역 위에 두는 안내 (Req 6.4). */
export function FallbackNotice({ appliedToleranceC }: FallbackNoticeProps) {
  return (
    <div role="status" className="glass min-w-0 rounded-3xl px-4 py-3">
      <p className="text-sm font-semibold text-slate-900">{STRINGS.notice.fallback}</p>
      {typeof appliedToleranceC === "number" ? (
        <p className="mt-0.5 text-xs text-slate-600">
          {STRINGS.recommendation.fallbackDetail(formatTemp(appliedToleranceC))}
        </p>
      ) : null}
    </div>
  );
}
