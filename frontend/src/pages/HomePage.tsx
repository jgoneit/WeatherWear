import { useCallback, useMemo } from "react";
import { Link } from "react-router";
import { api, type RecommendationResponse } from "../api/client";
import { ErrorRetry } from "../components/ErrorRetry";
import { FallbackNotice } from "../components/FallbackNotice";
import { InsufficientDataState } from "../components/InsufficientDataState";
import { useReportTodayTemperature } from "../components/layout/TodayTemperatureContext";
import { NotificationCard } from "../components/NotificationCard";
import { RecommendationCard } from "../components/RecommendationCard";
import { WeatherBento } from "../components/WeatherBento";
import { WeatherHero } from "../components/WeatherHero";
import { IS_DEV } from "../lib/env";
import { FALLBACK_TIMEZONE, formatDate } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";

interface HomePageProps {
  /** 데모 진입 링크 노출 여부. 테스트에서 바꿀 때 쓴다. 기본값은 `IS_DEV` (Req 14.1, 14.2). */
  isDev?: boolean;
}

const outfitHref = (id: number) => `/outfits/${id}`;

/**
 * 홈 화면 (design.md 13절 홈 레이아웃).
 * 히어로 기온 → Notification_Card → 1순위 추천 카드 → 날씨 Bento → 2~5순위 가로 캐러셀.
 * 화면 데이터는 GET /api/recommendations/today 1회로 받고(오늘 날씨 포함),
 * 날짜 표시 타임존만 GET /api/meta에서 읽는다 (Req 11.1, 11.4).
 * 수동 입력 필드는 두지 않는다 (Req 11.7).
 */
export function HomePage({ isDev = IS_DEV }: HomePageProps) {
  const recommendations = useAsync((signal) => api.getRecommendations({ signal }), []);
  const meta = useAsync((signal) => api.getMeta({ signal }), []);

  const data: RecommendationResponse | undefined = recommendations.data;
  const timeZone = meta.data?.app_timezone ?? FALLBACK_TIMEZONE;
  const reload = recommendations.reload;

  // 셸 배경 그라디언트에 오늘 기온을 알린다. 실패해도 이전 값을 유지한다.
  useReportTodayTemperature(data?.today.actual_temperature_c);

  const handleRetry = useCallback(() => {
    void reload();
  }, [reload]);
  const handleSaved = useCallback(() => {
    void reload();
  }, [reload]);

  // 시나리오는 observed_at이 없으므로 그때는 현재 시각으로 오늘 날짜를 표시한다.
  const todayIso = data?.today.observed_at ?? null;
  const todayDate = useMemo(
    () => formatDate(todayIso ?? new Date().toISOString(), timeZone),
    [todayIso, timeZone],
  );

  const hasItems = data !== undefined && data.status === "ok" && data.items.length > 0;
  const [top, ...rest] = hasItems ? data.items : [];

  return (
    <div className="flex w-full max-w-full min-w-0 flex-col gap-4">
      <div className="flex min-w-0 items-baseline justify-between gap-3 px-1">
        <h1 className="min-w-0 truncate text-lg font-semibold tracking-tight text-slate-900">
          {STRINGS.home.title}
        </h1>
        {data ? (
          <p className="shrink-0 text-sm text-slate-700 tabular-nums">{STRINGS.home.todayDate(todayDate)}</p>
        ) : null}
      </div>

      {recommendations.error !== undefined ? (
        <ErrorRetry onRetry={handleRetry} pending={recommendations.pending} />
      ) : null}

      {data === undefined && recommendations.error === undefined ? (
        <p role="status" className="glass rounded-3xl px-4 py-3 text-sm text-slate-700">
          {STRINGS.home.loading}
        </p>
      ) : null}

      {data ? (
        <>
          <WeatherHero today={data.today} />

          {hasItems && top ? (
            <>
              <NotificationCard
                outfit={top.outfit}
                todayTemperatureC={data.today.actual_temperature_c}
                onSaved={handleSaved}
              />

              <section aria-label={STRINGS.home.topRecommendation} className="flex min-w-0 flex-col gap-3">
                {data.fallback ? <FallbackNotice appliedToleranceC={data.applied_tolerance_c} /> : null}
                <RecommendationCard item={top} timeZone={timeZone} detailHref={outfitHref(top.outfit.id)} />
              </section>
            </>
          ) : (
            <InsufficientDataState />
          )}

          <WeatherBento weather={data.today} showNotice={false} />

          {hasItems && rest.length > 0 ? (
            <section aria-label={STRINGS.home.otherRecommendations} className="min-w-0">
              <h2 className="px-1 text-base font-semibold text-slate-900">{STRINGS.home.otherRecommendations}</h2>
              <ul
                data-testid="recommendation-carousel"
                className="mt-2 flex w-full min-w-0 snap-x snap-mandatory gap-3 overflow-x-auto overscroll-x-contain pb-2 [scrollbar-width:none]"
              >
                {rest.map((item) => (
                  <li key={item.outfit.id} className="w-[80%] max-w-[340px] shrink-0 snap-start">
                    <RecommendationCard item={item} timeZone={timeZone} detailHref={outfitHref(item.outfit.id)} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}

      {isDev ? (
        <Link
          to="/demo"
          className="glass flex min-h-11 items-center justify-center rounded-full px-4 text-sm font-semibold text-slate-900"
        >
          {STRINGS.home.demoLink}
        </Link>
      ) : null}
    </div>
  );
}
