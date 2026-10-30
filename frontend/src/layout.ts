// Оболонка сторінки: ліва панель кадрів (смужка / висувна на телефоні), права панель
// результату, повний екран, нічний режим, довідка, короткі повідомлення.

import { load, save } from "./storage";
import { $, h, icon } from "./util";

const UI_KEY = "astro-ui";
const NIGHT_KEY = "astro-night";

export const narrow = matchMedia("(max-width: 820px)");

/** Ліва панель кадрів і права панель результату: розгорнуті чи ні (пам'ятається).
 *  Поки користувач не вибрав сам, ліва панель розгорнута лише на широких екранах:
 *  на ноутбуці вона забирає в кадру забагато місця. */
export const ui = { side: window.innerWidth >= 1500, panel: true, ...load<Partial<{ side: boolean; panel: boolean }>>(UI_KEY, {}) };
// На телефоні ліва панель висувна й за замовчуванням схована
if (narrow.matches) ui.side = false;

function apply() {
  document.body.classList.toggle("side-collapsed", !ui.side);
  document.body.classList.toggle("panel-collapsed", !ui.panel);
  $("#btn-side").setAttribute("aria-expanded", String(ui.side));
  document.querySelectorAll(".panel-toggle").forEach((b) => b.setAttribute("aria-pressed", String(ui.panel)));
}

function persist() {
  // Висунута на телефоні панель — тимчасовий стан, його не запам'ятовуємо
  save(UI_KEY, { side: narrow.matches ? true : ui.side, panel: ui.panel });
  apply();
}

export function setSide(open: boolean) {
  ui.side = open;
  persist();
}

export function setPanel(open: boolean) {
  ui.panel = open;
  persist();
}

/** Чи видно зараз форму завантаження (а не смужку чи сховану висувну панель). */
export const uploadFormVisible = () => ui.side && !narrow.matches;

export function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.querySelector(".result")?.requestFullscreen().catch(() => {});
}

/** Кнопки для панелі інструментів переглядача: права панель і повний екран. */
export function viewerButtons(): HTMLElement[] {
  return [
    h("button.icon-btn.panel-toggle", { title: "Панель результату (])", "aria-pressed": String(ui.panel), onclick: () => setPanel(!ui.panel) }, icon("panel")),
    h("button.icon-btn", { title: "Повний екран (F)", onclick: toggleFullscreen }, icon("expand")),
  ];
}

/** Коротке повідомлення внизу екрана (коли форма завантаження згорнута). */
export function toast(text: string, kind: "info" | "error" = "info") {
  const el = h(`div.toast.${kind}`, { role: kind === "error" ? "alert" : "status" }, text);
  document.body.append(el);
  setTimeout(() => el.classList.add("out"), kind === "error" ? 6000 : 2500);
  setTimeout(() => el.remove(), kind === "error" ? 6600 : 3100);
}

// ---------------------------------------------------------------- нічний режим, довідка

const isNight = () => document.documentElement.classList.contains("night");

export function toggleNight(on = !isNight()) {
  document.documentElement.classList.toggle("night", on);
  $("#btn-night").setAttribute("aria-pressed", String(on));
  save(NIGHT_KEY, on);
}

export const help = () => $<HTMLDialogElement>("#help");

export function initLayout() {
  $("#btn-side").addEventListener("click", () => setSide(!ui.side));
  $(".layout").append(h("div.scrim", { onclick: () => setSide(false) }));
  $("#btn-night").addEventListener("click", () => toggleNight());
  $("#btn-help").addEventListener("click", () => help().showModal());
  // Раніше нічний режим зберігався як "1"/"0"; JSON.parse читає й таке
  toggleNight(Boolean(load<unknown>(NIGHT_KEY, false)));
  apply();
}
