import { Link } from "react-router";
import { api, apiUrl, type NotificationItem } from "../api/client";
import { formatDate, formatTime } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";
import { FixtureBadge } from "./FixtureBadge";

/**
 * 오늘(app_timezone) 만든 daily_brief 알림을 고른다 (Req 14.6).
 * GET /api/notifications?kind=daily_brief&limit=1 응답의 맨 앞 1건이 오늘 것일 때만 돌려준다.
 * 응답이 배열이 아니거나 비었거나 어제 이전 것이면 null이다.
 */
export function todayBrief(items: unknown, timeZone: string, now: Date): NotificationItem | null {
  if (!Array.isArray(items) || items.length === 0) return null;
  const latest = items[0] as NotificationItem;
  if (latest?.kind !== "daily_brief") return null;
  const created = formatDate(latest.created_at, timeZone);
  return created !== STRINGS.emptyValue && created === formatDate(now.toISOString(), timeZone) ? latest : null;
}

/** 알림 링크를 앱 안 경로로만 쓴다. 다른 출처나 이상한 값이면 홈으로 보낸다. */
export function briefHref(url: string): string {
  return url.startsWith("/") && !url.startsWith("//") ? url : "/";
}

interface DailyBriefCardProps {
  /** GET /api/meta의 app_timezone. "오늘" 판정과 시각 표시에 쓴다. */
  timeZone: string;
  /** 오늘 판정 기준 시각. 테스트에서 고정할 때 쓴다. */
  now?: Date;
}

/**
 * 홈 화면 맨 앞의 오늘 브리핑 배너 (Req 14.6, 14.7).
 * 오늘 브리핑이 없거나 불러오지 못하면 아무것도 그리지 않는다(홈의 다른 내용은 그대로다).
 * 누르면 알림 링크(보통 추천 착장 상세)로 이동한다.
 */
export function DailyBriefCard({ timeZone, now }: DailyBriefCardProps) {
  const notifications = useAsync(
    (signal) => api.getNotifications({ kind: "daily_brief", limit: 1 }, { signal }),
    [],
  );
  const brief = todayBrief(notifications.data, timeZone, now ?? new Date());
  if (!brief) return null;

  return (
    <Link
      to={briefHref(brief.url)}
      className="brief-card"
      data-testid="daily-brief-card"
      aria-label={`${STRINGS.brief.cardLabel}: ${brief.title}`}
    >
      {brief.photo_url ? (
        <span className="brief-card-photo">
          <img src={apiUrl(brief.photo_url)} alt={STRINGS.brief.photoAlt} loading="lazy" />
        </span>
      ) : null}
      <span className="brief-card-body">
        <span className="brief-card-header">
          <span>{STRINGS.brief.cardHeader(formatTime(brief.created_at, timeZone))}</span>
          {brief.weather_is_fixture === true ? <FixtureBadge /> : null}
        </span>
        <span className="brief-card-title">{brief.title}</span>
        <span className="brief-card-text">{brief.body}</span>
      </span>
    </Link>
  );
}
