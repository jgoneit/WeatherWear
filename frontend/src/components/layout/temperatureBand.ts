/**
 * 오늘 기온 구간 (design.md 13절 "배경").
 * ≤5°C 아이스 블루, 6~16°C 세이지, 17~24°C 피치, ≥25°C 코랄.
 * 정수 사이 값은 아래 구간으로 내린다: 5.5°C는 세이지, 16.5°C는 세이지, 24.5°C는 피치.
 * 기온을 모르면(null, NaN) 세이지를 쓴다. body 기본 배경과 같은 색이다.
 */
export type TemperatureBand = "icy" | "sage" | "peach" | "coral";

export const TEMPERATURE_BANDS: readonly TemperatureBand[] = ["icy", "sage", "peach", "coral"];

export const DEFAULT_BAND: TemperatureBand = "sage";

export function temperatureBand(celsius: number | null | undefined): TemperatureBand {
  if (celsius === null || celsius === undefined || Number.isNaN(celsius)) return DEFAULT_BAND;
  if (celsius <= 5) return "icy";
  if (celsius < 17) return "sage";
  if (celsius < 25) return "peach";
  return "coral";
}

/** 구간별 메시 그라디언트 유틸리티 클래스 (`src/styles/app.css`의 `@utility mesh-*`). */
export const BAND_MESH_CLASS: Record<TemperatureBand, string> = {
  icy: "mesh-icy",
  sage: "mesh-sage",
  peach: "mesh-peach",
  coral: "mesh-coral",
};
