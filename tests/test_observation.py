from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from astro_search import pipeline
from astro_search.config import Config

CFG = Config(data_dir=Path("."), catalog_dir=Path("."), host="", port=0, max_upload_mb=1,
             default_lat=49.8397, default_lon=24.0297, default_elevation_m=300,
             astrometry_cpulimit_s=1, web_dir=Path("."))


def meta(gps=None):
    lat, lon, elev = gps or (None, None, None)
    return SimpleNamespace(gps_lat=lat, gps_lon=lon, gps_elevation_m=elev)


def test_location_priority_user_exif_fallback_default():
    user = pipeline.Overrides(lat=1, lon=2, fallback_lat=5, fallback_lon=6)
    fallback = pipeline.Overrides(fallback_lat=5, fallback_lon=6, fallback_elevation_m=70)
    assert pipeline._resolve_location(user, meta((3, 4, 10)), CFG)["source"] == "user"
    assert pipeline._resolve_location(fallback, meta((3, 4, 10)), CFG)["source"] == "exif"
    loc = pipeline._resolve_location(fallback, meta(), CFG)
    assert (loc["lat"], loc["lon"], loc["elevation_m"], loc["source"]) == (5, 6, 70, "fallback")
    loc = pipeline._resolve_location(pipeline.Overrides(), meta(), CFG)
    assert (loc["lat"], loc["elevation_m"], loc["source"]) == (49.8397, 300, "default")


def obs(time_utc, source, local=None):
    return {"time_utc": time_utc, "location": {"lat": 1.0, "lon": 2.0, "source": source},
            "camera": {"local_time_without_offset": local}}


def test_observation_warnings():
    t = datetime(2025, 4, 19, tzinfo=timezone.utc).isoformat()
    assert pipeline._observation_warnings(obs(t, "user")) == []
    assert pipeline._observation_warnings(obs(t, "default"))[0].startswith("Місце зйомки невідоме")
    assert pipeline._observation_warnings(obs(None, "default"))[0].startswith("Час зйомки невідомий")
    assert "без часового поясу" in pipeline._observation_warnings(obs(None, "exif", "2025:04:19 21:54:31"))[0]


def test_reobserve_needs_a_solved_frame():
    with pytest.raises(ValueError):
        pipeline.reobserve({"solved": False}, CFG, lat=1, lon=2)
