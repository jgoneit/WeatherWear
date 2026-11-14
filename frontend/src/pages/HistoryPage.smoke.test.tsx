import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppMeta, Outfit } from "../api/client";
import { STRINGS } from "../lib/strings";
import { groupOutfitsByDate, HistoryPage } from "./HistoryPage";

function makeOutfit(id: number, capturedAt: string, overrides: Partial<Outfit> = {}): Outfit {
  return {
    id,
    captured_at: capturedAt,
    latitude: 37.5665,
    longitude: 126.978,
    source: "exif",
    metadata_synthetic: true,
    photo_url: `/api/outfits/${id}/photo`,
    tags: {
      upper_type: "knit",
      outer_type: "coat",
      lower_type: "slacks",
      colors: ["navy"],
      layers_visible: 2,
      confidence: 0.9,
    },
    vision_provider: "fixture",
    weather: {
      observed_at: capturedAt,
      actual_temperature_c: 9,
      apparent_temperature_c: null,
      relative_humidity_pct: null,
      wind_speed_kmh: null,
      precipitation_mm: null,
      weather_code: null,
      provider: "fixture",
      is_fixture: true,
    },
    effective_vote: null,
    ...overrides,
  };
}

const meta: AppMeta = {
  app_timezone: "Asia/Seoul",
  algorithm_version: "rule_based_v1",
  vision_provider: "fixture",
  weather_provider: "fixture",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** /api/outfits 응답을 순서대로 돌려주는 fetch 대체. /api/meta는 항상 성공. */
function stubFetch(outfitResponses: Array<() => Response>) {
  let call = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/meta")) return json(meta);
    const next = outfitResponses[Math.min(call, outfitResponses.length - 1)];
    call += 1;
    return next!();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/history"]}>
      <Routes>
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/outfits/:id" element={<p>detail page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("groupOutfitsByDate", () => {
  it("keeps API order and groups consecutive same-day items in APP_TIMEZONE", () => {
    const list = [
      makeOutfit(3, "2025-11-20T23:30:00+09:00"),
      // 14:40Z = 23:40 KST on 11-20: same Seoul date as the item above
      makeOutfit(2, "2025-11-20T14:40:00Z"),
      makeOutfit(1, "2025-11-19T08:00:00+09:00"),
    ];
    const sections = groupOutfitsByDate(list, "Asia/Seoul");
    expect(sections.map((s) => [s.date, s.outfits.map((o) => o.id)])).toEqual([
      ["2025-11-20", [3, 2]],
      ["2025-11-19", [1]],
    ]);
  });
});

describe("HistoryPage smoke", () => {
  it("renders items in returned order with temp, tags, vote and no-weather chip", async () => {
    stubFetch([
      () =>
        json([
          makeOutfit(3, "2025-11-20T08:30:00+09:00", { effective_vote: "comfortable" }),
          makeOutfit(2, "2025-11-20T07:30:00+09:00", { weather: null, effective_vote: "cold" }),
          makeOutfit(1, "2025-11-19T08:00:00+09:00"),
        ]),
    ]);
    renderPage();

    const items = await screen.findAllByTestId("outfit-grid-item");
    expect(items.map((el) => el.dataset.outfitId)).toEqual(["3", "2", "1"]);
    const [item0, item1, item2] = items as [HTMLElement, HTMLElement, HTMLElement];

    const first = within(item0);
    expect(first.getByTestId("outfit-temperature")).toHaveTextContent("9.0°C");
    expect(first.getByTestId("outfit-vote")).toHaveTextContent("😊 적당했어");
    expect(first.getByText("coat")).toBeInTheDocument();
    expect(first.getByText("knit")).toBeInTheDocument();
    expect(first.getByText("slacks")).toBeInTheDocument();
    expect(first.getByText("2025-11-20")).toBeInTheDocument();
    expect(first.getByRole("img")).toHaveAttribute("loading", "lazy");

    expect(within(item1).getByTestId("outfit-temperature")).toHaveTextContent(STRINGS.history.noWeather);
    expect(within(item2).getByTestId("outfit-vote")).toHaveTextContent(STRINGS.vote.none);

    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      `2025-11-20${STRINGS.history.count(2)}`,
      `2025-11-19${STRINGS.history.count(1)}`,
    ]);

    fireEvent.click(item0);
    expect(await screen.findByText("detail page")).toBeInTheDocument();
  });

  it("shows the empty state when there are no outfits", async () => {
    stubFetch([() => json([])]);
    renderPage();
    expect(await screen.findByText(STRINGS.history.empty)).toBeInTheDocument();
    expect(screen.queryByTestId("outfit-grid-item")).not.toBeInTheDocument();
  });

  it("shows error + retry on failure and loads the list after retry", async () => {
    stubFetch([
      () => json({ error: { code: "INTERNAL_ERROR", message: "서버 오류가 발생했습니다." } }, 500),
      () => json([makeOutfit(1, "2025-11-19T08:00:00+09:00")]),
    ]);
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(STRINGS.history.loadFailed);
    fireEvent.click(within(alert).getByRole("button", { name: STRINGS.error.retry }));

    expect(await screen.findAllByTestId("outfit-grid-item")).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});
