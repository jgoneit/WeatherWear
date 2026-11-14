import { act, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { STRINGS } from "../../lib/strings";
import { AppShell } from "./AppShell";
import { temperatureBand } from "./temperatureBand";
import { useReportTodayTemperature, useTodayTemperature } from "./TodayTemperatureContext";

function ReportingPage({ celsius }: { celsius: number | null | undefined }) {
  useReportTodayTemperature(celsius);
  return <h1>page</h1>;
}

let setFromTest: ((c: number | null) => void) | undefined;
function SetterPage() {
  setFromTest = useTodayTemperature().setTemperatureC;
  return <h1>setter</h1>;
}

function renderShell(page: ReactNode, props: Parameters<typeof AppShell>[0] = {}) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<AppShell {...props} />}>
          <Route index element={page} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

const background = () => screen.getByTestId("temperature-background");

describe("temperatureBand", () => {
  it.each([
    [-12, "icy"],
    [5, "icy"],
    [5.5, "sage"],
    [6, "sage"],
    [16, "sage"],
    [16.5, "sage"],
    [17, "peach"],
    [24, "peach"],
    [24.5, "peach"],
    [25, "coral"],
    [38, "coral"],
  ] as const)("%s°C는 %s 구간이다", (celsius, band) => {
    expect(temperatureBand(celsius)).toBe(band);
  });

  it("기온을 모르면 세이지를 쓴다", () => {
    expect(temperatureBand(null)).toBe("sage");
    expect(temperatureBand(undefined)).toBe("sage");
    expect(temperatureBand(Number.NaN)).toBe("sage");
  });
});

describe("AppShell", () => {
  it("헤더, 본문, 홈/기록 탭을 보여준다", () => {
    renderShell(<ReportingPage celsius={undefined} />, { showDemoTab: false });
    expect(screen.getByRole("banner")).toHaveTextContent(STRINGS.appName);
    expect(screen.getByRole("heading", { name: "page" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: STRINGS.nav.label });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([STRINGS.nav.home, STRINGS.nav.history]);
    expect(within(nav).getByRole("link", { name: STRINGS.nav.home })).toHaveAttribute("aria-current", "page");
  });

  it("개발 모드에서만 데모 탭을 보여준다", () => {
    renderShell(<ReportingPage celsius={undefined} />, { showDemoTab: true });
    const nav = screen.getByRole("navigation", { name: STRINGS.nav.label });
    expect(within(nav).getByRole("link", { name: STRINGS.nav.demo })).toHaveAttribute("href", "/demo");
  });

  it("기온을 받기 전에는 세이지 배경이다", () => {
    renderShell(<ReportingPage celsius={undefined} />);
    expect(background()).toHaveAttribute("data-band", "sage");
  });

  it("화면이 알려 준 오늘 기온으로 배경 구간을 바꾸고 활성 레이어만 보인다", () => {
    renderShell(<ReportingPage celsius={3} />);
    expect(background()).toHaveAttribute("data-band", "icy");
    const layers = background().querySelectorAll("[data-band-layer]");
    expect(layers).toHaveLength(4);
    for (const layer of layers) {
      const visible = layer.getAttribute("data-band-layer") === "icy";
      expect(layer.className).toContain(visible ? "opacity-100" : "opacity-0");
      expect(layer.className).toContain("duration-[600ms]");
      expect(layer.className).toContain("motion-reduce:transition-none");
    }
  });

  it("기온이 바뀌면 배경도 따라 바뀐다", () => {
    renderShell(<SetterPage />, { initialTemperatureC: 30 });
    expect(background()).toHaveAttribute("data-band", "coral");
    act(() => setFromTest?.(20));
    expect(background()).toHaveAttribute("data-band", "peach");
    act(() => setFromTest?.(null));
    expect(background()).toHaveAttribute("data-band", "sage");
  });

  it("Provider 밖에서 useReportTodayTemperature를 써도 오류가 없다", () => {
    render(<ReportingPage celsius={10} />);
    expect(screen.getByRole("heading", { name: "page" })).toBeInTheDocument();
  });
});
