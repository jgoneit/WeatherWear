import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Outfit } from "../api/client";
import { STRINGS } from "../lib/strings";
import { NotificationCard } from "./NotificationCard";

const outfit: Outfit = {
  id: 81,
  captured_at: "2025-11-20T08:30:00+09:00",
  latitude: 37.5665,
  longitude: 126.978,
  source: "demo_sidecar",
  photo_url: "/api/outfits/81/photo",
  tags: {
    upper_type: "hoodie",
    outer_type: "varsity_jacket",
    lower_type: "baggy_denim",
    colors: [],
    layers_visible: 2,
    confidence: 0.9,
  },
  vision_provider: "fixture",
  weather: null,
  effective_vote: "cold",
};

function renderCard(onSaved = vi.fn()) {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<NotificationCard outfit={outfit} todayTemperatureC={9} onSaved={onSaved} />} />
        <Route path="/outfits/:id" element={<p>detail page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return onSaved;
}

function deferredFetch() {
  let resolve!: (r: Response) => void;
  const fetchMock = vi.fn(() => new Promise<Response>((r) => (resolve = r)));
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, resolve: (r: Response) => resolve(r) };
}

const buttons = () => screen.getAllByRole("button").filter((b) => b.hasAttribute("data-vote"));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NotificationCard smoke", () => {
  it("shows header, temperature, thumbnail and 3 buttons with effective_vote pressed", () => {
    renderCard();
    expect(screen.getByText(STRINGS.feedback.notificationHeader)).toBeInTheDocument();
    expect(screen.getByText(STRINGS.feedback.question("9.0°C"))).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", expect.stringContaining("/api/outfits/81/photo"));
    expect(buttons().map((b) => [b.dataset.vote, b.textContent?.replace("✓", "")])).toEqual([
      ["cold", "🥶 추웠어"],
      ["comfortable", "😊 적당했어"],
      ["hot", "🥵 더웠어"],
    ]);
    expect(buttons().map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "false", "false"]);
  });

  it("posts once, disables while pending, then selects and calls onSaved", async () => {
    const { fetchMock, resolve } = deferredFetch();
    const onSaved = renderCard();
    const comfortable = screen.getByRole("button", { name: STRINGS.feedback.comfortable });
    fireEvent.click(comfortable);
    fireEvent.click(comfortable);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/outfits/81/feedback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ thermal_vote: "comfortable" });
    expect(buttons().every((b) => (b as HTMLButtonElement).disabled)).toBe(true);

    await act(async () => {
      resolve(
        new Response(JSON.stringify({ outfit_id: 81, thermal_vote: "comfortable", created_at: "2026-11-14T00:00:00Z" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    expect(onSaved).toHaveBeenCalledWith("comfortable");
    expect(buttons().map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "true", "false"]);
    expect(buttons().every((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.getByText("😊 적당했어").closest("article")).toBeInTheDocument();
  });

  it("keeps previous selection and shows toast on failure", async () => {
    const { resolve } = deferredFetch();
    const onSaved = renderCard();
    fireEvent.click(screen.getByRole("button", { name: STRINGS.feedback.hot }));
    await act(async () => {
      resolve(new Response(JSON.stringify({ error: { code: "FEEDBACK_SAVE_FAILED", message: "x" } }), { status: 500 }));
    });
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByText(STRINGS.feedback.saveFailed)).toBeInTheDocument();
    expect(buttons().map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "false", "false"]);
    expect(buttons().every((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.queryByText("detail page")).not.toBeInTheDocument();
  });

  it("navigates to detail on body tap", () => {
    renderCard();
    fireEvent.click(screen.getByTestId("notification-card"));
    expect(screen.getByText("detail page")).toBeInTheDocument();
  });
});
