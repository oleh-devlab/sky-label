"""Plate solving: куди дивилася камера.

Два розв'язувачі дають лише початкове наближення (проєкція TAN):
- tetra3rs — швидкий (мілісекунди), пробується першим;
- astrometry.net (solve-field) — запасний, якщо tetra3rs не впорався.

Далі refine() уточнює розв'язок за власним каталогом (HYG):
зіставляє зорі з виявленими точками і підганяє TAN-SIP (з дисторсією),
тож точність і звіт однакові незалежно від того, який розв'язувач спрацював.
"""

from __future__ import annotations

import importlib.util
import math
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import fit_wcs_from_points, proj_plane_pixel_scales
import astropy.units as u
from scipy.spatial import cKDTree

from .catalog import StarCatalog

T3_CENTROIDS = 80
# Крок ~1,25×: tetra3rs знаходить розв'язок, якщо оцінка поля в межах приблизно −30…+30 %.
# Порядок — від типових для телефонів значень до крайніх.
FOV_SWEEP = [30.5, 38, 24.4, 47.7, 19.5, 59.6, 15.6, 74.5, 12.5, 90, 10, 8]


@dataclass
class InitialSolution:
    solver: str
    wcs: WCS
    solve_ms: float
    info: dict


# ------------------------------------------------------------ підказки


def fov_hint_deg(focal_35mm: float | None, width: int, height: int) -> float | None:
    """Горизонтальне поле зору з еквівалентної фокусної відстані.

    Еквівалент рахується за діагоналлю кадру 35 мм (43,27 мм).
    Якщо кадр обрізаний, підказка буде завищеною, тому вона лише перша в переборі.
    """
    if not focal_35mm or focal_35mm <= 0:
        return None
    half_diag = math.atan(43.27 / (2 * focal_35mm))
    diag_px = math.hypot(width, height)
    return math.degrees(2 * math.atan(math.tan(half_diag) * width / diag_px))


def _fov_candidates(hint: float | None) -> list[float]:
    out: list[float] = []
    if hint:
        out += [hint, hint * 0.8, hint * 1.25]
    for f in FOV_SWEEP:
        if all(abs(f / o - 1) > 0.12 for o in out):
            out.append(f)
    return out


# ------------------------------------------------------------ tetra3rs


@lru_cache(maxsize=1)
def _tetra3_db(path: str):
    import tetra3rs

    return tetra3rs.SolverDatabase.load_from_file(path)


def tetra3_available(catalog_dir: Path) -> bool:
    return (importlib.util.find_spec("tetra3rs") is not None
            and (Path(catalog_dir) / "tetra3.db").exists())


def _wcs_from_mapping(pix2world, width: int, height: int) -> WCS:
    """TAN-WCS з довільної функції піксель → небо (вибірка сіткою)."""
    xs, ys = np.meshgrid(np.linspace(0, width - 1, 9), np.linspace(0, height - 1, 7))
    xs, ys = xs.ravel(), ys.ravel()
    ra, dec = pix2world(xs, ys)
    ok = np.isfinite(ra) & np.isfinite(dec)
    sky = SkyCoord(np.asarray(ra)[ok] * u.deg, np.asarray(dec)[ok] * u.deg)
    return fit_wcs_from_points((xs[ok], ys[ok]), sky, proj_point="center", projection="TAN")


def solve_tetra3(dets: np.ndarray, width: int, height: int, catalog_dir: Path,
                 fov_hint: float | None) -> InitialSolution | None:
    db = _tetra3_db(str(Path(catalog_dir) / "tetra3.db"))
    d = dets[:T3_CENTROIDS]
    cents = np.column_stack([d[:, 0] - width / 2, d[:, 1] - height / 2, d[:, 2]])
    t0 = time.perf_counter()
    tried = []
    for fov in _fov_candidates(fov_hint):
        tried.append(round(fov, 1))
        r = db.solve_from_centroids(cents, fov_estimate_deg=fov, image_width=width,
                                    image_height=height, solve_timeout_ms=3000)
        if r:
            def p2w(x, y, r=r):
                return r.pixel_to_world(np.asarray(x) - width / 2, np.asarray(y) - height / 2)

            return InitialSolution(
                solver="tetra3rs",
                wcs=_wcs_from_mapping(p2w, width, height),
                solve_ms=(time.perf_counter() - t0) * 1000,
                info={"fov_tried_deg": tried, "matches": int(r.num_matches),
                      "rmse_arcsec": round(float(r.rmse_arcsec), 1),
                      "probability": float(r.probability)},
            )
    return None


# ------------------------------------------------------------ astrometry.net


def astrometry_available() -> bool:
    return shutil.which("solve-field") is not None


