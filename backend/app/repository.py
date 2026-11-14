"""OutfitRepository: the only module that reads and writes the SQLite tables
(design.md §7).

- ``create_outfit_bundle`` stores Outfit + Weather_Snapshot + an optional
  ``demo_seed`` feedback in one transaction; any exception rolls the whole
  bundle back and is re-raised unchanged (Req 1.10, 2.6).
- ``captured_at`` keeps the sidecar's original UTC offset; ``captured_at_utc``
  and feedback ``created_at`` are fixed-width UTC text used for ordering (Req 2.2).
- Feedback is insert-only. There is no update or delete path (Req 9.5).
- Effective_Vote is computed by ``app.feedback.effective_vote`` (Req 9.4).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel
from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.db import (
    Feedback,
    Outfit,
    PhotoAnalysisRow,
    WeatherSnapshot,
    from_iso,
    session_scope,
    to_iso,
    to_utc_iso,
)
from app.domain import (
    ClothingTags,
    FeedbackSource,
    OutfitWithWeatherAndFeedback,
    ThermalVote,
    VisionAnalysis,
    VisionProviderName,
    WeatherReading,
)
from app.feedback import effective_vote

OUTFIT_SOURCE: Literal["demo_sidecar"] = "demo_sidecar"


class OutfitCreate(BaseModel):
    """Values the ingestion service passes in for one new Outfit."""

    sha256: str
    file_name: str  # file name relative to PHOTO_DIR, never an absolute path
    captured_at: AwareDatetime
    latitude: float
    longitude: float
    tags: ClothingTags
    vision_provider: VisionProviderName
    photo_type_confidence: float = 1.0
    photo_type_reason: str = "Accepted as an outfit by the caller."


class FeedbackRecord(BaseModel):
    id: int
    outfit_id: int
    thermal_vote: ThermalVote
    source: FeedbackSource
    created_at: AwareDatetime  # UTC


class OutfitRecord(BaseModel):
    """One Outfit row with its Weather_Snapshot and Effective_Vote (None if absent)."""

    id: int
    sha256: str
    file_name: str
    captured_at: AwareDatetime  # original offset
    latitude: float
    longitude: float
    source: Literal["demo_sidecar"]
    tags: ClothingTags
    vision_provider: VisionProviderName
    created_at: AwareDatetime
    weather: WeatherReading | None
    effective_vote: ThermalVote | None


class StoredPhotoAnalysis(BaseModel):
    sha256: str
    file_name: str
    analysis: VisionAnalysis
    vision_provider: VisionProviderName
    created_at: AwareDatetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stored_photo_analysis(row: PhotoAnalysisRow) -> StoredPhotoAnalysis:
    return StoredPhotoAnalysis(
        sha256=row.sha256,
        file_name=row.file_name,
        analysis=VisionAnalysis.model_validate_json(row.analysis_json),
        vision_provider=row.vision_provider,
        created_at=from_iso(row.created_at),
    )


def _feedback_record(row: Feedback) -> FeedbackRecord:
    return FeedbackRecord(
        id=row.id,
        outfit_id=row.outfit_id,
        thermal_vote=row.thermal_vote,
        source=row.source,
        created_at=from_iso(row.created_at),
    )


def _weather_reading(row: WeatherSnapshot) -> WeatherReading:
    return WeatherReading(
        observed_at=from_iso(row.observed_at),
        actual_temperature_c=row.actual_temperature_c,
        apparent_temperature_c=row.apparent_temperature_c,
        relative_humidity_pct=row.relative_humidity_pct,
        wind_speed_kmh=row.wind_speed_kmh,
        precipitation_mm=row.precipitation_mm,
        weather_code=row.weather_code,
        provider=row.provider,
        is_fixture=bool(row.is_fixture),
    )


def _tags(row: Outfit) -> ClothingTags:
    return ClothingTags(
        upper_type=row.upper_type,
        outer_type=row.outer_type,
        lower_type=row.lower_type,
        colors=json.loads(row.colors_json),
        layers_visible=row.layers_visible,
        confidence=row.vision_confidence,
    )


def _outfit_record(
    row: Outfit, snapshot: WeatherSnapshot | None, feedback: list[Feedback]
) -> OutfitRecord:
    return OutfitRecord(
        id=row.id,
        sha256=row.sha256,
        file_name=row.file_name,
        captured_at=from_iso(row.captured_at),
        latitude=row.latitude,
        longitude=row.longitude,
        source=row.source,
        tags=_tags(row),
        vision_provider=row.vision_provider,
        created_at=from_iso(row.created_at),
        weather=None if snapshot is None else _weather_reading(snapshot),
        effective_vote=effective_vote(_feedback_record(f) for f in feedback),
    )


class OutfitRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # -- writes ---------------------------------------------------------------

    def exists_sha(self, sha256: str) -> bool:
        with Session(self._engine) as session:
            stmt = select(Outfit.id).where(Outfit.sha256 == sha256).limit(1)
            return session.exec(stmt).first() is not None

    def save_photo_analysis(
        self,
        sha256: str,
        file_name: str,
        analysis: VisionAnalysis,
        vision_provider: VisionProviderName,
    ) -> StoredPhotoAnalysis:
        """Insert or replace the latest classification for one image hash."""
        now = to_utc_iso(_utc_now())
        with session_scope(self._engine) as session:
            row = session.exec(
                select(PhotoAnalysisRow).where(PhotoAnalysisRow.sha256 == sha256)
            ).first()
            if row is None:
                row = PhotoAnalysisRow(
                    sha256=sha256,
                    file_name=file_name,
                    photo_type=analysis.photo_type,
                    analysis_json=analysis.model_dump_json(),
                    vision_provider=vision_provider,
                    created_at=now,
                )
                session.add(row)
            else:
                row.file_name = file_name
                row.photo_type = analysis.photo_type
                row.analysis_json = analysis.model_dump_json()
                row.vision_provider = vision_provider
                row.created_at = now
                session.add(row)
            session.flush()
            return _stored_photo_analysis(row)

    def update_existing_outfit_analysis(
        self,
        sha256: str,
        analysis: VisionAnalysis,
        vision_provider: VisionProviderName,
    ) -> bool:
        """Refresh tags on a legacy Outfit after it is reclassified as outfit."""
        if analysis.photo_type != "outfit":
            raise ValueError("only outfit analysis can update an Outfit")
        with session_scope(self._engine) as session:
            row = session.exec(select(Outfit).where(Outfit.sha256 == sha256)).first()
            if row is None:
                return False
            tags = analysis.tags
            row.upper_type = tags.upper_type
            row.outer_type = tags.outer_type
            row.lower_type = tags.lower_type
            row.colors_json = json.dumps(tags.colors, ensure_ascii=False)
            row.layers_visible = tags.layers_visible
            row.vision_confidence = tags.confidence
            row.vision_provider = vision_provider
            session.add(row)
            session.flush()
            return True

    def create_outfit_bundle(
        self, outfit: OutfitCreate, weather: WeatherReading, seed_vote: ThermalVote | None
    ) -> int:
        """Insert Outfit + Weather_Snapshot (+ ``demo_seed`` feedback) atomically.

        Returns the new Outfit id. On any exception the transaction is rolled
        back, nothing is stored, and the exception propagates (Req 1.10, 2.6).
        """
        now = to_utc_iso(_utc_now())
        tags = outfit.tags
        analysis = VisionAnalysis(
            photo_type="outfit",
            photo_type_confidence=outfit.photo_type_confidence,
            reason=outfit.photo_type_reason,
            tags=tags,
        )
        with session_scope(self._engine) as session:
            stored_analysis = session.exec(
                select(PhotoAnalysisRow).where(PhotoAnalysisRow.sha256 == outfit.sha256)
            ).first()
            if stored_analysis is None:
                stored_analysis = PhotoAnalysisRow(
                    sha256=outfit.sha256,
                    file_name=outfit.file_name,
                    photo_type="outfit",
                    analysis_json=analysis.model_dump_json(),
                    vision_provider=outfit.vision_provider,
                    created_at=now,
                )
            else:
                stored_analysis.file_name = outfit.file_name
                stored_analysis.photo_type = "outfit"
                stored_analysis.analysis_json = analysis.model_dump_json()
                stored_analysis.vision_provider = outfit.vision_provider
                stored_analysis.created_at = now
            session.add(stored_analysis)

            row = Outfit(
                sha256=outfit.sha256,
                file_name=outfit.file_name,
                captured_at=to_iso(outfit.captured_at),
                captured_at_utc=to_utc_iso(outfit.captured_at),
                latitude=outfit.latitude,
                longitude=outfit.longitude,
                source=OUTFIT_SOURCE,
                upper_type=tags.upper_type,
                outer_type=tags.outer_type,
                lower_type=tags.lower_type,
                colors_json=json.dumps(tags.colors, ensure_ascii=False),
                layers_visible=tags.layers_visible,
                vision_confidence=tags.confidence,
                vision_provider=outfit.vision_provider,
                created_at=now,
            )
            session.add(row)
            session.flush()
            outfit_id = row.id
            session.add(
                WeatherSnapshot(
                    outfit_id=outfit_id,
                    observed_at=to_iso(weather.observed_at),
                    actual_temperature_c=weather.actual_temperature_c,
                    apparent_temperature_c=weather.apparent_temperature_c,
                    relative_humidity_pct=weather.relative_humidity_pct,
                    wind_speed_kmh=weather.wind_speed_kmh,
                    precipitation_mm=weather.precipitation_mm,
                    weather_code=weather.weather_code,
                    provider=weather.provider,
                    is_fixture=weather.is_fixture,
                    created_at=now,
                )
            )
            if seed_vote is not None:
                session.add(
                    Feedback(
                        outfit_id=outfit_id,
                        thermal_vote=seed_vote,
                        source="demo_seed",
                        created_at=now,
                    )
                )
            session.flush()
        return outfit_id

    def add_feedback(
        self, outfit_id: int, vote: ThermalVote, source: FeedbackSource, created_at: datetime
    ) -> FeedbackRecord:
        """INSERT one feedback row. Existing rows are never changed (Req 9.5).

        A missing Outfit raises the database's foreign-key ``IntegrityError``;
        the transaction is rolled back.
        """
        with session_scope(self._engine) as session:
            row = Feedback(
                outfit_id=outfit_id,
                thermal_vote=vote,
                source=source,
                created_at=to_utc_iso(created_at),
            )
            session.add(row)
            session.flush()
            return _feedback_record(row)

    # -- reads ----------------------------------------------------------------

    def get_photo_analysis(self, sha256: str) -> StoredPhotoAnalysis | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(PhotoAnalysisRow).where(PhotoAnalysisRow.sha256 == sha256)
            ).first()
            return None if row is None else _stored_photo_analysis(row)

    def get_outfit(self, outfit_id: int) -> OutfitRecord | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(Outfit)
                .join(PhotoAnalysisRow, PhotoAnalysisRow.sha256 == Outfit.sha256)
                .where(Outfit.id == outfit_id, PhotoAnalysisRow.photo_type == "outfit")
            ).first()
            if row is None:
                return None
            snapshot = session.exec(
                select(WeatherSnapshot).where(WeatherSnapshot.outfit_id == outfit_id)
            ).first()
            feedback = list(session.exec(select(Feedback).where(Feedback.outfit_id == outfit_id)))
            return _outfit_record(row, snapshot, feedback)

    def list_outfits(self) -> list[OutfitRecord]:
        """All Outfits ordered by ``captured_at_utc DESC, id DESC`` (Req 10.1)."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(Outfit)
                .join(PhotoAnalysisRow, PhotoAnalysisRow.sha256 == Outfit.sha256)
                .where(PhotoAnalysisRow.photo_type == "outfit")
                .order_by(col(Outfit.captured_at_utc).desc(), col(Outfit.id).desc())
            ).all()
            snapshots = {s.outfit_id: s for s in session.exec(select(WeatherSnapshot))}
            feedback: dict[int, list[Feedback]] = defaultdict(list)
            for f in session.exec(select(Feedback)):
                feedback[f.outfit_id].append(f)
            return [_outfit_record(r, snapshots.get(r.id), feedback[r.id]) for r in rows]

    def list_candidates(self) -> list[OutfitWithWeatherAndFeedback]:
        """Recommendation input for every Outfit (weather may be None)."""
        return [
            OutfitWithWeatherAndFeedback(
                outfit_id=r.id,
                captured_at=r.captured_at,
                tags=r.tags,
                weather=r.weather,
                effective_vote=r.effective_vote,
            )
            for r in self.list_outfits()
        ]

    def counts(self) -> dict[str, int]:
        """Row counts per table: ``outfits``, ``weather_snapshots``, ``feedback``."""
        with Session(self._engine) as session:
            return {
                "outfits": session.exec(
                    select(func.count())
                    .select_from(Outfit)
                    .join(PhotoAnalysisRow, PhotoAnalysisRow.sha256 == Outfit.sha256)
                    .where(PhotoAnalysisRow.photo_type == "outfit")
                ).one(),
                "weather_snapshots": session.exec(
                    select(func.count()).select_from(WeatherSnapshot)
                ).one(),
                "feedback": session.exec(select(func.count()).select_from(Feedback)).one(),
            }
