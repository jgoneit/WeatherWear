import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { DemoWeatherPicker } from "./DemoWeatherPicker";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("selects weather and reloads recommendations after saving", async () => {
  const request = vi.spyOn(api, "selectScenario").mockResolvedValue({} as Awaited<ReturnType<typeof api.selectScenario>>);
  const refresh = vi.fn();
  render(<DemoWeatherPicker selected="early-heat" onChange={refresh} />);
  expect(screen.getByRole("button", { name: "무더위 30°" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "폭염 38°" }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledWith("heatwave");
});

it("shows a retryable failure without claiming the selection succeeded", async () => {
  vi.spyOn(api, "selectScenario").mockRejectedValue(new Error("offline"));
  const refresh = vi.fn();
  render(<DemoWeatherPicker selected="early-heat" onChange={refresh} />);
  fireEvent.click(screen.getByRole("button", { name: "한파 −12°" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "한파 −12°" }).hasAttribute("disabled")).toBe(false);
});
