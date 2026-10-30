"""Планети, Місяць і горизонтальна система координат (висота й азимут).

Ефемерида de421 (JPL) береться з пакета skyfield-data, тож інтернет не потрібен.
Вона покриває 1900–2050 роки.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

import numpy as np
from astropy.wcs import WCS
from scipy.spatial import cKDTree

from .projection import clip_polyline, in_frame, pixel_scale_deg, project, unit_vectors

BODIES = [
    ("Mercury", "mercury"),
    ("Venus", "venus"),
    ("Mars", "mars barycenter"),
    ("Jupiter", "jupiter barycenter"),
    ("Saturn", "saturn barycenter"),
    ("Uranus", "uranus barycenter"),
    ("Neptune", "neptune barycenter"),
    ("Moon", "moon"),
    ("Sun", "sun"),
]

# Стандартна атмосфера для оцінки рефракції
REFRACTION = {"temperature_C": 10.0, "pressure_mbar": 1010.0}


@lru_cache(maxsize=1)
def ephemeris():
    """Шкала часу й ефемерида de421 (кешуються на весь процес)."""
    import warnings

    from skyfield.api import Loader

    # skyfield-data попереджає, що в пакеті застарів finals2000A.all
    # (таблиця обертання Землі). Ми його не використовуємо: timescale(builtin=True)
    # бере вбудовані в skyfield таблиці, а з пакета потрібна лише ефемерида de421.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        from skyfield_data import get_skyfield_data_path

        path = get_skyfield_data_path()
    load = Loader(path, expire=False)
    return load.timescale(builtin=True), load("de421.bsp")


def _observer(lat: float, lon: float, elevation_m: float):
    from skyfield.api import wgs84

    _, eph = ephemeris()
    return eph["earth"] + wgs84.latlon(lat, lon, elevation_m=elevation_m)


def bodies(wcs: WCS, width: int, height: int, when: datetime, lat: float, lon: float,
           elevation_m: float, dets: np.ndarray) -> list[dict]:
    """Тіла Сонячної системи, що потрапляють у кадр.

    Положення астрометричні (як у каталозі зір) і топоцентричні — з точки
    спостереження, що важливо для Місяця (паралакс до ~1°).
    Для кожного тіла вказано найближчу виявлену на кадрі точку і відхилення
    від неї: це пряма перевірка точності розв'язку.
    """
    from skyfield.magnitudelib import planetary_magnitude

    ts, eph = ephemeris()
    t = ts.from_datetime(when)
    obs = _observer(lat, lon, elevation_m).at(t)
    tree = cKDTree(dets[:, :2]) if len(dets) else None
    scale = pixel_scale_deg(wcs) * 3600
    out = []
    for name, key in BODIES:
        astrometric = obs.observe(eph[key])
        ra, dec, dist = astrometric.radec()
        xs, ys = project(wcs, width, height, ra._degrees, dec.degrees)
        if not in_frame(xs, ys, width, height)[0]:
            continue
        x, y = float(xs[0]), float(ys[0])
        alt, az, _ = astrometric.apparent().altaz()
        alt_app, _, _ = astrometric.apparent().altaz(**REFRACTION)
        item = {
            "name": name,
            "x": round(float(x), 2), "y": round(float(y), 2),
            "ra_deg": round(float(ra._degrees), 5), "dec_deg": round(float(dec.degrees), 5),
            "distance_au": round(float(dist.au), 5),
            "alt_deg": round(float(alt.degrees), 3),
            "alt_refracted_deg": round(float(alt_app.degrees), 3),
            "az_deg": round(float(az.degrees), 3),
            "mag": None,
            "nearest_detection": None,
        }
        if name not in ("Moon", "Sun"):
            try:
                item["mag"] = round(float(planetary_magnitude(astrometric)), 2)
            except Exception:
                pass
        if tree is not None:
            dpx, j = tree.query([x, y])
            radius_px = max(5.0, 600.0 / scale)  # пошук у межах ~10′
            if dpx <= radius_px:
                item["nearest_detection"] = {
                    "index": int(j),
                    "x": round(float(dets[j, 0]), 2), "y": round(float(dets[j, 1]), 2),
                    "offset_px": round(float(dpx), 2),
                    "offset_arcsec": round(float(dpx * scale), 1),
                }
        out.append(item)
    return out


def star_altaz(ra_deg: np.ndarray, dec_deg: np.ndarray, when: datetime, lat: float, lon: float,
               elevation_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Висота (без рефракції та з нею) і азимут для масиву зір."""
    from skyfield.api import Star

    ts, _ = ephemeris()
    t = ts.from_datetime(when)
    app = _observer(lat, lon, elevation_m).at(t).observe(
        Star(ra_hours=np.asarray(ra_deg) / 15.0, dec_degrees=np.asarray(dec_deg))).apparent()
    alt, az, _ = app.altaz()
    alt_r, _, _ = app.altaz(**REFRACTION)
    return alt.degrees, alt_r.degrees, az.degrees


