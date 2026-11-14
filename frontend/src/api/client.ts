/**
 * REST API 클라이언트.
 * 요청/응답 타입은 OpenAPI에서 생성한 `schema.d.ts`만 사용한다 (Req 10.7).
 * 모든 요청은 AbortController 타임아웃을 가진다: 홈·피드백 10초, 데모 30초 (Req 11.9, 13.7, 14.6).
 */
import { API_BASE_URL } from "../lib/env";
import { STRINGS } from "../lib/strings";
import type { components } from "./schema";

export type Schemas = components["schemas"];
export type RecommendationResponse = Schemas["RecommendationResponse"];
export type RecommendationItem = Schemas["RecommendationItemOut"];
export type WeatherContext = Schemas["WeatherContextOut"];
export type WeatherSnapshot = Schemas["WeatherSnapshotOut"];
export type Outfit = Schemas["OutfitOut"];
export type ClothingTags = Schemas["ClothingTags"];
export type ThermalVote = Schemas["FeedbackIn"]["thermal_vote"];
export type FeedbackOut = Schemas["FeedbackOut"];
export type ScanReport = Schemas["ScanReport"];
export type ScenarioList = Schemas["ScenarioListResponse"];
export type ScenarioSelection = Schemas["ScenarioSelection"];
export type DemoStatus = Schemas["DemoStatus"];
export type AppMeta = Schemas["AppMeta"];
export type ErrorResponse = Schemas["ErrorResponse"];

/** 화면 데이터와 피드백 저장의 제한 시간 (Req 11.9, 13.7). */
export const DEFAULT_TIMEOUT_MS = 10_000;
/** Demo_Admin_Panel 동작의 제한 시간 (Req 14.6). */
export const DEMO_TIMEOUT_MS = 30_000;

/** 클라이언트 쪽에서 만든 오류 코드. 서버 오류 코드는 ErrorResponse.error.code를 그대로 쓴다. */
export const CLIENT_ERROR_CODES = {
  timeout: "TIMEOUT",
  network: "NETWORK_ERROR",
  aborted: "ABORTED",
  invalidResponse: "INVALID_RESPONSE",
} as const;

/**
 * API 호출 실패.
 * `status`는 HTTP 상태 코드이고, 응답을 받지 못한 경우(타임아웃·네트워크·취소)는 0이다.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get isTimeout(): boolean {
    return this.code === CLIENT_ERROR_CODES.timeout;
  }
}

export interface RequestOptions extends Omit<RequestInit, "signal"> {
  timeoutMs: number;
  /** 호출자가 요청을 취소할 때 쓰는 신호(예: 화면 이탈). */
  signal?: AbortSignal;
}

/** API 경로를 API_BASE_URL 기준 URL로 바꾼다. `<img src>`의 photo_url에도 쓴다. */
export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

function isErrorResponse(body: unknown): body is ErrorResponse {
  if (typeof body !== "object" || body === null || !("error" in body)) return false;
  const error = (body as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

/** 2xx가 아닌 응답을 ApiError로 바꾼다. 본문이 ErrorResponse 형식이 아니면 `HTTP_<status>`를 코드로 쓴다. */
export async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (isErrorResponse(body)) {
    return new ApiError(response.status, body.error.code, body.error.message);
  }
  return new ApiError(response.status, `HTTP_${response.status}`, STRINGS.error.server);
}

export async function request<T>(path: string, options: RequestOptions): Promise<T> {
  const { timeoutMs, signal: externalSignal, headers, ...init } = options;
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  externalSignal?.addEventListener("abort", onExternalAbort);

  const requestHeaders = new Headers(headers);
  if (!requestHeaders.has("Accept")) requestHeaders.set("Accept", "application/json");

  try {
    let response: Response;
    try {
      response = await fetch(apiUrl(path), {
        ...init,
        headers: requestHeaders,
        signal: controller.signal,
      });
    } catch {
      if (timedOut) throw new ApiError(0, CLIENT_ERROR_CODES.timeout, STRINGS.error.timeout);
      if (controller.signal.aborted) {
        throw new ApiError(0, CLIENT_ERROR_CODES.aborted, STRINGS.error.aborted);
      }
      throw new ApiError(0, CLIENT_ERROR_CODES.network, STRINGS.error.network);
    }

    if (!response.ok) throw await toApiError(response);
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      if (timedOut) throw new ApiError(0, CLIENT_ERROR_CODES.timeout, STRINGS.error.timeout);
      throw new ApiError(response.status, CLIENT_ERROR_CODES.invalidResponse, STRINGS.error.server);
    }
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener("abort", onExternalAbort);
  }
}

type CallOptions = { signal?: AbortSignal };

function jsonBody(body: unknown): Pick<RequestInit, "body" | "headers"> {
  return { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } };
}

export const api = {
  getRecommendations: (o: CallOptions = {}) =>
    request<RecommendationResponse>("/api/recommendations/today", {
      timeoutMs: DEFAULT_TIMEOUT_MS,
      ...o,
    }),
  getWeatherToday: (o: CallOptions = {}) =>
    request<WeatherContext>("/api/weather/today", { timeoutMs: DEFAULT_TIMEOUT_MS, ...o }),
  getOutfits: (o: CallOptions = {}) =>
    request<Outfit[]>("/api/outfits", { timeoutMs: DEFAULT_TIMEOUT_MS, ...o }),
  getOutfit: (id: number, o: CallOptions = {}) =>
    request<Outfit>(`/api/outfits/${encodeURIComponent(String(id))}`, {
      timeoutMs: DEFAULT_TIMEOUT_MS,
      ...o,
    }),
  postFeedback: (id: number, thermalVote: ThermalVote, o: CallOptions = {}) =>
    request<FeedbackOut>(`/api/outfits/${encodeURIComponent(String(id))}/feedback`, {
      method: "POST",
      ...jsonBody({ thermal_vote: thermalVote } satisfies Schemas["FeedbackIn"]),
      timeoutMs: DEFAULT_TIMEOUT_MS,
      ...o,
    }),
  getMeta: (o: CallOptions = {}) =>
    request<AppMeta>("/api/meta", { timeoutMs: DEFAULT_TIMEOUT_MS, ...o }),

  // Demo_Admin_Panel (30초)
  scan: (o: CallOptions = {}) =>
    request<ScanReport>("/api/demo/scan", { method: "POST", timeoutMs: DEMO_TIMEOUT_MS, ...o }),
  getScenarios: (o: CallOptions = {}) =>
    request<ScenarioList>("/api/demo/scenarios", { timeoutMs: DEMO_TIMEOUT_MS, ...o }),
  selectScenario: (scenarioId: string, o: CallOptions = {}) =>
    request<ScenarioSelection>("/api/demo/scenarios/selected", {
      method: "PUT",
      ...jsonBody({ scenario_id: scenarioId } satisfies Schemas["ScenarioSelectIn"]),
      timeoutMs: DEMO_TIMEOUT_MS,
      ...o,
    }),
  clearScenario: (o: CallOptions = {}) =>
    request<void>("/api/demo/scenarios/selected", {
      method: "DELETE",
      timeoutMs: DEMO_TIMEOUT_MS,
      ...o,
    }),
  getStatus: (o: CallOptions = {}) =>
    request<DemoStatus>("/api/demo/status", { timeoutMs: DEMO_TIMEOUT_MS, ...o }),
};
