import { STRINGS } from "../lib/strings";
import { FixtureBadge } from "./FixtureBadge";

interface EstimateNoticeProps {
  /** 값이 fixture이면 "fixture" 배지를 함께 보인다. */
  isFixture?: boolean;
}

/** 날씨 값이 실측이 아니라 추정치/재분석값이라는 안내 (Req 4.9). */
export function EstimateNotice({ isFixture = false }: EstimateNoticeProps) {
  return (
    <p className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-slate-600">
      <span aria-hidden="true">ⓘ</span>
      <span className="min-w-0">{STRINGS.notice.estimate}</span>
      {isFixture ? <FixtureBadge /> : null}
    </p>
  );
}
