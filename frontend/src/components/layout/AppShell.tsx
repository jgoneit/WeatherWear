import { useEffect } from "react";
import { Outlet } from "react-router";
import { IS_DEV } from "../../lib/env";
import { autoReportLocation } from "../../lib/location";
import { BottomTabBar } from "./BottomTabBar";
import { GlassHeader } from "./GlassHeader";
import { TemperatureBackground } from "./TemperatureBackground";
import { TodayTemperatureProvider, useTodayTemperature } from "./TodayTemperatureContext";

interface AppShellProps {
  /** "데모" 탭 노출 여부. 기본값은 `IS_DEV` (Req 14.1, 14.2). */
  showDemoTab?: boolean;
  /** 첫 렌더의 오늘 기온. 보통은 비워 두고 홈 화면이 `useReportTodayTemperature`로 채운다. */
  initialTemperatureC?: number | null;
}

/** Responsive application shell with desktop navigation and mobile tabs. */
export function AppShell({ showDemoTab = IS_DEV, initialTemperatureC = null }: AppShellProps) {
  // 앱을 열 때 위치 권한이 이미 granted이고 30분이 지났으면 조용히 위치를 보낸다 (Req 16.6).
  // 권한을 요청하지 않으며(Req 16.2), 실패해도 화면에는 아무것도 띄우지 않는다.
  useEffect(() => {
    void autoReportLocation();
  }, []);

  return (
    <TodayTemperatureProvider initialTemperatureC={initialTemperatureC}>
      <ShellFrame showDemoTab={showDemoTab} />
    </TodayTemperatureProvider>
  );
}

function ShellFrame({ showDemoTab }: { showDemoTab: boolean }) {
  const { temperatureC } = useTodayTemperature();
  return (
    <div className="app-shell relative isolate mx-auto flex min-h-dvh w-full min-w-0 flex-col">
      <TemperatureBackground temperatureC={temperatureC} />
      <GlassHeader />
      <main className="app-main min-w-0 flex-1">
        <Outlet />
      </main>
      <BottomTabBar showDemoTab={showDemoTab} />
    </div>
  );
}
