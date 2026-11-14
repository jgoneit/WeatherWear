import { describe, expect, it } from "vitest";
import type { ClothingTags } from "../api/client";
import {
  displayTags,
  formatDate,
  formatHumidity,
  formatTemp,
  provenance,
  voteLabel,
} from "./format";

describe("formatTemp", () => {
  it("formats one decimal with °C", () => {
    expect(formatTemp(9)).toBe("9.0°C");
    expect(formatTemp(-12)).toBe("-12.0°C");
    expect(formatTemp(26.54)).toBe("26.5°C");
  });

  it("does not show negative zero", () => {
    expect(formatTemp(-0.04)).toBe("0.0°C");
    expect(formatTemp(-0)).toBe("0.0°C");
  });

  it("returns the placeholder for missing or non-finite values", () => {
    expect(formatTemp(null)).toBe("–");
    expect(formatTemp(undefined)).toBe("–");
    expect(formatTemp(Number.NaN)).toBe("–");
    expect(formatTemp(Number.POSITIVE_INFINITY)).toBe("–");
  });
});

describe("formatHumidity", () => {
  it("rounds to an integer percent", () => {
    expect(formatHumidity(55)).toBe("55%");
    expect(formatHumidity(55.4)).toBe("55%");
    expect(formatHumidity(55.5)).toBe("56%");
    expect(formatHumidity(0)).toBe("0%");
    expect(formatHumidity(100)).toBe("100%");
  });

  it("returns the placeholder for missing values", () => {
    expect(formatHumidity(null)).toBe("–");
    expect(formatHumidity(Number.NaN)).toBe("–");
  });
});

describe("formatDate", () => {
  it("uses the given timezone for the calendar date", () => {
    // 2025-01-14 16:30 UTC is 2025-01-15 01:30 in Seoul
    expect(formatDate("2025-01-14T16:30:00Z", "Asia/Seoul")).toBe("2025-01-15");
    expect(formatDate("2025-01-14T16:30:00Z", "UTC")).toBe("2025-01-14");
    expect(formatDate("2025-11-20T08:30:00+09:00", "Asia/Seoul")).toBe("2025-11-20");
    expect(formatDate("2025-11-20T08:30:00+09:00", "America/New_York")).toBe("2025-11-19");
  });

  it("falls back to Asia/Seoul for an invalid timezone", () => {
    expect(formatDate("2025-01-14T16:30:00Z", "Not/AZone")).toBe("2025-01-15");
  });

  it("returns the placeholder for missing or unparsable input", () => {
    expect(formatDate(null, "Asia/Seoul")).toBe("–");
    expect(formatDate("", "Asia/Seoul")).toBe("–");
    expect(formatDate("not a date", "Asia/Seoul")).toBe("–");
  });
});

describe("voteLabel", () => {
  it("maps each vote to its Korean label", () => {
    expect(voteLabel("cold")).toBe("추웠어");
    expect(voteLabel("comfortable")).toBe("적당했어");
    expect(voteLabel("hot")).toBe("더웠어");
    expect(voteLabel(null)).toBe("미평가");
    expect(voteLabel(undefined)).toBe("미평가");
  });
});

describe("displayTags", () => {
  const tags = (
    outer: ClothingTags["outer_type"],
    upper: ClothingTags["upper_type"],
    lower: ClothingTags["lower_type"],
  ): ClothingTags => ({
    outer_type: outer,
    upper_type: upper,
    lower_type: lower,
    colors: ["navy", "white"],
    layers_visible: 2,
    confidence: 0.9,
  });

  it("orders outer, upper, lower", () => {
    expect(displayTags(tags("coat", "knit", "slacks"))).toEqual(["coat", "knit", "slacks"]);
  });

  it("excludes none and unknown without filling from colors", () => {
    expect(displayTags(tags("none", "knit", "unknown"))).toEqual(["knit"]);
    expect(displayTags(tags("unknown", "unknown", "unknown"))).toEqual([]);
  });

  it("respects the limit", () => {
    expect(displayTags(tags("coat", "knit", "slacks"), 2)).toEqual(["coat", "knit"]);
    expect(displayTags(tags("coat", "knit", "slacks"), 0)).toEqual([]);
  });
});

describe("provenance", () => {
  it("labels only fixture or demo sources", () => {
    expect(
      provenance({ vision_provider: "fixture", source: "demo_sidecar", weather: { is_fixture: true } }),
    ).toEqual({ tags: true, weather: true, meta: true });
    expect(
      provenance({ vision_provider: "gemini", source: "demo_sidecar", weather: { is_fixture: false } }),
    ).toEqual({ tags: false, weather: false, meta: true });
  });

  it("does not label weather when there is none", () => {
    expect(provenance({ vision_provider: "gemini", source: "demo_sidecar", weather: null }).weather).toBe(
      false,
    );
  });
});
