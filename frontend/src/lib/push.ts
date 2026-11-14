/**
 * Web Push 켜기/끄기 (Req 16.2~16.5, 16.10).
 *
 * - 권한 요청과 Service Worker 등록은 `enablePush()` 안에서만 한다. 이 함수는 사용자가
 *   "알림 켜기"를 눌렀을 때만 부른다. 화면을 열 때는 `readPushState()`로 현재 상태만 읽는다.
 * - VAPID 공개키는 실행 중에 API에서 받는다. 새 `VITE_` 변수는 없다.
 * - 서버가 준 구독 id만 localStorage에 둔다. endpoint와 키는 저장하거나 출력하지 않는다.
 */
import { api, ApiError } from "../api/client";

export type PushState = "unsupported" | "off" | "on" | "blocked";

/** 브라우저가 Web Push에 필요한 기능을 갖췄는지. */
export interface PushEnv {
  secure: boolean;
  serviceWorker: boolean;
  pushManager: boolean;
  notification: boolean;
}

/** 푸시 기능이 쓰는 API 함수. 테스트에서 가짜로 바꿀 수 있게 묶어 둔다. */
export type PushApi = Pick<typeof api, "getVapidPublicKey" | "postPushSubscription" | "deletePushSubscription">;

export const SERVICE_WORKER_URL = "/sw.js";
export const PUSH_SUBSCRIPTION_ID_KEY = "weatherwear.pushSubscriptionId";

/**
 * 푸시 상태 판정 (Req 16.5, Property 27).
 * secure context가 아니거나 Service Worker·PushManager·Notification 중 하나라도 없으면 "unsupported",
 * 권한이 denied면 "blocked", 권한이 granted이고 구독이 있으면 "on", 그 밖에는 "off"다.
 */
export function pushState(env: PushEnv, permission: NotificationPermission | null, subscribed: boolean): PushState {
  if (!env.secure || !env.serviceWorker || !env.pushManager || !env.notification) return "unsupported";
  if (permission === "denied") return "blocked";
  if (permission === "granted" && subscribed) return "on";
  return "off";
}

