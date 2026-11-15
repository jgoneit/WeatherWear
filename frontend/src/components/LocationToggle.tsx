import { useCallback, useRef, useState } from "react";
import { api } from "../api/client";
import { formatDateTime } from "../lib/format";
import { reportLocation, type LocationApi } from "../lib/location";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";
import { ErrorRetry } from "./ErrorRetry";

interface LocationToggleProps {
  /** 받은 시각 표시에 쓰는 app_timezone. */
  timeZone: string;
  /** 테스트에서 가짜 API를 넣을 때 쓴다. */
  client?: LocationApi;
}

type Notice = { tone: "ok" | "warn" | "error"; text: string };

/**
 * 위치 상태(마지막 보고 시각과 Place_Label)와 "현재 위치 사용" (Req 16.6~16.8).
 * 화면을 열 때는 GET /api/location만 부르고 위치 권한을 요청하지 않는다.
 * 거부나 10초 초과는 기본 위치 안내만 보이고 화면을 막지 않는다.
 */
export function LocationToggle({ timeZone, client }: LocationToggleProps) {
  const location = useAsync((signal) => api.getLocation({ signal }), []);
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const busy = useRef(false);
  const reload = location.reload;

  const handleUse = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setWorking(true);
    setNotice(null);
    try {
      const result = await reportLocation(client);
      if (result === "sent") {
        setNotice({ tone: "ok", text: STRINGS.location.sent });
        void reload();
      } else {
        setNotice({ tone: "warn", text: STRINGS.location.fallback });
      }
    } catch {
      setNotice({ tone: "error", text: STRINGS.location.saveFailed });
    } finally {
      busy.current = false;
      setWorking(false);
    }
  }, [client, reload]);

  const state = location.data;
  return (
    <section aria-labelledby="location-toggle-title" className="automation-card" data-testid="location-toggle">
      <div className="automation-card-head">
        <h2 id="location-toggle-title">{STRINGS.location.title}</h2>
      </div>
      {location.error !== undefined ? (
        <ErrorRetry message={STRINGS.location.loadFailed} pending={location.pending} onRetry={() => void reload()} />
      ) : null}
      {state ? (
        <dl className="automation-facts">
          <div>
            <dt>{STRINGS.location.lastLabel}</dt>
            <dd data-testid="location-last">
              {state.last
                ? STRINGS.location.last(state.last.place_label, formatDateTime(state.last.received_at, timeZone))
                : STRINGS.location.none}
            </dd>
          </div>
        </dl>
      ) : null}
      {state ? <p className="automation-help">{STRINGS.location.briefSource(state.brief_place_label)}</p> : null}
      <p className="automation-help">{STRINGS.location.help}</p>
      <div className="automation-actions">
        <button
          type="button"
          className="automation-button"
          disabled={working}
          aria-busy={working}
          onClick={() => void handleUse()}
        >
          {working ? STRINGS.location.working : STRINGS.location.useCurrent}
        </button>
      </div>
      {notice ? (
        <p
          className="automation-note"
          data-tone={notice.tone}
          role={notice.tone === "error" ? "alert" : "status"}
          data-testid="location-notice"
        >
          {notice.text}
        </p>
      ) : null}
    </section>
  );
}
