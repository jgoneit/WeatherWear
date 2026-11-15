/**
 * 현재 위치 보고 (Req 16.2, 16.6, 16.7).
 *
 * - "현재 위치 사용" 버튼은 `reportLocation()`을 부른다. 권한 요청은 이때 브라우저가 띄운다.
 * - 앱을 열 때 셸은 `autoReportLocation()`을 부른다. 권한이 이미 granted이고 마지막 보고 후
 *   30분 이상 지났을 때만 조용히 보낸다. 이 경로는 권한을 요청하지 않는다.
 * - 마지막으로 보낸 시각만 localStorage에 둔다. 위경도는 저장하거나 출력하지 않는다.
 */
import { api } from "../api/client";

export type LocationReport = "sent" | "denied" | "timeout" | "unsupported";

/** 위치 기능이 쓰는 API 함수. */
export type LocationApi = Pick<typeof api, "postLocation">;

export const LOCATION_REPORTED_AT_KEY = "weatherwear.locationReportedAt";
/** 자동 재보고 최소 간격 (Req 16.6). */
export const LOCATION_REPORT_INTERVAL_MS = 30 * 60 * 1000;
/** getCurrentPosition 옵션 (Req 16.6): 저정확도, 10초 제한, 30분 이내 캐시 허용. */
export const GEOLOCATION_OPTIONS: PositionOptions = {
  enableHighAccuracy: false,
  timeout: 10_000,
  maximumAge: LOCATION_REPORT_INTERVAL_MS,
};

/**
 * 자동 재보고 판정 (Req 16.6, 16.12, Property 27).
 * 권한이 granted이고, 보낸 적이 없거나 마지막 보고 후 30분 이상 지났을 때만 true다.
 * 시계가 뒤로 간 경우(now < last)는 30분이 지나지 않은 것으로 본다.
 */
export function shouldReportLocation(
  permission: PermissionState | null,
  lastReportedAt: number | null,
  now: number,
): boolean {
  if (permission !== "granted") return false;
  if (lastReportedAt === null) return true;
  return now - lastReportedAt >= LOCATION_REPORT_INTERVAL_MS;
}

/** 마지막으로 위치를 보낸 시각(ms). 없거나 읽을 수 없으면 null. */
export function lastLocationReportAt(): number | null {
  try {
    const raw = window.localStorage.getItem(LOCATION_REPORTED_AT_KEY);
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function storeReportedAt(at: number): void {
  try {
    window.localStorage.setItem(LOCATION_REPORTED_AT_KEY, String(at));
  } catch {
    // 저장소를 쓸 수 없으면 다음에 열 때 다시 보낸다(권한이 granted일 때만).
  }
}

function geolocationAvailable(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext === true &&
    typeof navigator !== "undefined" &&
    "geolocation" in navigator
  );
}

/** 위치 권한 상태. Permissions API가 없거나 실패하면 null(모름)이다. 권한을 요청하지 않는다. */
export async function locationPermission(): Promise<PermissionState | null> {
  try {
    if (typeof navigator === "undefined" || !navigator.permissions?.query) return null;
    const status = await navigator.permissions.query({ name: "geolocation" });
    return status.state;
  } catch {
    return null;
  }
}

function currentPosition(): Promise<GeolocationPosition> {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, GEOLOCATION_OPTIONS);
  });
}

/**
 * 위치를 1회 얻어 POST /api/location으로 보낸다 (Req 16.6, 16.7).
 * 거부는 "denied", 10초 초과와 위치를 얻을 수 없음은 "timeout"(둘 다 기본 위치 안내),
 * secure context가 아니거나 Geolocation이 없으면 "unsupported"다.
 * 위치는 얻었지만 저장 API가 실패하면 그 ApiError를 던진다.
 */
export async function reportLocation(client: LocationApi = api, now: () => number = Date.now): Promise<LocationReport> {
  if (!geolocationAvailable()) return "unsupported";
  let position: GeolocationPosition;
  try {
    position = await currentPosition();
  } catch (error) {
    const code = (error as GeolocationPositionError | undefined)?.code;
    return code === 1 ? "denied" : "timeout";
  }
  const { latitude, longitude, accuracy } = position.coords;
  await client.postLocation({
    latitude,
    longitude,
    accuracy_m: Number.isFinite(accuracy) ? accuracy : null,
  });
  storeReportedAt(now());
  return "sent";
}

let autoReport: Promise<LocationReport | null> | null = null;

/**
 * 앱을 열 때의 조용한 위치 보고 (Req 16.6). 판정이 false면 아무것도 하지 않고 null이다.
 * 동시에 여러 번 불려도(StrictMode의 effect 2회 실행 등) 한 번만 보낸다. 오류는 삼킨다.
 */
export function autoReportLocation(client: LocationApi = api, now: () => number = Date.now): Promise<LocationReport | null> {
  if (autoReport) return autoReport;
  autoReport = (async () => {
    try {
      const permission = await locationPermission();
      if (!shouldReportLocation(permission, lastLocationReportAt(), now())) return null;
      return await reportLocation(client, now);
    } catch {
      return null;
    }
  })().finally(() => {
    autoReport = null;
  });
  return autoReport;
}
