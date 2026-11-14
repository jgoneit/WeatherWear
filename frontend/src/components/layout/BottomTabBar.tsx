import { NavLink } from "react-router";
import { IS_DEV } from "../../lib/env";
import { STRINGS } from "../../lib/strings";

interface BottomTabBarProps {
  /** "데모" 탭 노출 여부. 기본값은 개발 모드 플래그 (Req 14.1, 14.2). */
  showDemoTab?: boolean;
}

/** 떠 있는 pill 형태 글래스 탭바. 탭 타깃은 44px 이상이고 safe-area 하단 여백을 더한다. */
export function BottomTabBar({ showDemoTab = IS_DEV }: BottomTabBarProps) {
  const tabs = [
    { to: "/", label: STRINGS.nav.home, end: true },
    { to: "/history", label: STRINGS.nav.history, end: false },
    ...(showDemoTab ? [{ to: "/demo", label: STRINGS.nav.demo, end: false }] : []),
  ];

  return (
    <nav
      aria-label={STRINGS.nav.label}
      className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center px-4 pb-[calc(1rem+env(safe-area-inset-bottom))]"
    >
      <ul className="glass pointer-events-auto flex max-w-full min-w-0 gap-1 rounded-full p-1.5">
        {tabs.map((tab) => (
          <li key={tab.to} className="min-w-0">
            <NavLink
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                `flex min-h-11 min-w-16 items-center justify-center rounded-full px-4 text-sm font-medium transition-colors duration-200 motion-reduce:transition-none ${
                  isActive ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-white/60"
                }`
              }
            >
              {tab.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
