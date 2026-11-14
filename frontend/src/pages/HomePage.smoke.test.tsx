import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RecommendationItem, RecommendationResponse } from "../api/client";
import { STRINGS } from "../lib/strings";
import { HomePage } from "./HomePage";

function item(rank: number, id: number, fallback = false): RecommendationItem {
  return {
    rank,
    outfit: {
      id,
      captured_at: "2025-11-20T08:30:00+09:00",
      latitude: 37.5665,
      longitude: 126.978,
      source: "demo_sidecar",
      photo_url: `/api/outfits/${id}/photo`,
      tags: {
        upper_type: "knit",
        outer_type: "coat",
        lower_type: "slacks",
        colors: [],
        layers_visible: 2,
        confidence: 0.9,
      },
      vision_provider: "fixture",
      weather: {
        observed_at: "2025-11-20T08:00:00+09:00",
        actual_temperature_c: 8,
        apparent_temperature_c: null,
        relative_humidity_pct: null,
        wind_speed_kmh: null,
        precipitation_mm: null,
        weather_code: null,
        provider: "fixture",
        is_fixture: true,
      },
      effective_vote: null,
    },
    score: 110 - rank,
    temperature_diff_c: 1,
    selected_by_fallback: fallback,
    confidence_level: "medium",
    reasons: [],
    explanation: `설명 ${id}`,
  };
}

function response(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    status: "ok",
    algorithm_version: "rule_based_v1",
    fallback: false,
    applied_tolerance_c: 3,
    today: {
      actual_temperature_c: 9,
      apparent_temperature_c: 6.5,
      relative_humidity_pct: 55,
      wind_speed_kmh: 12,
      precipitation_mm: 0,
      observed_at: null,
      provider: "scenario",
      is_fixture: true,
      scenario_id: "chilly-morning",
      scenario_name: "쌀쌀한 아침",
    },
    items: [1, 2, 3, 4, 5].map((rank) => item(rank, 100 + rank)),
    ...overrides,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** URL별로 응답을 고르는 fetch 대체. 추천 응답은 호출 순서대로 꺼낸다. */
function stubFetch(recommendationResponses: Array<() => Response>) {
  const queue = [...recommendationResponses];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/meta")) {
      return json({
        app_timezone: "Asia/Seoul",
        algorithm_version: "rule_based_v1",
        vision_provider: "fixture",
        weather_provider: "fixture",
      });
    }
    const next = queue.length > 1 ? queue.shift()! : queue[0]!;
    return next();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderHome(isDev = false) {
  await act(async () => {
    render(
      <MemoryRouter>
        <HomePage isDev={isDev} />
      </MemoryRouter>,
    );
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HomePage smoke", () => {
  it("shows weather, notification card, top card and 2~5 carousel without inputs", async () => {
    const fetchMock = stubFetch([() => json(response())]);
    await renderHome();

    expect(screen.getByTestId("hero-temperature")).toHaveTextContent("9.0°C");
    expect(screen.getByTestId("notification-card")).toBeInTheDocument();
    const carousel = screen.getByTestId("recommendation-carousel");
    expect(within(carousel).getAllByTestId("recommendation-card")).toHaveLength(4);
    expect(screen.getAllByTestId("recommendation-card")).toHaveLength(5);
    expect(screen.queryByText(STRINGS.notice.fallback)).not.toBeInTheDocument();
    expect(document.querySelector("input, textarea, select")).toBeNull();
    expect(screen.queryByText(STRINGS.home.demoLink)).not.toBeInTheDocument();
    const recCalls = fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/recommendations/today"));
    expect(recCalls).toHaveLength(1);
  });

  it("shows FallbackNotice when fallback is true", async () => {
    stubFetch([() => json(response({ fallback: true, applied_tolerance_c: 5, items: [item(1, 7, true)] }))]);
    await renderHome();
    expect(screen.getByText(STRINGS.notice.fallback)).toBeInTheDocument();
    expect(screen.queryByTestId("recommendation-carousel")).not.toBeInTheDocument();
  });

  it("hides recommendation and notification cards for insufficient_data", async () => {
    stubFetch([() => json(response({ status: "insufficient_data", fallback: true, applied_tolerance_c: 5, items: [] }))]);
    await renderHome();
    expect(screen.getByText(STRINGS.notice.insufficientData)).toBeInTheDocument();
    expect(screen.queryByTestId("notification-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("recommendation-card")).not.toBeInTheDocument();
    expect(screen.getByTestId("hero-temperature")).toHaveTextContent("9.0°C");
  });

  it("keeps previous content and shows ErrorRetry when a reload fails", async () => {
    stubFetch([
      () => json(response()),
      () => json({ error: { code: "INTERNAL_ERROR", message: "x" } }, 500),
      () => json(response()),
    ]);
    await renderHome();
    expect(screen.getByTestId("notification-card")).toBeInTheDocument();

    // 피드백 저장 성공 → onSaved → 추천 재조회(500 실패). 다음 fetch 1회만 피드백 응답으로 바꾼다.
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementationOnce(async () => json({ outfit_id: 101, thermal_vote: "comfortable", created_at: "2025-11-20T00:00:00Z" }, 201));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /적당했어/ }));
    });

    expect(screen.getByRole("alert")).toHaveTextContent(STRINGS.error.loadFailed);
    expect(screen.getByTestId("hero-temperature")).toHaveTextContent("9.0°C");
    expect(screen.getByTestId("notification-card")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: STRINGS.error.retry }));
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the demo link only in dev mode", async () => {
    stubFetch([() => json(response())]);
    await renderHome(true);
    expect(screen.getByRole("link", { name: STRINGS.home.demoLink })).toHaveAttribute("href", "/demo");
  });
});
