// Сторінка віддається сервером з "/", а ресурси — з "/static/".
// Під час розробки (npm run dev) запити /api проксуються на запущений бекенд.
import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/",
  build: {
    outDir: process.env.ASTRO_WEB_DIR || "dist",
    emptyOutDir: true,
    assetsInlineLimit: 0,
  },
  server: {
    proxy: { "/api": process.env.ASTRO_API || "http://127.0.0.1:8080" },
  },
});
