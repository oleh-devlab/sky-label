"""Читання фото (JPG/PNG/TIFF/HEIC і RAW/DNG) та метаданих EXIF.

На виході маємо:
- сіре зображення float32 для пошуку зір;
- кольорове 8-бітне прев'ю тієї самої орієнтації й розміру,
  щоб піксельні координати підписів збігалися з ним 1:1;
- метадані: час, місце, витримку, фокусну відстань.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import exifread
import numpy as np
from PIL import Image, ImageOps

try:  # HEIC/HEIF з айфонів; без пакета просто не підтримується
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

RAW_EXTENSIONS = {
    ".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf",
    ".orf", ".rw2", ".pef", ".srw", ".3fr", ".iiq", ".rwl", ".x3f",
}

_PHOTOMETRIC_CFA = 32803
_PHOTOMETRIC_LINEAR_RAW = 34892


class UnsupportedImage(Exception):
    pass


@dataclass
class ImageMeta:
    time_utc: datetime | None = None
    # Час з EXIF без часового поясу: в UTC перевести неможливо.
    local_time_without_offset: str | None = None
    exposure_s: float | None = None
    focal_length_mm: float | None = None
    focal_length_35mm: float | None = None
    iso: int | None = None
    make: str | None = None
    model: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_elevation_m: float | None = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["time_utc"] = self.time_utc.isoformat() if self.time_utc else None
        return d


@dataclass
class LoadedImage:
    gray: np.ndarray            # float32, H×W
    preview: np.ndarray         # uint8, H×W×3
    kind: str                   # "raster" | "raw" | "raw-linear"
    meta: ImageMeta = field(default_factory=ImageMeta)

    @property
    def height(self) -> int:
        return int(self.gray.shape[0])

    @property
    def width(self) -> int:
        return int(self.gray.shape[1])


# ---------------------------------------------------------------- EXIF


def _ratio(v) -> float:
    return float(v.num) / float(v.den) if v.den else float("nan")


def _tag(tags, name):
    v = tags.get(name)
    return None if v is None else v.values


def _first_number(tags, name) -> float | None:
    v = _tag(tags, name)
    if not v:
        return None
    x = v[0] if isinstance(v, (list, tuple)) else v
    return _ratio(x) if hasattr(x, "num") else float(x)


def _parse_offset(s: str) -> timezone | None:
    s = s.strip()
    if len(s) != 6 or s[0] not in "+-" or s[3] != ":":
        return None
    sign = 1 if s[0] == "+" else -1
    return timezone(sign * timedelta(hours=int(s[1:3]), minutes=int(s[4:6])))


def _gps_coord(tags, name: str) -> float | None:
    v = _tag(tags, f"GPS {name}")
    ref = _tag(tags, f"GPS {name}Ref")
    if not v or not ref or len(v) != 3:
        return None
    deg = _ratio(v[0]) + _ratio(v[1]) / 60 + _ratio(v[2]) / 3600
    return -deg if str(ref).strip().upper() in ("S", "W") else deg


def read_meta(path: Path) -> ImageMeta:
    with open(path, "rb") as fh:
        tags = exifread.process_file(fh, details=False)
    m = ImageMeta()
    if not tags:
        return m

    dto = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    if dto:
        try:
            local = datetime.strptime(str(dto.values).strip(), "%Y:%m:%d %H:%M:%S")
            sub = tags.get("EXIF SubSecTimeOriginal")
            if sub and str(sub.values).strip().isdigit():
                frac = str(sub.values).strip()
                local += timedelta(seconds=int(frac) / 10 ** len(frac))
            off = tags.get("EXIF OffsetTimeOriginal") or tags.get("EXIF OffsetTime")
            tz = _parse_offset(str(off.values)) if off else None
            if tz is not None:
                m.time_utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
            else:
                m.local_time_without_offset = local.isoformat()
        except ValueError:
            pass

    m.exposure_s = _first_number(tags, "EXIF ExposureTime")
    m.focal_length_mm = _first_number(tags, "EXIF FocalLength")
    m.focal_length_35mm = _first_number(tags, "EXIF FocalLengthIn35mmFilm") or None
    iso = _first_number(tags, "EXIF ISOSpeedRatings")
    m.iso = int(iso) if iso else None
    m.make = str(tags["Image Make"].values).strip() if "Image Make" in tags else None
    m.model = str(tags["Image Model"].values).strip() if "Image Model" in tags else None

    lat, lon = _gps_coord(tags, "GPSLatitude"), _gps_coord(tags, "GPSLongitude")
    if lat is not None and lon is not None and not (lat == 0 and lon == 0):
        m.gps_lat, m.gps_lon = lat, lon
        alt = _first_number(tags, "GPS GPSAltitude")
        if alt is not None:
            ref = _tag(tags, "GPS GPSAltitudeRef")
            below = bool(ref) and (ref[0] if isinstance(ref, list) else ref) == 1
            m.gps_elevation_m = -alt if below else alt
    return m


# ---------------------------------------------------------------- пікселі


def _apply_orientation(a: np.ndarray, o: int) -> np.ndarray:
    """EXIF Orientation 1…8 → зображення в правильній орієнтації."""
    if o == 2:
        return a[:, ::-1]
    if o == 3:
        return a[::-1, ::-1]
    if o == 4:
        return a[::-1, :]
    if o == 5:
        return np.swapaxes(a, 0, 1)
    if o == 6:
        return np.swapaxes(a, 0, 1)[:, ::-1]
    if o == 7:
        return np.swapaxes(a, 0, 1)[::-1, ::-1]
    if o == 8:
        return np.swapaxes(a, 0, 1)[::-1, :]
    return a


def _stretch_linear(rgb: np.ndarray, saturation: float = 0.35) -> np.ndarray:
    """Лінійні дані RAW → придатне для перегляду 8-бітне прев'ю.

    Яскравість розтягується (asinh), а колір приглушується: у темних кадрах
    значення квантовані, і повний колір дає строкаті плями на небі.
    """
    lum = rgb.mean(axis=2)
    step = max(1, lum.size // 2_000_000)
    sample = lum.ravel()[::step]
    bg = float(np.percentile(sample, 50))
    top = float(np.percentile(sample, 99.95))
    x = np.clip((lum - bg) / max(top - bg, 1e-9), 0, None)
    a = 12.0
    y = np.arcsinh(a * x) / np.arcsinh(a)
    safe = np.where(lum > 0, lum, 1.0)
    ratio = np.where(lum[..., None] > 0, rgb / safe[..., None], 1.0)
    out = y[..., None] * (1.0 + saturation * (ratio - 1.0))
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def _load_linear_dng(path: Path) -> np.ndarray | None:
    """Лінійний DNG (вже дебаєризований, напр. Samsung Expert RAW з JPEG XL).

    LibRaw такі файли часто не відкриває, тому читаємо через tifffile.
    Повертає float32 H×W×3 у діапазоні 0…1 або None, якщо це не лінійний DNG.
    """
    import tifffile

    with tifffile.TiffFile(path) as tif:
        ifd0 = tif.pages[0]
        if "DNGVersion" not in ifd0.tags:
            return None
        candidates = [ifd0] + list(ifd0.pages or [])
        main = None
        for p in candidates:
            sub = p.tags.get("NewSubfileType")
            if (sub is None or sub.value == 0) and p.photometric == _PHOTOMETRIC_LINEAR_RAW:
                main = p
                break
        if main is None:
            return None
        a = main.asarray().astype(np.float32)

        def tag(name):
            """Значення тегу як список чисел (RATIONAL → дріб), або None."""
            for p in (main, ifd0):
                if name in p.tags:
                    t = p.tags[name]
                    v = t.value if isinstance(t.value, (tuple, list)) else (t.value,)
                    if int(t.dtype) in (5, 10):          # RATIONAL, SRATIONAL: n1, d1, n2, d2…
                        return [v[i] / v[i + 1] if v[i + 1] else 0.0 for i in range(0, len(v) - 1, 2)]
                    return [float(x) for x in v]
            return None

        if a.ndim == 2:
            a = a[:, :, None]
        black = tag("BlackLevel")
        black = float(np.mean(black)) if black else 0.0
        white = tag("WhiteLevel")
        white = float(max(white)) if white else float(a.max())
        a = (a - black) / max(white - black, 1.0)

        origin, size = tag("DefaultCropOrigin"), tag("DefaultCropSize")
        if origin and size and len(origin) == 2 and len(size) == 2:
            ox, oy = (int(round(v)) for v in origin)
            sx, sy = (int(round(v)) for v in size)
            a = a[oy:oy + sy, ox:ox + sx]

        orientation = int(ifd0.tags["Orientation"].value) if "Orientation" in ifd0.tags else 1

        # Баланс білого: лінійний DNG зберігається в кольорах сенсора,
        # де зелений канал значно сильніший; без цього прев'ю зелене.
        neutral = tag("AsShotNeutral")
        if neutral and len(neutral) == 3 and a.shape[2] == 3 and min(neutral) > 0:
            a = a / np.array(neutral, dtype=np.float32)

    if a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    return np.ascontiguousarray(_apply_orientation(a[:, :, :3], orientation))


def _load_rawpy(path: Path) -> np.ndarray:
    import rawpy

    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=True,
            output_bps=16,
            gamma=(1, 1),
        )
    return rgb.astype(np.float32) / 65535.0


def load_image(path: Path) -> LoadedImage:
    path = Path(path)
    meta = read_meta(path)
    is_raw = path.suffix.lower() in RAW_EXTENSIONS

    if is_raw:
        linear, dng_error = None, None
        if path.suffix.lower() == ".dng":
            try:
                linear = _load_linear_dng(path)
            except Exception as e:  # пошкоджений файл або невідомий кодек
                dng_error = e
        if linear is not None:
            kind = "raw-linear"
        else:
            try:
                linear = _load_rawpy(path)
            except Exception as e:  # LibRaw кидає власні типи винятків
                reason = dng_error or e
                raise UnsupportedImage(f"Не вдалося прочитати RAW: {reason}") from e
            kind = "raw"
        return LoadedImage(
            gray=linear.mean(axis=2).astype(np.float32),
            preview=_stretch_linear(linear),
            kind=kind,
            meta=meta,
        )

    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("I;16", "I;16B", "I", "F"):
                arr = np.asarray(im, dtype=np.float32)
                arr = arr / max(float(arr.max()), 1.0)
                rgb = np.repeat(arr[:, :, None], 3, axis=2)
                return LoadedImage(gray=arr, preview=_stretch_linear(rgb), kind="raster", meta=meta)
            rgb8 = np.asarray(im.convert("RGB"))
    except Exception as e:
        raise UnsupportedImage(f"Не вдалося прочитати зображення: {e}") from e

    gray = rgb8.astype(np.float32).mean(axis=2) / 255.0
    return LoadedImage(gray=gray, preview=np.ascontiguousarray(rgb8), kind="raster", meta=meta)
