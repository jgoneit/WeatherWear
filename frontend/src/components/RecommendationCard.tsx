import { useId, useState } from "react";
import { Link } from "react-router";
import { apiUrl, type RecommendationItem } from "../api/client";
import { displayTags, FALLBACK_TIMEZONE, formatDate, formatTemp, provenance } from "../lib/format";
import { clothingLabel, localizeExplanation } from "../lib/clothing";
import { STRINGS } from "../lib/strings";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { FixtureBadge } from "./FixtureBadge";
import { VirtualDataLabel } from "./VirtualDataLabel";
interface RecommendationCardProps { item: RecommendationItem; timeZone?: string; detailHref?: string; featured?: boolean; }
export function RecommendationCard({ item, timeZone = FALLBACK_TIMEZONE, detailHref, featured = false }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const { outfit } = item;
  const tags = displayTags(outfit.tags).map(clothingLabel);
  const labels = provenance(outfit);
  const title = tags.length ? tags.join(" · ") : STRINGS.recommendation.noTags;
  const photo = <img src={apiUrl(outfit.photo_url)} alt={STRINGS.recommendation.photoAlt(title)} loading="lazy" decoding="async"/>;
  return <article data-testid="recommendation-card" className={`recommendation ${featured ? "featured" : ""}`}>
    <div className="recommendation-photo">
      {detailHref ? <Link to={detailHref}>{photo}</Link> : photo}
      {featured ? <span className="rank-label">오늘의 추천</span> : null}
    </div>
    <div className="recommendation-body">
      {featured ? <span className="recommendation-kicker">오늘은 이렇게</span> : null}
      <h3 className="feature-title">{title}</h3>
      <p className="recommendation-summary">{formatTemp(outfit.weather?.actual_temperature_c)}에 입었던 옷{outfit.effective_vote === "comfortable" ? " · 편안했어요" : ""}</p>
      {featured ? <p className="recommendation-caption">{outfit.effective_vote === "comfortable" ? "비슷한 날씨에 편안했던 옷이에요. 오늘도 꺼내 입어 보세요." : "오늘과 비슷한 기온에 입었던 옷이에요. 옷장을 열기 전에 참고해 보세요."}</p> : null}
      <button type="button" aria-expanded={expanded} aria-controls={detailsId} onClick={() => setExpanded(v => !v)} className="recommendation-toggle">{expanded ? STRINGS.recommendation.collapse : STRINGS.recommendation.expand}</button>
      <div id={detailsId} hidden={!expanded} className="recommendation-details">
        <div className="recommendation-facts"><span>{STRINGS.recommendation.pastTemperature} <strong>{formatTemp(outfit.weather?.actual_temperature_c)}</strong></span><span>{STRINGS.recommendation.temperatureDiff} <strong>{formatTemp(item.temperature_diff_c)}</strong></span></div>
        <div className="recommendation-tags">{tags.map(tag => <span key={tag}>{tag}</span>)}</div>
        <div className="recommendation-meta"><ConfidenceBadge level={item.confidence_level}/>{labels.weather ? <FixtureBadge/> : null}{labels.tags ? <VirtualDataLabel/> : null}</div>
        <div className="recommendation-meta">{STRINGS.recommendation.capturedOn} {formatDate(outfit.captured_at, timeZone)} {labels.meta ? <VirtualDataLabel/> : null}</div>
        {item.selected_by_fallback ? <p>{STRINGS.recommendation.fallbackChip}</p> : null}
        <p>{localizeExplanation(item.explanation)}</p>
        {expanded && item.reasons.length ? <ul>{item.reasons.map(reason => <li key={reason}>{localizeExplanation(reason)}</li>)}</ul> : null}
      </div>
    </div>
  </article>;
}
