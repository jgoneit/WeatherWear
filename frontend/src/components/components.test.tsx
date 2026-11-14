import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { RecommendationItem, WeatherContext } from "../api/client";
import { STRINGS } from "../lib/strings";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { ErrorRetry } from "./ErrorRetry";
import { EstimateNotice } from "./EstimateNotice";
import { FallbackNotice } from "./FallbackNotice";
import { InsufficientDataState } from "./InsufficientDataState";
import { RecommendationCard } from "./RecommendationCard";
import { Toast } from "./Toast";
import { WeatherBento } from "./WeatherBento";
import { WeatherHero } from "./WeatherHero";

const today: WeatherContext = {
  actual_temperature_c: 9,
  apparent_temperature_c: 6.5,
  relative_humidity_pct: 55.4,
  wind_speed_kmh: 12,
  precipitation_mm: 0,
  observed_at: null,
  provider: "scenario",
  is_fixture: true,
  scenario_id: "chilly-morning",
  scenario_name: "쌀쌀한 아침",
};

function makeItem(overrides: Partial<RecommendationItem> = {}): RecommendationItem {
  return {
    rank: 1,
    score: 115,
    temperature_diff_c: 0,
    selected_by_fallback: false,
    confidence_level: "high",
    reasons: ["과거에 적당했음 (+15.0점)"],
    explanation:
      "오늘 9.0°C, 지난 9.0°C 기록과 기온 차이 0.0°C입니다. 당시 착장: varsity_jacket+hoodie+baggy_denim. 체감: 적당함 (과거에 적당했음, +15.0점).",
    outfit: {
      id: 81,
      captured_at: "2025-11-20T08:30:00+09:00",
      latitude: 37.5665,
      longitude: 126.978,
      source: "exif",
      metadata_synthetic: true,
      photo_url: "/api/outfits/81/photo",
      tags: {
        upper_type: "hoodie",
        outer_type: "varsity_jacket",
        lower_type: "baggy_denim",
        colors: ["navy"],
        layers_visible: 2,
        confidence: 0.9,
      },
      vision_provider: "fixture",
      weather: {
        observed_at: "2025-11-20T08:00:00+09:00",
        actual_temperature_c: 9,
        apparent_temperature_c: 7,
        relative_humidity_pct: 50,
        wind_speed_kmh: null,
        precipitation_mm: null,
        weather_code: null,
        provider: "fixture",
        is_fixture: true,
      },
      effective_vote: "comfortable",
    },
    ...overrides,
  };
}

describe("WeatherHero", () => {
  it("오늘 기온을 크게, 체감을 소수 첫째 자리로 보이고 추정치 안내와 fixture 배지를 붙인다", () => {
    render(<WeatherHero today={today} />);
    const hero = screen.getByTestId("hero-temperature");
    expect(hero).toHaveTextContent("9.0°C");
    expect(hero.className).toContain("text-7xl");
    expect(hero.className).toContain("tabular-nums");
    expect(screen.getByText(/체감 6\.5°C/)).toBeInTheDocument();
    expect(screen.getByText(/쌀쌀한 아침/)).toBeInTheDocument();
    expect(screen.getByText(STRINGS.notice.estimate)).toBeInTheDocument();
    expect(screen.getByText(STRINGS.notice.fixture)).toBeInTheDocument();
  });

  it("showNotice=false면 안내를 숨긴다", () => {
    render(<WeatherHero today={today} showNotice={false} />);
    expect(screen.queryByText(STRINGS.notice.estimate)).not.toBeInTheDocument();
  });
});

describe("WeatherBento", () => {
  it("체감·습도·강수·출처 타일 4개를 보인다", () => {
    render(<WeatherBento weather={today} />);
    const tiles = screen.getAllByRole("definition");
    expect(tiles).toHaveLength(4);
    expect(screen.getAllByRole("term").map((t) => t.textContent)).toEqual([
      STRINGS.home.apparentTemperature,
      STRINGS.home.humidity,
      STRINGS.home.precipitation,
      STRINGS.home.source,
    ]);
    expect(tiles[0]).toHaveTextContent("6.5°C");
    expect(tiles[1]).toHaveTextContent("55%");
    expect(tiles[2]).toHaveTextContent("0.0 mm");
    expect(tiles[3]).toHaveTextContent("시나리오 · 쌀쌀한 아침");
    expect(screen.getByText(STRINGS.notice.estimate)).toBeInTheDocument();
  });

  it("값이 없으면 –를 보이고 fixture가 아니면 배지를 붙이지 않는다", () => {
    render(
      <WeatherBento
        weather={{
          apparent_temperature_c: null,
          relative_humidity_pct: null,
          precipitation_mm: null,
          provider: "open_meteo",
          is_fixture: false,
        }}
      />,
    );
    const tiles = screen.getAllByRole("definition");
    expect(tiles.slice(0, 3).map((t) => t.textContent)).toEqual(["–", "–", "–"]);
    expect(tiles[3]).toHaveTextContent("Open-Meteo");
    expect(screen.queryByText(STRINGS.notice.fixture)).not.toBeInTheDocument();
  });
});

