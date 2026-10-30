"""Готує дані, потрібні для роботи офлайн. Запускається один раз під час збірки образу.

1. Каталог зір HYG 4.1 → stars.npz (лише зорі до 8-ї величини).
2. База шаблонів tetra3rs для поля зору 5–90° → tetra3.db.

Ефемерида de421 окремо не завантажується: її містить пакет skyfield-data.

    python scripts/prepare_data.py --out ./catalog
    python scripts/prepare_data.py --out ./catalog --hyg-csv hygdata_v41.csv   # без інтернету
"""

from __future__ import annotations

import argparse
import csv
import io
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
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    t = time.time()
    if args.hyg_csv:
        text = args.hyg_csv.read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(HYG_URL, timeout=120) as resp:
            text = resp.read().decode("utf-8")
    n = build_star_catalog(text, args.out)
    print(f"stars.npz: {n} зір до {MAG_LIMIT}m ({time.time() - t:.1f} с)")

    t = time.time()
    build_tetra3_db(args.out)
    print(f"tetra3.db: поле {T3_MIN_FOV:g}–{T3_MAX_FOV:g}°, зорі до {T3_MAG:g}m ({time.time() - t:.1f} с)")

    (args.out / "SOURCES.txt").write_text(
        "stars.npz — HYG Database v4.1 (David Nash, astronexus), CC BY-SA 4.0\n"
        f"  {HYG_URL}\n"
        "tetra3.db — згенеровано tetra3rs з каталогу Gaia DR3 (пакет gaia-catalog)\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
