import { Link } from "react-router";
import { api, type Outfit } from "../api/client";
import { ErrorRetry } from "../components/ErrorRetry";
import { OutfitGridItem } from "../components/OutfitGridItem";
import { IS_DEV } from "../lib/env";
import { FALLBACK_TIMEZONE, formatDate } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";

export interface OutfitDateSection {
  /** APP_TIMEZONE 기준 촬영일 `YYYY-MM-DD`. */
  date: string;
  outfits: Outfit[];
}

/**
 * API 반환 순서를 그대로 유지하면서, 연속된 같은 날짜끼리 섹션으로 묶는다 (Req 12.1).
 * 정렬을 다시 하지 않으므로 항목 순서는 GET /api/outfits 응답과 같다.
 */
export function groupOutfitsByDate(outfits: readonly Outfit[], timeZone: string): OutfitDateSection[] {
  const sections: OutfitDateSection[] = [];
  for (const outfit of outfits) {
    const date = formatDate(outfit.captured_at, timeZone);
    const last = sections[sections.length - 1];
    if (last && last.date === date) last.outfits.push(outfit);
    else sections.push({ date, outfits: [outfit] });
  }
  return sections;
}

/**
 * 착장 기록 화면 (Req 12.1, 12.2, 12.3, 12.4, 12.6, 12.7).
 * 2열 사진 그리드를 날짜 섹션(sticky 글래스 헤더)으로 나눠 보여준다.
 * 재조회가 실패해도 이미 표시한 목록은 유지하고 오류와 다시 시도 버튼을 함께 보인다.
 */
export function HistoryPage() {
  const outfits = useAsync((signal) => api.getOutfits({ signal }), []);
  // 타임존은 날짜 표시에만 쓰므로, 메타 요청이 실패해도 기본 타임존으로 목록을 보여준다.
  const meta = useAsync((signal) => api.getMeta({ signal }), []);
  const timeZone = meta.data?.app_timezone ?? FALLBACK_TIMEZONE;

  const list = outfits.data;
  const hasError = outfits.error !== undefined;

  return (
    <section className="flex min-w-0 flex-col gap-4" aria-busy={outfits.pending}>
      <h1 className="px-1 text-2xl font-semibold tracking-tight text-slate-900">{STRINGS.history.title}</h1>

      {hasError ? (
        <ErrorRetry
          message={STRINGS.history.loadFailed}
          pending={outfits.pending}
          onRetry={() => void outfits.reload()}
        />
      ) : null}

      {list === undefined ? (
        !hasError && outfits.pending ? (
          <p className="glass rounded-3xl px-4 py-3 text-sm text-slate-700">{STRINGS.history.loading}</p>
        ) : null
      ) : list.length === 0 ? (
        <EmptyHistory />
      ) : (
        <div className="flex min-w-0 flex-col gap-5">
          {groupOutfitsByDate(list, timeZone).map((section, index) => (
            <section key={`${section.date}-${index}`} aria-label={STRINGS.history.listLabel(section.date)} className="min-w-0">
              <h2 className="glass sticky top-[calc(3.5rem+env(safe-area-inset-top))] z-10 mb-3 flex items-center justify-between gap-2 rounded-full px-4 py-1.5 text-sm font-semibold text-slate-900 tabular-nums">
                <span>{section.date}</span>
                <span className="text-xs font-medium text-slate-600">{STRINGS.history.count(section.outfits.length)}</span>
              </h2>
              <ul className="grid min-w-0 grid-cols-2 gap-3">
                {section.outfits.map((outfit) => (
                  <li key={outfit.id} className="min-w-0">
                    <OutfitGridItem outfit={outfit} timeZone={timeZone} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

/** 기록 0건일 때의 안내 (Req 12.6). 데모 화면 링크는 개발 모드에서만 보인다 (Req 14.2). */
function EmptyHistory() {
  return (
    <div className="glass flex min-w-0 flex-col items-start gap-3 rounded-3xl p-5">
      <p className="text-sm text-slate-800">{STRINGS.history.empty}</p>
      {IS_DEV ? (
        <Link
          to="/demo"
          className="inline-flex min-h-11 items-center rounded-full bg-slate-900 px-4 text-sm font-semibold text-white"
        >
          {STRINGS.history.emptyDemoLink}
        </Link>
      ) : null}
    </div>
  );
}
