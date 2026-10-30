"""Готує дані, потрібні для роботи офлайн. Запускається один раз під час збірки образу.

1. Каталог зір HYG 4.1 → stars.npz (лише зорі до 8-ї величини).
2. Лінії сузір'їв МАС (d3-celestial) → constellations.npz: пари індексів зір у stars.npz.
3. База шаблонів tetra3rs для поля зору 5–90° → tetra3.db.

Ефемерида de421 окремо не завантажується: її містить пакет skyfield-data.

    python scripts/prepare_data.py --out ./catalog
    python scripts/prepare_data.py --out ./catalog --hyg-csv hygdata_v41.csv \
        --lines-json constellations.lines.json                                # без інтернету
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
import urllib.request
from pathlib import Path

import numpy as np

# Зафіксований коміт, щоб збірка була відтворюваною.
HYG_COMMIT = "c7f7f883fe678cc7680169a50ccd7dcc49b060ce"
HYG_URL = (
    "https://raw.githubusercontent.com/astronexus/HYG-Database/"
    f"{HYG_COMMIT}/hyg/CURRENT/hygdata_v41.csv"
)
MAG_LIMIT = 8.0

# Лінії сузір'їв: d3-celestial (Olaf Frohn), BSD 3-Clause; за сторінкою сузір'їв МАС
D3C_COMMIT = "7e720a3de062059d4c5400a379146a601d9010e0"
LINES_URL = (
    "https://raw.githubusercontent.com/ofrohn/d3-celestial/"
    f"{D3C_COMMIT}/data/constellations.lines.json"
)
# BSD 3-Clause вимагає поширювати текст ліцензії разом із даними
D3C_LICENSE = """\
Copyright (c) 2015, Olaf Frohn
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
# Вершина лінії має збігтися із зорею каталогу не далі за цю відстань
LINE_MATCH_DEG = 0.05

T3_MIN_FOV, T3_MAX_FOV, T3_MAG = 5.0, 90.0, 7.0


def _int(s: str) -> int:
    try:
        return int(float(s))
    except ValueError:
        return 0


def build_star_catalog(csv_text: str, out: Path) -> int:
    cols = {k: [] for k in
            ("ra", "dec", "pmra", "pmdec", "mag", "hip", "hr", "proper", "bayer", "flam", "con")}
    for r in csv.DictReader(io.StringIO(csv_text)):
        if r["id"] == "0" or not r["mag"]:          # id 0 — Сонце
            continue
        mag = float(r["mag"])
        if mag > MAG_LIMIT:
            continue
        cols["ra"].append(float(r["ra"]) * 15.0)    # години → градуси
        cols["dec"].append(float(r["dec"]))
        cols["pmra"].append(float(r["pmra"] or 0))
        cols["pmdec"].append(float(r["pmdec"] or 0))
        cols["mag"].append(mag)
        cols["hip"].append(_int(r["hip"]))
        cols["hr"].append(_int(r["hr"]))
        cols["proper"].append(r["proper"].strip())
        cols["bayer"].append(r["bayer"].strip())
        cols["flam"].append(r["flam"].strip())
        cols["con"].append(r["con"].strip())

    arrays = {
        "ra": np.array(cols["ra"]), "dec": np.array(cols["dec"]),
        "pmra": np.array(cols["pmra"]), "pmdec": np.array(cols["pmdec"]),
        "mag": np.array(cols["mag"], dtype=np.float32),
        "hip": np.array(cols["hip"], dtype=np.int32), "hr": np.array(cols["hr"], dtype=np.int32),
    }
    for k in ("proper", "bayer", "flam", "con"):
        arrays[k] = np.array(cols[k], dtype=str)
    np.savez_compressed(out / "stars.npz", **arrays)
    return len(cols["ra"])


# Копія astro_search.projection.unit_vectors: у Docker цей скрипт запускається
# раніше, ніж пакет astro_search потрапляє в образ, тож імпортувати його не можна.
def _unit(ra_deg, dec_deg) -> np.ndarray:
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def build_constellation_lines(lines_json: str, out: Path) -> tuple[int, int]:
    """Вершини ліній → найближчі зорі stars.npz.

    Зберігаються індекси зір, а не координати: тоді лінії йдуть точно через
    зорі каталогу з урахуванням власного руху на епоху кадру.
    """
    from scipy.spatial import cKDTree

    with np.load(out / "stars.npz") as z:
        tree = cKDTree(_unit(z["ra"], z["dec"]))
    con, a, b = [], [], []
    for feature in json.loads(lines_json)["features"]:
        for line in feature["geometry"]["coordinates"]:
            pts = np.asarray(line, float)
            dist, idx = tree.query(_unit(np.mod(pts[:, 0], 360.0), pts[:, 1]))
            if np.degrees(dist.max()) > LINE_MATCH_DEG:
                raise ValueError(f"{feature['id']}: вершина лінії не збігається з жодною зорею каталогу")
            for i, j in zip(idx[:-1], idx[1:]):
                if i != j:
                    con.append(feature["id"])
                    a.append(i)
                    b.append(j)
    np.savez_compressed(out / "constellations.npz", con=np.array(con, dtype=str),
                        a=np.array(a, dtype=np.int32), b=np.array(b, dtype=np.int32))
    return len(set(con)), len(con)


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read().decode("utf-8")


def build_tetra3_db(out: Path) -> None:
    import tetra3rs

    db = tetra3rs.SolverDatabase.generate_from_gaia(
        max_fov_deg=T3_MAX_FOV, min_fov_deg=T3_MIN_FOV, star_max_magnitude=T3_MAG
    )
    db.save_to_file(str(out / "tetra3.db"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--hyg-csv", type=Path, help="локальна копія hygdata_v41.csv замість завантаження")
    ap.add_argument("--lines-json", type=Path,
                    help="локальна копія constellations.lines.json (d3-celestial) замість завантаження")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    t = time.time()
    if args.hyg_csv:
        text = args.hyg_csv.read_text(encoding="utf-8")
    else:
        text = _fetch(HYG_URL)
    n = build_star_catalog(text, args.out)
    print(f"stars.npz: {n} зір до {MAG_LIMIT}m ({time.time() - t:.1f} с)")

    lines = args.lines_json.read_text(encoding="utf-8") if args.lines_json else _fetch(LINES_URL)
    n_con, n_seg = build_constellation_lines(lines, args.out)
    print(f"constellations.npz: {n_con} сузір'їв, {n_seg} відрізків")
    (args.out / "LICENSE-d3-celestial.txt").write_text(D3C_LICENSE, encoding="utf-8")

    t = time.time()
    build_tetra3_db(args.out)
    print(f"tetra3.db: поле {T3_MIN_FOV:g}–{T3_MAX_FOV:g}°, зорі до {T3_MAG:g}m ({time.time() - t:.1f} с)")

    (args.out / "SOURCES.txt").write_text(
        "stars.npz — HYG Database v4.1 (David Nash, astronexus), CC BY-SA 4.0\n"
        f"  {HYG_URL}\n"
        "constellations.npz — лінії сузір'їв з d3-celestial, (c) 2015 Olaf Frohn, BSD 3-Clause "
        "(див. LICENSE-d3-celestial.txt)\n"
        f"  {LINES_URL}\n"
        "tetra3.db — згенеровано tetra3rs з каталогу Gaia DR3 (пакет gaia-catalog)\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
