"""Single-process, fixture-only public demo. No personal images or model keys."""

import os

import uvicorn

from app.config import resolve_config
from app.ingestion.service import ImageIngestionService
from app.main import create_app


def build_demo_app():
    config, issues = resolve_config({
        "VISION_PROVIDER": "fixture",
        "WEATHER_PROVIDER": "fixture",
        "GEMINI_API_KEY": "",
        "DATABASE_URL": os.environ.get("DATABASE_URL", "sqlite:////data/db/weatherwear.db"),
        "PHOTO_DIR": os.environ.get("PHOTO_DIR", "/data/photos"),
    })
    if issues:
        raise RuntimeError("Invalid demo configuration")
    app = create_app(config)
    c = app.state.container
    report = ImageIngestionService(
        c.config.photo_dir, c.vision_provider, c.weather_provider,
        c.repository, c.config.app_timezone,
    ).scan()
    if report.failed or report.succeeded + report.duplicate < 15:
        raise RuntimeError("Bundled demo photos did not initialize successfully")
    c.scenario_store.select("early-heat")
    return app


if __name__ == "__main__":
    uvicorn.run(build_demo_app(), host="0.0.0.0", port=8000, workers=1)
