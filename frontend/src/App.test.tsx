import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "./App";
import { DemoPage } from "./pages/DemoPage";
import { STRINGS } from "./lib/strings";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

describe("AppRoutes", () => {
  it.each([
    ["/", STRINGS.home.title],
    ["/history", STRINGS.history.title],
    ["/outfits/42", STRINGS.detail.title],
    ["/demo", STRINGS.demo.title],
  ])("%s 경로에서 해당 화면 제목을 보여준다", (path, title) => {
    renderAt(path);
    expect(screen.getByRole("heading", { level: 1, name: title })).toBeInTheDocument();
  });

  it("상세 경로의 id를 읽는다", () => {
    renderAt("/outfits/42");
    expect(screen.getByText("#42")).toBeInTheDocument();
  });

  it("하단 탭바에 홈과 기록 탭이 있다", () => {
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: STRINGS.nav.label });
    expect(nav).toHaveTextContent(STRINGS.nav.home);
    expect(nav).toHaveTextContent(STRINGS.nav.history);
  });
});

describe("DemoPage", () => {
  it("개발 모드가 아니면 안내 문구만 보여준다", () => {
    render(<DemoPage isDev={false} />);
    expect(screen.getByText(STRINGS.demo.devOnly)).toBeInTheDocument();
    expect(screen.queryByText(STRINGS.demo.placeholder)).not.toBeInTheDocument();
  });
});
