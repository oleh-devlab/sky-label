import "./style.css";

import { api, type Health, type Job } from "./api";
import { ResultPanel } from "./panels";
import { acceptPastedPair, fillTzSelect, fmtPlace, geolocate, getHome, isoWithOffset, setHome } from "./place";
import { startStarfield } from "./starfield";
import { $, ago, duration, h, icon, num } from "./util";
import { Viewer, type Layer } from "./viewer";

// ---------------------------------------------------------------- стан

let jobs: Job[] = [];
let health: Health | null = null;
let currentId: string | null = null;
let current: { viewer: Viewer; panel: ResultPanel } | null = null;
let pollTimer = 0;

const main = $("#main");
const jobList = $("#jobs");

const routeId = () => location.hash.match(/^#\/([0-9a-f]{32})$/)?.[1] ?? null;

// ---------------------------------------------------------------- стан сервера

async function loadHealth() {
  try {
    health = await api.health();
  } catch {
    health = null;
  }
  const el = $("#health");
  if (!health) {
    el.replaceChildren(h("span.pill.bad", {}, h("i"), "сервер недоступний"));
    return;
  }
  const solvers = Object.entries(health.solvers);
  el.replaceChildren(
    h(`span.pill.${health.status === "ok" ? "good" : "bad"}`, { title: `версія ${health.version}` }, h("i"),
      health.status === "ok" ? `${num(health.catalog_stars, 0)} зір у каталозі` : "немає каталогу"),
    ...solvers.map(([name, ok]) => h(`span.pill.${ok ? "good" : "off"}`, { title: ok ? "розв'язувач доступний" : "розв'язувач недоступний" }, h("i"), name)),
    ...(health.queue_length ? [h("span.pill.warn", {}, h("i"), `у черзі: ${health.queue_length}`)] : []),
  );
  renderLocHint();
}

/** Підказка у формі: яке місце візьметься для фото без GPS. */
function renderLocHint() {
  const el = $("#default-loc");
  const home = getHome();
  if (home) {
    el.replaceChildren(`Для фото без GPS береться ваше звичне місце: ${fmtPlace(home)}. `,
      h("button.link", { type: "button", onclick: () => { setHome(null); renderLocHint(); } }, "Забути"));
  } else {
    const dl = health?.default_location;
    el.replaceChildren(dl
      ? `Для фото без GPS зараз береться місце за замовчуванням сервера (${num(dl.lat, 4)}°, ${num(dl.lon, 4)}°). Вкажіть своє й позначте «запам'ятати» — далі воно братиметься саме.`
      : "");
  }
  const remember = form.elements.namedItem("remember") as HTMLInputElement;
  remember.checked = !home;
  // Підказки в полях — звичне місце, а не чиєсь чуже за замовчуванням
  (form.elements.namedItem("lat") as HTMLInputElement).placeholder = home ? String(home.lat) : "напр. 50.45";
  (form.elements.namedItem("lon") as HTMLInputElement).placeholder = home ? String(home.lon) : "напр. 30.52";
}

// ---------------------------------------------------------------- список задач

const STATUS_UA: Record<Job["status"], string> = { queued: "у черзі", running: "обробка", done: "готово", failed: "помилка" };

function renderJobs() {
  $("#jobs-count").textContent = jobs.length ? String(jobs.length) : "";
  if (!jobs.length) {
    jobList.replaceChildren(h("li.empty", {}, "Тут з'являться ваші кадри."));
    return;
  }
  jobList.replaceChildren(...jobs.map((j) => {
    const thumb = h("img", { src: j.links.preview, alt: "", loading: "lazy" });
    thumb.addEventListener("error", () => thumb.replaceWith(h("span.thumb-ph")), { once: true });
    return h("li", {},
      h(`a.job${j.id === currentId ? ".active" : ""}`, { href: `#/${j.id}` },
        h("span.thumb", {}, j.status === "done" || j.status === "failed" ? thumb : h("span.thumb-ph.spin")),
        h("span.job-meta", {},
          h("b", { title: j.filename }, j.filename),
          h("small", {}, h(`span.status.${j.status}`, {}, STATUS_UA[j.status]), " · ", ago(j.created)))));
  }));
}

async function refreshJobs() {
  clearTimeout(pollTimer);
  try {
    const prev = new Map(jobs.map((j) => [j.id, j.status]));
    jobs = await api.jobs();
    renderJobs();
    // Відкрита задача змінила статус: перемалювати
    const cur = jobs.find((j) => j.id === currentId);
    if (cur && prev.has(cur.id) && prev.get(cur.id) !== cur.status) await openJob(cur.id);
    else if (cur && cur.status !== "done" && cur.status !== "failed") renderPending(cur);
  } catch {
    /* сервер перезапускається — спробуємо пізніше */
  }
  const busy = jobs.some((j) => j.status === "queued" || j.status === "running");
  if (busy) loadHealth();
  pollTimer = window.setTimeout(refreshJobs, busy ? 1500 : 10000);
}

// ---------------------------------------------------------------- головна область

function closeCurrent() {
  current?.viewer.destroy();
  current = null;
}

async function openJob(id: string | null) {
  currentId = id;
  renderJobs();
  if (!id) {
    closeCurrent();
    renderWelcome();
    return;
  }
  let job: Job;
  try {
    job = await api.job(id);
  } catch (e) {
    closeCurrent();
    main.replaceChildren(h("div.empty-state", {}, h("h1", {}, "Задачу не знайдено"), h("p", {}, String((e as Error).message)), h("a.btn", { href: "#/" }, "На головну")));
    return;
  }
  if (currentId !== id) return; // користувач уже перейшов деінде
  if (job.status === "done" && job.result) {
    closeCurrent();
    const viewer = new Viewer(job, job.result);
    const panel = new ResultPanel(job, job.result, viewer, async () => {
      await api.remove(job.id);
      location.hash = "#/";
      refreshJobs();
    }, async (body, remember) => {
      if (Object.keys(body).length) await api.observe(job.id, body);
      if (remember) setHome(remember);
      renderLocHint();
      await openJob(job.id);
    });
    viewer.onSelect = (t) => panel.highlight(t);
    current = { viewer, panel };
    main.replaceChildren(h("div.result", {}, viewer.el, panel.el));
  } else if (job.status === "failed") {
    closeCurrent();
    main.replaceChildren(h("div.empty-state.failed", {},
      h("div.failed-icon", {}, "✕"),
      h("h1", {}, "Не вдалося обробити кадр"),
      h("p", {}, job.filename),
      h("pre", {}, job.error ?? "невідома помилка"),
      h("div.row", {},
        h("a.btn", { href: job.links.original }, icon("download"), "Оригінал"),
        h("button.btn.danger", { onclick: async () => { await api.remove(job.id); location.hash = "#/"; refreshJobs(); } }, icon("trash"), "Видалити"))));
  } else {
    closeCurrent();
    renderPending(job);
  }
}

/** Обробка триває: прев'ю (коли з'явиться) під «радаром». */
function renderPending(job: Job) {
  const existing = main.querySelector<HTMLElement>(".pending");
  const stage = job.status === "queued" ? "Чекає в черзі" : "Шукаю зорі й розв'язую кадр";
  const elapsed = job.started ? duration(job.started) : duration(job.created);
  if (existing && existing.dataset.id === job.id) {
    $(".pending-stage", existing).textContent = stage;
    $(".pending-time", existing).textContent = elapsed;
    const img = $<HTMLImageElement>(".pending-img img", existing);
    if (img && !img.naturalWidth) img.src = `${job.links.preview}?t=${Date.now()}`;
    return;
  }
  const img = h("img", { src: job.links.preview, alt: "" });
  img.addEventListener("error", () => img.classList.add("missing"));
  img.addEventListener("load", () => img.classList.remove("missing"));
  main.replaceChildren(h("div.pending", { "data-id": job.id },
    h("div.pending-img", {}, img, h("div.radar"), h("div.scanline")),
    h("div.pending-text", {},
      h("p.eyebrow", {}, job.filename),
      h("h1.pending-stage", {}, stage),
      h("p.muted", {}, "минуло ", h("span.pending-time", {}, elapsed)),
      h("ol.steps", {},
        ["Читання файлу й EXIF", "Пошук зір на кадрі", "Plate solving: tetra3rs → astrometry.net", "Уточнення за каталогом HYG", "Планети, Місяць, сітка висоти"].map((t) => h("li", {}, t))))));
}

function renderWelcome() {
  const done = jobs.filter((j) => j.status === "done").length;
  main.replaceChildren(h("div.welcome", {},
    h("div.orbit", { "aria-hidden": "true" }, h("i"), h("i"), h("i")),
    h("h1", {}, "Що там, на вашому небі?"),
    h("p.lead", {}, "Завантажте фото нічного неба — з телефона чи камери, хоч через вікно. ",
      "astro-search знайде зорі, визначить, куди дивилася камера, підпише зорі, планети й Місяць і покаже, наскільки точно все збіглося."),
    h("div.features", {},
      feature("✦", "Plate solving", "tetra3rs за мілісекунди, astrometry.net як запасний варіант, уточнення TAN-SIP за каталогом HYG."),
      feature("♃", "Планети й Місяць", "Положення за ефемеридою JPL DE421 на момент зйомки з вашої точки на Землі."),
      feature("◎", "Горизонт", "Висота й азимут кожної точки кадру, сітка й лінія горизонту з урахуванням рефракції."),
      feature("⌖", "Чесна точність", "Скільки зір збіглося і наскільки вони відхилилися від каталогу, у кутових секундах.")),
    h("p.muted", {}, done ? `Уже розпізнано кадрів: ${done}. Виберіть будь-який ліворуч.` : "Усе працює локально й офлайн: фото нікуди не відправляються."),
  ));
}

const feature = (glyph: string, title: string, text: string) => h("div.feature", {}, h("span", {}, glyph), h("b", {}, title), h("p", {}, text));

// ---------------------------------------------------------------- завантаження

const form = $<HTMLFormElement>("#upload");
const fileInput = $<HTMLInputElement>("#file");

const initTzSelect = () => fillTzSelect(form.elements.namedItem("tz") as HTMLSelectElement);

function setFile(f: File | null) {
  const dz = $("#dropzone");
  dz.classList.toggle("has-file", !!f);
  $("#dz-title").textContent = f ? f.name : "Перетягніть фото неба";
  $("#dz-sub").textContent = f ? `${num(f.size / 1048576, 1)} МБ · натисніть, щоб замінити` : "або натисніть, щоб вибрати · JPG, PNG, HEIC, RAW/DNG";
  ($("#btn-send") as HTMLButtonElement).disabled = !f;
  $("#up-error").textContent = "";
}

fileInput.addEventListener("change", () => setFile(fileInput.files?.[0] ?? null));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = fileInput.files?.[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  // Десяткова кома теж годиться: «49,84» → «49.84»
  const val = (n: string) => (form.elements.namedItem(n) as HTMLInputElement).value.trim().replace(",", ".");
  if (val("time_local")) fd.append("time", isoWithOffset(val("time_local"), Number(val("tz"))));
  for (const k of ["lat", "lon", "elevation_m", "fov_deg"]) if (val(k)) fd.append(k, val(k));
  if (!!val("lat") !== !!val("lon")) {
    $("#up-error").textContent = "Вкажіть і широту, і довготу.";
    return;
  }
  // Звичне місце: сервер візьме його, лише якщо в EXIF немає GPS
  const home = getHome();
  if (home) {
    fd.append("fallback_lat", String(home.lat));
    fd.append("fallback_lon", String(home.lon));
    if (home.elevation_m != null) fd.append("fallback_elevation_m", String(home.elevation_m));
  }
  const rememberNew = (form.elements.namedItem("remember") as HTMLInputElement).checked && val("lat") && val("lon")
    ? { lat: Number(val("lat")), lon: Number(val("lon")), elevation_m: val("elevation_m") ? Number(val("elevation_m")) : null }
    : null;

  const btn = $<HTMLButtonElement>("#btn-send");
  const bar = $("#up-progress");
  btn.disabled = true;
  bar.hidden = false;
  try {
    const job = await api.upload(fd, (p) => bar.style.setProperty("--p", String(p)));
    if (rememberNew) setHome(rememberNew);
    form.reset();
    initTzSelect();
    renderLocHint();
    setFile(null);
    location.hash = `#/${job.id}`;
    refreshJobs();
  } catch (err) {
    $("#up-error").textContent = (err as Error).message;
    btn.disabled = false;
  } finally {
    bar.hidden = true;
    bar.style.setProperty("--p", "0");
  }
});