/** VAPID 공개키(base64url, 패딩 없음)를 `applicationServerKey`용 바이트로 바꾼다. */
export function urlBase64ToUint8Array(b64url: string): Uint8Array<ArrayBuffer> {
  const base64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob(padded);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

/** 현재 브라우저의 지원 여부. 아무 권한도 요청하지 않는다. */
export function detectPushEnv(): PushEnv {
  const hasWindow = typeof window !== "undefined";
  return {
    secure: hasWindow && window.isSecureContext === true,
    serviceWorker: typeof navigator !== "undefined" && "serviceWorker" in navigator,
    pushManager: hasWindow && "PushManager" in window,
    notification: hasWindow && "Notification" in window,
  };
}

function currentPermission(env: PushEnv): NotificationPermission | null {
  return env.notification ? Notification.permission : null;
}

export function storedSubscriptionId(): number | null {
  try {
    const raw = window.localStorage.getItem(PUSH_SUBSCRIPTION_ID_KEY);
    if (raw === null) return null;
    const id = Number(raw);
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

function storeSubscriptionId(id: number | null): void {
  try {
    if (id === null) window.localStorage.removeItem(PUSH_SUBSCRIPTION_ID_KEY);
    else window.localStorage.setItem(PUSH_SUBSCRIPTION_ID_KEY, String(id));
  } catch {
    // 저장소를 쓸 수 없으면(사생활 보호 모드 등) 다음에 열 때 "꺼짐"으로 보인다.
  }
}

/** 이미 등록된 Service Worker만 찾는다. 없으면 등록하지 않고 undefined다. */
async function existingRegistration(): Promise<ServiceWorkerRegistration | undefined> {
  try {
    return await navigator.serviceWorker.getRegistration("/");
  } catch {
    return undefined;
  }
}

/**
 * 화면을 열 때의 푸시 상태. 권한을 요청하거나 Service Worker를 등록하지 않는다 (Req 16.2).
 * 브라우저 구독과 서버 구독 id가 둘 다 있어야 "켜짐"이다.
 */
export async function readPushState(): Promise<PushState> {
  const env = detectPushEnv();
  const permission = currentPermission(env);
  const base = pushState(env, permission, false);
  if (base !== "off" || permission !== "granted") return base;
  const registration = await existingRegistration();
  let subscription: PushSubscription | null = null;
  try {
    subscription = (await registration?.pushManager.getSubscription()) ?? null;
  } catch {
    subscription = null;
  }
  return pushState(env, permission, subscription !== null && storedSubscriptionId() !== null);
}

function sameKey(current: ArrayBuffer | null | undefined, next: Uint8Array): boolean {
  if (!current) return false;
  const bytes = new Uint8Array(current);
  return bytes.length === next.length && bytes.every((value, index) => value === next[index]);
}

function subscriptionBody(subscription: PushSubscription) {
  const json = subscription.toJSON();
  return {
    endpoint: json.endpoint ?? subscription.endpoint,
    expirationTime: json.expirationTime ?? null,
    keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" },
  };
}

/**
 * "알림 켜기" (Req 16.3). 사용자 동작 안에서만 부른다.
 * 권한 요청 → `register("/sw.js")` → 공개키 → `subscribe` → POST → 구독 id 저장 순서다.
 * 권한을 거부하면 "blocked"(또는 닫으면 "off")를 돌려준다. API 저장이 실패하면 방금 만든
 * 브라우저 구독을 되돌리고 오류를 다시 던진다(화면은 이전 상태로 돌아간다).
 */
export async function enablePush(client: PushApi = api): Promise<PushState> {
  const env = detectPushEnv();
  if (pushState(env, "default", false) === "unsupported") return "unsupported";

  const permission = await Notification.requestPermission();
  if (permission !== "granted") return pushState(env, permission, false);

  await navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: "/" });
  // subscribe()는 활성 worker가 있는 registration이 필요하다.
  const registration = await navigator.serviceWorker.ready;
  const { public_key: publicKey } = await client.getVapidPublicKey();
  const applicationServerKey = urlBase64ToUint8Array(publicKey);

  // 서버 키가 바뀌었으면(Req 15.3) 이전 키로 만든 브라우저 구독을 먼저 해지한다.
  const existing = await registration.pushManager.getSubscription();
  let subscription: PushSubscription;
  if (existing && sameKey(existing.options.applicationServerKey, applicationServerKey)) {
    subscription = existing;
  } else {
    if (existing) await existing.unsubscribe().catch(() => false);
    subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
  }

  try {
    const created = await client.postPushSubscription(subscriptionBody(subscription));
    storeSubscriptionId(created.id);
  } catch (error) {
    await subscription.unsubscribe().catch(() => false);
    throw error;
  }
  return "on";
}

/**
 * "알림 끄기" (Req 16.4). 서버 구독을 먼저 지운 뒤 브라우저 구독을 해지한다.
 * 서버 삭제가 실패하면 아무것도 바꾸지 않고 오류를 던진다(화면은 "켜짐"을 유지한다).
 * 서버에 이미 없는 구독(404)은 지운 것으로 본다.
 */
export async function disablePush(client: PushApi = api): Promise<PushState> {
  const env = detectPushEnv();
  const id = storedSubscriptionId();
  if (id !== null) {
    try {
      await client.deletePushSubscription(id);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
  }
  storeSubscriptionId(null);

  const registration = env.serviceWorker ? await existingRegistration() : undefined;
  try {
    const subscription = (await registration?.pushManager.getSubscription()) ?? null;
    await subscription?.unsubscribe();
  } catch {
    // 서버 구독은 이미 지웠으므로 더 보내지 않는다. 남은 브라우저 구독은 다음 켜기에서 바뀐다.
  }
  return pushState(env, currentPermission(env), false);
}
