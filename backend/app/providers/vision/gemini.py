"""GeminiVisionProvider (design.md §4, Req 3.2, 3.4, 3.5, 3.6, 3.7, 3.10, 16.10).

Calls Gemini through the Google Cloud Agent Platform API (formerly Vertex AI)
in express mode: ``genai.Client(enterprise=True, api_key=...)`` with no
project or location. ``GEMINI_API_KEY`` holds an express-mode key; a Google AI
Studio key does not work here.

``GEMINI_MODEL`` may be a short ID (``gemini-3-flash-preview``) or a full
resource path (``projects/<n>/locations/global/publishers/google/models/<id>``).
A full path pins the client to the global endpoint; see ``build_client``.

One ``generate_content`` call per image with exactly two parts: the image
bytes with EXIF and every other metadata block removed, and the fixed
``PROMPT``. File names, sidecar values and EXIF never reach the API (Req 3.7).

Failure handling (Req 3.5), no retries anywhere:

- SDK retries are off (``HttpRetryOptions(attempts=1)``) and the SDK HTTP
  timeout is 30 s. The call also runs in a worker thread with a 30 s
  ``concurrent.futures`` timeout as a second guard.
- Timeout → ``VisionError("timeout")``; any other SDK/transport error →
  ``VisionError("api_error")``; a response that fails
  ``ClothingTags.model_validate_json`` → ``VisionError("schema_invalid")``.

Error messages and logs carry only the error type and HTTP status, never the
exception text, so the API key cannot leak through them (Req 16.5).
"""

from __future__ import annotations

import concurrent.futures
import io
import logging
from typing import Any, Literal

import httpx
from PIL import Image, ImageOps
from pydantic import SecretStr, ValidationError

from app.domain import ClothingTags
from app.providers.vision.base import VisionError

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0

# Fixed analysis prompt (Req 3.6). Nothing per-image is ever added to it.
PROMPT = (
    "사진 속 인물의 착장만 분석하라. 결과는 주어진 JSON 스키마의 6개 필드로만 답하라.\n"
    "- upper_type: 상의 종류, outer_type: 겉옷 종류, lower_type: 하의 종류. 짧은 영어 소문자 명사로 적어라 "
    "(예: knit, t_shirt, coat, padded_jacket, jeans, slacks, skirt).\n"
    "- 겉옷을 입지 않았으면 outer_type은 'none'이다.\n"
    "- 사진에서 보이지 않거나 판단할 수 없는 속성은 추측하지 말고 'unknown'으로 답하라.\n"
    "- 원단 두께, 보온성, 속옷은 판단하지 마라.\n"
    "- colors: 착장의 주요 색상을 영어 소문자로 최대 5개.\n"
    "- layers_visible: 겉으로 보이는 옷 겹 수(0~5 정수).\n"
    "- confidence: 분석 전체에 대한 확신도(0.0~1.0).\n"
    "사진에 인물이나 착장이 없거나 착장을 볼 수 없으면 세 종류 필드를 모두 'unknown', colors는 빈 목록, "
    "layers_visible은 0, confidence는 0.0으로 답하라."
)



def _response_schema() -> dict[str, Any]:
    """``ClothingTags`` JSON schema in a form the Gemini API accepts.

    Passing the model class directly makes the SDK send
    ``additional_properties: false`` (from ``extra="forbid"``), which the API
    rejects with 400 INVALID_ARGUMENT. The schema is otherwise unchanged
    (same fields, types, lengths and ranges). Extra keys are still rejected
    locally by ``ClothingTags.model_validate_json``.
    """

    def clean(node: Any, *, is_properties_map: bool = False) -> Any:
        if isinstance(node, dict):
            if is_properties_map:  # keys are field names, never filter them
                return {k: clean(v) for k, v in node.items()}
            return {
                k: clean(v, is_properties_map=(k == "properties"))
                for k, v in node.items()
                if k not in {"additionalProperties", "description"}
            }
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(ClothingTags.model_json_schema())