$("#btn-geo").addEventListener("click", async () => {
  const btn = $<HTMLButtonElement>("#btn-geo");
  const set = (n: string, v: number | null | undefined) => v != null && ((form.elements.namedItem(n) as HTMLInputElement).value = String(v));
  btn.disabled = true;
  try {
    const p = await geolocate();
    set("lat", p.lat);
    set("lon", p.lon);
    set("elevation_m", p.elevation_m != null ? Math.round(p.elevation_m) : null);
  } catch (err) {
    $("#up-error").textContent = `Не вдалося отримати місце: ${(err as Error).message}`;
  } finally {
    btn.disabled = false;
  }
});
acceptPastedPair(form.elements.namedItem("lat") as HTMLInputElement, form.elements.namedItem("lon") as HTMLInputElement);

// Перетягування файлу будь-куди у вікно
let dragDepth = 0;
const overlay = $("#drop-overlay");
window.addEventListener("dragenter", (e) => {
  if (!e.dataTransfer?.types.includes("Files")) return;
  dragDepth++;
  overlay.hidden = false;
});
window.addEventListener("dragleave", () => {
  if (--dragDepth <= 0) {
    dragDepth = 0;
    overlay.hidden = true;
  }
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  overlay.hidden = true;
  const f = e.dataTransfer?.files[0];
  if (!f) return;
  const dt = new DataTransfer();
  dt.items.add(f);
  fileInput.files = dt.files;
  setFile(f);
});

