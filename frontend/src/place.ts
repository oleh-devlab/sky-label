// Час і місце зйомки: часові пояси, «звичне місце» в браузері, форма зміни для готового кадру.

import type { ObservationUpdate } from "./api";
import { h, icon, num } from "./util";

// ---------------------------------------------------------------- часові пояси

/** Заповнити select зсувами UTC (у хвилинах), вибравши заданий. */
export function fillTzSelect(sel: HTMLSelectElement, selectedMin = -new Date().getTimezoneOffset()) {
  sel.replaceChildren();
  for (let m = -12 * 60; m <= 14 * 60; m += 30) {
    if (m % 60 && ![-570, -210, 210, 270, 330, 390, 570, 630].includes(m)) continue;
    const sign = m < 0 ? "−" : "+";
    const hh = String(Math.floor(Math.abs(m) / 60)).padStart(2, "0");
    const mm = String(Math.abs(m) % 60).padStart(2, "0");
    sel.append(h("option", { value: String(m), selected: m === selectedMin }, `UTC${sign}${hh}:${mm}`));
  }
}

/** datetime-local + зсув у хвилинах → ISO 8601 з поясом. */
export function isoWithOffset(local: string, offsetMin: number): string {
  const sec = local.length === 16 ? `${local}:00` : local;
  const sign = offsetMin < 0 ? "-" : "+";
  const a = Math.abs(offsetMin);
  return `${sec}${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}

/** Момент UTC → значення для datetime-local у заданому поясі. */
function localInput(isoUtc: string, offsetMin: number): string {
  const d = new Date(new Date(isoUtc).getTime() + offsetMin * 60000);
  return d.toISOString().slice(0, 19);
}

// ---------------------------------------------------------------- звичне місце

export interface Place {
  lat: number;
  lon: number;
  elevation_m?: number | null;
}

const HOME_KEY = "astro-home";

/** Місце, яке береться для фото без GPS. Живе лише в цьому браузері. */
export function getHome(): Place | null {
  try {
    const p = JSON.parse(localStorage.getItem(HOME_KEY) ?? "null");
    return p && Number.isFinite(p.lat) && Number.isFinite(p.lon) ? p : null;
  } catch {
    return null;
  }
}

export function setHome(p: Place | null) {
  try {
    if (p) localStorage.setItem(HOME_KEY, JSON.stringify(p));
    else localStorage.removeItem(HOME_KEY);
  } catch {
    /* приватний режим: просто не запам'ятаємо */
  }
}

export const fmtPlace = (p: Place) => `${num(p.lat, 4)}°, ${num(p.lon, 4)}°`;

/** «49.8397, 24.0297», «49,8397 24,0297», «49.84°N 24.03°E» → два числа (як копіюють з карт). */
export function parseLatLon(text: string): [number, number] | null {
  const m = text.trim().match(/^(-?\d+(?:[.,]\d+)?)\s*°?\s*([NSПнПд]?)\s*[,;\s]\s*(-?\d+(?:[.,]\d+)?)\s*°?\s*([EWСхЗх]?)$/i);
  if (!m) return null;
  let lat = Number(m[1].replace(",", "."));
  let lon = Number(m[3].replace(",", "."));
  if (/^(S|Пд)$/i.test(m[2])) lat = -lat;
  if (/^(W|Зх)$/i.test(m[4])) lon = -lon;
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}

/** Розбирати вставлені «широта, довгота» у поле широти: заповнити обидва поля. */
export function acceptPastedPair(latInput: HTMLInputElement, lonInput: HTMLInputElement) {
  latInput.addEventListener("paste", (e) => {
    const pair = parseLatLon(e.clipboardData?.getData("text") ?? "");
    if (!pair) return;
    e.preventDefault();
    latInput.value = String(pair[0]);
    lonInput.value = String(pair[1]);
    lonInput.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

export function geolocate(): Promise<Place> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("браузер не дає координат"));
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: +p.coords.latitude.toFixed(5), lon: +p.coords.longitude.toFixed(5), elevation_m: p.coords.altitude }),
      (err) => reject(new Error(err.message)),
      { timeout: 10000 },
    );
  });
}

// ---------------------------------------------------------------- форма для готового кадру

export interface PlaceFormOptions {
  timeUtc: string | null;
  place: Place;
  /** Місце було невідоме (за замовчуванням сервера): тоді навіть незмінені координати
   *  надсилаються як підтверджені, а «запам'ятати» ввімкнено одразу. */
  placeUnknown: boolean;
  focus: "time" | "place";
  onSubmit: (body: ObservationUpdate, remember: Place | null) => Promise<void>;
  onCancel: () => void;
}

export function placeForm(o: PlaceFormOptions): HTMLElement {
  const tz = h("select", { "aria-label": "Часовий пояс" });
  fillTzSelect(tz);
  const time = h("input", { type: "datetime-local", step: "1" });
  const initialTimeFor = (off: number) => (o.timeUtc ? localInput(o.timeUtc, off) : "");
  let prevTz = Number(tz.value);
  time.value = initialTimeFor(prevTz);
  // Поки час не правили, зміна поясу лише інакше показує той самий момент зйомки
  tz.addEventListener("change", () => {
    if (time.value === initialTimeFor(prevTz)) time.value = initialTimeFor(Number(tz.value));
    prevTz = Number(tz.value);
  });

  const lat = h("input", { type: "text", inputmode: "decimal", value: String(o.place.lat), "aria-label": "Широта" });
  const lon = h("input", { type: "text", inputmode: "decimal", value: String(o.place.lon), "aria-label": "Довгота" });
  const elev = h("input", { type: "number", step: "any", value: o.place.elevation_m != null ? String(Math.round(o.place.elevation_m)) : "", placeholder: "300", "aria-label": "Висота над рівнем моря" });
  acceptPastedPair(lat, lon);
  const remember = h("input", { type: "checkbox", checked: o.placeUnknown });
  const err = h("p.form-error", { role: "alert" });
  const save = h("button.btn.primary", { type: "submit" }, "Перерахувати небо");
  const geo = h("button.btn.ghost", { type: "button" }, icon("pin"), "Моє місце зараз");
  geo.addEventListener("click", async () => {
    geo.setAttribute("disabled", "");
    try {
      const p = await geolocate();
      lat.value = String(p.lat);
      lon.value = String(p.lon);
      if (p.elevation_m != null) elev.value = String(Math.round(p.elevation_m));
    } catch (e) {
      err.textContent = `Не вдалося отримати місце: ${(e as Error).message}`;
    } finally {
      geo.removeAttribute("disabled");
    }
  });

  const form = h("form.card.place-form", {},
    h("h4", {}, "Час і місце зйомки"),
    h("p.small.muted", {}, "Від них залежать висоти, азимути, горизонт і положення Місяця. Кадр заново не розпізнається — перерахунок займає секунду."),
    h("label", {}, "Час (місцевий)", h("span.row", {}, time, tz)),
    h("div.grid2", {},
      h("label", {}, "Широта, °", lat),
      h("label", {}, "Довгота, °", lon),
      h("label", {}, "Висота, м", elev),
      h("div.geo-cell", {}, geo)),
    h("p.hint", {}, "Можна вставити в поле широти пару «49.8397, 24.0297», як її копіюють з карт: південь і захід — зі знаком мінус."),
    h("label.check", {}, remember, "Запам'ятати як звичне місце: брати його для фото без GPS"),
    err,
    h("div.row", {}, save, h("button.btn.ghost", { type: "button", onclick: () => o.onCancel() }, "Скасувати")),
  );

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    const pair = parseLatLon(`${lat.value} ${lon.value}`);
    if (!pair) {
      err.textContent = "Широта від −90 до 90, довгота від −180 до 180 (десяткові градуси).";
      return;
    }
    const body: ObservationUpdate = {};
    if (time.value && time.value !== initialTimeFor(Number(tz.value))) body.time = isoWithOffset(time.value, Number(tz.value));
    const place: Place = { lat: pair[0], lon: pair[1], elevation_m: elev.value.trim() ? Number(elev.value) : null };
    const placeChanged = place.lat !== o.place.lat || place.lon !== o.place.lon || (place.elevation_m ?? null) !== (o.place.elevation_m ?? null);
    if (placeChanged || o.placeUnknown) {
      body.lat = place.lat;
      body.lon = place.lon;
      if (place.elevation_m != null) body.elevation_m = place.elevation_m;
    }
    if (!body.time && body.lat == null) {
      if (remember.checked) await o.onSubmit({}, place);
      else o.onCancel();
      return;
    }
    save.setAttribute("disabled", "");
    try {
      await o.onSubmit(body, remember.checked ? place : null);
    } catch (e2) {
      err.textContent = (e2 as Error).message;
      save.removeAttribute("disabled");
    }
  });
  queueMicrotask(() => (o.focus === "time" ? time : lat).focus());
  return form;
}
