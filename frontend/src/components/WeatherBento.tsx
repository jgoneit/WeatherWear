import type { ReactNode } from "react";
import type { WeatherContext, WeatherSnapshot } from "../api/client";
import { EMPTY_VALUE, formatHumidity } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { EstimateNotice } from "./EstimateNotice";

type BentoWeather = Pick<
  WeatherContext | WeatherSnapshot,
  "apparent_temperature_c" | "relative_humidity_pct" | "precipitation_mm" | "is_fixture"
> & {
  provider: WeatherContext["provider"] | WeatherSnapshot["provider"];
  scenario_name?: string | null;
};

interface WeatherBentoProps {
  weather: BentoWeather;
  /** 추정치 안내를 함께 보일지. 같은 화면에 안내가 이미 있으면 끈다. */
  showNotice?: boolean;
}

function formatPrecipitation(mm: number | null | undefined): string {
  if (typeof mm !== "number" || !Number.isFinite(mm)) return EMPTY_VALUE;
  return `${mm.toFixed(1)} ${STRINGS.weather.precipitationUnit}`;
}

function Tile({ label, value, extra }: { label: string; value: string; extra?: ReactNode }) {
  return (
    <div className="weather-tile">
      <dt className="text-xs font-medium text-slate-600">{label}</dt>
      <dd className="flex min-w-0 flex-wrap items-center gap-1.5 text-xl font-semibold text-slate-900 tabular-nums">
        <span className="min-w-0 truncate">{value}</span>
        {extra}
      </dd>
    </div>
  );
}

/** Secondary weather information; provenance is disclosed separately on Home. */
export function WeatherBento({ weather, showNotice = true }: WeatherBentoProps) {
  return (
    <section aria-label={STRINGS.weather.bentoLabel} className="weather-metrics min-w-0">
      <dl className="grid grid-cols-2 gap-3">
        <Tile label={STRINGS.home.humidity} value={formatHumidity(weather.relative_humidity_pct)} />
        <Tile label={STRINGS.home.precipitation} value={formatPrecipitation(weather.precipitation_mm)} />

      </dl>
      {showNotice ? (
        <div className="mt-2 px-1">
          <EstimateNotice isFixture={weather.is_fixture} />
        </div>
      ) : null}
    </section>
  );
}
