import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DemoStatus, RecommendationResponse, ScanReport, ScenarioList } from "../api/client";
import { STRINGS } from "../lib/strings";
import { DemoPage } from "./DemoPage";

const scenarios: ScenarioList = {
  scenarios: [
    {
      id: "chilly-morning",
      name: "쌀쌀한 아침",
      actual_temperature_c: 9,
      apparent_temperature_c: 6.5,
      relative_humidity_pct: 55,
      wind_speed_kmh: 12,
      precipitation_mm: 0,
    },
    {
      id: "heatwave",
      name: "기록적 폭염 (데이터 부족)",
      actual_temperature_c: 38,
      apparent_temperature_c: null,
      relative_humidity_pct: null,
      wind_speed_kmh: null,
      precipitation_mm: null,
    },
  ],
  selected_id: "chilly-morning",
};

const status: DemoStatus = {
  vision_provider: "fixture",
  weather_provider: "fixture",
  gemini_api_key_configured: false,
  outfit_count: 0,
  weather_snapshot_count: 0,
  feedback_count: 0,
  selected_scenario: null,
};

const scanReport: ScanReport = {
  scanned: 5,
  succeeded: 1,
  duplicate: 1,
  failed: 1,
  skipped_non_outfit: 1,
  uncertain: 1,
  errors: [{ file_name: "bad.png", stage: "validation", code: "INVALID_IMAGE_FORMAT", message: "이미지 형식이 올바르지 않습니다." }],
  decisions: [
    { file_name: "meal.jpg", photo_type: "non_outfit", confidence: 0.98, reason: "Only food is visible." },
    { file_name: "crop.jpg", photo_type: "uncertain", confidence: 0.55, reason: "The torso is cropped." },
  ],
};

const recommendations: RecommendationResponse = {
  status: "insufficient_data",
  algorithm_version: "rule_based_v1",
  fallback: true,
  applied_tolerance_c: 5,
  today: {
    actual_temperature_c: 38,
    apparent_temperature_c: null,
    relative_humidity_pct: null,
    wind_speed_kmh: null,
    precipitation_mm: null,
    observed_at: null,
    provider: "scenario",
    is_fixture: true,
    scenario_id: "heatwave",
    scenario_name: "기록적 폭염 (데이터 부족)",
  },
  items: [],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

function stubFetch(routes: Record<string, Handler>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected request ${key}`);
    return handler(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DemoPage smoke", () => {
  it("shows only the dev-only notice and calls no API outside dev mode", () => {
    const fetchMock = stubFetch({});
    render(<DemoPage isDev={false} />);
    expect(screen.getByText(STRINGS.demo.devOnly)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows status, scans, selects a scenario and recomputes", async () => {
    stubFetch({
      "GET /api/demo/status": () => json(status),
      "GET /api/demo/scenarios": () => json(scenarios),
      "POST /api/demo/scan": () => json(scanReport),
      "PUT /api/demo/scenarios/selected": () =>
        json({ selected_id: "heatwave", scenario: scenarios.scenarios[1] }),
      "GET /api/recommendations/today": () => json(recommendations),
    });
    render(<DemoPage isDev />);

    expect(await screen.findByTestId("status-api-key")).toHaveTextContent(STRINGS.demo.keyMissing);
    expect(screen.getByTestId("status-vision")).toHaveTextContent("fixture");
    expect(await screen.findByTestId("scenario-chilly-morning")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: STRINGS.demo.scan }));
    expect(await screen.findByTestId("scan-scanned")).toHaveTextContent("5");
    expect(screen.getByTestId("scan-non-outfit")).toHaveTextContent("1");
    expect(screen.getByTestId("scan-uncertain")).toHaveTextContent("1");
    expect(screen.getByTestId("scan-failed")).toHaveTextContent("1");
    expect(screen.getAllByTestId("scan-decision")).toHaveLength(2);
    expect(screen.getByText("meal.jpg")).toBeInTheDocument();
    const error = screen.getByTestId("scan-error");
    expect(error).toHaveTextContent("bad.png");
    expect(error).toHaveTextContent("이미지 형식이 올바르지 않습니다.");

    fireEvent.click(screen.getByTestId("scenario-heatwave"));
    await waitFor(() => expect(screen.getByTestId("scenario-heatwave")).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByTestId("scenario-chilly-morning")).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getAllByText(STRINGS.demo.scenarioApplied("기록적 폭염 (데이터 부족)", "38.0°C")).length,
    ).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: STRINGS.demo.recompute }));
    expect(await screen.findByTestId("recompute-summary")).toHaveTextContent(STRINGS.demo.recomputeInsufficient);
    expect(screen.getByText(`${STRINGS.demo.recomputeDone} · ${STRINGS.demo.done}`)).toBeInTheDocument();
  });

  it("shows no-failure text for a clean scan, then keeps the result when the next scan fails", async () => {
    let scanCalls = 0;
    stubFetch({
      "GET /api/demo/status": () => json(status),
      "GET /api/demo/scenarios": () => json(scenarios),
      "POST /api/demo/scan": () => {
        scanCalls += 1;
        return scanCalls === 1
          ? json({
              scanned: 2,
              succeeded: 2,
              duplicate: 0,
              failed: 0,
              skipped_non_outfit: 0,
              uncertain: 0,
              errors: [],
              decisions: [],
            })
          : json({ error: { code: "SCAN_IN_PROGRESS", message: "이미 스캔이 진행 중입니다." } }, 409);
      },
    });
    render(<DemoPage isDev />);
    const scanButton = await screen.findByRole("button", { name: STRINGS.demo.scan });

    fireEvent.click(scanButton);
    expect(await screen.findByText(STRINGS.demo.noFailures)).toBeInTheDocument();

    fireEvent.click(scanButton);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(STRINGS.demo.scanFailed);
    expect(alert).toHaveTextContent("이미 스캔이 진행 중입니다.");
    expect(screen.getByTestId("scan-succeeded")).toHaveTextContent("2");
    expect(scanButton).toBeEnabled();
  });

  it("keeps the previous selection when selecting an unknown scenario fails", async () => {
    stubFetch({
      "GET /api/demo/status": () => json(status),
      "GET /api/demo/scenarios": () => json(scenarios),
      "PUT /api/demo/scenarios/selected": () =>
        json({ error: { code: "UNKNOWN_SCENARIO", message: "알 수 없는 날씨 시나리오입니다." } }, 404),
    });
    render(<DemoPage isDev />);
    const target = await screen.findByTestId("scenario-heatwave");

    fireEvent.click(target);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/시나리오 선택에 실패했어요/)).toBeInTheDocument();
    expect(screen.getByTestId("scenario-chilly-morning")).toHaveAttribute("aria-pressed", "true");
    expect(target).toHaveAttribute("aria-pressed", "false");
    expect(target).toBeEnabled();
  });
});
