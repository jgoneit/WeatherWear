"""Gallery import routes (design.md §19, flow A).

Skeleton from tasks 1.7: paths, parameters, ``response_model`` and error
responses are the shared contract; bodies answer 501 ``NOT_IMPLEMENTED`` until
their task fills them.

- ``GET /api/gallery/imports`` and ``POST …/reanalyze``: filled by tasks 3.16.
- ``POST …/confirm-outfit``: filled by tasks 3.20 (``OutfitConfirmService``
  via ``deps.get_outfit_confirm_service``; container wiring in 7.1).

Responses never carry a server path; ``file_name`` is relative to Gallery_Dir
(Req 17.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi import status as http_status

from app.deps import not_implemented
from app.schemas import ErrorResponse
from app.schemas_automation import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    GalleryImportOut,
    GalleryImportStatus,
)

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


@router.get("/imports", response_model=list[GalleryImportOut])
def list_gallery_imports(
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    status: GalleryImportStatus | None = Query(None),
) -> list[GalleryImportOut]:
    """Gallery_File records except baselines, newest ``updated_at`` first (Req 17.3, 5.5, 6.9)."""
    # TODO(3.16): GalleryStore.list_imports(limit, status) -> GalleryImportOut.
    not_implemented("3.16")


@router.post(
    "/imports/{import_id}/reanalyze",
    response_model=GalleryImportOut,
    status_code=http_status.HTTP_202_ACCEPTED,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def reanalyze_gallery_import(import_id: int) -> GalleryImportOut:
    """Queue an ``uncertain``/``failed`` record for one more analysis (Req 6.8).

    404 ``GALLERY_IMPORT_NOT_FOUND``, 409 ``GALLERY_IMPORT_NOT_REANALYZABLE``.
    """
    # TODO(3.16): GalleryStore.requeue(import_id).
    not_implemented("3.16")


@router.post(
    "/imports/{import_id}/confirm-outfit",
    response_model=GalleryImportOut,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def confirm_gallery_import(import_id: int) -> GalleryImportOut:
    """"착장 맞음": store an ``uncertain`` photo as an Outfit without Vision (Req 6.12-6.15).

    200 with ``status=imported`` and ``outfit_id``; 404 ``GALLERY_IMPORT_NOT_FOUND``,
    409 ``GALLERY_IMPORT_NOT_CONFIRMABLE``, 500 ``STORAGE_ERROR``.
    """
    # TODO(3.20): OutfitConfirmService.confirm(import_id, now) via get_outfit_confirm_service.
    not_implemented("3.20")
