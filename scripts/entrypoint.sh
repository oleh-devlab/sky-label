#!/bin/sh
# Запуск контейнера: спершу зібрати фронтенд, потім стартувати сервер.
#
# Залежності фронтенду (node_modules) встановлені в образ під час docker build,
# тож збірка під час запуску працює офлайн і триває кілька секунд.
# Щоб правити сторінку без перезбирання образу, змонтуйте вихідний код:
#   docker run ... -v ./frontend/src:/app/frontend/src:ro astro-search
# і просто перезапустіть контейнер.
#
# ASTRO_WEB_BUILD=0 вимикає збірку: тоді віддається версія, зібрана під час docker build.
set -eu

WEB_DIR="${ASTRO_WEB_DIR:-/app/web}"
SRC_DIR="${ASTRO_WEB_SRC:-/app/frontend}"

if [ "${ASTRO_WEB_BUILD:-1}" = "1" ]; then
  echo "entrypoint: збираю фронтенд з $SRC_DIR"
  TMP="$(mktemp -d)"
  if (cd "$SRC_DIR" && ASTRO_WEB_DIR="$TMP" node node_modules/vite/bin/vite.js build --logLevel warn); then
    # Підміняємо вміст, а не саму теку: сервер віддає файли з неї напряму
    find "$WEB_DIR" -mindepth 1 -delete
    cp -R "$TMP"/. "$WEB_DIR"/
    echo "entrypoint: фронтенд зібрано в $WEB_DIR"
  elif [ -f "$WEB_DIR/index.html" ]; then
    echo "entrypoint: УВАГА: збірка не вдалася, лишаю попередню версію сторінки" >&2
  else
    echo "entrypoint: збірка не вдалася, а попередньої версії немає" >&2
    exit 1
  fi
  rm -rf "$TMP"
fi

exec "$@"
