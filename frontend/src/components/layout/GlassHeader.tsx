import type { ReactNode } from "react";
import { STRINGS } from "../../lib/strings";

interface GlassHeaderProps {
  /** 오른쪽 영역(예: fixture 배지). 없으면 비운다. */
  trailing?: ReactNode;
}

/** 상단 고정 글래스 헤더. 노치 기기를 위해 safe-area 상단 여백을 더한다. */
export function GlassHeader({ trailing }: GlassHeaderProps) {
  return (
    <header className="glass sticky top-0 z-20 flex min-w-0 items-center justify-between gap-3 rounded-b-3xl px-5 pt-[calc(0.75rem+env(safe-area-inset-top))] pb-3">
      <span className="truncate text-base font-semibold tracking-tight text-slate-900">{STRINGS.appName}</span>
      {trailing ? <div className="flex shrink-0 items-center gap-2">{trailing}</div> : null}
    </header>
  );
}