describe("안내·상태 컴포넌트", () => {
  it("EstimateNotice는 fixture일 때만 배지를 붙인다", () => {
    const { rerender } = render(<EstimateNotice />);
    expect(screen.getByText(STRINGS.notice.estimate)).toBeInTheDocument();
    expect(screen.queryByText(STRINGS.notice.fixture)).not.toBeInTheDocument();
    rerender(<EstimateNotice isFixture />);
    expect(screen.getByText(STRINGS.notice.fixture)).toBeInTheDocument();
  });

  it("FallbackNotice는 문구와 적용 허용 오차를 보인다", () => {
    render(<FallbackNotice appliedToleranceC={5} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(STRINGS.notice.fallback);
    expect(status).toHaveTextContent("5.0°C");
  });

  it("InsufficientDataState는 데이터 부족 문구를 보인다", () => {
    render(<InsufficientDataState />);
    expect(screen.getByRole("status")).toHaveTextContent(STRINGS.notice.insufficientData);
  });

  it.each([
    ["high", STRINGS.confidence.high],
    ["medium", STRINGS.confidence.medium],
    ["low", STRINGS.confidence.low],
  ] as const)("ConfidenceBadge %s", (level, label) => {
    render(<ConfidenceBadge level={level} />);
    expect(screen.getByText(label)).toHaveAttribute("data-level", level);
  });

  it("ErrorRetry는 메시지를 보이고 다시 시도를 호출하며 pending이면 비활성화한다", () => {
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorRetry onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent(STRINGS.error.loadFailed);
    fireEvent.click(screen.getByRole("button", { name: STRINGS.error.retry }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    rerender(<ErrorRetry onRetry={onRetry} pending />);
    expect(screen.getByRole("button", { name: STRINGS.error.retry })).toBeDisabled();
  });
});

describe("Toast", () => {
  it("메시지가 없으면 그리지 않는다", () => {
    const { container } = render(<Toast message={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("메시지를 보이고 시간이 지나면 onDismiss를 호출한다", () => {
    vi.useFakeTimers();
    try {
      const onDismiss = vi.fn();
      render(<Toast message={STRINGS.feedback.saveFailed} onDismiss={onDismiss} durationMs={1000} />);
      expect(screen.getByRole("status")).toHaveTextContent(STRINGS.feedback.saveFailed);
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(onDismiss).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("RecommendationCard", () => {
  it("사진, 과거 기온, 차이, 태그 칩, 신뢰도, 라벨을 보인다", () => {
    render(<RecommendationCard item={makeItem()} timeZone="Asia/Seoul" />);
    const card = screen.getByTestId("recommendation-card");
    expect(card.className).toContain("aspect-[4/5]");

    const img = within(card).getByRole("img");
    expect(img).toHaveAttribute("src", expect.stringMatching(/\/api\/outfits\/81\/photo$/));
    expect(img).toHaveAttribute("loading", "lazy");
    expect(img).toHaveAttribute("decoding", "async");

    expect(card).toHaveTextContent(STRINGS.recommendation.rank(1));
    expect(card).toHaveTextContent(`${STRINGS.recommendation.pastTemperature} 9.0°C`);
    expect(card).toHaveTextContent(`${STRINGS.recommendation.temperatureDiff} 0.0°C`);
    for (const tag of ["varsity_jacket", "hoodie", "baggy_denim"]) {
      expect(within(card).getByText(tag)).toBeInTheDocument();
    }
    expect(within(card).getByText(STRINGS.confidence.high)).toBeInTheDocument();
    expect(within(card).getByText(STRINGS.notice.fixture)).toBeInTheDocument();
    // 태그(fixture vision)와 촬영 시각·위치(demo_sidecar)에 각각 "가상 데이터"
    expect(within(card).getAllByText(STRINGS.notice.virtualData)).toHaveLength(2);
    expect(card).toHaveTextContent("2025-11-20");
    expect(within(card).queryByText(STRINGS.recommendation.fallbackChip)).not.toBeInTheDocument();
  });

  it("근거 펼치기로 설명 전체와 사유 목록을 보이고 다시 접는다", () => {
    render(<RecommendationCard item={makeItem()} />);
    const toggle = screen.getByRole("button", { name: STRINGS.recommendation.expand });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveTextContent(STRINGS.recommendation.collapse);
    expect(screen.getByRole("listitem")).toHaveTextContent("과거에 적당했음 (+15.0점)");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("태그가 없고 fallback 선택이며 fixture가 아니면 그에 맞게 표시한다", () => {
    const base = makeItem();
    const item = makeItem({
      selected_by_fallback: true,
      confidence_level: "low",
      outfit: {
        ...base.outfit,
        vision_provider: "gemini",
        tags: { ...base.outfit.tags, upper_type: "unknown", outer_type: "none", lower_type: "unknown" },
        weather: base.outfit.weather && { ...base.outfit.weather, is_fixture: false, provider: "open_meteo" },
      },
    });
    render(<RecommendationCard item={item} />);
    const card = screen.getByTestId("recommendation-card");
    expect(within(card).getAllByText(STRINGS.recommendation.noTags).length).toBeGreaterThan(0);
    expect(within(card).getByText(STRINGS.recommendation.fallbackChip)).toBeInTheDocument();
    expect(within(card).getByText(STRINGS.confidence.low)).toBeInTheDocument();
    expect(within(card).queryByText(STRINGS.notice.fixture)).not.toBeInTheDocument();
    expect(within(card).getAllByText(STRINGS.notice.virtualData)).toHaveLength(1);
  });

  it("detailHref가 있으면 사진을 상세 링크로 감싼다", () => {
    render(
      <MemoryRouter>
        <RecommendationCard item={makeItem()} detailHref="/outfits/81" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link")).toHaveAttribute("href", "/outfits/81");
  });
});
