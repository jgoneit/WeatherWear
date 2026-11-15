import { useCallback, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api, ApiError, type ClothingTags, type GalleryImport } from "../api/client";
import { briefHref } from "../components/DailyBriefCard";
import { ErrorRetry } from "../components/ErrorRetry";
import { FixtureBadge } from "../components/FixtureBadge";
import { GalleryImportList } from "../components/GalleryImportList";
import { LocationToggle } from "../components/LocationToggle";
import { PushToggle } from "../components/PushToggle";
import { FALLBACK_TIMEZONE, formatDateTime } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";

/** 최근 알림과 사진 처리 기록에 보여 줄 건수 (API 기본값과 같다). */
const LIST_LIMIT = 20;
/** "확인이 필요한 사진"은 한 번에 최대 100건(API 최대)까지 보여 준다. */
const REVIEW_LIMIT = 100;

/**
 * 태그가 모두 `unknown`이고 신뢰도가 0인 착장 (백엔드 `rule_based_v1.is_non_outfit`과 같은 규칙).
 * 이런 착장은 기록에는 보이지만 추천 후보에서는 빠진다 (Req 6.15, Phase 1 Req 6.10).
 */
export function isUntaggedOutfit(tags: ClothingTags): boolean {
  return (
    tags.upper_type === "unknown" &&
    tags.outer_type === "unknown" &&
    tags.lower_type === "unknown" &&
    tags.confidence === 0
  );
}

/**
 * 알림 탭 (Req 16.2, 16.8, 6.9, 5.5).
 * 푸시·위치 상태와 켜기, 매일 브리핑 설정(읽기 전용), 최근 알림, 확인이 필요한 사진, 사진 처리 기록을 보여 준다.
 * 화면을 열 때 알림·위치 권한을 요청하지 않는다.
 */
export function NotificationsPage() {
  const meta = useAsync((signal) => api.getMeta({ signal }), []);
  const timeZone = meta.data?.app_timezone ?? FALLBACK_TIMEZONE;
  const history = useAsync((signal) => api.getGalleryImports({ limit: LIST_LIMIT }, { signal }), []);
  const reloadHistory = history.reload;
  const handleImportsChanged = useCallback(() => {
    void reloadHistory();
  }, [reloadHistory]);

  return (
    <div className="notifications-page">
      <div className="page-heading">
        <div>
          <h1>{STRINGS.notifications.title}</h1>
          <p>{STRINGS.notifications.subtitle}</p>
        </div>
      </div>
      <PushToggle />
      <LocationToggle timeZone={timeZone} />
      <BriefSettings />
      <RecentNotifications timeZone={timeZone} />
      <UncertainReview timeZone={timeZone} onChanged={handleImportsChanged} />
      <Section id="import-history-title" title={STRINGS.gallery.historyTitle} testId="import-history">
        {history.error !== undefined ? (
          <ErrorRetry
            message={STRINGS.gallery.historyLoadFailed}
            pending={history.pending}
            onRetry={handleImportsChanged}
          />
        ) : null}
        {history.data === undefined ? (
          history.error === undefined ? <p className="automation-help">{STRINGS.notifications.loading}</p> : null
        ) : history.data.length === 0 ? (
          <p className="automation-help">{STRINGS.gallery.historyEmpty}</p>
        ) : (
          <GalleryImportList items={history.data} timeZone={timeZone} label={STRINGS.gallery.historyLabel} />
        )}
      </Section>
    </div>
  );
}

