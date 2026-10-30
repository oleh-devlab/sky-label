"""Перевірка точності уточнення на синтетичних кадрах з відомою «правдою».

Кадр рендериться з зір каталогу за заданим WCS, далі проходить звичайний шлях:
пошук зір → tetra3rs → refine(). Похибка рахується на сітці точок по ВСЬОМУ кадру,
зокрема там, де зір немає (модель там екстраполює). Скрипт завершується з кодом 1,
якщо якийсь випадок перевищує свій поріг.

    ASTRO_CATALOG_DIR=./catalog python scripts/check_accuracy.py

Випадки: без дисторсії, бочка, «хвиляста» дисторсія ширококутного телефона
(Galaxy S24 Ultra, 23 мм екв.), вузьке поле; зорі по всьому кадру, лише у верхній
половині («будинки» внизу), лише в одній чверті, лише яскраві.
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

import numpy as np
from astropy.wcs import WCS, Sip

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sky_label import catalog, config, projection, solve  # noqa: E402
from sky_label.stars import detect  # noqa: E402

W, H = 3000, 4000
EPOCH = 2025.3
# Дисторсія з розв'язку справжнього кадру Galaxy S24 Ultra (головна камера, 77″/px)
S24_PC = [[0.020010162593413, 0.0083828163611063], [-0.0083672482661543, 0.020009273603514]]
S24_SIP_A = {'0_2': -9.49678e-08, '0_3': -1.67384e-10, '0_5': 4.23027e-17, '0_7': -3.10158e-24, '1_1': 1.8306e-06, '1_2': -1.42816e-08, '1_4': 2.81667e-15, '1_6': -1.8708e-22, '2_0': 4.79414e-07, '2_1': -4.60648e-11, '2_3': 8.46053e-17, '2_5': -9.30474e-24, '3_0': -1.40893e-08, '3_2': 5.63334e-15, '3_4': -5.61241e-22, '4_1': 4.23027e-17, '4_3': -9.30474e-24, '5_0': 2.81667e-15, '5_2': -5.61241e-22, '6_1': -3.10158e-24, '7_0': -1.8708e-22}
S24_SIP_B = {'0_2': 1.78139e-06, '0_3': -1.44459e-08, '0_5': 2.85058e-15, '0_7': -1.89967e-22, '1_1': 4.81703e-07, '1_2': -3.31974e-10, '1_4': 1.13604e-16, '1_6': -9.50543e-24, '2_0': -2.0946e-07, '2_1': -1.42246e-08, '2_3': 5.70116e-15, '2_5': -5.69901e-22, '3_0': -3.19368e-10, '3_2': 2.27208e-16, '3_4': -2.85163e-23, '4_1': 2.85058e-15, '4_3': -5.69901e-22, '5_0': 1.13604e-16, '5_2': -2.85163e-23, '6_1': -1.89967e-22, '7_0': -9.50543e-24}
# Пороги, пікселі: (медіана, 95-й перцентиль) похибки по всьому кадру
LIMITS = {
    ("plain", "full"): (0.2, 0.5), ("plain", "half"): (0.2, 0.5),
    ("plain", "quarter"): (0.2, 0.5), ("plain", "bright"): (0.2, 0.5),
    ("barrel", "full"): (0.2, 0.5), ("barrel", "half"): (0.5, 3),
    ("barrel", "quarter"): (1.5, 5), ("barrel", "bright"): (5, 15),
    ("wavy", "full"): (0.2, 0.5), ("wavy", "half"): (0.5, 4),
    ("wavy", "quarter"): (2, 12), ("wavy", "bright"): (8, 20),
    ("tele", "full"): (0.2, 0.5), ("tele", "half"): (0.2, 0.5),
    ("tele", "quarter"): (0.5, 1.0), ("tele", "bright"): (0.2, 0.5),
}
TRUTH_UA = {"plain": "без дисторсії", "barrel": "бочка −40 px", "wavy": "хвиляста (S24)", "tele": "вузьке поле 20°"}
MASK_UA = {"full": "увесь кадр", "half": "верхня половина", "quarter": "одна чверть", "bright": "лише ≤3,8m"}


def make_truth(kind: str) -> WCS:
    w = WCS(naxis=2)
    w.wcs.crpix = [W / 2 + 1, H / 2 + 1]
    w.wcs.crval = [160.0, 55.8]
    cd = np.array(S24_PC)
    w.wcs.cd = cd / 3 if kind == "tele" else cd
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    if kind in ("barrel", "wavy"):
        w.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
        a, b = np.zeros((8, 8)), np.zeros((8, 8))
        if kind == "barrel":
            k = -40 / 2500 ** 3          # радіальне −40 px у кутах
            a[3, 0] = a[1, 2] = b[0, 3] = b[2, 1] = k
        else:
            for src, dst in ((S24_SIP_A, a), (S24_SIP_B, b)):
                for key, v in src.items():
                    p, q = map(int, key.split("_"))
                    dst[p, q] = v
        w.sip = Sip(a, b, None, None, w.wcs.crpix)
    w.wcs.set()
    return w


def render(truth: WCS, cat, ra, dec, mag_max: float) -> np.ndarray:
    rng = np.random.default_rng(1)
    near = projection.near_field(truth, W, H, ra, dec) & (cat.mag <= mag_max)
    x, y = projection.world2pix(truth, ra[near], dec[near])
    img = rng.normal(40, 4, (H, W)).astype(np.float32)
    yy, xx = np.mgrid[-6:7, -6:7]
    for cx, cy, m in zip(x, y, cat.mag[near]):
        if not (np.isfinite(cx) and 8 <= cx < W - 8 and 8 <= cy < H - 8):
            continue
        ix, iy = int(round(cx)), int(round(cy))
        g = np.exp(-((xx + ix - cx) ** 2 + (yy + iy - cy) ** 2) / (2 * 1.5 ** 2))
        img[iy - 6:iy + 7, ix - 6:ix + 7] += 3000 * 10 ** (-0.4 * (m - 1)) * g
    return np.clip(img, 0, 255)


def grid_error_px(wcs: WCS, truth: WCS) -> np.ndarray:
    xs, ys = np.meshgrid(np.linspace(0, W - 1, 31), np.linspace(0, H - 1, 41))
    r1, d1 = wcs.all_pix2world(xs.ravel(), ys.ravel(), 0)
    r2, d2 = truth.all_pix2world(xs.ravel(), ys.ravel(), 0)
    cos = np.einsum("ij,ij->i", projection.unit_vectors(r1, d1), projection.unit_vectors(r2, d2))
    scale = projection.pixel_scale_deg(truth)
    return np.degrees(np.arccos(np.clip(cos, -1, 1))) / scale


def main() -> int:
    warnings.filterwarnings("ignore")
    cfg = config.load()
    cat = catalog.load(cfg.catalog_dir)
    ra, dec = cat.positions_at(EPOCH)
    failed = 0
    print(f"{'дисторсія':<16} {'зорі':<16} {'медіана':>8} {'95 %':>8} {'макс':>8}  модель")
    for kind in ("plain", "barrel", "wavy", "tele"):
        truth = make_truth(kind)
        a = truth.pixel_to_world(0, H / 2)
        hint = a.separation(truth.pixel_to_world(W - 1, H / 2)).deg
        base = render(truth, cat, ra, dec, 6.5)
        for mask in ("full", "half", "quarter", "bright"):
            g = render(truth, cat, ra, dec, 3.8) if mask == "bright" else base.copy()
            if mask in ("half", "quarter"):
                g[H // 2:] = 40
            if mask == "quarter":
                g[:, W // 2:] = 40
            t0 = time.perf_counter()
            dets = detect(g)
            init = solve.solve_tetra3(dets, W, H, cfg.catalog_dir, hint)
            sol = solve.refine(init, dets, cat, EPOCH, W, H) if init else None
            lim = LIMITS[(kind, mask)]
            if sol is None:
                failed += 1
                print(f"{TRUTH_UA[kind]:<16} {MASK_UA[mask]:<16} НЕ РОЗВ'ЯЗАНО  ✗")
                continue
            e = grid_error_px(sol.wcs, truth)
            med, p95 = float(np.median(e)), float(np.percentile(e, 95))
            ok = med <= lim[0] and p95 <= lim[1]
            failed += not ok
            print(f"{TRUTH_UA[kind]:<16} {MASK_UA[mask]:<16} {med:8.2f} {p95:8.2f} {e.max():8.2f}  "
                  f"{sol.model} {time.perf_counter() - t0:.1f} с {'✓' if ok else f'✗ поріг {lim}'}")
    print("усе в межах порогів" if not failed else f"поза порогами: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
