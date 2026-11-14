import { useCallback, useMemo } from "react";
import { Link } from "react-router";
import { api, type RecommendationResponse } from "../api/client";
import { ErrorRetry } from "../components/ErrorRetry";
import { DemoWeatherPicker } from "../components/DemoWeatherPicker";
import { FallbackNotice } from "../components/FallbackNotice";
import { InsufficientDataState } from "../components/InsufficientDataState";
import { useReportTodayTemperature } from "../components/layout/TodayTemperatureContext";
import { NotificationCard } from "../components/NotificationCard";
import { RecommendationCard } from "../components/RecommendationCard";
import { WeatherBento } from "../components/WeatherBento";
import { WeatherHero } from "../components/WeatherHero";
import { IS_DEV, IS_PUBLIC_DEMO } from "../lib/env";
import { FALLBACK_TIMEZONE, formatDate } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";

interface HomePageProps {
  /** 데모 진입 링크 노출 여부. 테스트에서 바꿀 때 쓴다. 기본값은 `IS_DEV` (Req 14.1, 14.2). */
  isDev?: boolean;
}

const outfitHref = (id: number) => `/outfits/${id}`;

/** Daily weather, outfit recommendation and feedback; technical detail is opt-in. */
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
    <div className="home-page">
      <div className="page-heading">
        <div><h1>{STRINGS.home.title}</h1><p>날씨에 맞는, 나다운 옷.</p></div>
        {data ? <time>{todayDate.replaceAll("-", ".")}</time> : null}
      </div>
      {recommendations.error !== undefined ? <ErrorRetry onRetry={handleRetry} pending={recommendations.pending} /> : null}
      {data === undefined && recommendations.error === undefined ? <p role="status" className="glass rounded-3xl p-5">{STRINGS.home.loading}</p> : null}
      {data ? <>
        {IS_PUBLIC_DEMO ? <DemoWeatherPicker selected={data.today.scenario_id} onChange={handleRetry} /> : null}
        <div className="home-dashboard">
          <div className="home-weather"><WeatherHero today={data.today} /></div>
          <div className="home-metrics"><WeatherBento weather={data.today} showNotice={false}/></div>
          <section aria-label={STRINGS.home.topRecommendation} className="home-feature">
            {hasItems && top ? <>
              {data.fallback ? <FallbackNotice appliedToleranceC={data.applied_tolerance_c}/> : null}
              <RecommendationCard item={top} featured timeZone={timeZone} detailHref={outfitHref(top.outfit.id)}/>
            </> : <InsufficientDataState/>}
          </section>
          {hasItems && top ? <div className="home-feedback"><NotificationCard outfit={top.outfit} todayTemperatureC={data.today.actual_temperature_c} onSaved={handleSaved}/></div> : null}
        </div>
        {hasItems && rest.length > 0 ? <section aria-label={STRINGS.home.otherRecommendations} className="other-section">
          <div className="section-heading"><h2>{STRINGS.home.otherRecommendations}</h2><Link to="/history">모든 기록 보기 →</Link></div>
          <ul data-testid="recommendation-carousel" className="recommendation-list">
            {rest.map(item => <li key={item.outfit.id}><RecommendationCard item={item} timeZone={timeZone} detailHref={outfitHref(item.outfit.id)}/></li>)}
          </ul>
        </section> : null}
        <details className="data-disclosure"><summary>이 추천에 관하여</summary>
          <p>과거 착장의 기온과 체감 기록을 바탕으로 추천해요. {data.today.is_fixture ? "현재 데모 날씨와 예시 착장 데이터를 사용하고 있어요." : "날씨는 제공자의 추정치이며 실제 관측과 다를 수 있어요."}</p>
          {data.today.scenario_name ? <p>선택한 데모 날씨: {data.today.scenario_name}</p> : null}
          {isDev ? <Link to="/demo">{STRINGS.home.demoLink} →</Link> : null}
        </details>
      </> : null}
    </div>
  );
}
