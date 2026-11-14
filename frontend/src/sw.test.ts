import { describe, expect, it, vi } from "vitest";
import swSource from "../public/sw.js?raw";

const ORIGIN = "http://localhost:5173";

type Listener = (event: unknown) => void;

interface FakeClient {
  url: string;
  focus: ReturnType<typeof vi.fn>;
  navigate: ReturnType<typeof vi.fn>;
}

/** Service Worker 전역(`self`)을 흉내 내고 sw.js를 그 위에서 실행한다. */
function loadWorker(windows: FakeClient[] = []) {
  const listeners = new Map<string, Listener>();
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
    skipWaiting: vi.fn(),
    registration: { showNotification: vi.fn(async () => undefined) },
    clients: {
      claim: vi.fn(async () => undefined),
      matchAll: vi.fn(async () => windows),
      openWindow: vi.fn(async () => null),
    },
  };
  new Function("self", swSource)(self);

  /** 이벤트를 보내고 waitUntil에 넘긴 작업이 끝날 때까지 기다린다. */
  async function dispatch(type: string, event: Record<string, unknown>) {
    const pending: Promise<unknown>[] = [];
    const listener = listeners.get(type);
    if (!listener) throw new Error(`no ${type} listener`);
    listener({ ...event, waitUntil: (p: Promise<unknown>) => pending.push(p) });
    await Promise.all(pending);
    return pending.length;
  }
  return { self, dispatch };
}

function windowClient(url: string, navigate = vi.fn(async () => null)): FakeClient {
  const client: FakeClient = { url, navigate, focus: vi.fn() };
  client.focus.mockImplementation(async () => client);
  return client;
}

function pushEvent(payload: unknown) {
  return { data: { json: () => payload } };
}

function clickEvent(url: unknown) {
  return { notification: { close: vi.fn(), data: { url } } };
}

describe("sw.js push", () => {
  it("payload의 title·body로 tag가 고정된 알림을 띄우고 url을 data에 담는다", async () => {
    const { self, dispatch } = loadWorker();
    const waited = await dispatch(
      "push",
      pushEvent({ title: "오늘 아침 9°C", body: "코트를 입어요", url: "/outfits/3", notification_id: 7 }),
    );

    expect(waited).toBe(1);
    expect(self.registration.showNotification).toHaveBeenCalledWith("오늘 아침 9°C", {
      body: "코트를 입어요",
      tag: "weatherwear-daily-brief",
      data: { url: `${ORIGIN}/outfits/3` },
    });
  });

  it("payload가 없거나 JSON이 아니면 기본 제목과 앱 첫 화면을 쓴다", async () => {
    const empty = loadWorker();
    await empty.dispatch("push", { data: null });
    expect(empty.self.registration.showNotification).toHaveBeenCalledWith("WeatherWear", {
      body: "",
      tag: "weatherwear-daily-brief",
      data: { url: `${ORIGIN}/` },
    });

    const broken = loadWorker();
    await broken.dispatch("push", {
      data: {
        json: () => {
          throw new SyntaxError("bad json");
        },
      },
    });
    expect(broken.self.registration.showNotification).toHaveBeenCalledWith(
      "WeatherWear",
      expect.objectContaining({ data: { url: `${ORIGIN}/` } }),
    );
  });

  it("다른 출처 url은 앱 첫 화면으로 바꾼다", async () => {
    const { self, dispatch } = loadWorker();
    await dispatch("push", pushEvent({ title: "t", body: "b", url: "https://evil.example/x" }));
    expect(self.registration.showNotification).toHaveBeenCalledWith(
      "t",
      expect.objectContaining({ data: { url: `${ORIGIN}/` } }),
    );
  });
});

describe("sw.js notificationclick", () => {
  it("열린 WeatherWear 창이 있으면 앞으로 가져와 url로 이동한다", async () => {
    const other = windowClient("https://other.example/");
    const app = windowClient(`${ORIGIN}/history`);
    const { self, dispatch } = loadWorker([other, app]);
    const event = clickEvent(`${ORIGIN}/outfits/3`);

    await dispatch("notificationclick", event);

    expect(event.notification.close).toHaveBeenCalledTimes(1);
    expect(self.clients.matchAll).toHaveBeenCalledWith({ type: "window", includeUncontrolled: true });
    expect(app.focus).toHaveBeenCalledTimes(1);
    expect(app.navigate).toHaveBeenCalledWith(`${ORIGIN}/outfits/3`);
    expect(other.focus).not.toHaveBeenCalled();
    expect(self.clients.openWindow).not.toHaveBeenCalled();
  });

  it("열린 창이 없으면 url을 새 창으로 연다", async () => {
    const { self, dispatch } = loadWorker([windowClient("https://other.example/")]);
    const event = clickEvent(`${ORIGIN}/outfits/9`);

    await dispatch("notificationclick", event);

    expect(event.notification.close).toHaveBeenCalledTimes(1);
    expect(self.clients.openWindow).toHaveBeenCalledWith(`${ORIGIN}/outfits/9`);
  });

  it("창 이동이 거부되면(아직 제어하지 않는 창) 새 창으로 연다", async () => {
    const app = windowClient(`${ORIGIN}/`, vi.fn(async () => Promise.reject(new TypeError("not controlled"))));
    const { self, dispatch } = loadWorker([app]);

    await dispatch("notificationclick", clickEvent(`${ORIGIN}/outfits/1`));

    expect(app.focus).toHaveBeenCalledTimes(1);
    expect(self.clients.openWindow).toHaveBeenCalledWith(`${ORIGIN}/outfits/1`);
  });

  it("알림 data에 url이 없으면 앱 첫 화면으로 간다", async () => {
    const { self, dispatch } = loadWorker();
    await dispatch("notificationclick", { notification: { close: vi.fn(), data: null } });
    expect(self.clients.openWindow).toHaveBeenCalledWith(`${ORIGIN}/`);
  });
});

describe("sw.js lifecycle", () => {
  it("설치되면 바로 활성화하고, 활성화되면 열린 창을 제어한다", async () => {
    const { self, dispatch } = loadWorker();
    await dispatch("install", {});
    expect(self.skipWaiting).toHaveBeenCalledTimes(1);
    expect(await dispatch("activate", {})).toBe(1);
    expect(self.clients.claim).toHaveBeenCalledTimes(1);
  });
});
