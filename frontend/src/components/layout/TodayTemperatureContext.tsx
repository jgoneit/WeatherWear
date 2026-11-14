import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/**
 * 셸 배경이 쓰는 오늘 기온.
 * 홈 화면이 추천 응답(today.actual_temperature_c)을 받은 뒤 `useReportTodayTemperature`로 알려 준다.
 * 셸은 따로 API를 부르지 않는다. 값은 다른 탭으로 이동해도 유지된다.
 */
interface TodayTemperatureValue {
  temperatureC: number | null;
  setTemperatureC: (celsius: number | null) => void;
}

const noop = () => {};

const TodayTemperatureContext = createContext<TodayTemperatureValue>({
  temperatureC: null,
  setTemperatureC: noop,
});

interface ProviderProps {
  initialTemperatureC?: number | null;
  children: ReactNode;
}

export function TodayTemperatureProvider({ initialTemperatureC = null, children }: ProviderProps) {
  const [temperatureC, setTemperatureC] = useState<number | null>(initialTemperatureC);
  const value = useMemo(() => ({ temperatureC, setTemperatureC }), [temperatureC]);
  return <TodayTemperatureContext.Provider value={value}>{children}</TodayTemperatureContext.Provider>;
}

/** 현재 오늘 기온과 setter. Provider 밖에서는 null과 no-op setter를 돌려준다. */
export function useTodayTemperature(): TodayTemperatureValue {
  return useContext(TodayTemperatureContext);
}

/**
 * 화면이 받은 오늘 기온을 셸에 알린다. undefined면(아직 로딩 중) 이전 값을 유지한다.
 * Provider 밖(단독 렌더 테스트)에서는 아무것도 하지 않는다.
 */
export function useReportTodayTemperature(celsius: number | null | undefined): void {
  const { setTemperatureC } = useTodayTemperature();
  useEffect(() => {
    if (celsius !== undefined) setTemperatureC(celsius);
  }, [celsius, setTemperatureC]);
}
