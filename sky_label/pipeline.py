"""Повний цикл обробки одного фото → словник результату (result.json)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from . import catalog as catalog_mod
from . import projection, sky, solve
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
    # «Звичне місце» користувача: береться, лише якщо в EXIF немає GPS
    fallback_lat: float | None = None
    fallback_lon: float | None = None
    fallback_elevation_m: float | None = None


def _resolve_location(ov: Overrides, meta, cfg: Config) -> dict:
    """Місце спостереження: вручну → EXIF → звичне місце → за замовчуванням сервера."""
    if ov.lat is not None and ov.lon is not None:
        lat, lon, elev, source = ov.lat, ov.lon, ov.elevation_m, "user"
    elif meta.gps_lat is not None:
        lat, lon, elev, source = meta.gps_lat, meta.gps_lon, meta.gps_elevation_m, "exif"
    elif ov.fallback_lat is not None and ov.fallback_lon is not None:
        lat, lon, elev, source = ov.fallback_lat, ov.fallback_lon, ov.fallback_elevation_m, "fallback"
    else:
        lat, lon, elev, source = cfg.default_lat, cfg.default_lon, cfg.default_elevation_m, "default"
    elev = cfg.default_elevation_m if elev is None else elev
    return {"lat": lat, "lon": lon, "elevation_m": elev, "source": source}


def _observation_warnings(obs: dict) -> list[str]:
    """Попередження, що залежать лише від часу й місця (перераховуються разом з ними)."""
    out = []
    if obs["time_utc"] is None:
        if obs["camera"].get("local_time_without_offset"):
            out.append("У EXIF є місцевий час без часового поясу; вкажіть час з поясом, "
                       "щоб порахувати планети й висоту над горизонтом.")
        else:
            out.append("Час зйомки невідомий: планети й висота над горизонтом не рахуються.")
    loc = obs["location"]
    if loc["source"] == "default" and obs["time_utc"] is not None:
        out.append(f"Місце зйомки невідоме (у EXIF немає GPS): висоти, азимути, горизонт і Місяць "
                   f"пораховано для місця за замовчуванням ({loc['lat']:.4f}°, {loc['lon']:.4f}°). "
                   "Вкажіть, де знято фото.")
    return out


def _decimal_year(t: datetime) -> float:
    start = datetime(t.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(t.year + 1, 1, 1, tzinfo=timezone.utc)
    return t.year + (t - start) / (end - start)


def warmup(cfg: Config) -> None:
    """Завантажити заздалегідь усе, що кешується: каталог, лінії, базу tetra3rs, ефемериду."""
    catalog_mod.load(cfg.catalog_dir)
    catalog_mod.load_lines(cfg.catalog_dir)
    if solve.tetra3_available(cfg.catalog_dir):
        solve.load_tetra3(str(cfg.catalog_dir / "tetra3.db"))
    sky.ephemeris()


@contextmanager
def _timed(timings: dict[str, float], key: str):
    t0 = time.perf_counter()
    yield
    timings[key] = time.perf_counter() - t0


def process(image_path: Path, preview_path: Path, cfg: Config, ov: Overrides | None = None) -> dict:
    ov = ov or Overrides()
    timings: dict[str, float] = {}

    with _timed(timings, "load_s"):
        im = load_image(image_path)
        Image.fromarray(im.preview).save(preview_path, "JPEG", quality=90)
    observation = _observation(ov, im.meta, cfg)
    with _timed(timings, "detect_s"):
        dets = detect(im.gray, rgb=im.preview if im.kind == "raster" else None)

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
        "warnings": list(observation["warnings"]),
        "timings": timings,
    }
    when = datetime.fromisoformat(observation["time_utc"]) if observation["time_utc"] else None
    epoch = _decimal_year(when) if when else catalog_mod.J2000_YEAR
    sol = _solve(dets, im, ov, cfg, epoch, result)
    if sol is None:
        return _finish(result)

    W, H = im.width, im.height
    cat = catalog_mod.load(cfg.catalog_dir)
    result["solved"] = True
    result["solution"] = solve.describe(sol, W, H)
    result["stars"] = _label_stars(sol, cat, epoch, dets, W, H)
    lines = catalog_mod.load_lines(cfg.catalog_dir)
    if lines is not None:
        # Лінії сузір'їв від часу й місця не залежать
        with _timed(timings, "constellations_s"):
            ra_all, dec_all = cat.positions_at(epoch)
            result["constellations"] = sky.constellation_lines(sol.wcs, W, H, ra_all, dec_all, lines)
    _apply_observation(result, sol.wcs, dets)
    return _finish(result)


def _observation(ov: Overrides, meta, cfg: Config) -> dict:
    """Час (вручну → EXIF) і місце спостереження, дані камери, попередження про них."""
    when, time_source = ov.time_utc, "user" if ov.time_utc else None
    if when is None and meta.time_utc is not None:
        when, time_source = meta.time_utc, "exif"
    obs = {
        "time_utc": when.isoformat() if when else None,
        "time_source": time_source,
        "location": _resolve_location(ov, meta, cfg),
        "camera": meta.to_dict(),
    }
    obs["warnings"] = _observation_warnings(obs)
    return obs


def _solve(dets: np.ndarray, im, ov: Overrides, cfg: Config, epoch: float, result: dict) -> solve.Solution | None:
    """Початковий розв'язок (tetra3rs → astrometry.net) і уточнення за каталогом.

    Якщо не вдалося, дописує пояснення в result["warnings"] і повертає None.
    """
    warnings, timings = result["warnings"], result["timings"]
    if len(dets) < 6:
        warnings.append("Знайдено замало зір для розв'язання.")
        return None
    W, H = im.width, im.height
    with _timed(timings, "solve_s"):
        fov_hint = ov.fov_deg or solve.fov_hint_deg(im.meta.focal_length_35mm, W, H)
        init = None
        if solve.tetra3_available(cfg.catalog_dir):
            init = solve.solve_tetra3(dets, W, H, cfg.catalog_dir, fov_hint)
        if init is None and solve.astrometry_available():
            init = solve.solve_astrometry(dets, W, H, fov_hint, cfg.astrometry_cpulimit_s)
    if init is None:
        warnings.append("Не вдалося визначити, куди дивилася камера.")
        return None
    with _timed(timings, "refine_s"):
        sol = solve.refine(init, dets, catalog_mod.load(cfg.catalog_dir), epoch, W, H)
    if sol is None:
        warnings.append(f"{init.solver} дав розв'язок, але його не підтвердили зорі каталогу.")
    return sol


def _label_stars(sol: solve.Solution, cat, epoch: float, dets: np.ndarray, W: int, H: int) -> list[dict]:
    """Зорі каталогу до LABEL_MAG_LIMIT у кадрі, з відповідною точкою кадру, якщо вона є."""
    bright = cat.mag <= LABEL_MAG_LIMIT
    ra_b, dec_b = cat.positions_at(epoch, bright)
    sel, sx, sy = projection.stars_in_frame(sol.wcs, W, H, ra_b, dec_b)
    idx = np.nonzero(bright)[0][sel]
    matched = dict(zip(sol.matched_cat.tolist(), range(len(sol.matched_cat))))
    nearby = _nearby_detections(sol, idx, sx, sy, dets)
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
        stars.append(item)
    stars.sort(key=lambda s: s["mag"])
    return stars


def _apply_observation(result: dict, wcs, dets: np.ndarray) -> None:
    """Усе, що залежить від часу й місця: висоти зір, тіла Сонячної системи, сітка горизонту.

    Положення зір на кадрі й лінії сузір'їв від місця не залежать, тож тут не чіпаються.
    """
    obs = result["observation"]
    loc = obs["location"]
    stars = result["stars"]
    for st in stars:
        for k in ("alt_deg", "alt_refracted_deg", "az_deg"):
            st.pop(k, None)
    result["bodies"], result["horizontal"] = [], None
    result["timings"].pop("sky_s", None)
    if obs["time_utc"] is None:
        return
    t0 = time.perf_counter()
    when = datetime.fromisoformat(obs["time_utc"])
    lat, lon, elev = loc["lat"], loc["lon"], loc["elevation_m"]
    W, H = result["image"]["width"], result["image"]["height"]
    if stars:
        alt, alt_r, az = sky.star_altaz(np.array([s["ra_deg"] for s in stars]),
                                        np.array([s["dec_deg"] for s in stars]), when, lat, lon, elev)
        for st, a, ar, z in zip(stars, alt, alt_r, az):
            st.update({"alt_deg": round(float(a), 3), "alt_refracted_deg": round(float(ar), 3),
                       "az_deg": round(float(z), 3)})
    result["bodies"] = sky.bodies(wcs, W, H, when, lat, lon, elev, dets)
    result["horizontal"] = sky.horizontal(wcs, W, H, when, lat, lon, elev)
    result["timings"]["sky_s"] = time.perf_counter() - t0


def reobserve(result: dict, cfg: Config, time_utc: datetime | None = None,
              lat: float | None = None, lon: float | None = None,
              elevation_m: float | None = None) -> dict:
    """Новий час і/або місце для вже розв'язаного кадру — без повторного розпізнавання.

    Розв'язок (WCS) береться з результату. Положення зір на кадрі рахувалися на епоху
    старого часу; власний рух за роки зсуває їх щонайбільше на кілька ″, тож їх не чіпаємо.
    """
    if not result.get("solved"):
        raise ValueError("Кадр не розв'язано: висоти й планети рахувати нема від чого")
    obs = result["observation"]
    if time_utc is not None:
        obs["time_utc"], obs["time_source"] = time_utc.astimezone(timezone.utc).isoformat(), "user"
    if lat is not None and lon is not None:
        elev = cfg.default_elevation_m if elevation_m is None else elevation_m
        obs["location"] = {"lat": lat, "lon": lon, "elevation_m": elev, "source": "user"}
    elif elevation_m is not None:
        obs["location"]["elevation_m"] = elevation_m

    # Старі попередження про час і місце замінюються новими (у старих результатах їх
    # не виділено окремо, тож упізнаємо за початком тексту)
    old = set(obs.get("warnings", []))
    legacy = ("Час зйомки невідомий", "У EXIF є місцевий час без часового поясу", "Місце зйомки невідоме")
    obs["warnings"] = _observation_warnings(obs)
    result["warnings"] = [w for w in result["warnings"]
                          if w not in old and not w.startswith(legacy)] + obs["warnings"]

    dets = np.array([d[:2] for d in result["detections"]], dtype=float).reshape(-1, 2)
    result["timings"] = dict(result["timings"])
    _apply_observation(result, projection.wcs_from_header(result["solution"]["wcs_header"]), dets)
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
    scale = projection.pixel_scale_deg(sol.wcs) * 3600
    rms_px = float(np.sqrt(np.mean(sol.residual_arcsec ** 2))) / scale
    radius = max(3.0, 5 * rms_px)
    d, n = cKDTree(dets[:, :2]).query(np.column_stack([sx, sy]), distance_upper_bound=radius)
    fit_set = set(sol.matched_cat.tolist())
    return {int(i): {"index": int(n[k]), "offset_arcsec": round(float(d[k] * scale), 1), "in_fit": False}
            for k, i in enumerate(idx) if int(i) not in fit_set and np.isfinite(d[k])}


def _finish(result: dict) -> dict:
    result["timings"] = {k: round(v, 3) for k, v in result["timings"].items()}
    return result