def solve_astrometry(dets: np.ndarray, width: int, height: int, fov_hint: float | None,
                     cpulimit_s: int) -> InitialSolution | None:
    lo, hi = (fov_hint * 0.5, fov_hint * 2.0) if fov_hint else (2.0, 120.0)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="astrometry-") as tmp:
        tmp = Path(tmp)
        d = dets[:150]
        # astrometry.net рахує пікселі від 1
        Table([d[:, 0] + 1, d[:, 1] + 1, d[:, 2]], names=("X", "Y", "FLUX")).write(
            tmp / "stars.xyls", format="fits")
        cmd = [
            "solve-field", "--overwrite", "--no-plots",
            # допоміжні Python-скрипти astrometry.net ламаються з NumPy 2,
            # а для готового списку зір вони й не потрібні
            "--no-remove-lines", "--uniformize", "0",
            "--width", str(width), "--height", str(height),
            "--x-column", "X", "--y-column", "Y", "--sort-column", "FLUX",
            "--scale-units", "degwidth", "--scale-low", f"{lo:.2f}", "--scale-high", f"{hi:.2f}",
            "--cpulimit", str(cpulimit_s), "--dir", str(tmp),
            "--new-fits", "none", "--index-xyls", "none", "--rdls", "none",
            "--match", "none", "--corr", "none",
            str(tmp / "stars.xyls"),
        ]
        # solve-field запускає дочірні процеси, тож при таймауті
        # зупиняємо всю групу, а не лише його самого
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            proc.wait(timeout=cpulimit_s + 30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return None
        wcs_file = tmp / "stars.wcs"
        if not wcs_file.exists():
            return None
        from astropy.io import fits

        w = WCS(fits.getheader(wcs_file))

        def p2w(x, y, w=w):
            return w.all_pix2world(x, y, 0)

        return InitialSolution(
            solver="astrometry.net",
            wcs=_wcs_from_mapping(p2w, width, height),
            solve_ms=(time.perf_counter() - t0) * 1000,
            info={"scale_range_deg": [round(lo, 1), round(hi, 1)]},
        )


# ------------------------------------------------------------ уточнення


def _unit(ra_deg, dec_deg):
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def world2pix(wcs: WCS, ra, dec) -> tuple[np.ndarray, np.ndarray]:
    """Небо → пікселі, що не падає на розбіжності.

    Обернене перетворення з SIP шукається ітераціями, і для точок, де поліном
    дисторсії вже не описує кадр (кути, далеко за краєм), вони розбігаються:
    astropy тоді кидає NoConvergence на весь масив. Тут такі точки стають NaN,
    а решта лишається. Кожну відповідь перевіряємо прямим перетворенням.
    """
    ra = np.atleast_1d(np.asarray(ra, float))
    dec = np.atleast_1d(np.asarray(dec, float))
    if ra.size == 0:
        return np.zeros(0), np.zeros(0)
    x, y = (np.asarray(v, float) for v in wcs.all_world2pix(ra, dec, 0, quiet=True))
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.any():
        r2, d2 = wcs.all_pix2world(x[ok], y[ok], 0)
        cos_sep = np.einsum("ij,ij->i", _unit(ra[ok], dec[ok]), _unit(r2, d2))
        sep_px = np.degrees(np.arccos(np.clip(cos_sep, -1, 1))) / float(np.mean(proj_plane_pixel_scales(wcs.celestial)))
        good = np.isfinite(sep_px) & (sep_px < 0.5)
        ok[np.nonzero(ok)[0][~good]] = False
    x[~ok] = np.nan
    y[~ok] = np.nan
    return x, y


def near_field(wcs: WCS, width: int, height: int, ra, dec) -> np.ndarray:
    """Маска точок неба поблизу кадру.

    Проєкцію (особливо з SIP) можна застосовувати лише біля кадру:
    для далеких точок обернене перетворення розбігається.
    """
    c_ra, c_dec = wcs.all_pix2world([[width / 2, height / 2]], 0)[0]
    corners = wcs.all_pix2world([[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]], 0)
    center = SkyCoord(c_ra * u.deg, c_dec * u.deg)
    radius = max(center.separation(SkyCoord(r * u.deg, d * u.deg)).deg for r, d in corners) * 1.15
    cos_sep = _unit(ra, dec) @ _unit([c_ra], [c_dec])[0]
    return cos_sep > math.cos(math.radians(min(radius, 89.0)))


def stars_in_frame(wcs: WCS, width: int, height: int, ra: np.ndarray, dec: np.ndarray,
                   margin: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Індекси зір, що потрапляють у кадр, і їхні піксельні координати."""
    near = np.nonzero(near_field(wcs, width, height, ra, dec))[0]
    if near.size == 0:
        return near, np.zeros(0), np.zeros(0)
    x, y = world2pix(wcs, ra[near], dec[near])
    m = margin * max(width, height)
    inside = (np.isfinite(x) & np.isfinite(y) & (x >= -m) & (x <= width - 1 + m)
              & (y >= -m) & (y <= height - 1 + m))
    return near[inside], x[inside], y[inside]


def _match(cx, cy, dets_xy, radius) -> tuple[np.ndarray, np.ndarray]:
    """Взаємно найближчі пари каталог ↔ виявлена точка в межах радіуса."""
    if len(cx) == 0 or len(dets_xy) == 0:
        return np.zeros(0, int), np.zeros(0, int)
    cat_xy = np.column_stack([cx, cy])
    d1, j = cKDTree(dets_xy).query(cat_xy, distance_upper_bound=radius)
    _, back = cKDTree(cat_xy).query(dets_xy)
    ci = np.nonzero(np.isfinite(d1))[0]
    ci = ci[back[j[ci]] == ci]
    return ci, j[ci]


@dataclass
class Solution:
    solver: str
    wcs: WCS
    solve_ms: float
    solver_info: dict
    matched_cat: np.ndarray      # індекси в каталозі
    matched_det: np.ndarray      # індекси у списку виявлених точок
    residual_arcsec: np.ndarray


def refine(init: InitialSolution, dets: np.ndarray, cat: StarCatalog, epoch_year: float,
           width: int, height: int, mag_limit: float = 7.5) -> Solution | None:
    bright = cat.mag <= mag_limit
    idx_all = np.nonzero(bright)[0]
    ra_all, dec_all = cat.positions_at(epoch_year, bright)
    det_xy = dets[:, :2]
    wcs = init.wcs
    radius = max(8.0, 0.006 * math.hypot(width, height))
    ci = dj = None
    for _ in range(4):
        sel, cx, cy = stars_in_frame(wcs, width, height, ra_all, dec_all)
        ci_local, dj = _match(cx, cy, det_xy, radius)
        if len(ci_local) < 6:
            return None
        ci = sel[ci_local]
        sky = SkyCoord(ra_all[ci] * u.deg, dec_all[ci] * u.deg)
        for clip in range(3):
            n = len(ci)
            sip = 3 if n >= 60 else 2 if n >= 20 else None
            wcs = fit_wcs_from_points((det_xy[dj, 0], det_xy[dj, 1]), sky,
                                      proj_point="center", projection="TAN", sip_degree=sip)
            px, py = world2pix(wcs, ra_all[ci], dec_all[ci])
            res = np.hypot(px - det_xy[dj, 0], py - det_xy[dj, 1])
            if not np.isfinite(res).any():
                return None
            rms = float(np.sqrt(np.nanmean(res ** 2)))
            keep = np.isfinite(res) & (res <= max(3 * rms, 1.0))
            if keep.all() or keep.sum() < 6 or clip == 2:
                break
            # відкинути викиди (хибні пари) і підігнати ще раз
            ci, dj, sky = ci[keep], dj[keep], sky[keep]
        radius = max(3.0, 5 * rms)

    det_sky = wcs.pixel_to_world(det_xy[dj, 0], det_xy[dj, 1])
    sep = det_sky.separation(SkyCoord(ra_all[ci] * u.deg, dec_all[ci] * u.deg)).arcsec
    return Solution(
        solver=init.solver, wcs=wcs, solve_ms=init.solve_ms, solver_info=init.info,
        matched_cat=idx_all[ci], matched_det=dj, residual_arcsec=sep,
    )


def describe(sol: Solution, width: int, height: int) -> dict:
    w = sol.wcs
    center = w.pixel_to_world(width / 2, height / 2)
    up = w.pixel_to_world(width / 2, height / 2 - height * 0.05)
    left = w.pixel_to_world(0, height / 2)
    right = w.pixel_to_world(width - 1, height / 2)
    top = w.pixel_to_world(width / 2, 0)
    bottom = w.pixel_to_world(width / 2, height - 1)
    scale = float(np.mean(proj_plane_pixel_scales(w.celestial))) * 3600
    r = sol.residual_arcsec
    hdr = w.to_header(relax=True)
    return {
        "solver": sol.solver,
        "solve_ms": round(sol.solve_ms, 1),
        "solver_info": sol.solver_info,
        "center": {"ra_deg": round(center.ra.deg, 5), "dec_deg": round(center.dec.deg, 5)},
        "fov_deg": [round(left.separation(right).deg, 3), round(top.separation(bottom).deg, 3)],
        "scale_arcsec_per_px": round(scale, 3),
        # Напрямок «вгору» на кадрі: позиційний кут від півночі через схід
        "up_position_angle_deg": round(center.position_angle(up).deg, 2),
        "matched_stars": int(len(r)),
        "residual_arcsec": {
            "rms": round(float(np.sqrt(np.mean(r ** 2))), 1),
            "median": round(float(np.median(r)), 1),
            "p90": round(float(np.percentile(r, 90)), 1),
            "max": round(float(r.max()), 1),
        },
        "sip_order": int(w.sip.a_order) if w.sip is not None else None,
        "wcs_header": {k: hdr[k] for k in hdr if k not in ("COMMENT", "HISTORY", "")},
    }