function Section({ id, title, testId, children }: { id: string; title: string; testId?: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="automation-card" data-testid={testId}>
      <div className="automation-card-head">
        <h2 id={id}>{title}</h2>
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 매일 브리핑 설정 (읽기 전용, Req 16.8)
// ---------------------------------------------------------------------------

function BriefSettings() {
  const status = useAsync((signal) => api.getAutomationStatus({ signal }), []);
  const brief = status.data?.daily_brief;
  return (
    <Section id="brief-settings-title" title={STRINGS.notifications.briefTitle} testId="brief-settings">
      {status.error !== undefined ? (
        <ErrorRetry
          message={STRINGS.notifications.briefLoadFailed}
          pending={status.pending}
          onRetry={() => void status.reload()}
        />
      ) : null}
      {brief ? (
        <>
          <p className="automation-value" data-testid="brief-schedule">
            {brief.enabled ? STRINGS.notifications.briefEnabled(brief.time) : STRINGS.notifications.briefDisabled}
          </p>
          <p className="automation-help">
            {STRINGS.notifications.briefTimezone(brief.timezone)} · {STRINGS.notifications.briefReadOnly}
          </p>
        </>
      ) : status.error === undefined ? (
        <p className="automation-help">{STRINGS.notifications.loading}</p>
      ) : null}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 최근 알림 (Req 14.5, 16.8)
// ---------------------------------------------------------------------------

function RecentNotifications({ timeZone }: { timeZone: string }) {
  const list = useAsync((signal) => api.getNotifications({ limit: LIST_LIMIT }, { signal }), []);
  const items = list.data;
  return (
    <Section id="recent-notifications-title" title={STRINGS.notifications.recentTitle} testId="recent-notifications">
      {list.error !== undefined ? (
        <ErrorRetry
          message={STRINGS.notifications.recentLoadFailed}
          pending={list.pending}
          onRetry={() => void list.reload()}
        />
      ) : null}
      {items === undefined ? (
        list.error === undefined ? <p className="automation-help">{STRINGS.notifications.loading}</p> : null
      ) : items.length === 0 ? (
        <p className="automation-help">{STRINGS.notifications.recentEmpty}</p>
      ) : (
        <ul className="notification-list" aria-label={STRINGS.notifications.recentLabel}>
          {items.map((item) => (
            <li key={item.id} className="notification-item" data-testid="notification-item">
              <p className="notification-item-head">
                <span>{STRINGS.notifications.kind[item.kind]}</span>
                <span>{formatDateTime(item.created_at, timeZone)}</span>
                {item.weather_is_fixture === true ? <FixtureBadge /> : null}
              </p>
              <Link to={briefHref(item.url)} className="notification-item-title">
                {item.title}
              </Link>
              <p className="notification-item-body">{item.body}</p>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 확인이 필요한 사진 (Req 6.8, 6.9, 6.12~6.15, 16.8)
// ---------------------------------------------------------------------------

type Notice = { tone: "ok" | "warn" | "error"; text: string; detail?: string };

function failureReason(error: unknown): string {
  return error instanceof ApiError ? error.message : STRINGS.error.server;
}

/** 서버 상태가 화면과 달라 다시 불러와야 하는 응답 (이미 처리됨, 없어짐). */
function isStale(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 409 || error.status === 404);
}

function UncertainReview({ timeZone, onChanged }: { timeZone: string; onChanged: () => void }) {
  const uncertain = useAsync(
    (signal) => api.getGalleryImports({ status: "uncertain", limit: REVIEW_LIMIT }, { signal }),
    [],
  );
  // 성공한 항목은 다시 불러오지 않고 목록에서 뺀다. 목록을 새로 받으면 서버 값을 그대로 쓴다.
  const [removed, setRemoved] = useState<{ source: unknown; ids: ReadonlySet<number> }>({
    source: undefined,
    ids: new Set(),
  });
  const removedIds = removed.source === uncertain.data ? removed.ids : new Set<number>();
  const [busyIds, setBusyIds] = useState<ReadonlySet<number>>(new Set());
  const busy = useRef(new Set<number>());
  const [notice, setNotice] = useState<Notice | null>(null);
  const data = uncertain.data;
  const reloadUncertain = uncertain.reload;

  const removeItem = useCallback(
    (id: number) => {
      setRemoved((prev) => ({
        source: data,
        ids: new Set([...(prev.source === data ? prev.ids : []), id]),
      }));
    },
    [data],
  );

  const runFor = useCallback(
    async (item: GalleryImport, action: () => Promise<Notice>, failText: string) => {
      if (busy.current.has(item.id)) return;
      busy.current.add(item.id);
      setBusyIds(new Set(busy.current));
      setNotice(null);
      try {
        const done = await action();
        removeItem(item.id);
        setNotice(done);
        onChanged();
      } catch (error) {
        if (isStale(error)) {
          // 409(이미 처리됨·확정할 수 없음)·404면 항목 상태를 다시 불러와 보여 준다.
          setNotice({ tone: "warn", text: STRINGS.gallery.stateChanged, detail: failureReason(error) });
          void reloadUncertain();
          onChanged();
        } else {
          setNotice({ tone: "error", text: failText, detail: failureReason(error) });
        }
      } finally {
        busy.current.delete(item.id);
        setBusyIds(new Set(busy.current));
      }
    },
    [onChanged, reloadUncertain, removeItem],
  );

  const handleConfirm = useCallback(
    (item: GalleryImport) =>
      runFor(
        item,
        async () => {
          const result = await api.confirmImport(item.id);
          let untagged = false;
          if (result.outfit_id !== null) {
            try {
              untagged = isUntaggedOutfit((await api.getOutfit(result.outfit_id)).tags);
            } catch {
              // 안내만 못 할 뿐 확정은 이미 끝났다.
            }
          }
          return {
            tone: "ok",
            text: STRINGS.gallery.confirmed,
            detail: untagged ? STRINGS.gallery.confirmedNoTags : undefined,
          };
        },
        STRINGS.gallery.confirmFailed,
      ),
    [runFor],
  );

  const handleReanalyze = useCallback(
    (item: GalleryImport) =>
      runFor(
        item,
        async () => {
          await api.reanalyzeImport(item.id);
          return { tone: "ok", text: STRINGS.gallery.reanalyzeQueued };
        },
        STRINGS.gallery.reanalyzeFailed,
      ),
    [runFor],
  );

  const visible = data?.filter((item) => !removedIds.has(item.id));

  return (
    <Section id="uncertain-review-title" title={STRINGS.gallery.reviewTitle} testId="uncertain-review">
      <p className="automation-help">{STRINGS.gallery.reviewHelp}</p>
      {notice ? (
        <div
          className="automation-note"
          data-tone={notice.tone}
          role={notice.tone === "error" ? "alert" : "status"}
          data-testid="review-notice"
        >
          <p>{notice.text}</p>
          {notice.detail ? <p>{notice.detail}</p> : null}
        </div>
      ) : null}
      {uncertain.error !== undefined ? (
        <ErrorRetry
          message={STRINGS.gallery.reviewLoadFailed}
          pending={uncertain.pending}
          onRetry={() => void reloadUncertain()}
        />
      ) : null}
      {visible === undefined ? (
        uncertain.error === undefined ? <p className="automation-help">{STRINGS.notifications.loading}</p> : null
      ) : visible.length === 0 ? (
        <p className="automation-help">{STRINGS.gallery.reviewEmpty}</p>
      ) : (
        <GalleryImportList
          items={visible}
          timeZone={timeZone}
          label={STRINGS.gallery.reviewLabel}
          renderActions={(item) => {
            const working = busyIds.has(item.id);
            return (
              <div className="import-item-actions">
                <button
                  type="button"
                  className="automation-button"
                  disabled={working}
                  aria-busy={working}
                  onClick={() => void handleConfirm(item)}
                >
                  {working ? STRINGS.gallery.working : STRINGS.gallery.confirm}
                </button>
                <button
                  type="button"
                  className="automation-button"
                  data-variant="secondary"
                  disabled={working}
                  onClick={() => void handleReanalyze(item)}
                >
                  {STRINGS.gallery.reanalyze}
                </button>
              </div>
            );
          }}
        />
      )}
    </Section>
  );
}
