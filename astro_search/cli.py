"""Обробити одне фото без сервера:

    python -m astro_search.cli photo.dng --out result.json
    python -m astro_search.cli photo.jpg --time 2024-11-01T22:30:01+02:00 --lat 49.84 --lon 24.03
"""

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime
from pathlib import Path

from . import config
from .pipeline import Overrides, process


def main() -> None:
    ap = argparse.ArgumentParser(description="Plate solving і підписи для фото неба")
    ap.add_argument("image", type=Path)
    ap.add_argument("--out", type=Path, help="куди записати повний result.json")
    ap.add_argument("--preview", type=Path, default=Path("preview.jpg"))
    ap.add_argument("--time", help="ISO 8601 з часовим поясом")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--elevation-m", type=float)
    ap.add_argument("--fov-deg", type=float)
    a = ap.parse_args()

    warnings.filterwarnings("ignore", module="astropy")
    t = datetime.fromisoformat(a.time) if a.time else None
    if t is not None and t.tzinfo is None:
        ap.error("--time: вкажіть часовий пояс")
    r = process(a.image, a.preview, config.load(),
                Overrides(time_utc=t, lat=a.lat, lon=a.lon, elevation_m=a.elevation_m, fov_deg=a.fov_deg))
    if a.out:
        a.out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Розв'язано: {'так' if r['solved'] else 'ні'}   {r['timings']}")
    for w in r["warnings"]:
        print("  !", w)
    s = r["solution"]
    if s:
        c, res = s["center"], s["residual_arcsec"]
        print(f"Центр RA {c['ra_deg']:.3f}° Dec {c['dec_deg']:+.3f}°, поле {s['fov_deg'][0]:.1f}×{s['fov_deg'][1]:.1f}°, "
              f"{s['scale_arcsec_per_px']:.1f}″/px, розв'язувач {s['solver']}")
        print(f"Зір збіглося: {s['matched_stars']}, похибка rms {res['rms']}″, медіана {res['median']}″")
    for st in r["stars"][:12]:
        print(f"  {st['name']:<16} {st['designation'] or '':<9} {st['mag']:>5}m  x={st['x']:.0f} y={st['y']:.0f}")
    for b in r["bodies"]:
        nd = b["nearest_detection"]
        off = f"відхилення {nd['offset_arcsec']}″" if nd else "на кадрі не знайдено"
        print(f"  {b['name']:<16} {'':<9} {b['mag'] if b['mag'] is not None else '':>5}m  "
              f"x={b['x']:.0f} y={b['y']:.0f}  висота {b['alt_deg']:.1f}°, {off}")


if __name__ == "__main__":
    main()
