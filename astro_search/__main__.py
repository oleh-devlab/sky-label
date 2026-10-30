"""Запуск сервера: python -m astro_search"""

import logging

from aiohttp import web

from . import config
from .server import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    web.run_app(create_app(cfg), host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
