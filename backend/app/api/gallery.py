"""Gallery import routes (design.md §19, flow A tasks 3.16, 3.20).

- ``GET /api/gallery/imports?limit&status``: Gallery_File records except
  baselines, newest ``updated_at`` first, with the Photo_Gate decision and the
  capture-info origin (Req 17.3, 5.5, 6.9).
- ``POST /api/gallery/imports/{import_id}/reanalyze``: 202 for an
  ``uncertain``/``failed`` record (back to ``pending``, attempts 0); 404
  ``GALLERY_IMPORT_NOT_FOUND``; 409 ``GALLERY_IMPORT_NOT_REANALYZABLE`` (Req 6.8).
- ``POST /api/gallery/imports/{import_id}/confirm-outfit``: "착장 맞음" for an
  ``uncertain`` record, without Vision (Req 6.12–6.15). 200 with
  ``status=imported`` and ``outfit_id``; 404; 409 ``GALLERY_IMPORT_NOT_CONFIRMABLE``
  (state, or original missing/changed); 500 ``STORAGE_ERROR``.

Responses never carry a server path; ``file_name`` is relative to Gallery_Dir
(Req 17.2).

The store is read through ``_gallery_store_or_none`` and checked with
``require`` inside the route body: FastAPI resolves dependencies before it
validates query parameters, so a raising dependency would turn
``?limit=0`` (422) into 501 while the store is not wired yet (tasks 7.1).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status

from app.deps import AppContainer, ApiError, get_container, get_outfit_confirm_service, require
from app.gallery.confirm import ConfirmOutcome, OutfitConfirmService
from app.gallery.store import GalleryFileRow, GalleryStore
from app.schemas import ErrorResponse
from app.schemas_automation import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    GalleryImportOut,
    GalleryImportStatus,
)

router = APIRouter(prefix="/api/gallery", tags=["gallery"])

NOT_FOUND_MESSAGE = "사진 처리 기록을 찾을 수 없습니다."
NOT_REANALYZABLE_MESSAGE = "이 사진은 다시 분석할 수 없는 상태입니다."
NOT_CONFIRMABLE_MESSAGE = "이 사진은 착장으로 확정할 수 없는 상태입니다."
SOURCE_MISSING_MESSAGE = "이 사진은 착장으로 확정할 수 없는 상태입니다. 원본 사진을 찾을 수 없습니다."
STORAGE_ERROR_MESSAGE = "착장 기록을 저장하지 못했습니다."


def _gallery_store_or_none(container: AppContainer = Depends(get_container)) -> Any:
    return container.gallery_store


def import_out(row: GalleryFileRow) -> GalleryImportOut:
    """API shape of one record: file name relative to Gallery_Dir, no paths (Req 17.2)."""
    return GalleryImportOut(
        id=row.id,
        file_name=row.file_name,
        status=row.status,  # never "baseline" here
        attempts=row.attempts,
        last_error_stage=row.last_error_stage,
        last_error_code=row.last_error_code,
        next_attempt_at=row.next_attempt_at,
        outfit_id=row.outfit_id,
        photo_type=row.photo_type,  # type: ignore[arg-type]
        photo_type_confidence=row.photo_type_confidence,
        reason=row.reason,
        capture_origin=row.capture_origin,
        first_seen_at=row.first_seen_at,
        updated_at=row.updated_at,
    )


def _not_found() -> ApiError:
    return ApiError(404, "GALLERY_IMPORT_NOT_FOUND", NOT_FOUND_MESSAGE)


@router.get("/imports", response_model=list[GalleryImportOut])
def list_gallery_imports(
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    status: GalleryImportStatus | None = Query(None),
    store: GalleryStore | None = Depends(_gallery_store_or_none),
) -> list[GalleryImportOut]:
    """Gallery_File records except baselines, newest ``updated_at`` first (Req 17.3, 5.5, 6.9)."""
    gallery = require(store, "3.3/7.1")
    return [import_out(row) for row in gallery.list_imports(limit, status)]


@router.post(
    "/imports/{import_id}/reanalyze",
    response_model=GalleryImportOut,
    status_code=http_status.HTTP_202_ACCEPTED,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def reanalyze_gallery_import(
    import_id: int,
    store: GalleryStore | None = Depends(_gallery_store_or_none),
) -> GalleryImportOut:
    """Queue an ``uncertain``/``failed`` record for one more analysis (Req 6.8).

    404 ``GALLERY_IMPORT_NOT_FOUND``, 409 ``GALLERY_IMPORT_NOT_REANALYZABLE``.
    """
    gallery = require(store, "3.3/7.1")
    result = gallery.reset_for_reanalyze(import_id)
    if result == "not_found":
        raise _not_found()
    if result == "not_allowed":
        raise ApiError(409, "GALLERY_IMPORT_NOT_REANALYZABLE", NOT_REANALYZABLE_MESSAGE)
    row = gallery.get(import_id)
    if row is None:  # deleted in between
        raise _not_found()
    return import_out(row)


@router.post(
    "/imports/{import_id}/confirm-outfit",
    response_model=GalleryImportOut,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def confirm_gallery_import(
    import_id: int,
    service: OutfitConfirmService = Depends(get_outfit_confirm_service),
) -> GalleryImportOut:
    """"착장 맞음": store an ``uncertain`` photo as an Outfit without Vision (Req 6.12-6.15).

    200 with ``status=imported`` and ``outfit_id``; 404 ``GALLERY_IMPORT_NOT_FOUND``,
    409 ``GALLERY_IMPORT_NOT_CONFIRMABLE``, 500 ``STORAGE_ERROR``.
    """
    outcome, row = service.confirm(import_id)
    if outcome is ConfirmOutcome.NOT_FOUND:
        raise _not_found()
    if outcome is ConfirmOutcome.NOT_CONFIRMABLE:
        raise ApiError(409, "GALLERY_IMPORT_NOT_CONFIRMABLE", NOT_CONFIRMABLE_MESSAGE)
    if outcome is ConfirmOutcome.SOURCE_MISSING:
        raise ApiError(409, "GALLERY_IMPORT_NOT_CONFIRMABLE", SOURCE_MISSING_MESSAGE)
    if outcome is ConfirmOutcome.FAILED or row is None:
        raise ApiError(500, "STORAGE_ERROR", STORAGE_ERROR_MESSAGE)
    return import_out(row)
