"""Налаштування через змінні середовища."""

import os
from dataclasses import dataclass
from pathlib import Path


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    # Де зберігаються задачі: оригінали, прев'ю, результати.
    data_dir: Path
    # Де лежать підготовлені під час збірки каталог зір і база tetra3rs.
    catalog_dir: Path
    host: str
    port: int
    max_upload_mb: int
    # Місце спостереження за замовчуванням, якщо в EXIF немає GPS
    # і користувач не передав координати.
    default_lat: float
    default_lon: float
    default_elevation_m: float
    # Скільки секунд дозволено astrometry.net на один кадр.
    astrometry_cpulimit_s: int


def load() -> Config:
    return Config(
        data_dir=Path(os.environ.get("ASTRO_DATA_DIR", "./data")),
        catalog_dir=Path(os.environ.get("ASTRO_CATALOG_DIR", "./catalog")),
        host=os.environ.get("ASTRO_HOST", "127.0.0.1"),
        port=int(os.environ.get("ASTRO_PORT", 8080)),
        max_upload_mb=int(os.environ.get("ASTRO_MAX_UPLOAD_MB", 300)),
        default_lat=_float("ASTRO_DEFAULT_LAT", 49.8397),    # Львів
        default_lon=_float("ASTRO_DEFAULT_LON", 24.0297),
        default_elevation_m=_float("ASTRO_DEFAULT_ELEVATION_M", 300),
        astrometry_cpulimit_s=int(os.environ.get("ASTRO_ASTROMETRY_CPULIMIT", 60)),
    )
