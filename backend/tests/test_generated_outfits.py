"""The bundled supplement works without the separately distributed personal album."""

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import resolve_config
from app.main import create_app
from app.recommendation.rule_based_v1 import temperature_diff, to_decimal

DEMO = Path(__file__).resolve().parents[2] / "data" / "demo"


def test_bundled_outfits_support_all_three_temperature_bands(tmp_path: Path) -> None:
    catalog = json.loads((DEMO / "generated_outfits.json").read_text(encoding="utf-8"))["photos"]
    assert len(catalog) == 15
    photos = tmp_path / "photos"
    photos.mkdir()
    for entry in catalog:
        image = DEMO / "photos" / entry["file"]
        assert hashlib.sha256(image.read_bytes()).hexdigest() == entry["sha256"]
        shutil.copy2(image, photos / image.name)
        sidecar = image.with_suffix(".metadata.json")
        shutil.copy2(sidecar, photos / sidecar.name)
    config, issues = resolve_config({
        "DATABASE_URL": f"sqlite:///{tmp_path / 'generated.db'}", "PHOTO_DIR": str(photos),
    })
    assert not issues
    with TestClient(create_app(config)) as client:
        report = client.post("/api/demo/scan").json()
        assert report["succeeded"] == 15
        assert report["uncertain"] == report["failed"] == report["skipped_non_outfit"] == 0
        # The scenario temperatures come from scripts/select_scenarios.py (rules S1~S4 on
        # all 189 photos). On the bundle alone the results follow rule_based_v1:
        # candidates within 3.0, else within 5.0 (fallback), else insufficient data.
        temps = {entry["sha256"]: to_decimal(entry["weather"]["actual_temperature_c"]) for entry in catalog}
        votes = {
            entry["sha256"]: json.loads((DEMO / "photos" / Path(entry["file"]).with_suffix(".metadata.json").name)
                                        .read_text(encoding="utf-8")).get("thermal_vote")
            for entry in catalog
        }
        scenarios = json.loads((DEMO / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
        for scenario in scenarios:
            today = to_decimal(scenario["actual_temperature_c"])
            diffs = {sha: temperature_diff(t, today) for sha, t in temps.items()}
            within = {sha for sha, d in diffs.items() if d <= Decimal("3.0")}
            fallback = not within
            expected = within or {sha for sha, d in diffs.items() if d <= Decimal("5.0")}
            assert len(expected) <= 5, scenario["id"]
            assert client.put("/api/demo/scenarios/selected", json={"scenario_id": scenario["id"]}).status_code == 200
            result = client.get("/api/recommendations/today").json()
            assert result["status"] == ("ok" if expected else "insufficient_data"), scenario["id"]
            assert result["fallback"] is (fallback or not expected)
            served = set()
            for item in result["items"]:
                assert item["outfit"]["metadata_synthetic"] is True
                photo = client.get(item["outfit"]["photo_url"])
                assert photo.status_code == 200
                digest = hashlib.sha256(photo.content).hexdigest()
                assert item["outfit"]["effective_vote"] == votes[digest]  # seed votes from the sidecars (V3/V4)
                served.add(digest)
            assert served == expected, scenario["id"]
        again = client.post("/api/demo/scan").json()
        assert again["duplicate"] == 15 and again["succeeded"] == 0
