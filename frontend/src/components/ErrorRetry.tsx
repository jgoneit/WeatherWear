import { STRINGS } from "../lib/strings";

interface ErrorRetryProps {
  onRetry: () => void;
  /** 보여줄 메시지. 기본값은 "데이터를 불러오지 못했어요". */
  message?: string;
  /** 다시 시도 중이면 버튼을 비활성화한다. */
  pending?: boolean;
}

/** 불러오기 실패 안내와 다시 시도 버튼. 이전 내용은 호출한 화면이 그대로 둔다 (Req 11.9). */
export function ErrorRetry({ onRetry, message = STRINGS.error.loadFailed, pending = false }: ErrorRetryProps) {
  return (
    <div role="alert" className="glass flex min-w-0 items-center justify-between gap-3 rounded-3xl px-4 py-3">
      <p className="min-w-0 text-sm font-medium text-slate-900">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        disabled={pending}
        className="min-h-11 shrink-0 rounded-full bg-slate-900 px-4 text-sm font-semibold text-white transition active:scale-95 disabled:opacity-50 motion-reduce:transition-none"
      >
        {STRINGS.error.retry}
      </button>
    </div>
  );
}
