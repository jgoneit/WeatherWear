import { useCallback, useRef, useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, apiUrl, type Outfit, type ThermalVote } from "../api/client";
import { formatTemp } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { FeedbackButtons } from "./FeedbackButtons";
import { Toast } from "./Toast";

interface NotificationCardProps {
  /** 1순위 추천 Outfit (Req 13.1). */
  outfit: Outfit;
  /** 오늘 WeatherContext의 actual_temperature_c (Req 13.1). */
  todayTemperatureC: number | null;
  /** 피드백 저장 성공 후 호출된다. 홈은 여기서 추천을 다시 조회한다 (Req 13.6). */
  onSaved?: (vote: ThermalVote) => void;
}

/**
 * Compact feedback card; successful saves also announce confirmation.
 * - 버튼을 탭하면 확인 없이 POST 1회, 대기 중 3버튼 비활성화.
 * - 성공하면 탭한 버튼을 선택 상태로 바꾸고 onSaved() 호출.
 * - 실패/10초 초과면 토스트를 띄우고 요청 이전 선택을 유지한다.
 * - 버튼 영역을 뺀 본문을 탭하면 /outfits/:id 로 이동한다.
 */
export function NotificationCard({ outfit, todayTemperatureC, onSaved }: NotificationCardProps) {
  const navigate = useNavigate();
  const detailHref = `/outfits/${outfit.id}`;
  const serverVote = outfit.effective_vote ?? null;

  const [selected, setSelected] = useState<ThermalVote | null>(serverVote);
  const [pendingVote, setPendingVote] = useState<ThermalVote | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  // 같은 렌더 안에서 연속 탭이 들어와도 요청은 1회만 보낸다 (Req 13.4, 13.5).
  const inFlight = useRef(false);

  // 추천 재조회로 표시 Outfit이나 서버의 effective_vote가 바뀌면 선택 상태를 맞춘다.
  const [syncedKey, setSyncedKey] = useState(`${outfit.id}:${serverVote}`);
  const currentKey = `${outfit.id}:${serverVote}`;
  if (syncedKey !== currentKey) {
    setSyncedKey(currentKey);
    setSelected(serverVote);
  }

  const handleSelect = useCallback(
    async (vote: ThermalVote) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setPendingVote(vote);
      setToast(null);
      try {
        await api.postFeedback(outfit.id, vote);
        setSelected(vote);
        setToast(STRINGS.feedback.saved);
        onSaved?.(vote);
      } catch {
        // 선택 상태는 요청 전에 바꾸지 않았으므로 그대로 유지된다 (Req 13.7).
        setToast(STRINGS.feedback.saveFailed);
      } finally {
        inFlight.current = false;
        setPendingVote(null);
      }
    },
    [outfit.id, onSaved],
  );

  const handleBodyClick = (event: MouseEvent) => {
    // 내부 Link가 이미 이동을 처리했으면 다시 이동하지 않는다.
    if (event.defaultPrevented) return;
    navigate(detailHref);
  };

  const dismissToast = useCallback(() => setToast(null), []);
  const tempLabel = formatTemp(todayTemperatureC);

  return (
    <>
      <article
        data-testid="notification-card"
        aria-label={STRINGS.feedback.notificationLabel}
        onClick={handleBodyClick}
        className="notification flex min-w-0 cursor-pointer flex-col gap-4"
      >
        <Link
          to={detailHref}
          aria-label={`${STRINGS.feedback.openDetail}: ${STRINGS.feedback.question(tempLabel)}`}
          className="flex min-w-0 items-start gap-3 rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2 text-xs font-medium text-slate-600">
<span className="notification-title">{STRINGS.feedback.notificationHeader}</span>
            </div>
            <p className="mt-1.5 text-[15px] leading-snug font-semibold text-slate-900">
              {STRINGS.feedback.question(tempLabel)}
            </p>
          </div>
          <img
            src={apiUrl(outfit.photo_url)}
            alt={STRINGS.feedback.thumbnailAlt}
            width={56}
            height={56}
            decoding="async"
            className="size-14 shrink-0 rounded-xl bg-slate-200 object-cover"
          />
        </Link>

        <FeedbackButtons
          selected={selected}
          pending={pendingVote !== null}
          pendingVote={pendingVote}
          onSelect={handleSelect}
        />
      </article>
      <Toast message={toast} onDismiss={dismissToast} />
    </>
  );
}
