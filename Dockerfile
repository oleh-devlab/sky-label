# astro-search: локальний сервіс plate solving для фото нічного неба.
#
#   docker build -t astro-search .
#   docker run --rm -p 127.0.0.1:8080:8080 -v astro-search-data:/data astro-search
#
# Інтернет потрібен лише під час збірки. Готовий контейнер працює офлайн.

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

COPY astro_search/ astro_search/

RUN useradd --system --uid 10001 --create-home app \
 && mkdir -p /data && chown app:app /data
USER app

ENV ASTRO_DATA_DIR=/data \
    ASTRO_CATALOG_DIR=/opt/astro-catalog \
    ASTRO_HOST=0.0.0.0 \
    ASTRO_PORT=8080
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

CMD ["python", "-m", "astro_search"]
