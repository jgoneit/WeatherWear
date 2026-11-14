import { IS_DEV } from "../lib/env";
import { STRINGS } from "../lib/strings";

interface DemoPageProps {
  /** 테스트에서 개발 모드 여부를 바꿀 때 사용한다. 기본값은 import.meta.env.DEV. */
  isDev?: boolean;
}

/** 데모 관리 화면 placeholder. 개발 모드에서만 컨트롤을 노출한다 (Req 14.1, 14.2). */
export function DemoPage({ isDev = IS_DEV }: DemoPageProps) {
  return (
    <section className="glass rounded-(--radius-card) p-5">
      <h1 className="text-2xl font-semibold tracking-tight">{STRINGS.demo.title}</h1>
      <p className="mt-2 text-slate-700">{isDev ? STRINGS.demo.placeholder : STRINGS.demo.devOnly}</p>
    </section>
  );
}
