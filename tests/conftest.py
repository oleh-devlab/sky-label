"""Тести без каталогу й мережі: геометрія, підгонка дисторсії, вибір місця, параметри API.

    pip install -r requirements-dev.txt
    python -m pytest
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pytest
from astropy.wcs import WCS, Sip

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", module="astropy")

W, H = 4000, 3000


def make_wcs(radial: dict[int, float] | None = None, crval=(80.0, 20.0), scale=0.02) -> WCS:
    """TAN (або TAN-SIP з радіальною дисторсією u·r^k, v·r^k) з центром у центрі кадру."""
    w = WCS(naxis=2)
    w.wcs.crpix = [W / 2 + 1, H / 2 + 1]
    w.wcs.crval = list(crval)
    w.wcs.cd = [[-scale, 0], [0, scale]]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    if radial:
        order = max(radial) + 1
        a, b = np.zeros((order + 1, order + 1)), np.zeros((order + 1, order + 1))
        for k, c in radial.items():
            m = k // 2
            from math import comb
            for j in range(m + 1):
                a[2 * j + 1, 2 * m - 2 * j] += c * comb(m, j)
                b[2 * j, 2 * m - 2 * j + 1] += c * comb(m, j)
        w.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
        w.sip = Sip(a, b, None, None, w.wcs.crpix)
    w.wcs.set()
    return w


@pytest.fixture
def rng():
    return np.random.default_rng(0)
