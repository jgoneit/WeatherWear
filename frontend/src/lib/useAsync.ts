import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  /** 마지막으로 성공한 결과. 재조회 중이거나 재조회가 실패해도 지우지 않는다 (Req 11.9). */
  data: T | undefined;
  /** 마지막 요청의 오류. 새 요청을 시작하거나 성공하면 지운다. */
  error: unknown;
  /** 요청이 진행 중인지 여부. 중복 요청을 막는 버튼 비활성에 쓴다 (Req 13.5, 14.5). */
  pending: boolean;
  /** 같은 로더로 다시 요청한다. 진행 중인 이전 요청은 취소한다. */
  reload: () => Promise<void>;
}

export interface UseAsyncOptions {
  /** false면 마운트 시 자동으로 요청하지 않는다. 기본값 true. */
  immediate?: boolean;
}

/**
 * 비동기 로더를 실행하고 data/error/pending 상태를 관리하는 작은 훅.
 * `deps`가 바뀌거나 `reload()`를 부르면 새로 요청하며, 늦게 도착한 이전 응답은 무시한다.
 */
export function useAsync<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  { immediate = true }: UseAsyncOptions = {},
): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState<boolean>(immediate);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const run = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setPending(true);
    setError(undefined);
    try {
      const result = await loaderRef.current(controller.signal);
      if (!mountedRef.current || controllerRef.current !== controller) return;
      setData(result);
    } catch (err) {
      if (!mountedRef.current || controllerRef.current !== controller) return;
      setError(err);
    } finally {
      if (mountedRef.current && controllerRef.current === controller) {
        controllerRef.current = null;
        setPending(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (immediate) void run();
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
    // deps는 호출자가 정한다(loader는 ref로 최신 값을 쓴다).
  }, deps);

  return { data, error, pending, reload: run };
}
