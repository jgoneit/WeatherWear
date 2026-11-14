import { STRINGS } from "../lib/strings";

/** fixture 태그, 데모 사이드카의 촬영 시각·위치에 붙이는 "가상 데이터" 라벨 (Req 15.4). */
export function VirtualDataLabel() {
  return (
    <span className="inline-flex shrink-0 items-center rounded-full border border-slate-900/15 bg-white/70 px-2 py-0.5 text-[11px] font-medium text-slate-700">
      {STRINGS.notice.virtualData}
    </span>
  );
}
