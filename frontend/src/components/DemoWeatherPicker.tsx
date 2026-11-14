import { useState } from "react";
import { api } from "../api/client";

const OPTIONS = [
  { id: "cold-snap", label: "한파 −12°" },
  { id: "early-heat", label: "무더위 30°" },
  { id: "heatwave", label: "폭염 38°" },
];

export function DemoWeatherPicker({ selected, onChange }: { selected?: string | null; onChange: () => void }) {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  async function select(id: string) {
    setPending(true);
    setFailed(false);
    try {
      await api.selectScenario(id);
      onChange();
    } catch {
      setFailed(true);
    } finally {
      setPending(false);
    }
  }
  return <section className="demo-weather-picker" aria-label="데모 날씨">
    <p>예시 착장으로 둘러보기</p>
    <div>{OPTIONS.map(option => <button type="button" key={option.id}
      aria-pressed={selected === option.id} disabled={pending}
      onClick={() => void select(option.id)}>{option.label}</button>)}</div>
    {failed ? <p role="alert">날씨를 바꾸지 못했어요. 다시 선택해 주세요.</p> : null}
  </section>;
}
