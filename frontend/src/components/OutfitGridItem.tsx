import { Link } from "react-router";
import { apiUrl, type Outfit } from "../api/client";
import { displayTags, FALLBACK_TIMEZONE, formatDate, formatTemp, voteLabel } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { clothingLabel } from "../lib/clothing";

interface OutfitGridItemProps {
  outfit: Outfit;
  /** 촬영일 표시 타임존 (GET /api/meta의 app_timezone). */
  timeZone?: string;
}

/**
 * 착장 기록 그리드 항목 (Req 12.2, 12.3, 12.4, 15.4).
 * 4:5 사진, 우상단 기온 칩(날씨 없으면 "날씨 정보 없음"), 하단 평가 이모지 칩,
 * 아래쪽에 촬영일과 태그 최대 3개를 보여준다. 탭하면 상세 화면으로 이동한다.
 */
export function OutfitGridItem({ outfit, timeZone = FALLBACK_TIMEZONE }: OutfitGridItemProps) {
  const tags = displayTags(outfit.tags).map(clothingLabel);
  const temperature = outfit.weather ? formatTemp(outfit.weather.actual_temperature_c) : STRINGS.history.noWeather;
  const vote = outfit.effective_vote;
  const emoji = vote ? STRINGS.history.voteEmoji[vote] : null;
  const tagText = tags.length > 0 ? tags.join(", ") : STRINGS.recommendation.noTags;

  return (
    <Link
      to={`/outfits/${outfit.id}`}
      data-testid="outfit-grid-item"
      data-outfit-id={outfit.id}
      className="history-item group flex min-w-0 flex-col gap-1.5 rounded-2xl outline-offset-2 focus-visible:outline-2 focus-visible:outline-slate-900"
    >
      <div className="relative aspect-[4/5] w-full min-w-0 overflow-hidden rounded-2xl bg-slate-200 shadow-glass">
        <img
          src={apiUrl(outfit.photo_url)}
          alt={STRINGS.history.photoAlt(tagText)}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 size-full object-cover transition-transform duration-300 group-active:scale-[0.98] motion-reduce:transition-none"
        />

        <div className="absolute top-2 right-2 flex max-w-[calc(100%-1rem)] flex-wrap items-center justify-end gap-1">
          <span
            data-testid="outfit-temperature"
            className="glass rounded-full px-2 py-0.5 text-xs font-semibold text-slate-900 tabular-nums"
          >
            {temperature}
          </span>

        </div>

        <span
          data-testid="outfit-vote"
          aria-label={`${STRINGS.history.voteLabel}: ${voteLabel(vote)}`}
          className="glass absolute bottom-2 left-2 max-w-[calc(100%-1rem)] truncate rounded-full px-2 py-0.5 text-xs font-medium text-slate-900"
        >
          {emoji ? <span aria-hidden="true">{emoji} </span> : null}
          {voteLabel(vote)}
        </span>
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-1 px-0.5 text-xs text-slate-700">
        <span className="tabular-nums">{formatDate(outfit.captured_at, timeZone)}</span>

      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-1 px-0.5">
        {tags.length > 0 ? (
          tags.map((tag) => (
            <span
              key={tag}
              className="max-w-full truncate rounded-full bg-white/75 px-2 py-0.5 text-[11px] font-medium text-slate-900"
            >
              {tag}
            </span>
          ))
        ) : (
          <span className="text-[11px] text-slate-600">{STRINGS.recommendation.noTags}</span>
        )}

      </div>
    </Link>
  );
}
