import { BAND_MESH_CLASS, TEMPERATURE_BANDS, temperatureBand } from "./temperatureBand";

interface TemperatureBackgroundProps {
  temperatureC: number | null;
}

/**
 * 기온 적응형 메시 그라디언트 배경.
 * CSS 그라디언트는 직접 전환되지 않으므로 구간별 레이어 4개를 겹치고 opacity를 600ms로 전환한다.
 * prefers-reduced-motion이면 전환 없이 바로 바뀐다.
 */
export function TemperatureBackground({ temperatureC }: TemperatureBackgroundProps) {
  const active = temperatureBand(temperatureC);
  return (
    <div
      aria-hidden="true"
      data-testid="temperature-background"
      data-band={active}
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
    >
      {TEMPERATURE_BANDS.map((band) => (
        <div
          key={band}
          data-band-layer={band}
          className={`absolute inset-0 ${BAND_MESH_CLASS[band]} transition-opacity duration-[600ms] ease-out motion-reduce:transition-none ${
            band === active ? "opacity-100" : "opacity-0"
          }`}
        />
      ))}
    </div>
  );
}
