import type { ReactNode } from "react";
import { Link } from "react-router";
import type { GalleryImport, GalleryImportStatus } from "../api/client";
import { formatDateTime } from "../lib/format";
import { STRINGS } from "../lib/strings";

/** 처리 상태 칩 문구 (Req 6.9, 17.3): 가져옴·중복·비착장 제외·확인 필요·재시도 대기·실패 등. */
export function galleryStatusLabel(status: GalleryImportStatus): string {
  return STRINGS.gallery.status[status];
}

interface GalleryImportListProps {
  items: readonly GalleryImport[];
  /** 시각 표시에 쓰는 app_timezone. */
  timeZone: string;
  /** 목록의 접근성 이름. */
  label: string;
  /** 항목마다 붙일 버튼 등(예: "착장 맞음"·"다시 분석"). */
  renderActions?: (item: GalleryImport) => ReactNode;
}

/**
 * 감시로 들어온 사진의 처리 기록 목록 (Req 5.5, 6.9, 17.3, 17.4).
 * 파일 이름은 Gallery_Dir 기준 이름만 온다. `capture_origin=estimated`에는 "촬영 정보 추정" 칩을 붙인다.
 */
export function GalleryImportList({ items, timeZone, label, renderActions }: GalleryImportListProps) {
  return (
    <ul className="import-list" aria-label={label}>
      {items.map((item) => (
        <li key={item.id} className="import-item" data-testid="gallery-import" data-status={item.status}>
          <div className="import-item-head">
            <p className="import-item-name">{item.file_name}</p>
            <span className="status-chip" data-status={item.status}>
              {galleryStatusLabel(item.status)}
            </span>
            {item.capture_origin === "estimated" ? (
              <span className="status-chip" data-status="estimated">
                {STRINGS.gallery.estimated}
              </span>
            ) : null}
          </div>
          <p className="import-item-meta">
            <span>{formatDateTime(item.updated_at, timeZone)}</span>
            {item.photo_type_confidence !== null ? (
              <span>{STRINGS.gallery.confidence(Math.round(item.photo_type_confidence * 100))}</span>
            ) : null}
            {item.attempts > 1 ? <span>{STRINGS.gallery.attempts(item.attempts)}</span> : null}
            {item.last_error_code ? (
              <span>{STRINGS.gallery.lastError(item.last_error_stage ?? STRINGS.emptyValue, item.last_error_code)}</span>
            ) : null}
            {item.status === "retry_wait" && item.next_attempt_at ? (
              <span>{STRINGS.gallery.nextAttempt(formatDateTime(item.next_attempt_at, timeZone))}</span>
            ) : null}
          </p>
          {item.reason ? <p className="import-item-reason">{item.reason}</p> : null}
          {item.outfit_id !== null ? (
            <Link to={`/outfits/${item.outfit_id}`} className="import-item-link">
              {STRINGS.gallery.openOutfit}
            </Link>
          ) : null}
          {renderActions ? renderActions(item) : null}
        </li>
      ))}
    </ul>
  );
}
