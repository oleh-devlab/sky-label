"""Підгонка WCS «TAN + дисторсія» за парами зоря каталогу ↔ точка на кадрі.

Чому не astropy.wcs.utils.fit_wcs_from_points:
- він рахує поліном у сирих пікселях (u⁵ при u≈2000 — це ~10¹⁶), тож уже з 4–5-го
  степеня задача погано обумовлена й оптимізатор не сходиться;
- у ширококутних об'єктивах телефонів дисторсія «хвиляста» (зміщення змінює знак
  двічі вздовж радіуса). Її описують члени u·r⁴, v·r⁴, u·r⁶… — повний поліном
  3-го степеня цього не вміє, а повний 5–7-го має забагато параметрів.

Тут модель лінійна за коефіцієнтами: стандартні координати ξ, η (гномонічна
проєкція відносно точки дотику) = поліном від нормованих (u, v) з центру кадру.
Розв'язується звичайними найменшими квадратами. Точка дотику уточнюється
кількома ітераціями, щоб центр кадру точно відповідав CRVAL. Результат
перетворюється на стандартний TAN-SIP, тож далі з ним працює звичайний astropy WCS.

Яку модель брати, вирішує перехресна перевірка: похибка на зорях, яких
підгонка не бачила. Береться найпростіша модель, не помітно гірша за найкращу.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb

import numpy as np
from astropy.wcs import WCS, Sip

# Від простої до складної: (степінь повного полінома, радіальні степені r^k).
# Радіальний член r^k додає стовпці u·r^k і v·r^k.
MODELS: list[tuple[int, tuple[int, ...]]] = [
    (1, ()),
    (2, ()),
    (3, ()),
    (3, (4,)),
    (3, (4, 6)),
    (5, ()),
]
CV_FOLDS = 5
# Скільки зір потрібно на один параметр (на одну вісь), щоб модель узагалі розглядати
MIN_STARS_PER_PARAM = 4
# Простіша модель перемагає, якщо її похибка не більш ніж на 5 % гірша за найкращу
CV_TOLERANCE = 1.05
TANGENT_ITERATIONS = 3

Model = tuple[int, tuple[int, ...]]


def describe_model(model: Model) -> str:
    order, radial = model
    if order == 1 and not radial:
        return "без дисторсії (TAN)"
    text = f"поліном {order}-го степеня"
    if radial:
        sup = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
        text += " + радіальні " + ", ".join(f"r{str(k).translate(sup)}" for k in radial)
    return text


# ---------------------------------------------------------------- проєкція


def gnomonic(ra, dec, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """Небо → стандартні координати ξ, η (градуси) відносно точки дотику."""
    ra, dec = np.radians(ra), np.radians(dec)
    ra0, dec0 = np.radians(ra0), np.radians(dec0)
    cosc = np.sin(dec0) * np.sin(dec) + np.cos(dec0) * np.cos(dec) * np.cos(ra - ra0)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = np.cos(dec) * np.sin(ra - ra0) / cosc
        eta = (np.cos(dec0) * np.sin(dec) - np.sin(dec0) * np.cos(dec) * np.cos(ra - ra0)) / cosc
    bad = cosc <= 0  # за 90° від точки дотику проєкція не визначена
    xi, eta = np.where(bad, np.nan, xi), np.where(bad, np.nan, eta)
    return np.degrees(xi), np.degrees(eta)


def degnomonic(xi: float, eta: float, ra0: float, dec0: float) -> tuple[float, float]:
    """Стандартні координати → небо (для зсуву точки дотику)."""
    xi, eta = np.radians(xi), np.radians(eta)
    ra0, dec0 = np.radians(ra0), np.radians(dec0)
    den = np.cos(dec0) - eta * np.sin(dec0)
    ra = ra0 + np.arctan2(xi, den)
    dec = np.arctan2(np.sin(dec0) + eta * np.cos(dec0), np.hypot(xi, den))
    return float(np.degrees(ra) % 360), float(np.degrees(dec))


# ---------------------------------------------------------------- модель


def _columns(model: Model) -> list[dict[tuple[int, int], float]]:
    """Стовпці матриці як суми одночленів u^p·v^q з вагами."""
    order, radial = model
    cols = [{(p, n - p): 1.0} for n in range(order + 1) for p in range(n, -1, -1)]
    for k in radial:
        m = k // 2
        # (u²+v²)^m = Σ C(m,j)·u^(2j)·v^(2m−2j); множимо на u і на v
        cols.append({(2 * j + 1, 2 * m - 2 * j): float(comb(m, j)) for j in range(m + 1)})
        cols.append({(2 * j, 2 * m - 2 * j + 1): float(comb(m, j)) for j in range(m + 1)})
    return cols


def n_params(model: Model) -> int:
    return len(_columns(model))


def _design(u: np.ndarray, v: np.ndarray, cols, scale: float) -> np.ndarray:
    un, vn = u / scale, v / scale
    return np.column_stack([sum(w * un ** p * vn ** q for (p, q), w in c.items()) for c in cols])


@dataclass
class PolyFit:
    model: Model
    crpix: tuple[float, float]   # 0-based центр кадру
    crval: tuple[float, float]
    scale: float                 # нормування (u, v), пікселі
    a: np.ndarray                # коефіцієнти для ξ
    b: np.ndarray                # коефіцієнти для η

    def predict(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        M = _design(x - self.crpix[0], y - self.crpix[1], _columns(self.model), self.scale)
        return M @ self.a, M @ self.b

    def pixel_scale_deg(self) -> float:
        cd = self._monomials()[1]
        return float(np.sqrt(abs(np.linalg.det(cd))))

    def _monomials(self):
        """Коефіцієнти одночленів у пікселях: сталі, матриця CD, решта."""
        mono_a: dict[tuple[int, int], float] = {}
        mono_b: dict[tuple[int, int], float] = {}
        for c, ca, cb in zip(_columns(self.model), self.a, self.b):
            for (p, q), w in c.items():
                k = w / self.scale ** (p + q)
                mono_a[(p, q)] = mono_a.get((p, q), 0.0) + ca * k
                mono_b[(p, q)] = mono_b.get((p, q), 0.0) + cb * k
        cd = np.array([[mono_a[(1, 0)], mono_a[(0, 1)]], [mono_b[(1, 0)], mono_b[(0, 1)]]])
        return (mono_a[(0, 0)], mono_b[(0, 0)]), cd, mono_a, mono_b

    def to_wcs(self) -> WCS:
        _, cd, mono_a, mono_b = self._monomials()
        order = max(p + q for p, q in mono_a)
        w = WCS(naxis=2)
        w.wcs.crpix = [self.crpix[0] + 1, self.crpix[1] + 1]   # FITS рахує пікселі від 1
        w.wcs.crval = list(self.crval)
        w.wcs.cd = cd
        if order >= 2:
            # SIP: ξ,η = CD·(u + f(u,v), v + g(u,v)), тож f, g = CD⁻¹·(нелінійна частина)
            w.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
            inv = np.linalg.inv(cd)
            A = np.zeros((order + 1, order + 1))
            B = np.zeros_like(A)
            for (p, q) in mono_a:
                if p + q >= 2:
                    A[p, q], B[p, q] = inv @ np.array([mono_a[(p, q)], mono_b[(p, q)]])
            w.sip = Sip(A, B, None, None, w.wcs.crpix)
        else:
            w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.set()
        return w


def fit(x: np.ndarray, y: np.ndarray, ra: np.ndarray, dec: np.ndarray, width: int, height: int,
        ra0: float, dec0: float, model: Model) -> PolyFit:
    """Найменші квадрати; точка дотику зсувається, доки центр кадру не стане CRVAL."""
    crpix = (width / 2, height / 2)
    scale = float(np.hypot(width, height) / 2)
    cols = _columns(model)
    M = _design(x - crpix[0], y - crpix[1], cols, scale)
    for _ in range(TANGENT_ITERATIONS):
        xi, eta = gnomonic(ra, dec, ra0, dec0)
        ok = np.isfinite(xi) & np.isfinite(eta)
        a, *_ = np.linalg.lstsq(M[ok], xi[ok], rcond=None)
        b, *_ = np.linalg.lstsq(M[ok], eta[ok], rcond=None)
        # Перший стовпець — сталий член: де опинився центр кадру відносно точки дотику
        ra0, dec0 = degnomonic(a[0], b[0], ra0, dec0)
    xi, eta = gnomonic(ra, dec, ra0, dec0)
    ok = np.isfinite(xi) & np.isfinite(eta)
    a, *_ = np.linalg.lstsq(M[ok], xi[ok], rcond=None)
    b, *_ = np.linalg.lstsq(M[ok], eta[ok], rcond=None)
    return PolyFit(model, crpix, (ra0, dec0), scale, a, b)


def residuals_px(f: PolyFit, x, y, ra, dec) -> np.ndarray:
    """Відхилення в пікселях, пораховані в площині дотику (без оберненого SIP)."""
    px, py = f.predict(x, y)
    xi, eta = gnomonic(ra, dec, *f.crval)
    return np.hypot(px - xi, py - eta) / f.pixel_scale_deg()


def feasible(n_stars: int) -> list[Model]:
    """Моделі, для яких вистачає зір (з запасом на перехресну перевірку)."""
    usable = n_stars * (CV_FOLDS - 1) / CV_FOLDS
    return [m for m in MODELS if usable >= MIN_STARS_PER_PARAM * n_params(m)] or [MODELS[0]]


def cross_validate(x, y, ra, dec, width: int, height: int, ra0: float, dec0: float,
                   model: Model, seed: int = 0) -> float:
    """rms похибки (пікселі) на зорях, що не брали участі в підгонці."""
    folds = np.random.default_rng(seed).permutation(len(x)) % CV_FOLDS
    errs = []
    for k in range(CV_FOLDS):
        test = folds == k
        if not test.any():
            continue
        f = fit(x[~test], y[~test], ra[~test], dec[~test], width, height, ra0, dec0, model)
        errs.append(residuals_px(f, x[test], y[test], ra[test], dec[test]))
    e = np.concatenate(errs)
    return float(np.sqrt(np.nanmean(e ** 2)))


def choose(x, y, ra, dec, width: int, height: int, ra0: float, dec0: float) -> tuple[Model, dict[Model, float]]:
    """Найпростіша модель, чия похибка на перехресній перевірці близька до найкращої."""
    scores = {m: cross_validate(x, y, ra, dec, width, height, ra0, dec0, m) for m in feasible(len(x))}
    best = min(scores.values())
    for m in MODELS:
        if m in scores and scores[m] <= best * CV_TOLERANCE:
            return m, scores
    return min(scores, key=scores.get), scores
