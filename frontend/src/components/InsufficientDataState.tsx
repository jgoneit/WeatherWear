import { STRINGS } from "../lib/strings";

/** 추천할 과거 착장이 없을 때 추천 카드 대신 보이는 안내 (Req 6.6, 11.8). */
export function InsufficientDataState() {
  return (
    <section role="status" className="glass min-w-0 rounded-3xl px-5 py-6 text-center">
      <p className="text-3xl" aria-hidden="true">
        🧺
      </p>
      <p className="mt-2 text-base font-semibold text-slate-900">{STRINGS.notice.insufficientData}</p>
      <p className="mt-1 text-sm text-slate-600">{STRINGS.recommendation.insufficientDataDetail}</p>
    </section>
  );
}
