"""Unit tests for GeminiVisionProvider (task 4.4, Req 3.2, 3.4, 3.5, 3.6, 3.7).

Every test injects a fake client; no request leaves the process.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import httpx
import pytest
from google.genai import _transformers, errors, types
from PIL import Image
from pydantic import SecretStr

from app.config import resolve_config
from app.domain import VisionAnalysis
from app.providers.vision.base import VisionError
from app.providers.vision.factory import build_vision_provider
from app.providers.vision.gemini import (
    PROMPT,
    RESPONSE_SCHEMA,
    GeminiVisionProvider,
    _build_client,
    strip_metadata,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_JPG = REPO_ROOT / "data" / "demo" / "photos" / "2025-01-08_081500_KST.jpg"
FAKE_KEY = "AIzaFAKE-test-key-0123456789abcdef"
MODEL = "gemini-4-hydrogen"
GPS_IFD = 0x8825

VALID_TAGS = {
    "upper_type": "knit",
    "outer_type": "coat",
    "lower_type": "slacks",
    "colors": ["navy", "cream"],
    "layers_visible": 2,
    "confidence": 0.8,
}
VALID_ANALYSIS = {
    "photo_type": "outfit",
    "photo_type_confidence": 0.95,
    "reason": "A full outfit is visible.",
    "tags": VALID_TAGS,
}


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, behavior) -> None:
        self.behavior = behavior
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.behavior(**kwargs)


class FakeClient:
    def __init__(self, behavior) -> None:
        self.models = _FakeModels(behavior)


def _returns(text: str | None):
    return lambda **_: _FakeResponse(text)


def _raises(exc: BaseException):
    def behavior(**_):
        raise exc

    return behavior


def _jpeg_with_gps() -> bytes:
    img = Image.new("RGB", (16, 12), (200, 100, 50))
    exif = Image.Exif()
    exif[0x010E] = "secret description"  # ImageDescription
    exif[0x0132] = "2025:01:08 08:15:00"  # DateTime
    exif.get_ifd(GPS_IFD).update({1: "N", 2: (37.0, 33.0, 21.6), 3: "E", 4: (126.0, 55.0, 26.4)})
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _png_with_text() -> bytes:
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "captured at Seoul 37.5665,126.9780")
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(buf, format="PNG", pnginfo=info, exif=exif)
    return buf.getvalue()


def _provider(behavior, **kwargs) -> tuple[GeminiVisionProvider, FakeClient]:
    client = FakeClient(behavior)
    return GeminiVisionProvider(model=MODEL, api_key=SecretStr(FAKE_KEY), client=client, **kwargs), client


# --- EXIF stripping (Req 3.7) ---


def test_strip_metadata_removes_jpeg_exif_and_gps() -> None:
    original = _jpeg_with_gps()
    assert Image.open(io.BytesIO(original)).getexif().get_ifd(GPS_IFD)  # precondition

    clean = strip_metadata(original, "image/jpeg")

    with Image.open(io.BytesIO(clean)) as img:
        assert img.format == "JPEG"
        assert img.size == (16, 12)
        assert len(img.getexif()) == 0
        assert "exif" not in img.info
    assert b"Exif" not in clean
    assert b"secret description" not in clean


def test_strip_metadata_removes_png_text_and_exif() -> None:
    clean = strip_metadata(_png_with_text(), "image/png")

    with Image.open(io.BytesIO(clean)) as img:
        assert img.format == "PNG"
        assert img.size == (8, 8)
        assert len(img.getexif()) == 0
        assert "Comment" not in img.info
    assert b"Seoul" not in clean
    assert b"secret description" not in clean


@pytest.mark.skipif(not DEMO_JPG.is_file(), reason="demo photo not prepared")
def test_strip_metadata_removes_demo_photo_gps() -> None:
    clean = strip_metadata(DEMO_JPG.read_bytes(), "image/jpeg")
    with Image.open(io.BytesIO(clean)) as img:
        assert len(img.getexif()) == 0
        assert not img.getexif().get_ifd(GPS_IFD)


def test_strip_metadata_applies_orientation() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW on display
    buf = io.BytesIO()
    Image.new("RGB", (20, 10)).save(buf, format="JPEG", exif=exif)

    with Image.open(io.BytesIO(strip_metadata(buf.getvalue(), "image/jpeg"))) as img:
        assert img.size == (10, 20)


# --- request shape (Req 3.2, 3.6, 3.7) ---


def test_request_contains_only_stripped_image_and_fixed_prompt() -> None:
    provider, client = _provider(_returns(json.dumps(VALID_ANALYSIS)))

    provider.analyze(_jpeg_with_gps(), "image/jpeg", "ab" * 32)

    assert len(client.models.calls) == 1
    call = client.models.calls[0]
    assert set(call) == {"model", "contents", "config"}
    assert call["model"] == MODEL
    image_part, prompt = call["contents"]
    assert isinstance(image_part, types.Part)
    assert image_part.inline_data.mime_type == "image/jpeg"
    sent = image_part.inline_data.data
    assert len(Image.open(io.BytesIO(sent)).getexif()) == 0
    assert b"secret description" not in sent
    assert prompt == PROMPT
    config = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == RESPONSE_SCHEMA
    assert config.temperature == 0


def test_response_schema_matches_vision_analysis_without_additional_properties() -> None:
    expected = VisionAnalysis.model_json_schema()
    assert RESPONSE_SCHEMA["required"] == expected["required"]
    assert set(RESPONSE_SCHEMA["properties"]) == set(expected["properties"])
    assert RESPONSE_SCHEMA["properties"]["photo_type"]["enum"] == [
        "outfit", "non_outfit", "uncertain"
    ]
    garment_schema = RESPONSE_SCHEMA["$defs"]["ClothingTags"]
    assert garment_schema["properties"]["colors"]["maxItems"] == 5
    assert garment_schema["properties"]["confidence"]["maximum"] == 1.0
    assert "long_puffer" in garment_schema["properties"]["outer_type"]["enum"]
    assert "t_shirt" in garment_schema["properties"]["upper_type"]["enum"]
    assert "shorts" in garment_schema["properties"]["lower_type"]["enum"]
    # The API rejects additional_properties with 400 INVALID_ARGUMENT.
    sdk_schema = _transformers.t_schema(_build_client(SecretStr(FAKE_KEY)), RESPONSE_SCHEMA)
    assert "additional_properties" not in json.dumps(sdk_schema.model_dump(exclude_none=True), default=str)


def test_prompt_instructs_photo_types_and_canonical_categories() -> None:
    assert all(value in PROMPT for value in ("outfit", "non_outfit", "uncertain"))
    assert "unknown" in PROMPT
    assert "none" in PROMPT
    assert "long_puffer" in PROMPT
    assert "t_shirt" in PROMPT
    assert "shorts" in PROMPT
    assert "추측하지 마라" in PROMPT


# --- response validation and failures (Req 3.4, 3.5) ---


def test_valid_response_returns_analysis() -> None:
    provider, _ = _provider(_returns(json.dumps(VALID_ANALYSIS)))
    assert provider.analyze(_png_with_text(), "image/png") == VisionAnalysis(**VALID_ANALYSIS)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "not json",
        json.dumps({**VALID_ANALYSIS, "photo_type_confidence": 1.5}),
        json.dumps({**VALID_ANALYSIS, "photo_type": "holiday"}),
        json.dumps({
            **VALID_ANALYSIS,
            "tags": {**VALID_TAGS, "colors": ["a", "b", "c", "d", "e", "f"]},
        }),
        json.dumps({
            **VALID_ANALYSIS,
            "tags": {**VALID_TAGS, "outer_type": "puffer coat"},
        }),
        json.dumps({
            **VALID_ANALYSIS,
            "tags": {k: v for k, v in VALID_TAGS.items() if k != "lower_type"},
        }),
        json.dumps({
            **VALID_ANALYSIS,
            "tags": {**VALID_TAGS, "fabric": "wool"},
        }),
        json.dumps({
            **VALID_ANALYSIS,
            "photo_type": "non_outfit",
            "tags": VALID_TAGS,
        }),
    ],
)
def test_invalid_response_is_schema_invalid_without_retry(text: str | None) -> None:
    provider, client = _provider(_returns(text))
    with pytest.raises(VisionError) as info:
        provider.analyze(_jpeg_with_gps(), "image/jpeg")
    assert info.value.kind == "schema_invalid"
    assert len(client.models.calls) == 1


@pytest.mark.parametrize(
    "exc",
    [
        errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}),
        errors.ServerError(503, {"error": {"code": 503, "message": "down", "status": "UNAVAILABLE"}}),
        httpx.ConnectError("connection refused"),
        RuntimeError("boom"),
    ],
)
def test_api_failure_is_api_error_without_retry(exc: BaseException) -> None:
    provider, client = _provider(_raises(exc))
    with pytest.raises(VisionError) as info:
        provider.analyze(_jpeg_with_gps(), "image/jpeg")
    assert info.value.kind == "api_error"
    assert len(client.models.calls) == 1


def test_sdk_timeout_is_timeout() -> None:
    provider, client = _provider(_raises(httpx.ReadTimeout("timed out")))
    with pytest.raises(VisionError) as info:
        provider.analyze(_jpeg_with_gps(), "image/jpeg")
    assert info.value.kind == "timeout"
    assert len(client.models.calls) == 1


def test_hung_call_hits_futures_timeout() -> None:
    release = threading.Event()

    def hang(**_):
        release.wait(5)
        return _FakeResponse(json.dumps(VALID_ANALYSIS))

    provider, client = _provider(hang, timeout_seconds=0.2)
    try:
        with pytest.raises(VisionError) as info:
            provider.analyze(_jpeg_with_gps(), "image/jpeg")
    finally:
        release.set()
    assert info.value.kind == "timeout"
    assert len(client.models.calls) == 1


def test_undecodable_image_is_not_sent() -> None:
    provider, client = _provider(_returns(json.dumps(VALID_ANALYSIS)))
    with pytest.raises(VisionError) as info:
        provider.analyze(b"not an image", "image/jpeg")
    assert info.value.kind == "api_error"
    assert client.models.calls == []


def test_errors_and_repr_never_contain_key() -> None:
    provider, _ = _provider(_raises(RuntimeError(f"failure with key={FAKE_KEY}")))
    with pytest.raises(VisionError) as info:
        provider.analyze(_jpeg_with_gps(), "image/jpeg")
    assert FAKE_KEY not in str(info.value)
    assert FAKE_KEY not in repr(provider)


# --- client construction and factory (Req 3.2) ---


def test_default_client_disables_retries_and_sets_30s_timeout() -> None:
    client = _build_client(SecretStr(FAKE_KEY))
    http_options = client._api_client._http_options
    assert http_options.timeout == 30_000
    assert http_options.retry_options.attempts == 1


def test_factory_builds_gemini_provider_with_configured_model() -> None:
    cfg, _ = resolve_config({"VISION_PROVIDER": "gemini", "GEMINI_API_KEY": FAKE_KEY, "GEMINI_MODEL": "gemini-x-test"})
    fake = FakeClient(_returns(json.dumps(VALID_ANALYSIS)))

    provider = build_vision_provider(cfg, gemini_client=fake)

    assert isinstance(provider, GeminiVisionProvider)
    assert provider.name == "gemini"
    provider.analyze(_jpeg_with_gps(), "image/jpeg")
    assert fake.models.calls[0]["model"] == "gemini-x-test"
