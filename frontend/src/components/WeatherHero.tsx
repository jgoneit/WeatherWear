import type { WeatherContext } from "../api/client";
import { formatTemp } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { EstimateNotice } from "./EstimateNotice";

interface WeatherHeroProps {
  today: Pick<
    WeatherContext,
    "actual_temperature_c" | "apparent_temperature_c" | "is_fixture" | "scenario_name"
  >;
  /** 추정치 안내를 함께 보일지. 같은 화면에 안내가 이미 있으면 끈다. */
  showNotice?: boolean;
}

/** 오늘 기온을 크게 보여주는 히어로 영역 (Req 11.1, 11.2). */
export function WeatherHero({ today, showNotice = true }: WeatherHeroProps) {
  return (
    <section aria-label={STRINGS.home.todayTemperature} className="min-w-0 px-1">
      <p className="text-sm font-medium text-slate-700">
        {STRINGS.home.todayTemperature}
        {today.scenario_name ? (
          <span className="ml-2 text-slate-500">
            · {STRINGS.weather.providerScenario}: {today.scenario_name}
          </span>
        ) : null}
      </p>
      <p
        data-testid="hero-temperature"
        className="text-7xl font-semibold tracking-tight text-slate-900 tabular-nums"
      >
        {formatTemp(today.actual_temperature_c)}
      </p>
      <p className="mt-1 text-base text-slate-700 tabular-nums">
        {STRINGS.weather.apparentPrefix} {formatTemp(today.apparent_temperature_c)}
      </p>
      {showNotice ? (
        <div className="mt-2">
          <EstimateNotice isFixture={today.is_fixture} />
        </div>
      ) : null}
    </section>
  );
}
