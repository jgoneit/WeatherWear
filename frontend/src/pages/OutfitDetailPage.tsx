import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError, apiUrl, type Outfit, type ThermalVote, type WeatherSnapshot } from "../api/client";
import { ErrorRetry } from "../components/ErrorRetry";
import { EstimateNotice } from "../components/EstimateNotice";
import { FixtureBadge } from "../components/FixtureBadge";
import { VirtualDataLabel } from "../components/VirtualDataLabel";
import {
  displayTags,
  EMPTY_VALUE,
  FALLBACK_TIMEZONE,
  formatDate,
  formatHumidity,
  formatTemp,
  metadataSourceLabel,
  provenance,
  voteLabel,
} from "../lib/format";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";

const S = STRINGS.detail;

const VOTE_EMOJI: Record<ThermalVote, string> = {
  cold: "🥶",
  comfortable: "😊",
  hot: "🥵",
};

const VOTE_CHIP_CLASS: Record<ThermalVote, string> = {
  cold: "bg-cold text-white",
  comfortable: "bg-mild text-white",
  hot: "bg-hot text-white",
};

/** URL의 `:id`를 양의 정수로 바꾼다. 숫자가 아니면 null. */
function parseOutfitId(raw: string | undefined): number | null {
  if (raw === undefined || !/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

/** 촬영 시각의 HH:mm (지정 타임존). 형식 오류나 잘못된 타임존이면 빈 문자열. */
function formatTime(iso: string, timeZone: string): string {
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) return "";
  const format = (tz: string) =>
    new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(
      instant,
    );
  try {
    return format(timeZone);
  } catch {
    return format(FALLBACK_TIMEZONE);
  }
}

function tagValue(value: string): string {
  if (value === "none") return S.tagNone;
  if (value === "unknown") return S.tagUnknown;
  return value;
}

function isNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** 착장 상세 화면: 전체폭 사진 + bottom-sheet 글래스 패널 (Req 12.4, 12.5, 12.7, 4.9, 15.4). */
export function OutfitDetailPage() {
  const { id: rawId } = useParams();
  const outfitId = parseOutfitId(rawId);

  const outfit = useAsync<Outfit>(
    (signal) =>
      outfitId === null
        ? Promise.reject(new ApiError(404, "OUTFIT_NOT_FOUND", S.notFound))
        : api.getOutfit(outfitId, { signal }),
    [outfitId],
  );
  const meta = useAsync((signal) => api.getMeta({ signal }), []);
  const timeZone = meta.data?.app_timezone ?? FALLBACK_TIMEZONE;

  // 다른 착장으로 이동한 직후에는 이전 착장을 보여주지 않는다.
  const data = outfit.data && outfit.data.id === outfitId ? outfit.data : undefined;
  const retry = () => void outfit.reload();

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 items-center gap-3">
        <Link
          to="/history"
          className="glass inline-flex min-h-11 shrink-0 items-center rounded-full px-4 text-sm font-semibold text-slate-900"
        >
          ← {S.backToHistory}
        </Link>
        <h1 className="min-w-0 truncate text-xl font-semibold tracking-tight text-slate-900">{S.title}</h1>
      </div>

      {outfit.error !== undefined ? (
        <ErrorRetry
          onRetry={retry}
          pending={outfit.pending}
          message={isNotFound(outfit.error) ? S.notFound : STRINGS.error.loadFailed}
        />
      ) : null}

      {data ? (
        <OutfitDetail outfit={data} timeZone={timeZone} />
      ) : outfit.pending ? (
        <p role="status" className="glass rounded-3xl p-5 text-sm text-slate-700">
          {S.loading}
        </p>
      ) : null}
    </div>
  );
}

