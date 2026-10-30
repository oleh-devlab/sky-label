// Екрани головної області, крім результату: вітання, обробка, помилка, «не знайдено».

import type { Job } from "./api";
import { pickAndUpload } from "./upload";
import { $, duration, h, icon } from "./util";

const feature = (glyph: string, title: string, text: string) =>
  h("div.feature", {}, h("span", {}, glyph), h("b", {}, title), h("p", {}, text));

export function welcomeScreen(doneCount: number): HTMLElement {
  return h("div.welcome", {},
    h("div.orbit", { "aria-hidden": "true" }, h("i"), h("i"), h("i")),
    h("h1", {}, "Що там, на вашому небі?"),
    h("p.lead", {}, "Завантажте фото нічного неба — з телефона чи камери, хоч через вікно. ",
      "astro-search знайде зорі, визначить, куди дивилася камера, підпише зорі, планети й Місяць і покаже, наскільки точно все збіглося."),
    h("button.btn.primary.cta", { onclick: pickAndUpload }, icon("upload"), "Вибрати фото"),
    h("div.features", {},
      feature("✦", "Plate solving", "tetra3rs за мілісекунди, astrometry.net як запасний варіант, уточнення TAN-SIP за каталогом HYG."),
      feature("♃", "Планети й Місяць", "Положення за ефемеридою JPL DE421 на момент зйомки з вашої точки на Землі."),
      feature("◎", "Горизонт", "Висота й азимут кожної точки кадру, сітка й лінія горизонту з урахуванням рефракції."),
      feature("⌖", "Чесна точність", "Скільки зір збіглося і наскільки вони відхилилися від каталогу, у кутових секундах.")),
    h("p.muted", {}, doneCount ? `Уже розпізнано кадрів: ${doneCount}. Виберіть будь-який ліворуч.` : "Усе працює локально й офлайн: фото нікуди не відправляються."),
  );
}

const pendingStage = (job: Job) => (job.status === "queued" ? "Чекає в черзі" : "Шукаю зорі й розв'язую кадр");
const pendingElapsed = (job: Job) => duration(job.started ?? job.created);

/** Обробка триває: прев'ю (коли з'явиться) під «радаром». */
export function pendingScreen(job: Job): HTMLElement {
  const img = h("img", { src: job.status === "running" ? job.links.preview : null, alt: "", class: "missing" });
  img.addEventListener("error", () => img.classList.add("missing"));
  img.addEventListener("load", () => img.classList.remove("missing"));
  return h("div.pending", { "data-id": job.id },
    h("div.pending-img", {}, img, h("div.radar"), h("div.scanline")),
    h("div.pending-text", {},
      h("p.eyebrow", {}, job.filename),
      h("h1.pending-stage", {}, pendingStage(job)),
      h("p.muted", {}, "минуло ", h("span.pending-time", {}, pendingElapsed(job))),
      h("ol.steps", {},
        ["Читання файлу й EXIF", "Пошук зір на кадрі", "Plate solving: tetra3rs → astrometry.net", "Уточнення за каталогом HYG", "Планети, Місяць, сітка висоти"].map((t) => h("li", {}, t)))));
}

/** Оновити вже показаний екран обробки, не перемальовуючи анімацію. */
export function updatePending(el: HTMLElement, job: Job) {
  $(".pending-stage", el).textContent = pendingStage(job);
  $(".pending-time", el).textContent = pendingElapsed(job);
  const img = $<HTMLImageElement>(".pending-img img", el);
  // Прев'ю з'являється лише після читання файлу: поки задача в черзі, його не просимо
  if (img && !img.naturalWidth && job.status === "running") img.src = `${job.links.preview}?t=${Date.now()}`;
}

export function failedScreen(job: Job, onDelete: () => void): HTMLElement {
  return h("div.empty-state.failed", {},
    h("div.failed-icon", {}, "✕"),
    h("h1", {}, "Не вдалося обробити кадр"),
    h("p", {}, job.filename),
    h("pre", {}, job.error ?? "невідома помилка"),
    h("div.row", {},
      h("a.btn", { href: job.links.original }, icon("download"), "Оригінал"),
      h("button.btn.danger", { onclick: onDelete }, icon("trash"), "Видалити")));
}

export function notFoundScreen(message: string): HTMLElement {
  return h("div.empty-state", {}, h("h1", {}, "Задачу не знайдено"), h("p", {}, message), h("a.btn", { href: "#/" }, "На головну"));
}
