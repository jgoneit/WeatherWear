import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Outfit } from "../api/client";
import { STRINGS } from "../lib/strings";
import { OutfitDetailPage } from "./OutfitDetailPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const meta = {
  app_timezone: "Asia/Seoul",
  algorithm_version: "rule_based_v1",
  vision_provider: "fixture",
  weather_provider: "fixture",
};

function makeOutfit(overrides: Partial<Outfit> = {}): Outfit {
  return {
    id: 21,
    captured_at: "2025-11-20T08:30:00+09:00",
    latitude: 37.5665,
    longitude: 126.978,
    source: "demo_sidecar",
    photo_url: "/api/outfits/21/photo",
    tags: {
      upper_type: "sweater",
      outer_type: "none",
      lower_type: "unknown",
      colors: ["cream", "navy"],
      layers_visible: 1,
      confidence: 0.85,
    },
    vision_provider: "fixture",
    weather: {
      observed_at: "2025-11-20T08:00:00+09:00",
      actual_temperature_c: 8,
      apparent_temperature_c: 6.5,
      relative_humidity_pct: null,
      wind_speed_kmh: 12,
      precipitation_mm: null,
      weather_code: null,
      provider: "fixture",
      is_fixture: true,
    },
    effective_vote: "comfortable",
    ...overrides,
  };
}

/** /api/meta와 /api/outfits/:id 응답을 돌려주는 fetch. */
function stubFetch(outfitResponse: () => Response) {
  const fetchMock = vi.fn(async (url: string) =>
    String(url).endsWith("/api/meta") ? jsonResponse(meta) : outfitResponse(),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/outfits/:id" element={<OutfitDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("OutfitDetailPage smoke", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("사진, 태그 전체, 값이 있는 날씨 타일, 평가, 라벨을 보여준다", async () => {
    stubFetch(() => jsonResponse(makeOutfit()));
    renderAt("/outfits/21");

    expect(await screen.findByTestId("outfit-detail")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "/api/outfits/21/photo");

    // 태그 6개 필드
    expect(screen.getByText("sweater")).toBeInTheDocument();
    expect(screen.getByText(STRINGS.detail.tagNone)).toBeInTheDocument();
    expect(screen.getByText(STRINGS.detail.tagUnknown)).toBeInTheDocument();
    expect(screen.getByText("cream")).toBeInTheDocument();
    expect(screen.getByText("navy")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();

    // 날씨: 실제·체감·풍속만, 습도·강수(null)는 타일 없음
    expect(screen.getByText("8.0°C")).toBeInTheDocument();
    expect(screen.getByText("6.5°C")).toBeInTheDocument();
    expect(screen.getByText("12.0 km/h")).toBeInTheDocument();
    expect(screen.queryByText(STRINGS.home.humidity)).not.toBeInTheDocument();
    expect(screen.queryByText(STRINGS.home.precipitation)).not.toBeInTheDocument();
    expect(screen.getByText(STRINGS.notice.estimate)).toBeInTheDocument();
    expect(screen.getByText(STRINGS.notice.fixture)).toBeInTheDocument();

    // 평가, 촬영 시각(서울), 가상 데이터 라벨(태그 + 메타)
    expect(screen.getByText(STRINGS.vote.comfortable)).toBeInTheDocument();
    expect(screen.getByText("2025-11-20 08:30")).toBeInTheDocument();
    expect(screen.getAllByText(STRINGS.notice.virtualData)).toHaveLength(2);
  });

  it("날씨가 없고 미평가인 착장은 안내 문구를 보여준다", async () => {
    stubFetch(() =>
      jsonResponse(makeOutfit({ weather: null, effective_vote: null, vision_provider: "gemini" })),
    );
    renderAt("/outfits/21");

    expect(await screen.findByText(STRINGS.detail.noWeather)).toBeInTheDocument();
    expect(screen.getByText(STRINGS.vote.none)).toBeInTheDocument();
    expect(screen.queryByText(STRINGS.notice.fixture)).not.toBeInTheDocument();
    expect(screen.getAllByText(STRINGS.notice.virtualData)).toHaveLength(1);
  });

  it("404면 오류와 다시 시도를 보여주고, 다시 시도하면 다시 요청한다", async () => {
    let calls = 0;
    const fetchMock = stubFetch(() => {
      calls += 1;
      return calls === 1
        ? jsonResponse({ error: { code: "OUTFIT_NOT_FOUND", message: "착장을 찾을 수 없습니다." } }, 404)
        : jsonResponse(makeOutfit());
    });
    renderAt("/outfits/21");

    expect(await screen.findByText(STRINGS.detail.notFound)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: STRINGS.error.retry }));

    expect(await screen.findByTestId("outfit-detail")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/outfits/21"))).toHaveLength(2);
  });

  it("서버 오류면 불러오기 실패 안내를 보여준다", async () => {
    stubFetch(() => jsonResponse({ error: { code: "INTERNAL_ERROR", message: "x" } }, 500));
    renderAt("/outfits/21");
    expect(await screen.findByText(STRINGS.error.loadFailed)).toBeInTheDocument();
  });

  it("숫자가 아닌 id는 요청 없이 찾을 수 없음을 보여준다", async () => {
    const fetchMock = stubFetch(() => jsonResponse(makeOutfit()));
    renderAt("/outfits/abc");
    expect(await screen.findByText(STRINGS.detail.notFound)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/outfits/"))).toBe(false);
  });
});
