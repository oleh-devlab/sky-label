# astro-search: локальний сервіс plate solving для фото нічного неба.
#
#   docker build -t astro-search .
#   docker run --rm -p 127.0.0.1:8080:8080 -v astro-search-data:/data astro-search
#
# Інтернет потрібен лише під час збірки. Готовий контейнер працює офлайн.
# Фронтенд збирається під час кожного запуску контейнера (scripts/entrypoint.sh).

# ---- Node і залежності фронтенду
FROM node:22-bookworm-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Перевірка типів під час збірки образу: помилки видно одразу, а не на запуску
RUN npm run typecheck

# ---- основний образ
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# astrometry.net — запасний розв'язувач; індекси Tycho-2 для поля від ~1° і ширше
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates \
      astrometry.net astrometry-data-tycho2-10-19 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH=/opt/venv/bin:$PATH

# Каталог зір HYG і база tetra3rs (~85 МБ)
COPY scripts/ scripts/
RUN python scripts/prepare_data.py --out /opt/astro-catalog

# Node потрібен у робочому образі: фронтенд збирається під час запуску контейнера
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /app/frontend/ frontend/

COPY astro_search/ astro_search/

RUN useradd --system --uid 10001 --create-home app \
 && mkdir -p /data /app/web \
 && chown -R app:app /data /app/web /app/frontend \
 && chmod +x scripts/entrypoint.sh
USER app

# Перша збірка ще в образі: запасний варіант, якщо на запуску збірка не вдасться
RUN cd frontend && ASTRO_WEB_DIR=/app/web node node_modules/vite/bin/vite.js build --logLevel warn

ENV ASTRO_DATA_DIR=/data \
    ASTRO_CATALOG_DIR=/opt/astro-catalog \
    ASTRO_WEB_DIR=/app/web \
    ASTRO_HOST=0.0.0.0 \
    ASTRO_PORT=8080
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["python", "-m", "astro_search"]
