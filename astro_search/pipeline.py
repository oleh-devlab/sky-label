"""Повний цикл обробки одного фото → словник результату (result.json)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from . import catalog as catalog_mod
from . import sky, solve
from .config import Config
from .ingest import load_image
from .stars import detect

LABEL_MAG_LIMIT = 6.5
RESULT_VERSION = 1


@dataclass
class Overrides:
    """Те, що користувач передав разом із фото; має пріоритет над EXIF."""
    time_utc: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    elevation_m: float | None = None
    fov_deg: float | None = None


def _decimal_year(t: datetime) -> float:
    start = datetime(t.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(t.year + 1, 1, 1, tzinfo=timezone.utc)
    return t.year + (t - start) / (end - start)


def process(image_path: Path, preview_path: Path, cfg: Config, ov: Overrides | None = None) -> dict:
    ov = ov or Overrides()
    timings: dict[str, float] = {}
    warnings: list[str] = []

    t0 = time.perf_counter()
    im = load_image(image_path)
    Image.fromarray(im.preview).save(preview_path, "JPEG", quality=90)
    timings["load_s"] = time.perf_counter() - t0

    # --- час і місце
    when, time_source = ov.time_utc, "user" if ov.time_utc else None
    if when is None and im.meta.time_utc is not None:
        when, time_source = im.meta.time_utc, "exif"
    if when is None:
        if im.meta.local_time_without_offset:
            warnings.append("У EXIF є місцевий час без часового поясу; передайте час з поясом, "
                            "щоб порахувати планети й висоту над горизонтом.")
        else:
            warnings.append("Час зйомки невідомий: планети й висота над горизонтом не рахуються.")

    if ov.lat is not None and ov.lon is not None:
        lat, lon, elev, loc_source = ov.lat, ov.lon, ov.elevation_m, "user"
    elif im.meta.gps_lat is not None:
        lat, lon, elev, loc_source = im.meta.gps_lat, im.meta.gps_lon, im.meta.gps_elevation_m, "exif"
    else:
        lat, lon, elev, loc_source = cfg.default_lat, cfg.default_lon, cfg.default_elevation_m, "default"
    elev = cfg.default_elevation_m if elev is None else elev

    observation = {
        "time_utc": when.isoformat() if when else None,
        "time_source": time_source,
        "location": {"lat": lat, "lon": lon, "elevation_m": elev, "source": loc_source},
        "camera": im.meta.to_dict(),
    }

    # --- зорі на кадрі
    t0 = time.perf_counter()
    dets = detect(im.gray, rgb=im.preview if im.kind == "raster" else None)
    timings["detect_s"] = time.perf_counter() - t0

    result = {
        "version": RESULT_VERSION,
        "image": {"width": im.width, "height": im.height, "kind": im.kind},
        "observation": observation,
        "solved": False,
        "solution": None,
        "stars": [],
        "bodies": [],
        "horizontal": None,
        "constellations": [],
        "detections": [[round(float(x), 2), round(float(y), 2), float(f)] for x, y, f, _ in dets],
        "warnings": warnings,
        "timings": timings,
    }
    if len(dets) < 6:
        warnings.append("Знайдено замало зір для розв'язання.")
        return _finish(result)

    # --- plate solving
    t0 = time.perf_counter()
    fov_hint = ov.fov_deg or solve.fov_hint_deg(im.meta.focal_length_35mm, im.width, im.height)
    init = None
    if solve.tetra3_available(cfg.catalog_dir):
        init = solve.solve_tetra3(dets, im.width, im.height, cfg.catalog_dir, fov_hint)
    if init is None and solve.astrometry_available():
        init = solve.solve_astrometry(dets, im.width, im.height, fov_hint, cfg.astrometry_cpulimit_s)
    timings["solve_s"] = time.perf_counter() - t0
    if init is None:
        warnings.append("Не вдалося визначити, куди дивилася камера.")
        return _finish(result)

    t0 = time.perf_counter()
    cat = catalog_mod.load(cfg.catalog_dir)
    epoch = _decimal_year(when) if when else catalog_mod.J2000_YEAR
    sol = solve.refine(init, dets, cat, epoch, im.width, im.height)
    timings["refine_s"] = time.perf_counter() - t0
    if sol is None:
        warnings.append(f"{init.solver} дав розв'язок, але його не підтвердили зорі каталогу.")
        return _finish(result)

    result["solved"] = True
    result["solution"] = solve.describe(sol, im.width, im.height)

    # --- підписи зір
    bright = cat.mag <= LABEL_MAG_LIMIT
    ra_b, dec_b = cat.positions_at(epoch, bright)
    sel, sx, sy = solve.stars_in_frame(sol.wcs, im.width, im.height, ra_b, dec_b)
    idx = np.nonzero(bright)[0][sel]
    matched = dict(zip(sol.matched_cat.tolist(), range(len(sol.matched_cat))))
    nearby = _nearby_detections(sol, idx, sx, sy, dets)
    if when is not None and len(idx):
        alt, alt_r, az = sky.star_altaz(ra_b[sel], dec_b[sel], when, lat, lon, elev)
    stars = []
    for k, i in enumerate(idx):
        item = cat.describe(int(i))
        item.update({
            "x": round(float(sx[k]), 2), "y": round(float(sy[k]), 2),
            "ra_deg": round(float(ra_b[sel][k]), 5), "dec_deg": round(float(dec_b[sel][k]), 5),
            "detection": None,
        })
        m = matched.get(int(i))
        if m is not None:
            j = int(sol.matched_det[m])
            item["detection"] = {"index": j, "offset_arcsec": round(float(sol.residual_arcsec[m]), 1),
                                 "in_fit": True}
        elif int(i) in nearby:
            item["detection"] = nearby[int(i)]
        if when is not None:
            item.update({"alt_deg": round(float(alt[k]), 3), "alt_refracted_deg": round(float(alt_r[k]), 3),
                         "az_deg": round(float(az[k]), 3)})
        stars.append(item)
    stars.sort(key=lambda s: s["mag"])
    result["stars"] = stars

    # --- лінії сузір'їв (від часу не залежать)
    lines = catalog_mod.load_lines(cfg.catalog_dir)
    if lines is not None:
        t0 = time.perf_counter()
        ra_all, dec_all = cat.positions_at(epoch)
        result["constellations"] = sky.constellation_lines(sol.wcs, im.width, im.height, ra_all, dec_all, lines)
        timings["constellations_s"] = time.perf_counter() - t0

    # --- планети, Місяць, горизонт
    if when is not None:
        t0 = time.perf_counter()
        result["bodies"] = sky.bodies(sol.wcs, im.width, im.height, when, lat, lon, elev, dets)
        result["horizontal"] = sky.horizontal(sol.wcs, im.width, im.height, when, lat, lon, elev)
        timings["sky_s"] = time.perf_counter() - t0
    return _finish(result)


def _nearby_detections(sol, idx, sx, sy, dets) -> dict[int, dict]:
    """Точки кадру біля зір, що не ввійшли в підгонку.

    Підгонка бере лише надійні пари й відкидає викиди. Найяскравіші зорі на фото
    телефона часто пересвічені, і їхній центр зсунутий на 2–3 px: для моделі це
    викид, але сама зоря на кадрі є, і показувати її «не знайденою» неправильно.
    Тісні подвійні зливаються в одну пляму, тож одна точка може належати кільком
    зорям каталогу. Випадковий збіг у межах кількох пікселів малоймовірний
    (сотні точок на мільйони пікселів).
    """
    from scipy.spatial import cKDTree

    if len(dets) == 0 or len(idx) == 0:
        return {}
    scale = float(np.mean(solve.proj_plane_pixel_scales(sol.wcs.celestial))) * 3600
    rms_px = float(np.sqrt(np.mean(sol.residual_arcsec ** 2))) / scale
    radius = max(3.0, 5 * rms_px)
    d, n = cKDTree(dets[:, :2]).query(np.column_stack([sx, sy]), distance_upper_bound=radius)
    fit_set = set(sol.matched_cat.tolist())
    return {int(i): {"index": int(n[k]), "offset_arcsec": round(float(d[k] * scale), 1), "in_fit": False}
            for k, i in enumerate(idx) if int(i) not in fit_set and np.isfinite(d[k])}


def _finish(result: dict) -> dict:
    result["timings"] = {k: round(v, 3) for k, v in result["timings"].items()}
    return result
