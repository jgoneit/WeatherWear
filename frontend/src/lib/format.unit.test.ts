import { describe, expect, it } from "vitest";
import {
  displayTags,
  formatDate,
  formatHumidity,
  formatTemp,
  metadataSourceLabel,
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
  const tags = (outer: string, upper: string, lower: string) => ({
    outer_type: outer,
    upper_type: upper,
    lower_type: lower,
    colors: ["navy", "white"],
    layers_visible: 2,
    confidence: 0.9,
  });

  it("orders outer, upper, lower", () => {
    expect(displayTags(tags("코트", "니트", "슬랙스"))).toEqual(["코트", "니트", "슬랙스"]);
  });

  it("excludes none and unknown without filling from colors", () => {
    expect(displayTags(tags("none", "니트", "unknown"))).toEqual(["니트"]);
    expect(displayTags(tags("unknown", "unknown", "unknown"))).toEqual([]);
  });

  it("respects the limit", () => {
    expect(displayTags(tags("코트", "니트", "슬랙스"), 2)).toEqual(["코트", "니트"]);
    expect(displayTags(tags("코트", "니트", "슬랙스"), 0)).toEqual([]);
  });
});

describe("provenance", () => {
  it("labels only fixture or synthetic sources", () => {
    expect(
      provenance({ vision_provider: "fixture", metadata_synthetic: true, weather: { is_fixture: true } }),
    ).toEqual({ tags: true, weather: true, meta: true });
    expect(
      provenance({ vision_provider: "gemini", metadata_synthetic: false, weather: { is_fixture: false } }),
    ).toEqual({ tags: false, weather: false, meta: false });
  });

  it("does not label weather when there is none", () => {
    expect(provenance({ vision_provider: "gemini", metadata_synthetic: true, weather: null }).weather).toBe(
      false,
    );
  });
});

describe("metadataSourceLabel", () => {
  it("maps each metadata source to its Korean label", () => {
    expect(metadataSourceLabel("exif")).toBe("사진 내장 메타데이터");
    expect(metadataSourceLabel("demo_sidecar")).toBe("사이드카 파일");
  });
});
