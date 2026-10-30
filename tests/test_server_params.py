import pytest

from astro_search.server import BadRequest, _parse_params


def test_time_is_converted_to_utc():
    assert _parse_params({"time": "2025-04-19T21:54:31+03:00"}) == {"time": "2025-04-19T18:54:31+00:00"}


@pytest.mark.parametrize("fields, message", [
    ({"time": "2025-04-19T21:54:31"}, "часовий пояс"),
    ({"time": "вчора"}, "ISO 8601"),
    ({"lat": "50"}, "разом"),
    ({"lat": "91", "lon": "0"}, "межах"),
    ({"lat": "x", "lon": "0"}, "число"),
    ({"fov_deg": "0"}, "1…180"),
    ({"fallback_lon": "30"}, "разом"),
])
def test_invalid_fields(fields, message):
    with pytest.raises(BadRequest, match=message):
        _parse_params(fields)


def test_fallback_place():
    assert _parse_params({"fallback_lat": "48.9", "fallback_lon": "24.7", "fallback_elevation_m": "250"}) == {
        "fallback_lat": 48.9, "fallback_lon": 24.7, "fallback_elevation_m": 250.0}