// ---------------------------------------------------------------- нічний режим, клавіші

const nightBtn = $("#btn-night");
function setNight(on: boolean) {
  document.documentElement.classList.toggle("night", on);
  nightBtn.setAttribute("aria-pressed", String(on));
  try {
    localStorage.setItem("astro-night", on ? "1" : "0");
  } catch {
    /* приватний режим */
  }
}
nightBtn.addEventListener("click", () => setNight(!document.documentElement.classList.contains("night")));
try {
  setNight(localStorage.getItem("astro-night") === "1");
} catch {
  /* приватний режим */
}

const help = $<HTMLDialogElement>("#help");
$("#btn-help").addEventListener("click", () => help.showModal());

const LAYER_KEYS: Record<string, Layer> = { s: "stars", p: "bodies", c: "constellations", g: "grid", d: "detections", l: "labels" };
window.addEventListener("keydown", (e) => {
  const t = e.target as HTMLElement;
  if (e.ctrlKey || e.metaKey || e.altKey || t.matches("input, select, textarea") || help.open) return;
  const k = e.key.toLowerCase();
  if (k === "n") return setNight(!document.documentElement.classList.contains("night"));
  if (k === "?") return help.showModal();
  if (!current) return;
  const v = current.viewer;
  if (k === "+" || k === "=") v.zoomBy(1.6);
  else if (k === "-") v.zoomBy(1 / 1.6);
  else if (k === "0") v.fit();
  else if (k === "escape") v.selectTarget(null);
  else if (k === "/") {
    e.preventDefault();
    current.panel.focusSearch();
  } else if (LAYER_KEYS[k]) v.toggle(LAYER_KEYS[k]);
});

// ---------------------------------------------------------------- старт

startStarfield($<HTMLCanvasElement>("#starfield"));
initTzSelect();
renderLocHint();
window.addEventListener("hashchange", () => openJob(routeId()));
loadHealth();
setInterval(loadHealth, 30000);
refreshJobs().then(() => openJob(routeId()));
