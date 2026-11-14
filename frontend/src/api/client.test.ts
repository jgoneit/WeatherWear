import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, DEFAULT_TIMEOUT_MS, DEMO_TIMEOUT_MS, api, request } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** 응답하지 않다가 signal이 abort되면 AbortError로 거부하는 fetch. */
function hangingFetch() {
  return vi.fn((_url: string, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () =>
        reject(new DOMException("The operation was aborted.", "AbortError")),
      );
    });
  });
}

async function catchError(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (err) {
    return err;
  }
  throw new Error("expected promise to reject");
}

describe("request timeout", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("홈 데이터 요청은 10초가 지나면 TIMEOUT ApiError로 실패한다", async () => {
    const fetchMock = hangingFetch();
    vi.stubGlobal("fetch", fetchMock);

    const settled = vi.fn();
    const result = catchError(api.getRecommendations()).then((err) => {
      settled();
      return err;
    });

    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS - 1);
    expect(settled).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    const err = await result;
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 0, code: "TIMEOUT" });
    expect((err as ApiError).isTimeout).toBe(true);
  });

  it("피드백 저장은 10초, 데모 스캔은 30초 제한을 쓴다", async () => {
    vi.stubGlobal("fetch", hangingFetch());

    const feedback = vi.fn();
    const scan = vi.fn();
    void catchError(api.postFeedback(1, "cold")).then(feedback);
    void catchError(api.scan()).then(scan);

    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS);
    expect(feedback).toHaveBeenCalledWith(expect.objectContaining({ code: "TIMEOUT" }));
    expect(scan).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(DEMO_TIMEOUT_MS - DEFAULT_TIMEOUT_MS);
    expect(scan).toHaveBeenCalledWith(expect.objectContaining({ code: "TIMEOUT" }));
  });

  it("호출자가 취소하면 TIMEOUT이 아닌 ABORTED로 실패한다", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const controller = new AbortController();
    const result = catchError(request("/api/meta", { timeoutMs: 1_000, signal: controller.signal }));
    controller.abort();
    expect(await result).toMatchObject({ status: 0, code: "ABORTED" });
  });
});

describe("ApiError parsing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("ErrorResponse 본문의 code와 message를 그대로 쓴다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error: { code: "OUTFIT_NOT_FOUND", message: "착장을 찾을 수 없습니다." } }, 404),
      ),
    );
    const err = await catchError(api.getOutfit(999));
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      status: 404,
      code: "OUTFIT_NOT_FOUND",
      message: "착장을 찾을 수 없습니다.",
    });
  });

  it("ErrorResponse 형식이 아닌 오류 본문은 HTTP_<status> 코드가 된다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: 502 })));
    expect(await catchError(api.getOutfits())).toMatchObject({ status: 502, code: "HTTP_502" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "nope" }, 500)));
    expect(await catchError(api.getOutfits())).toMatchObject({ status: 500, code: "HTTP_500" });
  });

  it("연결 실패는 status 0, NETWORK_ERROR가 된다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    expect(await catchError(api.getMeta())).toMatchObject({ status: 0, code: "NETWORK_ERROR" });
  });
});

describe("api endpoints", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("postFeedback은 thermal_vote JSON 본문으로 POST한다", async () => {
    const created = { outfit_id: 7, thermal_vote: "comfortable", created_at: "2026-11-14T04:00:00Z" };
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(created, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.postFeedback(7, "comfortable")).resolves.toEqual(created);

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/outfits/7/feedback");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ thermal_vote: "comfortable" });
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
  });

  it("selectScenario는 PUT, clearScenario는 DELETE이고 204에는 undefined를 돌려준다", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "DELETE"
        ? new Response(null, { status: 204 })
        : jsonResponse({ selected_id: "cold-snap", scenario: { id: "cold-snap" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.selectScenario("cold-snap");
    await expect(api.clearScenario()).resolves.toBeUndefined();

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/demo/scenarios/selected", "PUT"],
      ["/api/demo/scenarios/selected", "DELETE"],
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))).toEqual({ scenario_id: "cold-snap" });
  });
});