RESPONSE_SCHEMA: dict[str, Any] = _response_schema()

# Formats Pillow can re-encode, keyed by MIME type.
_PIL_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def strip_metadata(image: bytes, mime_type: str) -> bytes:
    """Re-encode ``image`` from raw pixels only, dropping EXIF (incl. GPS), XMP,
    ICC, PNG text chunks and comments.

    EXIF orientation is applied to the pixels first so the photo keeps its
    visual orientation after the tag is gone. The output keeps the input
    format so ``mime_type`` stays valid.
    """
    fmt = _PIL_FORMATS.get(mime_type)
    if fmt is None:
        raise ValueError(f"unsupported mime type: {mime_type}")
    with Image.open(io.BytesIO(image)) as src:
        src.load()
        img = ImageOps.exif_transpose(src)
    if fmt == "JPEG":
        if img.mode not in ("L", "RGB", "CMYK"):
            img = img.convert("RGB")
    elif img.mode in ("P", "PA") or (fmt == "WEBP" and img.mode not in ("RGB", "RGBA")):
        # A palette is image metadata frombytes cannot carry, so expand it.
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        img = img.convert("RGBA" if has_alpha else "RGB")
    # frombytes builds a new image with an empty ``info`` dict, so no metadata
    # can be carried over by the encoder.
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    out = io.BytesIO()
    if fmt == "JPEG":
        clean.save(out, format="JPEG", quality=95)
    else:
        clean.save(out, format=fmt)
    return out.getvalue()


GLOBAL_BASE_URL = "https://aiplatform.googleapis.com/"
GLOBAL_API_VERSION = "v1"


def is_resource_path(model: str) -> bool:
    """True for a full model resource path such as
    ``projects/<n>/locations/global/publishers/google/models/gemini-4-hydrogen``."""
    return model.startswith("projects/")


def model_id(model: str) -> str:
    """Short model ID: the last path segment of a resource path, else ``model``."""
    return model.rstrip("/").rsplit("/", 1)[-1]


def build_client(api_key: SecretStr | str, model: str | None = None) -> Any:
    """Agent Platform (formerly Vertex AI) express-mode client (Req 3.2, 16.10).

    ``enterprise=True`` + ``api_key`` puts the key in the ``x-goog-api-key``
    header. No ``project``/``location``/credentials are passed, and the legacy
    ``vertexai`` flag is not passed alongside ``enterprise`` (same flag; the
    SDK raises ``ValueError`` when the two differ).

    When ``model`` is a full resource path (``projects/...``), the client is
    pinned to the global endpoint ``https://aiplatform.googleapis.com/`` with
    ``api_version="v1"`` and the caller passes the full path as the model.
    Some models (e.g. ``gemini-4-hydrogen``) are served only from ``global``;
    a short ID can resolve to a regional location and return 404.
    Short IDs (e.g. ``gemini-3-flash-preview``) keep the SDK defaults.

    Shared by ``GeminiVisionProvider`` and ``scripts/check_gemini_model.py``.
    """
    from google import genai  # noqa: PLC0415  (fixture mode never imports the SDK)
    from google.genai import types  # noqa: PLC0415

    key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
    http_kwargs: dict[str, Any] = {
        "timeout": int(TIMEOUT_SECONDS * 1000),  # milliseconds
        "retry_options": types.HttpRetryOptions(attempts=1),  # 1 attempt = no retries
    }
    if model is not None and is_resource_path(model):
        http_kwargs["base_url"] = GLOBAL_BASE_URL
        http_kwargs["api_version"] = GLOBAL_API_VERSION
    return genai.Client(enterprise=True, api_key=key, http_options=types.HttpOptions(**http_kwargs))


_build_client = build_client  # backward-compatible name


