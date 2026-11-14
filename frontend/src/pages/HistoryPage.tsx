import { STRINGS } from "../lib/strings";

/** 착장 기록 화면 placeholder. task 14.1에서 구현한다. */
export function HistoryPage() {
  return (
    <section className="glass rounded-(--radius-card) p-5">
      <h1 className="text-2xl font-semibold tracking-tight">{STRINGS.history.title}</h1>
      <p className="mt-2 text-slate-700">{STRINGS.history.placeholder}</p>
    </section>
  );
}
