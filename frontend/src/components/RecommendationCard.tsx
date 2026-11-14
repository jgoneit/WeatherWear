import { useId, useState } from "react";
import { Link } from "react-router";
import { apiUrl, type RecommendationItem } from "../api/client";
import { displayTags, FALLBACK_TIMEZONE, formatDate, formatTemp, provenance } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { FixtureBadge } from "./FixtureBadge";
import { VirtualDataLabel } from "./VirtualDataLabel";

interface RecommendationCardProps {
  item: RecommendationItem;
  /** 촬영일 표시 타임존 (GET /api/meta의 app_timezone). */
  timeZone?: string;
  /** 있으면 사진을 상세 화면 링크로 감싼다. 예: `/outfits/12`. */
  detailHref?: string;
}

/**
 * 추천 착장 카드: 4:5 사진 전면 + 하단 글래스 오버레이
 * (과거 기온, 기온 차이, 태그 칩, 신뢰도 배지, 근거 2줄 + 펼치기) (Req 11.1, 8.2, 15.4).
 */
export function RecommendationCard({ item, timeZone = FALLBACK_TIMEZONE, detailHref }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const { outfit } = item;
  const tags = displayTags(outfit.tags);
  const labels = provenance(outfit);
  const tagText = tags.length > 0 ? tags.join(", ") : STRINGS.recommendation.noTags;

  const photo = (
    <img
      src={apiUrl(outfit.photo_url)}
      alt={STRINGS.recommendation.photoAlt(tagText)}
      loading="lazy"
      decoding="async"
      className="absolute inset-0 size-full object-cover"
    />
  );

  return (
    <article
      data-testid="recommendation-card"
      className="relative aspect-[4/5] w-full min-w-0 overflow-hidden rounded-3xl bg-slate-200 shadow-glass"
    >
      {detailHref ? (
        <Link to={detailHref} className="absolute inset-0 block">
          {photo}
        </Link>
      ) : (
        photo
      )}

      <span className="glass absolute top-3 left-3 rounded-full px-3 py-1 text-xs font-semibold text-slate-900">
        {STRINGS.recommendation.rank(item.rank)}
      </span>

      <div className="glass absolute inset-x-2 bottom-2 flex max-h-[85%] min-w-0 flex-col gap-2 overflow-y-auto rounded-3xl p-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-700 tabular-nums">
          <span>
            {STRINGS.recommendation.pastTemperature}{" "}
            <strong className="text-base font-semibold text-slate-900">
              {formatTemp(outfit.weather?.actual_temperature_c)}
            </strong>
          </span>
          <span>
            {STRINGS.recommendation.temperatureDiff}{" "}
            <strong className="font-semibold text-slate-900">{formatTemp(item.temperature_diff_c)}</strong>
          </span>
          {labels.weather ? <FixtureBadge /> : null}
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          {tags.length > 0 ? (
            tags.map((tag) => (
              <span
                key={tag}
                className="max-w-full truncate rounded-full bg-white/80 px-2.5 py-1 text-xs font-medium text-slate-900"
              >
                {tag}
              </span>
            ))
          ) : (
            <span className="text-xs text-slate-600">{STRINGS.recommendation.noTags}</span>
          )}
          {labels.tags ? <VirtualDataLabel /> : null}
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-slate-600">
          <ConfidenceBadge level={item.confidence_level} />
          {item.selected_by_fallback ? (
            <span className="rounded-full bg-warm/15 px-2 py-0.5 font-medium text-slate-900">
              {STRINGS.recommendation.fallbackChip}
            </span>
          ) : null}
          <span className="tabular-nums">
            {STRINGS.recommendation.capturedOn} {formatDate(outfit.captured_at, timeZone)}
          </span>
          {labels.meta ? <VirtualDataLabel /> : null}
        </div>

        <div id={detailsId} className="min-w-0 text-sm text-slate-800">
          <p className={expanded ? "" : "line-clamp-2"}>{item.explanation}</p>
          {expanded && item.reasons.length > 0 ? (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-700">
              {item.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
        </div>

        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={detailsId}
          onClick={() => setExpanded((open) => !open)}
          className="min-h-11 self-start rounded-full px-3 text-sm font-semibold text-slate-900 underline-offset-4 hover:underline"
        >
          {expanded ? STRINGS.recommendation.collapse : STRINGS.recommendation.expand}
        </button>
      </div>
    </article>
  );
}
