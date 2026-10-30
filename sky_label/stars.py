"""Пошук зір на кадрі: центри з точністю до частки пікселя.

Алгоритм розрахований на кадри з телефона, зокрема крізь вікно:
1. фон оцінюється медіанами по блоках і віднімається;
2. шум оцінюється робастно (MAD);
3. після легкого згладжування беруться локальні максимуми вище порогу;
4. для кожного кандидата рахується центр ваги і потік у ядрі 7×7;
5. відкидаються протяжні об'єкти (краї рам, засвітки): у них
   значна частина світла лежить у кільці навколо ядра;
6. відкидаються «гарячі пікселі»: одиничний яскравий піксель без сусідів.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as nd

CORE_R = 3        # ядро 7×7
RING_R = 8        # кільце до 17×17
PEAK_R = 4        # мінімальна відстань між двома максимумами
EDGE = RING_R + 1


def _background(img: np.ndarray, block: int = 64) -> np.ndarray:
    h, w = img.shape
    ph, pw = -h % block, -w % block
    padded = np.pad(img, ((0, ph), (0, pw)), mode="edge")
    blocks = padded.reshape(padded.shape[0] // block, block, padded.shape[1] // block, block)
    med = np.median(blocks, axis=(1, 3))
    med = nd.median_filter(med, size=3, mode="nearest")
    zoom = (padded.shape[0] / med.shape[0], padded.shape[1] / med.shape[1])
    bg = nd.zoom(med, zoom, order=1, mode="nearest", grid_mode=True)
    return bg[:h, :w]


def _noise(x: np.ndarray) -> float:
    step = max(1, x.size // 1_000_000)
    s = x.ravel()[::step]
    mad = float(np.median(np.abs(s - np.median(s))))
    if mad > 0:
        return 1.4826 * mad
    # Квантовані темні кадри (багато однакових значень): MAD = 0
    lo, hi = np.percentile(s, [5, 95])
    core = s[(s >= lo) & (s <= hi)]
    return float(core.std()) or 1e-6


_RY, _RX = np.mgrid[-RING_R:RING_R + 1, -RING_R:RING_R + 1]


def _elongation(win: np.ndarray) -> float:
    """Відношення осей еліпса другого моменту: 1 — круг, багато — лінія."""
    s = float(win.sum())
    if s <= 0:
        return 99.0
    w = win / s
    my, mx = float((_RY * w).sum()), float((_RX * w).sum())
    cyy = float(((_RY - my) ** 2 * w).sum())
    cxx = float(((_RX - mx) ** 2 * w).sum())
    cxy = float(((_RX - mx) * (_RY - my) * w).sum())
    tr, det = cxx + cyy, cxx * cyy - cxy * cxy
    disc = max(tr * tr / 4 - det, 0.0) ** 0.5
    l1, l2 = tr / 2 + disc, tr / 2 - disc
    return (l1 / l2) ** 0.5 if l2 > 1e-9 else 99.0


def detect(gray: np.ndarray, max_sources: int = 400, k_sigma: float = 5.0,
           rgb: np.ndarray | None = None, max_saturation: float = 0.6) -> np.ndarray:
    """Повертає масив N×4: x, y, flux, peak (координати від 0, x вправо, y вниз),
    відсортований за спаданням потоку.

    Якщо передано rgb (8-бітний кадр), відкидаються насичено кольорові точки:
    підписи й позначки, намальовані поверх фото, вогні літаків, світлофори.
    Зорі на фото майже безбарвні."""
    img = gray.astype(np.float32)
    resid = img - _background(img)
    smooth = nd.gaussian_filter(resid, 1.0)
    sigma = _noise(smooth)

    peaks = (smooth == nd.maximum_filter(smooth, size=2 * PEAK_R + 1)) & (smooth > k_sigma * sigma)
    peaks[:EDGE, :] = peaks[-EDGE:, :] = False
    peaks[:, :EDGE] = peaks[:, -EDGE:] = False
    ys, xs = np.nonzero(peaks)
    if ys.size == 0:
        return np.zeros((0, 4))
    order = np.argsort(-smooth[ys, xs])[: max_sources * 8]
    ys, xs = ys[order], xs[order]

    sat_level = float(img.max()) * 0.98
    out = []
    yy, xx = np.mgrid[-CORE_R:CORE_R + 1, -CORE_R:CORE_R + 1]
    for y, x in zip(ys, xs):
        core = resid[y - CORE_R:y + CORE_R + 1, x - CORE_R:x + CORE_R + 1]
        w = np.clip(core, 0, None)
        flux = float(w.sum())
        if flux <= 0:
            continue
        win = np.clip(resid[y - RING_R:y + RING_R + 1, x - RING_R:x + RING_R + 1], 0, None)
        ring = float(win.sum()) - flux
        elong = _elongation(win)
        if elong > 3.0:
            continue  # лінія: край рами, слід літака чи супутника
        if ring / flux > 1.5 and (elong > 1.6 or smooth[y, x] < 50 * sigma):
            continue  # протяжний об'єкт; яскраві круглі (планети з ореолом) лишаються
        peak = float(img[y, x])
        c = float(resid[y, x])
        neigh = (resid[y - 1, x] + resid[y + 1, x] + resid[y, x - 1] + resid[y, x + 1]) / 4
        if peak < sat_level and c > 0 and neigh < 0.15 * c:
            continue  # гарячий піксель
        if rgb is not None:
            col = rgb[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3).astype(np.float32).mean(axis=0)
            if col.max() > 0 and (col.max() - col.min()) / col.max() > max_saturation:
                continue  # насичений колір: не зоря
        out.append((x + float((xx * w).sum()) / flux, y + float((yy * w).sum()) / flux, flux, peak))

    if not out:
        return np.zeros((0, 4))
    arr = np.array(out)
    return arr[np.argsort(-arr[:, 2])][:max_sources]
