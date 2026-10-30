// Завантаження фото: форма в лівій панелі, «+» на смужці, кнопка на головній,
// перетягування будь-куди у вікно. Підказка, яке місце візьметься для фото без GPS.

import { api, type Health, type Job } from "./api";
import { toast, uploadFormVisible } from "./layout";
import { acceptPastedPair, fillTzSelect, fmtPlace, geolocate, getHome, isoWithOffset, setHome } from "./place";
import { $, h, num } from "./util";

let onUploaded: (job: Job) => void = () => {};
let health: () => Health | null = () => null;

const form = () => $<HTMLFormElement>("#upload");
const field = <T extends HTMLInputElement | HTMLSelectElement = HTMLInputElement>(name: string) =>
  form().elements.namedItem(name) as T;

/** Підказка у формі: яке місце візьметься для фото без GPS. */
export function renderLocHint() {
  const el = $("#default-loc");
  const home = getHome();
  if (home) {
    el.replaceChildren(`Для фото без GPS береться ваше звичне місце: ${fmtPlace(home)}. `,
      h("button.link", { type: "button", onclick: () => { setHome(null); renderLocHint(); } }, "Забути"));
  } else {
    const dl = health()?.default_location;
    el.replaceChildren(dl
      ? `Для фото без GPS зараз береться місце за замовчуванням сервера (${num(dl.lat, 4)}°, ${num(dl.lon, 4)}°). Вкажіть своє й позначте «запам'ятати» — далі воно братиметься саме.`
      : "");
  }
  field("remember").checked = !home;
  // Підказки в полях — звичне місце, а не чиєсь чуже за замовчуванням
  field("lat").placeholder = home ? String(home.lat) : "напр. 50.45";
  field("lon").placeholder = home ? String(home.lon) : "напр. 30.52";
}

/** Додати звичне місце: сервер візьме його, лише якщо в EXIF немає GPS. */
function appendHome(fd: FormData) {
  const home = getHome();
  if (home) {
    fd.append("fallback_lat", String(home.lat));
    fd.append("fallback_lon", String(home.lon));
    if (home.elevation_m != null) fd.append("fallback_elevation_m", String(home.elevation_m));
  }
}

/** Надіслати й показати прогрес і у формі, і на «+» смужки. */
async function send(fd: FormData) {
  const bar = $("#up-progress");
  const rail = $(".rail-add");
  const progress = (p: number) => {
    bar.style.setProperty("--p", String(p));
    rail.style.setProperty("--p", String(p));
  };
  bar.hidden = false;
  rail.classList.add("busy");
  try {
    onUploaded(await api.upload(fd, progress));
  } finally {
    bar.hidden = true;
    rail.classList.remove("busy");
    progress(0);
  }
}

/** Одразу завантажити файл без форми. Час і місце, якщо треба, можна вказати
 *  вже на сторінці результату. */
export async function quickUpload(f: File) {
  const fd = new FormData();
  fd.append("file", f);
  appendHome(fd);
  toast(`Завантажую ${f.name}…`);
  try {
    await send(fd);
  } catch (err) {
    toast((err as Error).message, "error");
  }
}

/** Відкрити вибір файлу (кнопка на головній): вибране завантажиться одразу. */
export const pickAndUpload = () => $<HTMLInputElement>("#rail-file").click();

function setFile(f: File | null) {
  $("#dropzone").classList.toggle("has-file", !!f);
  $("#dz-title").textContent = f ? f.name : "Перетягніть фото неба";
  $("#dz-sub").textContent = f ? `${num(f.size / 1048576, 1)} МБ · натисніть, щоб замінити` : "або натисніть, щоб вибрати · JPG, PNG, HEIC, RAW/DNG";
  $<HTMLButtonElement>("#btn-send").disabled = !f;
  $("#up-error").textContent = "";
}

async function submitForm(e: SubmitEvent) {
  e.preventDefault();
  const f = $<HTMLInputElement>("#file").files?.[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  // Десяткова кома теж годиться: «49,84» → «49.84»
  const val = (n: string) => field(n).value.trim().replace(",", ".");
  if (val("time_local")) fd.append("time", isoWithOffset(val("time_local"), Number(val("tz"))));
  for (const k of ["lat", "lon", "elevation_m", "fov_deg"]) if (val(k)) fd.append(k, val(k));
  if (!!val("lat") !== !!val("lon")) {
    $("#up-error").textContent = "Вкажіть і широту, і довготу.";
    return;
  }
  appendHome(fd);
  const rememberNew = field("remember").checked && val("lat") && val("lon")
    ? { lat: Number(val("lat")), lon: Number(val("lon")), elevation_m: val("elevation_m") ? Number(val("elevation_m")) : null }
    : null;

  const btn = $<HTMLButtonElement>("#btn-send");
  btn.disabled = true;
  try {
    await send(fd);
    if (rememberNew) setHome(rememberNew);
    form().reset();
    fillTzSelect(field<HTMLSelectElement>("tz"));
    renderLocHint();
    setFile(null);
  } catch (err) {
    $("#up-error").textContent = (err as Error).message;
    btn.disabled = false;
  }
}

async function fillMyPlace() {
  const btn = $<HTMLButtonElement>("#btn-geo");
  const set = (n: string, v: number | null | undefined) => v != null && (field(n).value = String(v));
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
}

/** Перетягування файлу будь-куди у вікно. */
function bindDrop() {
  let depth = 0;
  const overlay = $("#drop-overlay");
  window.addEventListener("dragenter", (e) => {
    if (!e.dataTransfer?.types.includes("Files")) return;
    depth++;
    overlay.hidden = false;
  });
  window.addEventListener("dragleave", () => {
    if (--depth <= 0) {
      depth = 0;
      overlay.hidden = true;
    }
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    overlay.hidden = true;
    const f = e.dataTransfer?.files[0];
    if (!f) return;
    // Форму не видно — завантажити одразу; інакше покласти файл у форму, щоб дописати час і місце
    if (!uploadFormVisible()) {
      quickUpload(f);
      return;
    }
    const dt = new DataTransfer();
    dt.items.add(f);
    $<HTMLInputElement>("#file").files = dt.files;
    setFile(f);
  });
}

export function initUpload(opts: { onUploaded: (job: Job) => void; health: () => Health | null }) {
  ({ onUploaded, health } = opts);
  const fileInput = $<HTMLInputElement>("#file");
  fileInput.addEventListener("change", () => setFile(fileInput.files?.[0] ?? null));
  form().addEventListener("submit", submitForm);
  const railFile = $<HTMLInputElement>("#rail-file");
  railFile.addEventListener("change", () => {
    const f = railFile.files?.[0];
    railFile.value = "";
    if (f) quickUpload(f);
  });
  $("#btn-geo").addEventListener("click", fillMyPlace);
  acceptPastedPair(field("lat"), field("lon"));
  bindDrop();
  fillTzSelect(field<HTMLSelectElement>("tz"));
  renderLocHint();
}