def uses_thinking_level(model: str) -> bool:
    """Gemini 3/4 models get ``thinking_level=LOW`` so a call fits in 30 s.

    Gemini 4 Hydrogen defaults to ``HIGH`` and rejects ``MINIMAL`` with 400.
    Gemini 2.5 models use ``thinking_budget`` instead, so they get nothing.
    A full resource path is judged by its last segment (the model ID).
    """
    return model_id(model).startswith(("gemini-3", "gemini-4"))


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, concurrent.futures.TimeoutError, httpx.TimeoutException)):
        return True
    return type(exc).__name__ in {"ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "Timeout"}


class GeminiVisionProvider:
    """Vision_Provider backed by the official ``google-genai`` SDK."""

    name: Literal["gemini"] = "gemini"

    def __init__(
        self,
        model: str,
        api_key: SecretStr | str,
        client: Any | None = None,
        *,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        if not model:
            raise ValueError("model is required")
        self.model = model
        self._timeout = timeout_seconds
        key = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        # ``client`` lets tests inject a fake with ``models.generate_content``.
        self._client = client if client is not None else build_client(key, model)

    def __repr__(self) -> str:
        return f"GeminiVisionProvider(model={self.model!r})"

    def build_request(self, image: bytes, mime_type: str) -> dict[str, Any]:
        """Keyword arguments for ``client.models.generate_content``."""
        from google.genai import types  # noqa: PLC0415

        clean = strip_metadata(image, mime_type)
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,  # derived from ClothingTags
            "temperature": 0,  # ignored by Gemini 4 Hydrogen, kept for other models
        }
        if uses_thinking_level(self.model):
            config["thinking_config"] = types.ThinkingConfig(thinking_level="LOW")
        return {
            "model": self.model,
            "contents": [types.Part.from_bytes(data=clean, mime_type=mime_type), PROMPT],
            "config": types.GenerateContentConfig(**config),
        }

    def analyze(self, image: bytes, mime_type: str, sha256: str | None = None) -> ClothingTags:
        """Analyze one image once. ``sha256`` is unused (the API never sees it)."""
        try:
            request = self.build_request(image, mime_type)
        except Exception as exc:  # never send unstripped bytes
            logger.warning("Gemini 요청용 이미지 전처리 실패: %s", type(exc).__name__)
            raise VisionError("api_error", "이미지 메타데이터를 제거하지 못했습니다.") from None

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="gemini-vision")
        try:
            future = executor.submit(self._client.models.generate_content, **request)
            response = future.result(timeout=self._timeout)
        except Exception as exc:
            if _is_timeout(exc):
                logger.warning("Gemini 분석 시간 초과 (%.0f초)", self._timeout)
                raise VisionError("timeout", f"Gemini 응답이 {self._timeout:.0f}초 안에 오지 않았습니다.") from None
            status = getattr(exc, "code", None)
            if isinstance(status, int) and not isinstance(status, bool):
                # HTTP error from Agent Platform (4xx incl. unsupported model,
                # or 5xx). Log only the model ID and status code (Req 3.10).
                logger.warning("Gemini API 오류: model=%s, status=%d", self.model, status)
                raise VisionError("api_error", f"Gemini API 오류 (HTTP {status})") from None
            # Transport or unexpected error: no status code; the class name
            # carries no request data, the message text is never logged.
            logger.warning("Gemini API 오류: model=%s, error=%s", self.model, type(exc).__name__)
            raise VisionError("api_error", f"Gemini API 오류 ({type(exc).__name__})") from None
        finally:
            # Do not wait for a hung call; the worker thread ends when the SDK
            # timeout fires.
            executor.shutdown(wait=False, cancel_futures=True)

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            logger.warning("Gemini 응답에 JSON 본문이 없습니다.")
            raise VisionError("schema_invalid", "Gemini 응답이 비어 있습니다.")
        try:
            return ClothingTags.model_validate_json(text)
        except ValidationError as exc:
            logger.warning("Gemini 응답 스키마 검증 실패 (%d건)", exc.error_count())
            raise VisionError("schema_invalid", "Gemini 응답이 의류 태그 스키마와 맞지 않습니다.") from None
