"""Небо ↔ пікселі кадру за WCS: безпечне обернене перетворення, межі кадру.

Усі піксельні координати — від 0, x вправо, y вниз (як у прев'ю й result.json).
"""

from __future__ import annotations

import math

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales


def unit_vectors(ra_deg, dec_deg) -> np.ndarray:
    """Одиничні вектори N×3 для напрямків на небі (градуси)."""
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def pixel_scale_deg(wcs: WCS) -> float:
    """Середній масштаб кадру, градуси на піксель."""
    return float(np.mean(proj_plane_pixel_scales(wcs.celestial)))


def frame_center(wcs: WCS, width: int, height: int) -> tuple[float, float]:
    """RA, Dec центру кадру — точка дотику для проєкції TAN.

    Береться саме центр кадру, а не середнє координат зір: біля полюса світу
    RA перескакує 0°/360°, і середнє опиняється деінде на небі.
    """
    ra, dec = wcs.all_pix2world([[width / 2, height / 2]], 0)[0]
    c = SkyCoord(ra * u.deg, dec * u.deg)
    return float(c.ra.deg), float(c.dec.deg)


def wcs_from_header(header: dict) -> WCS:
    """WCS із заголовка, збереженого в result.json (solution.wcs_header)."""
    from astropy.io import fits

    h = fits.Header()
    for k, v in header.items():
        h[k] = v
    return WCS(h)


def near_field(wcs: WCS, width: int, height: int, ra, dec) -> np.ndarray:
    """Маска точок неба поблизу кадру.

    Проєкцію (особливо з SIP) можна застосовувати лише біля кадру:
    для далеких точок обернене перетворення розбігається.
    """
    c_ra, c_dec = wcs.all_pix2world([[width / 2, height / 2]], 0)[0]
    corners = wcs.all_pix2world([[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]], 0)
    center = SkyCoord(c_ra * u.deg, c_dec * u.deg)
    radius = max(center.separation(SkyCoord(r * u.deg, d * u.deg)).deg for r, d in corners) * 1.15
    cos_sep = unit_vectors(ra, dec) @ unit_vectors([c_ra], [c_dec])[0]
    return cos_sep > math.cos(math.radians(min(radius, 89.0)))


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
        cos_sep = np.einsum("ij,ij->i", unit_vectors(ra[ok], dec[ok]), unit_vectors(r2, d2))
        sep_px = np.degrees(np.arccos(np.clip(cos_sep, -1, 1))) / pixel_scale_deg(wcs)
        good = np.isfinite(sep_px) & (sep_px < 0.5)
        ok[np.nonzero(ok)[0][~good]] = False
    x[~ok] = np.nan
    y[~ok] = np.nan
    return x, y


def project(wcs: WCS, width: int, height: int, ra, dec) -> tuple[np.ndarray, np.ndarray]:
    """Небо → пікселі; для точок далеко від кадру повертає NaN."""
    ra, dec = np.atleast_1d(np.asarray(ra, float)), np.atleast_1d(np.asarray(dec, float))
    x, y = np.full(ra.shape, np.nan), np.full(ra.shape, np.nan)
    near = near_field(wcs, width, height, ra, dec)
    if near.any():
        x[near], y[near] = world2pix(wcs, ra[near], dec[near])
    return x, y


def in_frame(x, y, width: int, height: int):
    return np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x <= width - 1) & (y >= 0) & (y <= height - 1)


def stars_in_frame(wcs: WCS, width: int, height: int, ra: np.ndarray,
                   dec: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Індекси зір, що потрапляють у кадр, і їхні піксельні координати."""
    near = np.nonzero(near_field(wcs, width, height, ra, dec))[0]
    if near.size == 0:
        return near, np.zeros(0), np.zeros(0)
    x, y = world2pix(wcs, ra[near], dec[near])
    inside = in_frame(x, y, width, height)
    return near[inside], x[inside], y[inside]


def clip_polyline(x, y, width: int, height: int) -> list[list[list[float]]]:
    """Розбиває ламану на шматки, що лежать у кадрі (координати з точністю 0,1 px)."""
    ok = in_frame(x, y, width, height)
    lines, cur = [], []
    for xi, yi, k in zip(x, y, ok):
        if k:
            cur.append([round(float(xi), 1), round(float(yi), 1)])
        elif cur:
            if len(cur) > 1:
                lines.append(cur)
            cur = []
    if len(cur) > 1:
        lines.append(cur)
    return lines
