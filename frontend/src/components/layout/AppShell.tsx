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

/**
 * 앱 공통 레이아웃 (design.md 13절 Soft Glass + Bento).
 * 기온 적응형 배경, sticky 글래스 헤더, 떠 있는 하단 탭바로 구성한다.
 * 360px 폭에서도 가로 스크롤이 생기지 않도록 min-w-0, overflow-x-clip, max-w-full을 둔다 (Req 11.6).
 */
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
    <div className="relative isolate mx-auto flex min-h-dvh w-full max-w-[430px] min-w-0 flex-col overflow-x-clip">
      <TemperatureBackground temperatureC={temperatureC} />
      <GlassHeader />
      <main className="w-full max-w-full min-w-0 flex-1 px-4 pt-4 pb-[calc(6rem+env(safe-area-inset-bottom))]">
        <Outlet />
      </main>
      <BottomTabBar showDemoTab={showDemoTab} />
    </div>
  );
}