function OutfitDetail({ outfit, timeZone }: { outfit: Outfit; timeZone: string }) {
  const labels = provenance(outfit);
  const shownTags = displayTags(outfit.tags);
  const altText = shownTags.length > 0 ? shownTags.join(", ") : STRINGS.recommendation.noTags;
  const time = formatTime(outfit.captured_at, timeZone);

  return (
    <article className="min-w-0" data-testid="outfit-detail">
      <div className="-mx-4 overflow-hidden bg-slate-200">
        <img
          src={apiUrl(outfit.photo_url)}
          alt={S.photoAlt(altText)}
          decoding="async"
          className="aspect-[4/5] w-full max-w-full object-cover"
        />
      </div>

      <div className="glass relative -mt-8 flex min-w-0 flex-col gap-5 rounded-3xl p-5">
        <div aria-hidden="true" className="mx-auto -mt-2 h-1.5 w-10 rounded-full bg-slate-900/15" />

        <Section title={S.voteTitle}>
          <VoteChip vote={outfit.effective_vote} />
        </Section>

        <Section title={S.capturedAt} extra={labels.meta ? <VirtualDataLabel /> : null}>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Field label={S.capturedAt} value={`${formatDate(outfit.captured_at, timeZone)} ${time}`.trim()} />
            <Field label={S.location} value={`${outfit.latitude.toFixed(4)}, ${outfit.longitude.toFixed(4)}`} />
          </dl>
          <p className="mt-2 text-xs text-slate-600" data-testid="metadata-source">
            {S.metadataSource(metadataSourceLabel(outfit.source))}
          </p>
        </Section>

        <Section title={S.tagsTitle} extra={labels.tags ? <VirtualDataLabel /> : null}>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Field label={S.outerType} value={tagValue(outfit.tags.outer_type)} />
            <Field label={S.upperType} value={tagValue(outfit.tags.upper_type)} />
            <Field label={S.lowerType} value={tagValue(outfit.tags.lower_type)} />
            <Field label={S.layersVisible} value={String(outfit.tags.layers_visible)} />
            <Field label={S.confidence} value={`${Math.round(outfit.tags.confidence * 100)}%`} />
            <div className="col-span-2 min-w-0">
              <dt className="text-xs font-medium text-slate-600">{S.colors}</dt>
              <dd className="mt-1 flex min-w-0 flex-wrap gap-1.5">
                {outfit.tags.colors.length > 0 ? (
                  outfit.tags.colors.map((color, index) => (
                    <span
                      key={`${color}-${index}`}
                      className="max-w-full truncate rounded-full bg-white/80 px-2.5 py-1 text-xs font-medium text-slate-900"
                    >
                      {color}
                    </span>
                  ))
                ) : (
                  <span className="text-slate-600">{S.noColors}</span>
                )}
              </dd>
            </div>
          </dl>
        </Section>

        <Section title={S.weatherTitle} extra={labels.weather ? <FixtureBadge /> : null}>
          {outfit.weather ? (
            <DetailWeatherBento weather={outfit.weather} />
          ) : (
            <p className="text-sm text-slate-700">{S.noWeather}</p>
          )}
        </Section>
      </div>
    </article>
  );
}

function Section({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section className="min-w-0">
      <h2 className="mb-2 flex min-w-0 flex-wrap items-center gap-1.5 text-base font-semibold text-slate-900">
        <span>{title}</span>
        {extra}
      </h2>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-slate-600">{label}</dt>
      <dd className="mt-0.5 break-words font-medium text-slate-900 tabular-nums">{value || EMPTY_VALUE}</dd>
    </div>
  );
}

function VoteChip({ vote }: { vote: ThermalVote | null }) {
  const label = voteLabel(vote);
  if (vote === null) {
    return (
      <span className="inline-flex min-h-9 items-center rounded-full bg-white/80 px-4 text-sm font-semibold text-slate-700">
        {label}
      </span>
    );
  }
  return (
    <span
      className={`inline-flex min-h-9 items-center gap-1.5 rounded-full px-4 text-sm font-semibold ${VOTE_CHIP_CLASS[vote]}`}
    >
      <span aria-hidden="true">{VOTE_EMOJI[vote]}</span>
      {label}
    </span>
  );
}

/** 실제 기온은 항상, 체감·습도·풍속·강수는 값이 있을 때만 타일로 보인다. */
function DetailWeatherBento({ weather }: { weather: WeatherSnapshot }) {
  const tiles: { label: string; value: string }[] = [
    { label: S.actualTemperature, value: formatTemp(weather.actual_temperature_c) },
  ];
  if (isNumber(weather.apparent_temperature_c)) {
    tiles.push({ label: STRINGS.home.apparentTemperature, value: formatTemp(weather.apparent_temperature_c) });
  }
  if (isNumber(weather.relative_humidity_pct)) {
    tiles.push({ label: STRINGS.home.humidity, value: formatHumidity(weather.relative_humidity_pct) });
  }
  if (isNumber(weather.wind_speed_kmh)) {
    tiles.push({ label: S.windSpeed, value: `${weather.wind_speed_kmh.toFixed(1)} ${S.windUnit}` });
  }
  if (isNumber(weather.precipitation_mm)) {
    tiles.push({
      label: STRINGS.home.precipitation,
      value: `${weather.precipitation_mm.toFixed(1)} ${STRINGS.weather.precipitationUnit}`,
    });
  }

  return (
    <div className="min-w-0" aria-label={STRINGS.weather.bentoLabel} role="group">
      <dl className="grid grid-cols-2 gap-3">
        {tiles.map((tile, index) => (
          <div
            key={tile.label}
            className={`flex min-w-0 flex-col gap-1 rounded-3xl bg-white/60 p-4 ${index === 0 ? "col-span-2" : ""}`}
          >
            <dt className="text-xs font-medium text-slate-600">{tile.label}</dt>
            <dd
              className={`min-w-0 truncate font-semibold text-slate-900 tabular-nums ${index === 0 ? "text-4xl tracking-tight" : "text-xl"}`}
            >
              {tile.value}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-2 px-1">
        {/* fixture 배지는 섹션 제목 옆에 이미 있다. */}
        <EstimateNotice />
      </div>
    </div>
  );
}
