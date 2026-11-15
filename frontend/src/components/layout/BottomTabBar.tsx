import { NavLink } from "react-router";
import { IS_DEV } from "../../lib/env";
import { STRINGS } from "../../lib/strings";
import { Icon } from "../Icon";
export function BottomTabBar({ showDemoTab = IS_DEV }: { showDemoTab?: boolean }) {
  const tabs = [
    { to: "/", label: STRINGS.nav.home, icon: "home" as const, end: true },
    { to: "/history", label: STRINGS.nav.history, icon: "grid" as const, end: false },
    { to: "/notifications", label: STRINGS.nav.notifications, icon: "bell" as const, end: false },
    ...(showDemoTab ? [{ to: "/demo", label: STRINGS.nav.demo, icon: "settings" as const, end: false }] : []),
  ];
  return <nav aria-label={STRINGS.nav.label} className="app-nav"><ul>{tabs.map(tab =>
    <li key={tab.to}><NavLink to={tab.to} end={tab.end} className={({ isActive }) => isActive ? "nav-link is-active" : "nav-link"}>
      <Icon name={tab.icon}/><span>{tab.label}</span>
    </NavLink></li>
  )}</ul></nav>;
}
