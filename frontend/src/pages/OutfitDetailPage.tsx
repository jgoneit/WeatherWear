import { useParams } from "react-router";
import { STRINGS } from "../lib/strings";

/** 착장 상세 화면 placeholder. 이후 task에서 구현한다. */
export function OutfitDetailPage() {
  const { id } = useParams();

  return (
    <section className="glass rounded-(--radius-card) p-5">
      <h1 className="text-2xl font-semibold tracking-tight">{STRINGS.detail.title}</h1>
      <p className="mt-2 text-slate-700">{STRINGS.detail.placeholder}</p>
      <p className="mt-1 text-sm text-slate-500 tabular-nums">#{id}</p>
    </section>
  );
}
