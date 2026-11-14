/**
 * 화면 표시 형식 순수 함수 (Req 4.9, 11.2, 11.3, 11.4, 12.2, 15.4).
 * I/O가 없고 같은 입력에 항상 같은 출력을 낸다.
 */
import type { ClothingTags, Outfit, ThermalVote } from "../api/client";
import { STRINGS } from "./strings";

/** 값이 없을 때 쓰는 표시 문자열. */
export const EMPTY_VALUE = STRINGS.emptyValue;

/** `formatDate`에 넘긴 타임존이 유효하지 않을 때 쓰는 기본 타임존 (백엔드 APP_TIMEZONE 기본값). */
export const FALLBACK_TIMEZONE = "Asia/Seoul";

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** "-0.0"이나 "-0" 같은 음의 0 표기를 없앤다. */
function stripNegativeZero(text: string): string {
  return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text;
}

/** 기온을 소수 첫째 자리 °C로 표시한다. 예: 9 → "9.0°C", null → "–" (Req 11.2). */
export function formatTemp(celsius: number | null | undefined): string {
  if (!isFiniteNumber(celsius)) return EMPTY_VALUE;
  return `${stripNegativeZero(celsius.toFixed(1))}°C`;
}

/** 습도를 정수 %로 표시한다. 예: 55.4 → "55%", null → "–" (Req 11.3). */
export function formatHumidity(percent: number | null | undefined): string {
  if (!isFiniteNumber(percent)) return EMPTY_VALUE;
  return `${stripNegativeZero(String(Math.round(percent)))}%`;
}

const dateFormatterCache = new Map<string, Intl.DateTimeFormat>();

function dateFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = dateFormatterCache.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    dateFormatterCache.set(timeZone, formatter);
  }
  return formatter;
}

function safeDateFormatter(timeZone: string): Intl.DateTimeFormat {
  try {
    return dateFormatter(timeZone);
  } catch {
    return dateFormatter(FALLBACK_TIMEZONE);
  }
}

/**
 * ISO 8601 시각을 지정 타임존의 날짜 `YYYY-MM-DD`로 표시한다 (Req 11.4, 12.2).
 * 타임존은 GET /api/meta의 `app_timezone`을 넘긴다. 유효하지 않은 타임존이면 Asia/Seoul을 쓴다.
 * 파싱할 수 없는 시각이나 null은 "–"를 반환한다.
 */
export function formatDate(iso: string | null | undefined, timeZone: string): string {
  if (typeof iso !== "string" || iso.length === 0) return EMPTY_VALUE;
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) return EMPTY_VALUE;

  const parts = safeDateFormatter(timeZone).formatToParts(instant);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? "";
  const year = part("year").padStart(4, "0");
  return `${year}-${part("month")}-${part("day")}`;
}

/** Effective_Vote를 화면 표기로 바꾼다. 없으면 "미평가" (Req 12.2). */
export function voteLabel(
  vote: ThermalVote | null | undefined,
): "추웠어" | "적당했어" | "더웠어" | "미평가" {
  switch (vote) {
    case "cold":
      return STRINGS.vote.cold;
    case "comfortable":
      return STRINGS.vote.comfortable;
    case "hot":
      return STRINGS.vote.hot;
    default:
      return STRINGS.vote.none;
  }
}

/** 표시하지 않는 태그 값. 백엔드 `display_tags`와 같은 규칙이다. */
const HIDDEN_TAG_VALUES: ReadonlySet<string> = new Set(["none", "unknown"]);

/**
 * 표시용 의류 태그를 고른다 (백엔드 `explain.display_tags`와 같은 규칙).
 * [outer_type, upper_type, lower_type] 순서로 "none"/"unknown"을 빼고 최대 `limit`개.
 * 부족해도 colors로 채우지 않는다.
 */
export function displayTags(
  tags: Pick<ClothingTags, "outer_type" | "upper_type" | "lower_type">,
  limit = 3,
): string[] {
  return [tags.outer_type, tags.upper_type, tags.lower_type]
    .filter((value) => !HIDDEN_TAG_VALUES.has(value))
    .slice(0, Math.max(0, limit));
}

export interface Provenance {
  /** 의류 태그에 "fixture" 라벨을 붙일지 (vision_provider가 fixture일 때). */
  tags: boolean;
  /** 날씨에 "fixture" 배지를 붙일지 (weather.is_fixture가 true일 때). */
  weather: boolean;
  /** 촬영 시각·위치에 "가상 데이터" 라벨을 붙일지 (metadata_synthetic이 true일 때). */
  meta: boolean;
}

/**
 * fixture/가상 데이터 라벨을 어디에 붙일지 정한다 (Req 4.9, 15.4).
 * 날씨가 없으면 날씨 라벨은 false다.
 */
export function provenance(
  outfit: Pick<Outfit, "vision_provider" | "metadata_synthetic"> & {
    weather: Pick<NonNullable<Outfit["weather"]>, "is_fixture"> | null;
  },
): Provenance {
  return {
    tags: outfit.vision_provider === "fixture",
    weather: outfit.weather?.is_fixture === true,
    meta: outfit.metadata_synthetic === true,
  };
}

/** 촬영 시각·위치 출처 표기 (Req 12.5). */
export function metadataSourceLabel(
  source: Outfit["source"],
): "사진 내장 메타데이터" | "사이드카 파일" {
  return source === "exif" ? STRINGS.detail.metadataSourceLabel.exif : STRINGS.detail.metadataSourceLabel.demo_sidecar;
}
