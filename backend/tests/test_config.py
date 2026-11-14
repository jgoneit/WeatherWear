"""Unit tests for the Config_Loader (backend/app/config.py)."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import (
    RawSettings,
    RedactingFilter,
    load_config,
    parse_cors_origins,
    resolve_config,
)

# Obviously fake value; never a real key.
FAKE_KEY = "fake-test-key-0123456789abcdef"

ALL_VARS = (
    "GEMINI_API_KEY", "VISION_PROVIDER", "GEMINI_MODEL", "WEATHER_PROVIDER", "DATABASE_URL",
    "PHOTO_DIR", "APP_TIMEZONE", "TEMPERATURE_TOLERANCE_C", "DEFAULT_LATITUDE",
    "DEFAULT_LONGITUDE", "CORS_ORIGINS",
)


def test_empty_mapping_gives_defaults_without_issues():
    cfg, issues = resolve_config({})
    assert issues == []
    assert cfg.vision_provider == "fixture"
    assert cfg.weather_provider == "fixture"
    assert cfg.gemini_model == "gemini-4-hydrogen"
    assert cfg.database_url == "sqlite:///./data/weatherwear.db"
    assert cfg.photo_dir == Path("./data/demo/photos")
    assert cfg.app_timezone.key == "Asia/Seoul"
    assert cfg.temperature_tolerance_c == Decimal("3.0")
    assert (cfg.default_latitude, cfg.default_longitude) == (37.5665, 126.9780)
    assert cfg.cors_origins == ("http://localhost:5173",)
    assert cfg.gemini_api_key_configured is False


def test_valid_values_are_used():
    cfg, issues = resolve_config({
        "WEATHER_PROVIDER": "open_meteo", "TEMPERATURE_TOLERANCE_C": "4.9",
        "DEFAULT_LATITUDE": "-90", "DEFAULT_LONGITUDE": "180", "APP_TIMEZONE": "Europe/London",
        "CORS_ORIGINS": " http://a.test , ,http://b.test:3000 ",
    })
    assert issues == []
    assert cfg.weather_provider == "open_meteo"
    assert cfg.temperature_tolerance_c == Decimal("4.9")
    assert (cfg.default_latitude, cfg.default_longitude) == (-90.0, 180.0)
    assert cfg.app_timezone.key == "Europe/London"
    assert cfg.cors_origins == ("http://a.test", "http://b.test:3000")


def test_invalid_providers_fall_back_to_fixture_with_issue():
    cfg, issues = resolve_config({"VISION_PROVIDER": "openai", "WEATHER_PROVIDER": "Fixture"})
    assert cfg.vision_provider == "fixture"
    assert cfg.weather_provider == "fixture"
    assert [i.variable for i in issues] == ["VISION_PROVIDER", "WEATHER_PROVIDER"]
    assert "openai" not in issues[0].message
    assert "gemini, fixture" in issues[0].message
    assert "open_meteo, fixture" in issues[1].message


def test_gemini_without_key_or_model_falls_back_and_names_missing_vars():
    cfg, issues = resolve_config({"VISION_PROVIDER": "gemini", "GEMINI_API_KEY": "", "GEMINI_MODEL": " "})
    assert cfg.vision_provider == "fixture"
    assert [i.variable for i in issues] == ["GEMINI_API_KEY", "GEMINI_MODEL"]


def test_gemini_with_key_and_model_is_used_and_key_is_hidden():
    cfg, issues = resolve_config({"VISION_PROVIDER": "gemini", "GEMINI_API_KEY": FAKE_KEY, "GEMINI_MODEL": "m-1"})
    assert issues == []
    assert cfg.vision_provider == "gemini"
    assert cfg.gemini_model == "m-1"
    assert cfg.gemini_api_key.get_secret_value() == FAKE_KEY
    assert cfg.gemini_api_key_configured is True
    assert FAKE_KEY not in repr(cfg) and FAKE_KEY not in str(cfg)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_tolerance_is_default_without_issue(value):
    cfg, issues = resolve_config({"TEMPERATURE_TOLERANCE_C": value})
    assert cfg.temperature_tolerance_c == Decimal("3.0")
    assert issues == []


@pytest.mark.parametrize("value", ["abc", "nan", "NaN", "inf", "-Infinity", "0", "-1", "5.0", "5", "7.2"])
def test_invalid_tolerance_falls_back_with_issue(value):
    cfg, issues = resolve_config({"TEMPERATURE_TOLERANCE_C": value})
    assert cfg.temperature_tolerance_c == Decimal("3.0")
    assert [i.variable for i in issues] == ["TEMPERATURE_TOLERANCE_C"]
    assert "0 초과 5.0 미만" in issues[0].message


@pytest.mark.parametrize(
    ("name", "value"),
    [("DEFAULT_LATITUDE", "90.0001"), ("DEFAULT_LATITUDE", "north"), ("DEFAULT_LATITUDE", "nan"),
     ("DEFAULT_LONGITUDE", "-180.5"), ("DEFAULT_LONGITUDE", "inf")],
)
def test_invalid_coordinates_fall_back_with_issue(name, value):
    cfg, issues = resolve_config({name: value})
    assert (cfg.default_latitude, cfg.default_longitude) == (37.5665, 126.9780)
    assert [i.variable for i in issues] == [name]


@pytest.mark.parametrize("value", ["Mars/Olympus", "../etc/passwd", "Asia/"])
def test_invalid_timezone_falls_back_with_issue(value):
    cfg, issues = resolve_config({"APP_TIMEZONE": value})
    assert cfg.app_timezone.key == "Asia/Seoul"
    assert [i.variable for i in issues] == ["APP_TIMEZONE"]


def test_cors_with_only_separators_falls_back_with_issue():
    cfg, issues = resolve_config({"CORS_ORIGINS": " , ,"})
    assert cfg.cors_origins == ("http://localhost:5173",)
    assert [i.variable for i in issues] == ["CORS_ORIGINS"]


def test_parse_cors_origins():
    assert parse_cors_origins("http://a.test,  http://b.test ,,") == ("http://a.test", "http://b.test")
    assert parse_cors_origins("") == ()


def test_env_precedence_process_over_dotenv_over_default(tmp_path, monkeypatch):
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("WEATHER_PROVIDER=open_meteo\nTEMPERATURE_TOLERANCE_C=2.5\n", encoding="utf-8")
    monkeypatch.setenv("TEMPERATURE_TOLERANCE_C", "4")

    raw = RawSettings(_env_file=env_file)
    assert raw.TEMPERATURE_TOLERANCE_C == "4"         # process env wins
    assert raw.WEATHER_PROVIDER == "open_meteo"        # .env beats default
    assert raw.VISION_PROVIDER == "fixture"            # default

    cfg, issues = load_config(env_file)
    assert issues == []
    assert cfg.temperature_tolerance_c == Decimal("4")
    assert cfg.weather_provider == "open_meteo"


def test_load_config_logs_missing_variable_names_only(tmp_path, monkeypatch, caplog):
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VISION_PROVIDER", "gemini")
    with caplog.at_level(logging.ERROR, logger="app.config"):
        cfg, _ = load_config(tmp_path / "missing.env")
    assert cfg.vision_provider == "fixture"
    assert "GEMINI_API_KEY" in caplog.text


def _emit(record_filter: RedactingFilter, logger_name: str) -> str:
    """Log a message and an exception that both contain the key; return formatted output."""
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = Capture()
    handler.addFilter(record_filter)
    log = logging.getLogger(logger_name)
    log.addHandler(handler)
    log.propagate = False
    try:
        log.warning("calling with key=%s", FAKE_KEY)
        try:
            raise RuntimeError(f"auth failed for {FAKE_KEY}")
        except RuntimeError:
            log.exception("request failed")
    finally:
        log.removeHandler(handler)
    return "\n".join(records)


def test_redacting_filter_hides_key_in_message_and_exception():
    output = _emit(RedactingFilter([FAKE_KEY]), "test.redact")
    assert FAKE_KEY not in output
    assert "key=***" in output
    assert "auth failed for ***" in output


def test_redacting_filter_with_empty_secret_is_noop():
    flt = RedactingFilter([""])
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    assert flt.filter(record) is True
    assert record.getMessage() == "hello world"
