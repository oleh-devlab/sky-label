"""Черга задач: одна задача обробляється за раз в окремому процесі.

Кожна задача живе в теці data/jobs/<id>/:
    job.json       — статус і параметри
    original.<ext> — завантажений файл
    preview.jpg    — прев'ю тієї самої орієнтації й розміру
    result.json    — результат (коли готово)
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import os
import shutil
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    filename: str
    original: str
    status: str = QUEUED
    created: str = field(default_factory=_now)
    started: str | None = None
    finished: str | None = None
    error: str | None = None
    params: dict = field(default_factory=dict)


# ---------------------------------------------------- робочий процес

_worker_cfg: Config | None = None


def _worker_init(cfg: Config) -> None:
    global _worker_cfg
    _worker_cfg = cfg


def _worker_warmup() -> None:
    """Завантажити каталог, базу tetra3rs і ефемериду заздалегідь."""
    from . import catalog, sky, solve

    catalog.load(_worker_cfg.catalog_dir)
    if solve.tetra3_available(_worker_cfg.catalog_dir):
        solve._tetra3_db(str(_worker_cfg.catalog_dir / "tetra3.db"))
    sky._ephemeris()


def _worker_run(job_dir: str, original: str, params: dict) -> None:
    import warnings

    from .pipeline import Overrides, process

    warnings.filterwarnings("ignore", module="astropy")
    ov = Overrides(
        time_utc=datetime.fromisoformat(params["time"]) if params.get("time") else None,
        lat=params.get("lat"), lon=params.get("lon"),
        elevation_m=params.get("elevation_m"), fov_deg=params.get("fov_deg"),
    )
    d = Path(job_dir)
    result = process(d / original, d / "preview.jpg", _worker_cfg, ov)
    tmp = d / "result.json.tmp"
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "result.json")


# ---------------------------------------------------- менеджер


class JobManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.data_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.pool: ProcessPoolExecutor | None = None
        self._task: asyncio.Task | None = None

    # -- зберігання
    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _save(self, job: Job) -> None:
        p = self.job_dir(job.id) / "job.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def _load_existing(self) -> None:
        for d in sorted(self.root.iterdir()):
            f = d / "job.json"
            if not f.is_file():
                continue
            try:
                job = Job(**json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                log.warning("Пошкоджений %s, пропускаю", f)
                continue
            if job.status in (QUEUED, RUNNING):
                # Сервер зупинили посеред обробки: поставити в чергу знову
                job.status, job.started = QUEUED, None
                self._save(job)
                self.queue.put_nowait(job.id)
            self.jobs[job.id] = job

    # -- життєвий цикл
    async def _make_pool(self) -> None:
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)
        ctx = multiprocessing.get_context("spawn")
        self.pool = ProcessPoolExecutor(max_workers=1, mp_context=ctx,
                                        initializer=_worker_init, initargs=(self.cfg,))
        await asyncio.get_running_loop().run_in_executor(self.pool, _worker_warmup)

    async def start(self) -> None:
        await self._make_pool()
        self._load_existing()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)

    # -- операції
    def new_job_dir(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        d = self.job_dir(job_id)
        d.mkdir(parents=True)
        return job_id, d

    def submit(self, job_id: str, filename: str, original: str, params: dict) -> Job:
        job = Job(id=job_id, filename=filename, original=original, params=params)
        self._save(job)
        self.jobs[job_id] = job
        self.queue.put_nowait(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def result(self, job_id: str) -> dict | None:
        f = self.job_dir(job_id) / "result.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None

    def delete(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status == RUNNING:
            return False
        del self.jobs[job_id]          # якщо ще в черзі, воркер її пропустить
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        return True

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if job is None or job.status != QUEUED:
                continue
            job.status, job.started = RUNNING, _now()
            self._save(job)
            try:
                await loop.run_in_executor(self.pool, _worker_run, str(self.job_dir(job_id)),
                                           job.original, job.params)
                job.status = DONE
            except BrokenProcessPool:
                # Робочий процес аварійно завершився (напр. збій у нативній бібліотеці)
                log.error("Задача %s: робочий процес упав, перезапускаю", job_id)
                job.status, job.error = FAILED, "Робочий процес аварійно завершився"
                await self._make_pool()
            except Exception as e:
                log.error("Задача %s: %s", job_id, traceback.format_exc())
                job.status, job.error = FAILED, f"{type(e).__name__}: {e}"
            job.finished = _now()
            if job_id in self.jobs:
                self._save(job)
