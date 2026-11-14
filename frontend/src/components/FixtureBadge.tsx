import { STRINGS } from "../lib/strings";

/** fixture 날씨 값에 붙이는 배지 (Req 4.9, 15.4). */
export function FixtureBadge() {
  return (
    <span className="inline-flex shrink-0 items-center rounded-full bg-slate-900/80 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-white uppercase">
      {STRINGS.notice.fixture}
    </span>
  );
}
