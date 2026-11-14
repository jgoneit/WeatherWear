import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useAsync } from "./useAsync";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useAsync", () => {
  it("마운트 시 로더를 실행하고 pending → data로 바뀐다", async () => {
    const d = deferred<string>();
    const { result } = renderHook(() => useAsync(() => d.promise, []));

    expect(result.current.pending).toBe(true);
    expect(result.current.data).toBeUndefined();

    await act(async () => d.resolve("first"));
    expect(result.current).toMatchObject({ pending: false, data: "first", error: undefined });
  });

  it("재조회가 실패해도 이전 데이터를 유지하고 error를 채운다", async () => {
    const loader = vi.fn<(signal: AbortSignal) => Promise<string>>().mockResolvedValueOnce("first");
    const { result } = renderHook(() => useAsync(loader, []));
    await waitFor(() => expect(result.current.data).toBe("first"));

    const failure = new Error("boom");
    const next = deferred<string>();
    loader.mockReturnValueOnce(next.promise);

    let reloading!: Promise<void>;
    act(() => {
      reloading = result.current.reload();
    });
    expect(result.current.pending).toBe(true);
    expect(result.current.data).toBe("first");

    await act(async () => {
      next.reject(failure);
      await reloading;
    });
    expect(result.current).toMatchObject({ pending: false, data: "first", error: failure });
  });

  it("늦게 도착한 이전 응답은 무시하고 이전 요청의 signal을 abort한다", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const signals: AbortSignal[] = [];
    const loader = vi
      .fn((signal: AbortSignal) => {
        signals.push(signal);
        return signals.length === 1 ? first.promise : second.promise;
      });
    const { result } = renderHook(() => useAsync(loader, []));

    act(() => {
      void result.current.reload();
    });
    expect(signals[0]?.aborted).toBe(true);

    await act(async () => second.resolve("new"));
    await act(async () => first.resolve("stale"));
    expect(result.current).toMatchObject({ data: "new", pending: false });
  });

  it("immediate=false면 reload 전까지 요청하지 않는다", async () => {
    const loader = vi.fn(async () => 1);
    const { result } = renderHook(() => useAsync(loader, [], { immediate: false }));
    expect(loader).not.toHaveBeenCalled();
    expect(result.current.pending).toBe(false);

    await act(() => result.current.reload());
    expect(loader).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe(1);
  });
});
