import { useCallback, useEffect, useRef, useState } from "react";
import { disablePush, enablePush, readPushState, type PushApi, type PushState } from "../lib/push";
import { STRINGS } from "../lib/strings";
import { Toast } from "./Toast";

interface PushToggleProps {
  /** 테스트에서 가짜 API를 넣을 때 쓴다. 기본값은 실제 API 클라이언트. */
  client?: PushApi;
}

const HELP: Record<PushState, string> = {
  unsupported: STRINGS.push.unsupportedHelp,
  off: STRINGS.push.offHelp,
  on: STRINGS.push.onHelp,
  blocked: STRINGS.push.blockedHelp,
};

/**
 * 푸시 상태 4종(지원 안 함·꺼짐·켜짐·차단됨)과 켜기/끄기 (Req 16.2~16.5).
 * 화면을 열 때는 상태만 읽고 권한을 요청하지 않는다. 권한 요청은 "알림 켜기"를 누를 때만 일어난다.
 * 저장이 실패하면 토스트를 띄우고 이전 상태로 되돌린다.
 */
export function PushToggle({ client }: PushToggleProps) {
  const [state, setState] = useState<PushState | null>(null);
  const [working, setWorking] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const busy = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    void readPushState().then((next) => {
      if (mounted.current) setState(next);
    });
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = useCallback(
    async (action: (c?: PushApi) => Promise<PushState>) => {
      if (busy.current) return;
      busy.current = true;
      const previous = state;
      setWorking(true);
      setToast(null);
      try {
        const next = await action(client);
        if (mounted.current) setState(next);
      } catch {
        if (mounted.current) {
          setState(previous);
          setToast(STRINGS.push.saveFailed);
        }
      } finally {
        busy.current = false;
        if (mounted.current) setWorking(false);
      }
    },
    [client, state],
  );

  const dismissToast = useCallback(() => setToast(null), []);

  return (
    <section aria-labelledby="push-toggle-title" className="automation-card" data-testid="push-toggle">
      <div className="automation-card-head">
        <h2 id="push-toggle-title">{STRINGS.push.title}</h2>
        <span
          className="state-chip"
          data-state={state ?? "loading"}
          data-testid="push-state"
          aria-label={state ? `${STRINGS.push.stateLabel}: ${STRINGS.push.state[state]}` : undefined}
        >
          {state ? STRINGS.push.state[state] : STRINGS.emptyValue}
        </span>
      </div>
      {state ? (
        <p className="automation-help" role={state === "blocked" ? "note" : undefined}>
          {HELP[state]}
        </p>
      ) : (
        <p className="automation-help">{STRINGS.notifications.loading}</p>
      )}
      {state === "off" || state === "on" ? (
        <div className="automation-actions">
          <button
            type="button"
            className="automation-button"
            data-variant={state === "on" ? "secondary" : undefined}
            disabled={working}
            aria-busy={working}
            onClick={() => void run(state === "on" ? disablePush : enablePush)}
          >
            {working ? STRINGS.push.working : state === "on" ? STRINGS.push.disable : STRINGS.push.enable}
          </button>
        </div>
      ) : null}
      <Toast message={toast} onDismiss={dismissToast} />
    </section>
  );
}
