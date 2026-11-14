"""Outfit router (design.md §11, task 10.2).

- ``GET /api/outfits``: every Outfit, ``captured_at`` instant DESC then id DESC
  (Req 10.1); an empty database gives 200 ``[]`` (Req 10.2).
- ``GET /api/outfits/{id}``: one Outfit or 404 ``OUTFIT_NOT_FOUND`` (Req 10.3, 10.5).
- ``GET /api/outfits/{id}/photo``: the image file. The path is built from the
  stored file name and must resolve inside PHOTO_DIR; otherwise 404
  ``PHOTO_NOT_FOUND``. No server path is ever put in a response, and no
  ``StaticFiles`` mount exists (Req 10.8, 10.9).
- ``POST /api/outfits/{id}/feedback``: 201 ``FeedbackOut`` (Req 9.1). FastAPI
  validates the body first (422 ``INVALID_THERMAL_VOTE``, handled in
  ``main.py``, Req 9.2), then ``FeedbackService`` checks the Outfit exists
  (404, Req 9.3) and stores only the vote (Req 9.7). A failed insert is 500
  ``FEEDBACK_SAVE_FAILED`` (Req 9.8).

``OutfitOut`` is built with ``app.recommendation.service.outfit_out`` so the
shape is the same as in recommendation items. ``response_model_exclude_none``
is never used: absent ``weather`` / ``effective_vote`` serialize as ``null``
(Req 10.4).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.config import AppConfig
from app.deps import ApiError, get_config, get_feedback_service, get_repository
from app.feedback import FeedbackService, OutfitNotFound
from app.ingestion.image_check import mime_type_for
from app.recommendation.service import outfit_out
from app.repository import OutfitRepository
from app.schemas import ErrorResponse, FeedbackIn, FeedbackOut, OutfitOut

router = APIRouter(prefix="/api/outfits", tags=["outfits"])

_NOT_FOUND = {404: {"model": ErrorResponse}}

PHOTO_NOT_FOUND_MESSAGE = "사진을 찾을 수 없습니다."


def photo_not_found() -> ApiError:
    return ApiError(404, "PHOTO_NOT_FOUND", PHOTO_NOT_FOUND_MESSAGE)


def resolve_photo_path(photo_dir: Path, file_name: str) -> Path | None:
    """Absolute path of a stored photo, or None when it is unsafe or missing.

    The resolved path (symlinks followed) must be a regular file directly
    under the resolved PHOTO_DIR with a supported image extension.
    """
    try:
        root = photo_dir.resolve()
        candidate = (root / file_name).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if candidate.parent != root:
        return None
    try:
        mime_type_for(candidate.suffix)
    except KeyError:
        return None
    if not candidate.is_file():
        return None
    return candidate


@router.get("", response_model=list[OutfitOut])
def list_outfits(repo: OutfitRepository = Depends(get_repository)) -> list[OutfitOut]:
    return [outfit_out(record) for record in repo.list_outfits()]


@router.get("/{outfit_id}", response_model=OutfitOut, responses=_NOT_FOUND)
def get_outfit(
    outfit_id: int, repo: OutfitRepository = Depends(get_repository)
) -> OutfitOut:
    record = repo.get_outfit(outfit_id)
    if record is None:
        raise OutfitNotFound(outfit_id)
    return outfit_out(record)


@router.get(
    "/{outfit_id}/photo",
    response_class=FileResponse,
    responses={200: {"content": {"image/*": {}}}, **_NOT_FOUND},
)
def get_outfit_photo(
    outfit_id: int,
    repo: OutfitRepository = Depends(get_repository),
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    record = repo.get_outfit(outfit_id)
    if record is None:
        raise photo_not_found()
    path = resolve_photo_path(config.photo_dir, record.file_name)
    if path is None:
        raise photo_not_found()
    return FileResponse(path, media_type=mime_type_for(path.suffix))


@router.post(
    "/{outfit_id}/feedback",
    response_model=FeedbackOut,
    status_code=201,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def post_feedback(
    outfit_id: int,
    body: FeedbackIn,
    service: FeedbackService = Depends(get_feedback_service),
) -> FeedbackOut:
    record = service.submit(outfit_id, body.thermal_vote)
    return FeedbackOut(
        outfit_id=record.outfit_id,
        thermal_vote=record.thermal_vote,
        created_at=record.created_at,
    )
