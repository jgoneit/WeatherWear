"""Demo admin routes (design.md §11, Req 1, 5, 14, 16.5).

- ``POST /api/demo/scan`` takes no request body. Any body (including a path)
  is ignored; the scan always uses the configured PHOTO_DIR (Req 1.9). Only one
  scan runs at a time: if the process-wide lock is taken the answer is 409
  ``SCAN_IN_PROGRESS`` (Req 1.13). ``PhotoDirUnavailable`` becomes 503
  ``PHOTO_DIR_UNAVAILABLE`` through the handler in ``main.py`` (Req 1.11).
- ``GET /api/demo/status`` exposes the Gemini key only as a boolean (Req 16.5).
- Scenario routes list, select and clear the in-memory scenario (Req 5.1, 5.3,
  5.7, 14.3). An unknown id raises ``UnknownScenario`` (404, selection kept).
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, Response, status

from app.config import AppConfig
from app.deps import (
    ApiError,
    get_config,
    get_ingestion_service,
    get_repository,
    get_scan_lock,
    get_scenario_store,
)
from app.ingestion.service import ImageIngestionService
from app.repository import OutfitRepository
from app.schemas import (
    DemoStatus,
    ErrorResponse,
    ScanReport,
    ScenarioListResponse,
    ScenarioOut,
    ScenarioSelection,
    ScenarioSelectIn,
)
from app.today_weather import ScenarioStore, WeatherScenario

router = APIRouter(prefix="/api/demo", tags=["demo"])

SCAN_IN_PROGRESS_MESSAGE = "이미 스캔이 진행 중입니다."


def scenario_out(scenario: WeatherScenario) -> ScenarioOut:
    return ScenarioOut(**scenario.model_dump())


@router.post(
    "/scan",
    response_model=ScanReport,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def scan(
    lock: threading.Lock = Depends(get_scan_lock),
    service: ImageIngestionService = Depends(get_ingestion_service),
) -> ScanReport:
    if not lock.acquire(blocking=False):
        raise ApiError(409, "SCAN_IN_PROGRESS", SCAN_IN_PROGRESS_MESSAGE)
    try:
        return service.scan()
    finally:
        lock.release()


@router.get("/status", response_model=DemoStatus)
def demo_status(
    config: AppConfig = Depends(get_config),
    repo: OutfitRepository = Depends(get_repository),
    store: ScenarioStore = Depends(get_scenario_store),
) -> DemoStatus:
    counts = repo.counts()
    selected = store.selected()
    return DemoStatus(
        vision_provider=config.vision_provider,
        weather_provider=config.weather_provider,
        gemini_api_key_configured=config.gemini_api_key_configured,
        outfit_count=counts["outfits"],
        weather_snapshot_count=counts["weather_snapshots"],
        feedback_count=counts["feedback"],
        selected_scenario=scenario_out(selected) if selected is not None else None,
    )


@router.get("/scenarios", response_model=ScenarioListResponse)
def list_scenarios(store: ScenarioStore = Depends(get_scenario_store)) -> ScenarioListResponse:
    selected = store.selected()
    return ScenarioListResponse(
        scenarios=[scenario_out(s) for s in store.list()],
        selected_id=selected.id if selected is not None else None,
    )


@router.put(
    "/scenarios/selected",
    response_model=ScenarioSelection,
    responses={404: {"model": ErrorResponse}},
)
def select_scenario(
    body: ScenarioSelectIn, store: ScenarioStore = Depends(get_scenario_store)
) -> ScenarioSelection:
    scenario = store.select(body.scenario_id)  # UnknownScenario -> 404, selection kept
    return ScenarioSelection(selected_id=scenario.id, scenario=scenario_out(scenario))


@router.delete(
    "/scenarios/selected",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def clear_scenario(store: ScenarioStore = Depends(get_scenario_store)) -> Response:
    store.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
