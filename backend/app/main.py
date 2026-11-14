"""FastAPI application factory (design.md §11).

``create_app(config=None)``:

1. Without an injected config, reads settings (process env > repo-root ``.env``
   > defaults) and resolves them with ``resolve_config``.
2. Installs the log format and ``RedactingFilter`` (Req 16.5), then logs every
   ``ConfigIssue`` at ERROR (Req 15.6, 16.8, 16.9).
3. Resolves relative ``DATABASE_URL`` / ``PHOTO_DIR`` against the repository
   root, so ``uvicorn app.main:app`` works from ``backend/``.
4. Builds the engine (``create_all``), providers and services
   (``app.deps.build_container``).
5. Adds ``CORSMiddleware`` for ``CORS_ORIGINS`` only (Req 16.7), the common
   error handlers, and the routers in ``app/api/``.

Every error response is ``{"error": {"code", "message"}}`` without stack
traces or server paths. A validation error on the body of
``POST /api/outfits/{id}/feedback`` is 422 ``INVALID_THERMAL_VOTE`` (Req 9.2);
any other validation error is 422 ``VALIDATION_ERROR``.

The module attribute ``app`` (used by ``uvicorn app.main:app``) is created on
first access, so importing ``create_app`` in tests never reads the real
``.env`` or touches ``data/weatherwear.db``.
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import (
    DEFAULT_USER_PHOTO_DIR,
    AppConfig,
    ConfigIssue,
    RawSettings,
    RedactingFilter,
    configure_logging,
    resolve_config,
    user_photo_dir_conflicts,
)
from app.deps import ApiError, build_container
from app.feedback import FeedbackSaveFailed, OutfitNotFound
from app.ingestion.service import PhotoDirUnavailable
from app.recommendation.base import MissingTodayTemperature
from app.schemas import ErrorDetail, ErrorResponse
from app.today_weather import UnknownScenario

logger = logging.getLogger(__name__)

# backend/app/main.py -> repository root is parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

# Router modules under app/api/ (tasks 10.2-10.4). Each module exposes
# ``router: APIRouter``; all of them are required. The last six are Phase 2
# (Phase 2 tasks 1.7; bodies filled by 1.8, 3.16, 3.20, 4.13 and 7.2).
ROUTER_MODULES: tuple[str, ...] = (
    "outfits",
    "demo",
    "weather",
    "recommendations",
    "meta",
    "gallery",
    "location",
    "notifications",
    "push",
    "daily_brief",
    "automation",
)

# Loggers whose handlers do not propagate to root under uvicorn's log config.
_EXTRA_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_FEEDBACK_PATH = re.compile(r"^/api/outfits/[^/]+/feedback/?$")

# Phase 2 (design.md §19): body validation errors on these POST paths get
# their own code instead of VALIDATION_ERROR.
_BODY_ERROR_CODES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_FEEDBACK_PATH, "INVALID_THERMAL_VOTE"),
    (re.compile(r"^/api/location/?$"), "INVALID_LOCATION"),
    (re.compile(r"^/api/push/subscriptions/?$"), "PUSH_SUBSCRIPTION_INVALID"),
)

MESSAGES = {
    "SCAN_IN_PROGRESS": "이미 스캔이 진행 중입니다.",
    "PHOTO_DIR_UNAVAILABLE": "사진 폴더에 접근할 수 없습니다. 설정을 확인해 주세요.",
    "UNKNOWN_SCENARIO": "알 수 없는 날씨 시나리오입니다.",
    "TODAY_TEMPERATURE_MISSING": "오늘 기온 정보를 가져오지 못했습니다.",
    "OUTFIT_NOT_FOUND": "착장을 찾을 수 없습니다.",
    "PHOTO_NOT_FOUND": "사진을 찾을 수 없습니다.",
    "INVALID_THERMAL_VOTE": "체감 값은 cold, comfortable, hot 중 하나여야 합니다.",
    "VALIDATION_ERROR": "요청 형식이 올바르지 않습니다.",
    "FEEDBACK_SAVE_FAILED": "피드백을 저장하지 못했습니다.",
    "INTERNAL_ERROR": "서버 오류가 발생했습니다.",
    "NOT_FOUND": "요청한 경로를 찾을 수 없습니다.",
    "METHOD_NOT_ALLOWED": "허용되지 않는 요청 방식입니다.",
    "HTTP_ERROR": "요청을 처리할 수 없습니다.",
    # Phase 2 (design.md "오류 코드 표")
    "INVALID_LOCATION": "위치 값이 올바르지 않습니다.",
    "GALLERY_IMPORT_NOT_FOUND": "사진 처리 기록을 찾을 수 없습니다.",
    "GALLERY_IMPORT_NOT_REANALYZABLE": "이 사진은 다시 분석할 수 없는 상태입니다.",
    "GALLERY_IMPORT_NOT_CONFIRMABLE": "이 사진은 착장으로 확정할 수 없는 상태입니다.",
    "DAILY_BRIEF_IN_PROGRESS": "브리핑을 만드는 중입니다. 잠시 뒤 다시 시도해 주세요.",
    "PUSH_SUBSCRIPTION_INVALID": "푸시 구독 정보가 올바르지 않습니다.",
    "PUSH_SUBSCRIPTION_NOT_FOUND": "푸시 구독을 찾을 수 없습니다.",
    "WEB_PUSH_DISABLED": "웹 푸시가 꺼져 있습니다.",
    "VAPID_KEY_UNAVAILABLE": "푸시 키를 준비하지 못했습니다.",
    # Phase 2 skeleton routes before their task fills them (tasks 1.7)
    "NOT_IMPLEMENTED": "아직 준비되지 않은 기능입니다.",
}

# The nine Phase 2 error codes of design.md "오류 코드 표".
PHASE2_ERROR_CODES: tuple[str, ...] = (
    "INVALID_LOCATION",
    "GALLERY_IMPORT_NOT_FOUND",
    "GALLERY_IMPORT_NOT_REANALYZABLE",
    "GALLERY_IMPORT_NOT_CONFIRMABLE",
    "DAILY_BRIEF_IN_PROGRESS",
    "PUSH_SUBSCRIPTION_INVALID",
    "PUSH_SUBSCRIPTION_NOT_FOUND",
    "WEB_PUSH_DISABLED",
    "VAPID_KEY_UNAVAILABLE",
)


def error_response(status_code: int, code: str, message: str | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message or MESSAGES[code]))
    return JSONResponse(status_code=status_code, content=body.model_dump())


# --- Config and logging -----------------------------------------------------


def _absolute(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else (root / path).resolve()


def resolve_paths(config: AppConfig, root: Path = REPO_ROOT) -> AppConfig:
    """Make relative PHOTO_DIR and SQLite file paths absolute against ``root``.

    Phase 1 behaviour, unchanged: Phase 2 paths are handled by
    ``resolve_automation_paths``, which ``create_app`` calls right after this.
    """
    photo_dir = _absolute(config.photo_dir, root)

    database_url = config.database_url
    url = make_url(database_url)
    database = url.database
    if (
        url.drivername.startswith("sqlite")
        and database
        and database != ":memory:"
        and not database.startswith("file:")
        and not Path(database).is_absolute()
    ):
        url = url.set(database=str((root / database).resolve()))
        database_url = url.render_as_string(hide_password=False)

    return replace(config, photo_dir=photo_dir, database_url=database_url)


def resolve_automation_paths(
    config: AppConfig,
    root: Path = REPO_ROOT,
    *,
    issues: list[ConfigIssue] | None = None,
) -> AppConfig:
    """Phase 2 paths against ``root`` (Req 18.3) and the USER_PHOTO_DIR check (Req 4.2).

    GALLERY_DIR, USER_PHOTO_DIR and VAPID_KEY_DIR become absolute. When
    USER_PHOTO_DIR equals, contains or sits inside PHOTO_DIR or GALLERY_DIR
    (compared as absolute paths), the default is used; if the default
    conflicts too, watching is turned off. Each problem is one ``ConfigIssue``,
    logged at ERROR and, when ``issues`` is given, appended to it.
    """
    automation = config.automation
    found: list[ConfigIssue] = []
    photo_dir = _absolute(config.photo_dir, root)
    gallery_dir = _absolute(automation.gallery_dir, root)
    user_photo_dir = _absolute(automation.user_photo_dir, root)
    vapid_key_dir = _absolute(automation.vapid_key_dir, root)
    watch_enabled = automation.gallery_watch_enabled

    if user_photo_dir_conflicts(user_photo_dir, photo_dir, gallery_dir):
        found.append(
            ConfigIssue(
                variable="USER_PHOTO_DIR",
                message=(
                    "USER_PHOTO_DIR는 PHOTO_DIR, GALLERY_DIR와 같거나 서로 포함하면 안 됩니다. "
                    "기본값 ./data/user/photos를 사용합니다."
                ),
            )
        )
        user_photo_dir = _absolute(DEFAULT_USER_PHOTO_DIR, root)
        if user_photo_dir_conflicts(user_photo_dir, photo_dir, gallery_dir):
            found.append(
                ConfigIssue(
                    variable="USER_PHOTO_DIR",
                    message=(
                        "USER_PHOTO_DIR 기본값도 PHOTO_DIR 또는 GALLERY_DIR와 겹칩니다. "
                        "갤러리 감시를 끕니다."
                    ),
                )
            )
            watch_enabled = False

    log_config_issues(found)
    if issues is not None:
        issues.extend(found)
    resolved = replace(
        automation,
        gallery_watch_enabled=watch_enabled,
        gallery_dir=gallery_dir,
        user_photo_dir=user_photo_dir,
        vapid_key_dir=vapid_key_dir,
    )
    return replace(config, automation=resolved)


def install_logging(config: AppConfig) -> RedactingFilter:
    """Log format + RedactingFilter on root and uvicorn handlers (Req 16.5).

    Filters left by an earlier ``create_app`` call are removed first so that
    handlers do not collect one filter per app instance.
    """
    loggers = [logging.getLogger(), *(logging.getLogger(n) for n in _EXTRA_LOGGERS)]
    for log in loggers:
        for handler in log.handlers:
            for old in [f for f in handler.filters if isinstance(f, RedactingFilter)]:
                handler.removeFilter(old)
    redacting = configure_logging(config)
    for log in loggers[1:]:
        for handler in log.handlers:
            handler.addFilter(redacting)
    return redacting


def log_config_issues(issues: Iterable[ConfigIssue]) -> None:
    for item in issues:
        logger.error("설정 오류 [%s]: %s", item.variable, item.message)


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> tuple[AppConfig, list[ConfigIssue]]:
    raw = RawSettings(_env_file=env_file)  # type: ignore[call-arg]
    return resolve_config(raw.model_dump())


# --- Error handlers ---------------------------------------------------------


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        body_error = any(err.get("loc", ())[:1] == ("body",) for err in exc.errors())
        if request.method == "POST" and body_error:
            for pattern, code in _BODY_ERROR_CODES:
                if pattern.match(request.url.path):
                    return error_response(422, code)
        return error_response(422, "VALIDATION_ERROR")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        response = error_response(exc.status_code, code)
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(OutfitNotFound)
    async def _outfit_not_found(_: Request, __: OutfitNotFound) -> JSONResponse:
        return error_response(404, "OUTFIT_NOT_FOUND")

    @app.exception_handler(FeedbackSaveFailed)
    async def _feedback_failed(_: Request, __: FeedbackSaveFailed) -> JSONResponse:
        return error_response(500, "FEEDBACK_SAVE_FAILED")

    @app.exception_handler(UnknownScenario)
    async def _unknown_scenario(_: Request, __: UnknownScenario) -> JSONResponse:
        return error_response(404, "UNKNOWN_SCENARIO")

    @app.exception_handler(MissingTodayTemperature)
    async def _missing_today(_: Request, __: MissingTodayTemperature) -> JSONResponse:
        return error_response(503, "TODAY_TEMPERATURE_MISSING")

    @app.exception_handler(PhotoDirUnavailable)
    async def _photo_dir(_: Request, __: PhotoDirUnavailable) -> JSONResponse:
        return error_response(503, "PHOTO_DIR_UNAVAILABLE")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Logged through RedactingFilter; the response never carries the traceback.
        logger.error(
            "Unhandled error on %s %s", request.method, request.url.path, exc_info=exc
        )
        return error_response(500, "INTERNAL_ERROR")


# --- Routers ----------------------------------------------------------------


def _include_routers(app: FastAPI) -> None:
    """Include ``app.api.<name>.router`` for every module in ROUTER_MODULES.

    A missing module raises ``ModuleNotFoundError`` so a router can never be
    skipped silently.
    """
    for name in ROUTER_MODULES:
        module = importlib.import_module(f"app.api.{name}")
        app.include_router(module.router)


# --- App factory ------------------------------------------------------------


def create_app(
    config: AppConfig | None = None, *, env_file: Path | None = DEFAULT_ENV_FILE
) -> FastAPI:
    """Build the app. Pass ``config`` in tests so no ``.env`` file is read."""
    issues: list[ConfigIssue] = []
    if config is None:
        config, issues = load_settings(env_file)
    install_logging(config)
    log_config_issues(issues)
    config = resolve_automation_paths(resolve_paths(config))

    container = build_container(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            container.engine.dispose()

    app = FastAPI(title="WeatherWear API", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    app.state.config = config

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    _register_error_handlers(app)
    _include_routers(app)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Lazy module attribute ``app`` for ``uvicorn app.main:app``."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
