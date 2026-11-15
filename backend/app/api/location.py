"""Location routes (design.md §11, §19, Req 9; lead, tasks 1.8).

- ``POST /api/location`` stores the rounded Last_Location and answers with it
  and its Place_Label. A body that fails ``LocationIn`` validation becomes 422
  ``INVALID_LOCATION`` in ``main.py``; ``LocationService`` checks again.
- ``GET /api/location`` returns the last report (or null), the default
  location, and which location the daily brief will use.

The Place_Label function is injected (``deps.get_place_label``). Until tasks
7.1 connects flow B's ``app.notifications.places.place_label`` it is
``deps.fallback_place_label`` ("현재 위치").
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends

from app.config import AppConfig
from app.deps import ApiError, get_config, get_location_service, get_place_label
from app.location import InvalidLocation, LocationService, StoredLocation
from app.schemas import ErrorResponse
from app.schemas_automation import LocationIn, LocationOut, LocationStateOut

router = APIRouter(prefix="/api", tags=["location"])

INVALID_LOCATION_MESSAGE = "위치 값이 올바르지 않습니다."


def location_out(stored: StoredLocation, place_label: Callable[[float, float], str]) -> LocationOut:
    return LocationOut(
        latitude=stored.latitude,
        longitude=stored.longitude,
        accuracy_m=stored.accuracy_m,
        received_at=stored.received_at,
        place_label=place_label(stored.latitude, stored.longitude),
    )


@router.post(
    "/location",
    response_model=LocationOut,
    responses={422: {"model": ErrorResponse}},
)
def report_location(
    body: LocationIn,
    service: LocationService = Depends(get_location_service),
    place_label: Callable[[float, float], str] = Depends(get_place_label),
) -> LocationOut:
    try:
        stored = service.report(body.latitude, body.longitude, body.accuracy_m)
    except InvalidLocation:
        raise ApiError(422, "INVALID_LOCATION", INVALID_LOCATION_MESSAGE) from None
    return location_out(stored, place_label)


@router.get("/location", response_model=LocationStateOut)
def get_location(
    config: AppConfig = Depends(get_config),
    service: LocationService = Depends(get_location_service),
    place_label: Callable[[float, float], str] = Depends(get_place_label),
) -> LocationStateOut:
    last = service.last()
    brief, source = service.brief_location()
    return LocationStateOut(
        last=None if last is None else location_out(last, place_label),
        default_latitude=config.default_latitude,
        default_longitude=config.default_longitude,
        brief_source=source,
        brief_place_label=place_label(brief.latitude, brief.longitude),
    )
