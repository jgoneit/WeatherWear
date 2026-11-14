import { STRINGS } from "../lib/strings";

/** 홈 화면 placeholder. task 13.5에서 구현한다. */
export function HomePage() {
  return (
    <section className="glass rounded-(--radius-card) p-5">
      <h1 className="text-2xl font-semibold tracking-tight">{STRINGS.home.title}</h1>
      <p className="mt-2 text-slate-700">{STRINGS.home.placeholder}</p>
    </section>
  );
}
