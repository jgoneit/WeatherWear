"""The bundled supplement works without the separately distributed personal album."""

import hashlib
import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import resolve_config
from app.main import create_app

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
        for scenario in ("cold-snap", "early-heat", "heatwave"):
            expected = {entry["sha256"] for entry in catalog if entry["scenario_id"] == scenario}
            assert len(expected) == 5
            assert client.put("/api/demo/scenarios/selected", json={"scenario_id": scenario}).status_code == 200
            result = client.get("/api/recommendations/today").json()
            assert result["status"] == "ok" and result["fallback"] is False
            assert len(result["items"]) == 5
            served = set()
            for item in result["items"]:
                assert item["temperature_diff_c"] <= 3.0
                assert item["outfit"]["metadata_synthetic"] is True
                assert item["outfit"]["effective_vote"] is None
                photo = client.get(item["outfit"]["photo_url"])
                assert photo.status_code == 200
                served.add(hashlib.sha256(photo.content).hexdigest())
            assert served == expected
        again = client.post("/api/demo/scan").json()
        assert again["duplicate"] == 15 and again["succeeded"] == 0
