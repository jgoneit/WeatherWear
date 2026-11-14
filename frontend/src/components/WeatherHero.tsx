import type { WeatherContext } from "../api/client";
import { formatTemp } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { Icon } from "./Icon";
interface WeatherHeroProps {
  today: Pick<WeatherContext, "actual_temperature_c" | "apparent_temperature_c" | "is_fixture" | "scenario_name">;
}
export function WeatherHero({ today }: WeatherHeroProps) {
  const temp = today.actual_temperature_c;
  return <section aria-label={STRINGS.home.todayTemperature} className="weather-hero">
    <div className="weather-heading"><span>{STRINGS.home.todayTemperature}</span><Icon name="temperature" size={24}/></div>
    <p data-testid="hero-temperature" className="hero-temperature tabular-nums">{temp == null ? "–" : `${temp}°`}</p>
    <p className="weather-feels">체감 {formatTemp(today.apparent_temperature_c)}</p>
  </section>;
}
