import { Outlet } from "react-router";
import { IS_DEV } from "../../lib/env";
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
