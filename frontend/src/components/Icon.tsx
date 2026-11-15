import type { CSSProperties } from "react";
export function Icon({ name, size = 20, style }: { name: "hanger" | "home" | "grid" | "settings" | "temperature" | "arrow" | "bell"; size?: number; style?: CSSProperties }) {
  const paths = {
    hanger: <><path d="M9 6a3 3 0 0 1 6 0c0 2-3 2-3 4v1L3 17a1.2 1.2 0 0 0 .7 2h16.6a1.2 1.2 0 0 0 .7-2l-9-6" /></>,
    home: <><path d="m3 10 9-7 9 7v10H3Z" /><path d="M9 20v-7h6v7" /></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    settings: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor"/><circle cx="15" cy="17" r="3" fill="currentColor"/></>,
    temperature: <><path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0Z"/><path d="M12 9v9M17 6h3M17 10h2"/></>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>,
    bell: <><path d="M6 10a6 6 0 0 1 12 0c0 4.5 1.4 6.2 2.4 7H3.6C4.6 16.2 6 14.5 6 10Z"/><path d="M10 20a2 2 0 0 0 4 0"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>{paths[name]}</svg>;
}
