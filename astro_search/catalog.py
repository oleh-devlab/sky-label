"""Каталог зір (HYG 4.1, підмножина до 8-ї величини), підготовлений під час збірки.

Джерело: HYG Database, David Nash / astronexus, CC BY-SA 4.0
https://github.com/astronexus/HYG-Database
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

GREEK = {
    "Alp": "α", "Bet": "β", "Gam": "γ", "Del": "δ", "Eps": "ε", "Zet": "ζ",
    "Eta": "η", "The": "θ", "Iot": "ι", "Kap": "κ", "Lam": "λ", "Mu": "μ",
    "Nu": "ν", "Xi": "ξ", "Omi": "ο", "Pi": "π", "Rho": "ρ", "Sig": "σ",
    "Tau": "τ", "Ups": "υ", "Phi": "φ", "Chi": "χ", "Psi": "ψ", "Ome": "ω",
}
SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

STARS_FILE = "stars.npz"
J2000_YEAR = 2000.0


def bayer_symbol(bayer: str) -> str:
    """'Del-1' → 'δ¹', 'Alp' → 'α'."""
    if not bayer:
        return ""
    letter, _, num = bayer.partition("-")
    return GREEK.get(letter, letter) + num.translate(SUPERSCRIPT)


def designation(bayer: str, flam: str, con: str, hip: int, hr: int) -> str:
    if bayer and con:
        return f"{bayer_symbol(bayer)} {con}"
    if flam and con:
        return f"{flam} {con}"
    if hip:
        return f"HIP {hip}"
    if hr:
        return f"HR {hr}"
    return ""


@dataclass
class StarCatalog:
    ra: np.ndarray          # градуси, J2000, епоха 2000.0
    dec: np.ndarray
    pmra: np.ndarray        # мас/рік, μα·cosδ
    pmdec: np.ndarray       # мас/рік
    mag: np.ndarray
    hip: np.ndarray
    hr: np.ndarray
    proper: np.ndarray      # власна назва (може бути порожньою)
    bayer: np.ndarray
    flam: np.ndarray
    con: np.ndarray

    def __len__(self) -> int:
        return int(self.ra.size)

    def positions_at(self, year: float, mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Координати з урахуванням власного руху на задану епоху (лінійно)."""
        sel = slice(None) if mask is None else mask
        dt = year - J2000_YEAR
        dec = self.dec[sel] + self.pmdec[sel] * dt / 3.6e6
        cosd = np.cos(np.radians(self.dec[sel]))
        ra = self.ra[sel] + self.pmra[sel] * dt / 3.6e6 / np.clip(cosd, 1e-6, None)
        return np.mod(ra, 360.0), dec

    def describe(self, i: int) -> dict:
        hip, hr = int(self.hip[i]), int(self.hr[i])
        des = designation(str(self.bayer[i]), str(self.flam[i]), str(self.con[i]), hip, hr)
        proper = str(self.proper[i])
        return {
            "name": proper or des,
            "proper_name": proper or None,
            "designation": des or None,
            "constellation": str(self.con[i]) or None,
            "hip": hip or None,
            "hr": hr or None,
            "mag": round(float(self.mag[i]), 2),
        }


@lru_cache(maxsize=2)
def load(catalog_dir: Path) -> StarCatalog:
    with np.load(Path(catalog_dir) / STARS_FILE, allow_pickle=False) as z:
        return StarCatalog(**{k: z[k] for k in StarCatalog.__dataclass_fields__})
