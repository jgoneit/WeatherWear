"""Fixture-only startup and deployment boundaries; no AWS API calls."""

import json
import runpy
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def test_public_demo_initializes_without_model_credentials(tmp_path, monkeypatch):
    demo = ROOT / "data" / "demo"
    photos = tmp_path / "photos"
    photos.mkdir()
    for entry in json.loads((demo / "generated_outfits.json").read_text())["photos"]:
        source = demo / "photos" / entry["file"]
        shutil.copy2(source, photos / source.name)
        sidecar = source.with_suffix(".metadata.json")
        shutil.copy2(sidecar, photos / sidecar.name)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'demo.db'}")
    monkeypatch.setenv("PHOTO_DIR", str(photos))
    monkeypatch.setenv("VISION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "ignored-for-public-demo")
    build = runpy.run_path(str(ROOT / "deploy/aws/serve_demo.py"))["build_demo_app"]
    for _ in range(2):
        with TestClient(build()) as client:
            meta = client.get("/api/meta").json()
            assert meta["vision_provider"] == meta["weather_provider"] == "fixture"
            assert len(client.get("/api/outfits").json()) == 15
            result = client.get("/api/recommendations/today").json()
            assert result["today"]["scenario_id"] == "early-heat"
            assert len(result["items"]) == 5


def test_cloud_template_pins_source_and_limits_ingress():
    render = runpy.run_path(str(ROOT / "deploy/aws/deploy.py"))["template"]
    revision = "a" * 40
    doc = render(revision)
    resources = doc["Resources"]
    ingress = resources["SecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert len(ingress) == 1
    assert ingress[0]["FromPort"] == ingress[0]["ToPort"] == 80
    assert "CidrIp" not in ingress[0] and "SourcePrefixListId" in ingress[0]
    user_data = resources["Instance"]["Properties"]["UserData"]["Fn::Base64"]["Fn::Sub"]
    assert revision in user_data and "git clone" not in user_data
    assert "cfn-signal" in user_data
    assert len(user_data.encode()) < 16384