def horizontal(wcs: WCS, width: int, height: int, when: datetime, lat: float, lon: float,
               elevation_m: float) -> dict:
    """Куди дивився телефон і сітка висоти/азимута в піксельних координатах.

    Висоти геометричні (без рефракції); горизонт — висота 0°.
    """
    ts, _ = ephemeris()
    t = ts.from_datetime(when)
    obs = _observer(lat, lon, elevation_m).at(t)

    def altaz_to_pix(alt, az):
        pos = obs.from_altaz(alt_degrees=np.asarray(alt, float), az_degrees=np.asarray(az, float))
        ra, dec, _ = pos.radec()
        return project(wcs, width, height, ra._degrees, dec.degrees)

    def pix_to_altaz(px, py):
        ra, dec = wcs.all_pix2world([px], [py], 0)
        alt, alt_r, az = star_altaz(ra, dec, when, lat, lon, elevation_m)
        return float(alt[0]), float(alt_r[0]), float(az[0])

    c_alt, c_alt_r, c_az = pix_to_altaz(width / 2, height / 2)
    # Куди на кадрі вказує зеніт: кут від напрямку «вгору» за годинниковою стрілкою
    zx, zy = altaz_to_pix(min(c_alt + 1.0, 89.9), c_az)
    if c_alt + 1.0 >= 89.9 or not (np.isfinite(zx[0]) and np.isfinite(zy[0])):
        zenith_angle = None
    else:
        zenith_angle = float(np.degrees(np.arctan2(zx[0] - width / 2, height / 2 - zy[0]))) % 360

    grid = []
    az_dense = np.arange(0, 360.5, 0.5)
    for alt in range(0, 90, 10):
        x, y = altaz_to_pix(np.full_like(az_dense, alt), az_dense)
        lines = clip_polyline(x, y, width, height)
        if lines:
            grid.append({"kind": "alt", "value": alt, "lines": lines})
    alt_dense = np.arange(-5, 89.6, 0.5)
    for az in range(0, 360, 15):
        x, y = altaz_to_pix(alt_dense, np.full_like(alt_dense, az))
        lines = clip_polyline(x, y, width, height)
        if lines:
            grid.append({"kind": "az", "value": az, "lines": lines})

    # Діапазон висот: сіткою по всьому кадру, а не лише за кутами — найвища точка
    # буває всередині (кадр захоплює зеніт), найнижча — посеред краю
    gx, gy = np.meshgrid(np.linspace(0, width - 1, 21), np.linspace(0, height - 1, 21))
    g_ra, g_dec = wcs.all_pix2world(gx.ravel(), gy.ravel(), 0)
    g_alt, _, _ = star_altaz(g_ra, g_dec, when, lat, lon, elevation_m)
    lo, hi = float(np.min(g_alt)), float(np.max(g_alt))
    zx, zy = altaz_to_pix([90.0], [0.0])
    if in_frame(zx, zy, width, height)[0]:
        hi = 90.0
    return {
        "center": {"alt_deg": round(c_alt, 3), "alt_refracted_deg": round(c_alt_r, 3),
                   "az_deg": round(c_az, 3)},
        "alt_range_deg": [round(lo, 2), round(hi, 2)],
        "zenith_direction_deg": None if zenith_angle is None else round(zenith_angle, 2),
        "grid": grid,
    }


def constellation_lines(wcs: WCS, width: int, height: int, ra: np.ndarray, dec: np.ndarray,
                        lines) -> list[dict]:
    """Лінії сузір'їв у піксельних координатах, обрізані краями кадру.

    ra, dec — координати всіх зір каталогу на епоху кадру; lines — catalog.ConstellationLines.
    Кожен відрізок іде дугою великого кола між двома зорями. У проєкції TAN така
    дуга — пряма, але дисторсія SIP її вигинає, тож дуга береться з кроком ~1°.
    Підпис сузір'я ставиться в середину його зір, що потрапили в кадр.
    """
    a_u = unit_vectors(ra[lines.a], dec[lines.a])
    b_u = unit_vectors(ra[lines.b], dec[lines.b])
    omega = np.arccos(np.clip(np.einsum("ij,ij->i", a_u, b_u), -1, 1))
    n = 24
    t = np.linspace(0, 1, n)[None, :, None]
    sin_o = np.sin(omega)[:, None, None]
    # Сферична інтерполяція (slerp) між кінцями кожного відрізка
    with np.errstate(invalid="ignore", divide="ignore"):
        pts = (np.sin((1 - t) * omega[:, None, None]) * a_u[:, None, :]
               + np.sin(t * omega[:, None, None]) * b_u[:, None, :]) / sin_o
    pts = pts.reshape(-1, 3)
    p_ra = np.degrees(np.arctan2(pts[:, 1], pts[:, 0])) % 360
    p_dec = np.degrees(np.arcsin(np.clip(pts[:, 2], -1, 1)))
    x, y = project(wcs, width, height, p_ra, p_dec)
    x, y = x.reshape(-1, n), y.reshape(-1, n)

    out: dict[str, dict] = {}
    for k, con in enumerate(lines.con.tolist()):
        segs = clip_polyline(x[k], y[k], width, height)
        if not segs:
            continue
        item = out.setdefault(con, {"id": con, "lines": [], "stars": []})
        item["lines"] += segs
        for end in (0, n - 1):
            if in_frame(x[k, end], y[k, end], width, height):
                item["stars"].append((float(x[k, end]), float(y[k, end])))
    result = []
    for item in out.values():
        pts_xy = item.pop("stars") or [p for seg in item["lines"] for p in seg]
        cx, cy = np.mean(pts_xy, axis=0)
        item["label"] = [round(float(cx), 1), round(float(cy), 1)]
        result.append(item)
    return result

