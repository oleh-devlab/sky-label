"""HTTP API (aiohttp).

    GET    /api/health                стан сервісу: розв'язувачі, каталог
    POST   /api/jobs                  завантажити фото (multipart), отримати id задачі
    GET    /api/jobs                  список задач
    GET    /api/jobs/{id}             статус і, коли готово, результат
    GET    /api/jobs/{id}/preview     прев'ю (JPEG) у тих самих піксельних координатах
    GET    /api/jobs/{id}/original    завантажений оригінал
    DELETE /api/jobs/{id}             видалити задачу
    POST   /api/jobs/{id}/observation новий час і/або місце для готового кадру (JSON),
                                      небо перераховується без повторного розпізнавання

Поля форми POST /api/jobs:
    file          фото (обов'язково): JPG/PNG/TIFF/HEIC або RAW/DNG
    time          час зйомки ISO 8601 з часовим поясом, напр. 2024-11-01T22:30:01+02:00
    lat, lon      координати місця зйомки в градусах
    elevation_m   висота над рівнем моря, м
    fov_deg       підказка: горизонтальне поле зору, градуси
    fallback_lat, fallback_lon, fallback_elevation_m
                  «звичне місце»: береться, лише якщо в EXIF немає GPS
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from . import __version__, catalog, pipeline, solve
from .config import Config
from .ingest import RAW_EXTENSIONS
from .jobs import DONE, JobManager

log = logging.getLogger(__name__)

RASTER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic", ".heif", ".bmp"}
ALLOWED_EXTENSIONS = RASTER_EXTENSIONS | RAW_EXTENSIONS
JOB_ID = r"{id:[0-9a-f]{32}}"
_dumps = functools.partial(json.dumps, ensure_ascii=False)

CFG = web.AppKey("cfg", Config)
JOBS = web.AppKey("jobs", JobManager)


class BadRequest(Exception):
    pass


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def _error(status: int, message: str) -> web.Response:
    return _json({"error": message}, status=status)


def _job_json(job, with_result: dict | None = None) -> dict:
    d = asdict(job)
    base = f"/api/jobs/{job.id}"
    d["links"] = {"self": base, "preview": f"{base}/preview", "original": f"{base}/original"}
    if with_result is not None:
        d["result"] = with_result
    return d


# ---------------------------------------------------------------- параметри


def _parse_float(name: str, raw: str, lo: float, hi: float) -> float:
    try:
        v = float(raw)
    except ValueError:
        raise BadRequest(f"{name}: очікується число") from None
    if not math.isfinite(v) or not lo <= v <= hi:
        raise BadRequest(f"{name}: має бути в межах {lo:g}…{hi:g}")
    return v


def _parse_params(fields: dict[str, str]) -> dict:
    p: dict = {}
    if fields.get("time"):
        try:
            t = datetime.fromisoformat(fields["time"].strip())
        except ValueError:
            raise BadRequest("time: очікується ISO 8601, напр. 2024-11-01T22:30:01+02:00") from None
        if t.tzinfo is None:
            raise BadRequest("time: вкажіть часовий пояс (+02:00 або Z)")
        p["time"] = t.astimezone(timezone.utc).isoformat()
    has_lat, has_lon = bool(fields.get("lat")), bool(fields.get("lon"))
    if has_lat != has_lon:
        raise BadRequest("lat і lon передаються разом")
    if has_lat:
        p["lat"] = _parse_float("lat", fields["lat"], -90, 90)
        p["lon"] = _parse_float("lon", fields["lon"], -180, 180)
    if fields.get("elevation_m"):
        p["elevation_m"] = _parse_float("elevation_m", fields["elevation_m"], -500, 9000)
    if fields.get("fov_deg"):
        p["fov_deg"] = _parse_float("fov_deg", fields["fov_deg"], 1, 180)
    has_flat, has_flon = bool(fields.get("fallback_lat")), bool(fields.get("fallback_lon"))
    if has_flat != has_flon:
        raise BadRequest("fallback_lat і fallback_lon передаються разом")
    if has_flat:
        p["fallback_lat"] = _parse_float("fallback_lat", fields["fallback_lat"], -90, 90)
        p["fallback_lon"] = _parse_float("fallback_lon", fields["fallback_lon"], -180, 180)
    if fields.get("fallback_elevation_m"):
        p["fallback_elevation_m"] = _parse_float("fallback_elevation_m", fields["fallback_elevation_m"], -500, 9000)
    return p


# ---------------------------------------------------------------- обробники


async def health(request: web.Request) -> web.Response:
    cfg = request.app[CFG]
    try:
        n_stars = len(catalog.load(cfg.catalog_dir))
    except FileNotFoundError:
        n_stars = 0
    return _json({
        "status": "ok" if n_stars else "no_catalog",
        "version": __version__,
        "solvers": {"tetra3rs": solve.tetra3_available(cfg.catalog_dir),
                    "astrometry.net": solve.astrometry_available()},
        "catalog_stars": n_stars,
        "ephemeris": "de421",
        "default_location": {"lat": cfg.default_lat, "lon": cfg.default_lon,
                             "elevation_m": cfg.default_elevation_m},
        "queue_length": request.app[JOBS].queue.qsize(),
    })


async def create_job(request: web.Request) -> web.Response:
    cfg, jobs = request.app[CFG], request.app[JOBS]
    if not request.content_type.startswith("multipart/"):
        return _error(415, "Очікується multipart/form-data з полем file")
    limit = cfg.max_upload_mb * 1024 * 1024
    reader = await request.multipart()
    fields: dict[str, str] = {}
    job_id = job_dir = original = filename = None
    try:
        async for part in reader:
            if part.name == "file" and part.filename:
                if job_id is not None:
                    raise BadRequest("Лише один файл на задачу")
                filename = Path(part.filename).name
                ext = Path(filename).suffix.lower()
                if ext not in ALLOWED_EXTENSIONS:
                    raise BadRequest(f"Непідтримуваний тип файлу: {ext or 'без розширення'}")
                job_id, job_dir = jobs.new_job_dir()
                original = f"original{ext}"
                size = 0
                with open(job_dir / original, "wb") as fh:
                    while chunk := await part.read_chunk(1 << 20):
                        size += len(chunk)
                        if size > limit:
                            raise BadRequest(f"Файл більший за {cfg.max_upload_mb} МБ")
                        fh.write(chunk)
                if size == 0:
                    raise BadRequest("Порожній файл")
            elif part.name:
                fields[part.name] = (await part.text())[:200]
        if job_id is None:
            raise BadRequest("Немає поля file")
        params = _parse_params(fields)
    except BadRequest as e:
        if job_dir is not None:
            shutil.rmtree(job_dir, ignore_errors=True)
        return _error(400, str(e))

    job = jobs.submit(job_id, filename, original, params)
    return _json(_job_json(job), status=202)


async def list_jobs(request: web.Request) -> web.Response:
    jobs = sorted(request.app[JOBS].jobs.values(), key=lambda j: j.created, reverse=True)
    return _json([_job_json(j) for j in jobs])


async def get_job(request: web.Request) -> web.Response:
    jobs = request.app[JOBS]
    job = jobs.get(request.match_info["id"])
    if job is None:
        return _error(404, "Задачу не знайдено")
    return _json(_job_json(job, jobs.result(job.id) if job.status == DONE else None))


async def get_preview(request: web.Request) -> web.StreamResponse:
    jobs = request.app[JOBS]
    job = jobs.get(request.match_info["id"])
    f = jobs.job_dir(request.match_info["id"]) / "preview.jpg"
    if job is None or not f.is_file():
        return _error(404, "Прев'ю ще немає")
    return web.FileResponse(f, headers={"Cache-Control": "private, max-age=3600"})


async def get_original(request: web.Request) -> web.StreamResponse:
    jobs = request.app[JOBS]
    job = jobs.get(request.match_info["id"])
    if job is None:
        return _error(404, "Задачу не знайдено")
    return web.FileResponse(jobs.job_dir(job.id) / job.original, headers={
        "Content-Disposition": f'attachment; filename="{job.filename}"'})


async def delete_job(request: web.Request) -> web.Response:
    jobs = request.app[JOBS]
    job_id = request.match_info["id"]
    if jobs.get(job_id) is None:
        return _error(404, "Задачу не знайдено")
    if not jobs.delete(job_id):
        return _error(409, "Задача зараз обробляється")
    return web.Response(status=204)


async def set_observation(request: web.Request) -> web.Response:
    """Новий час і/або місце: висоти, планети, Місяць і горизонт перераховуються за секунду."""
    cfg, jobs = request.app[CFG], request.app[JOBS]
    job_id = request.match_info["id"]
    job = jobs.get(job_id)
    if job is None:
        return _error(404, "Задачу не знайдено")
    if job.status != DONE:
        return _error(409, "Кадр ще не оброблено")
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return _error(400, "Очікується JSON-об'єкт: time, lat, lon, elevation_m")
    allowed = {"time", "lat", "lon", "elevation_m"}
    fields = {k: str(v) for k, v in body.items() if k in allowed and v not in (None, "")}
    try:
        params = _parse_params(fields)
    except BadRequest as e:
        return _error(400, str(e))
    if not params:
        return _error(400, "Вкажіть час (time) або місце (lat і lon)")
    result = jobs.result(job_id)
    if result is None or not result.get("solved"):
        return _error(409, "Кадр не розв'язано: висоти й планети рахувати нема від чого")
    when = datetime.fromisoformat(params["time"]) if "time" in params else None
    result = await asyncio.get_running_loop().run_in_executor(None, functools.partial(
        pipeline.reobserve, result, cfg, when, params.get("lat"), params.get("lon"), params.get("elevation_m")))
    job = jobs.save_result(job_id, result, params)
    return _json(_job_json(job, result))


async def index(request: web.Request) -> web.StreamResponse:
    page = request.app[CFG].web_dir / "index.html"
    if page.is_file():
        # Сторінка посилається на ресурси з хешем в імені, тож її саму не кешуємо
        return web.FileResponse(page, headers={"Cache-Control": "no-cache"})
    return web.Response(text=(
        "astro-search працює, але вебсторінку не зібрано (немає index.html у ASTRO_WEB_DIR).\n"
        "Зберіть її: cd frontend && npm ci && npm run build. API доступне за адресою /api/.\n"),
        content_type="text/plain", charset="utf-8")


# ---------------------------------------------------------------- застосунок


def create_app(cfg: Config) -> web.Application:
    app = web.Application(client_max_size=(cfg.max_upload_mb + 1) * 1024 * 1024)
    app[CFG] = cfg
    app[JOBS] = JobManager(cfg)

    async def on_startup(app: web.Application) -> None:
        await app[JOBS].start()
        log.info("Готово: каталог %s, дані %s", cfg.catalog_dir, cfg.data_dir)

    async def on_cleanup(app: web.Application) -> None:
        await app[JOBS].stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/jobs", create_job)
    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_get(f"/api/jobs/{JOB_ID}", get_job)
    app.router.add_delete(f"/api/jobs/{JOB_ID}", delete_job)
    app.router.add_get(f"/api/jobs/{JOB_ID}/preview", get_preview)
    app.router.add_get(f"/api/jobs/{JOB_ID}/original", get_original)
    app.router.add_post(f"/api/jobs/{JOB_ID}/observation", set_observation)
    # Тека може з'явитися вже після старту (збірка фронтенду), тож додаємо маршрут завжди
    cfg.web_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/static/", cfg.web_dir)
    return app
