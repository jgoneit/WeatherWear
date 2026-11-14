import { useEffect } from "react";
import { STRINGS } from "../lib/strings";

interface ToastProps {
  /** 표시할 메시지. null이면 아무것도 그리지 않는다. */
  message: string | null;
  /** 닫기 버튼이나 자동 닫힘 때 호출된다. */
  onDismiss?: () => void;
  /** 자동으로 닫히기까지의 시간(ms). 0이면 자동으로 닫지 않는다. */
  durationMs?: number;
}

/** 하단 탭바 위에 잠깐 떠 있는 알림 (Req 13.7에서 사용). */
export function Toast({ message, onDismiss, durationMs = 3000 }: ToastProps) {
  useEffect(() => {
    if (message === null || !onDismiss || durationMs <= 0) return;
    const timer = setTimeout(onDismiss, durationMs);
    return () => clearTimeout(timer);
  }, [message, onDismiss, durationMs]);

  if (message === null) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(6rem+env(safe-area-inset-bottom))] z-40 flex justify-center px-4">
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-auto flex min-w-0 max-w-full items-center gap-3 rounded-full bg-slate-900/90 px-4 py-2.5 text-sm text-white shadow-lg"
      >
        <span className="min-w-0">{message}</span>
        {onDismiss ? (
          <button
            type="button"
            onClick={onDismiss}
            aria-label={STRINGS.toast.dismiss}
            className="flex size-8 shrink-0 items-center justify-center rounded-full text-white/80 hover:text-white"
          >
            ✕
          </button>
        ) : null}
      </div>
    </div>
  );
}
