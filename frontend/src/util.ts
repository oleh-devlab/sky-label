// Дрібні допоміжні функції: DOM, форматування, назви.

type Attrs = Record<string, string | number | boolean | null | undefined | EventListener>;
type Child = Node | string | number | null | undefined | false;

/** Створити HTML-елемент: h("div.card#x", {title: "…"}, дочірні…). */
export function h<K extends keyof HTMLElementTagNameMap>(
  spec: K | `${K}.${string}` | `${K}#${string}`,
  attrs: Attrs = {},
  ...children: (Child | Child[])[]
): HTMLElementTagNameMap[K] {
  const [tagAndId, ...classes] = spec.split(".");
  const [tag, id] = tagAndId.split("#");
  const el = document.createElement(tag as K);
  if (id) el.id = id;
  if (classes.length) el.className = classes.join(" ");
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";

export function s<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: (Child | Child[])[]): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

function setAttrs(el: Element, attrs: Attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (typeof v === "function") el.addEventListener(k.replace(/^on/, ""), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

function append(el: Element, children: (Child | Child[])[]) {
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
}

export function icon(name: string): SVGSVGElement {
  return s("svg", { "aria-hidden": "true" }, s("use", { href: `#i-${name}` }));
}

export const $ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document) => root.querySelector(sel) as T;

// ---------------------------------------------------------------- числа

const nf = (digits: number) => new Intl.NumberFormat("uk-UA", { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const num = (v: number, digits = 1) => nf(digits).format(v);

/** Кутова відстань у ″ → зручний запис: 12″, 3,4′, 1,25°. */
export function arc(arcsec: number): string {
  if (Math.abs(arcsec) < 90) return `${num(arcsec, 0)}″`;
  if (Math.abs(arcsec) < 5400) return `${num(arcsec / 60, 1)}′`;
  return `${num(arcsec / 3600, 2)}°`;
}

/** Пряме сходження в годинах, хвилинах, секундах. */
export function hms(deg: number): string {
  let t = (((deg % 360) + 360) % 360) / 15;
  const hh = Math.floor(t);
  t = (t - hh) * 60;
  const mm = Math.floor(t);
  const ss = (t - mm) * 60;
  return `${hh}ʰ ${String(mm).padStart(2, "0")}ᵐ ${num(ss, 1).padStart(4, "0")}ˢ`;
}

/** Схилення в градусах, мінутах, секундах. */
export function dms(deg: number): string {
  const sign = deg < 0 ? "−" : "+";
  let t = Math.abs(deg);
  const d = Math.floor(t);
  t = (t - d) * 60;
  const m = Math.floor(t);
  const sec = Math.round((t - m) * 60);
  return `${sign}${d}° ${String(m).padStart(2, "0")}′ ${String(sec).padStart(2, "0")}″`;
}

const COMPASS = ["Пн", "ПнПнСх", "ПнСх", "СхПнСх", "Сх", "СхПдСх", "ПдСх", "ПдПдСх", "Пд", "ПдПдЗх", "ПдЗх", "ЗхПдЗх", "Зх", "ЗхПнЗх", "ПнЗх", "ПнПнЗх"];
export const compass = (az: number) => COMPASS[Math.round((((az % 360) + 360) % 360) / 22.5) % 16];

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("uk-UA", { dateStyle: "medium", timeStyle: "medium" });
}

export function ago(iso: string): string {
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 60) return "щойно";
  if (sec < 3600) return `${Math.floor(sec / 60)} хв тому`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} год тому`;
  return new Date(iso).toLocaleDateString("uk-UA", { day: "numeric", month: "short" });
}

export function duration(fromIso: string, toIso?: string | null): string {
  const sec = Math.max(0, ((toIso ? new Date(toIso) : new Date()).getTime() - new Date(fromIso).getTime()) / 1000);
  return sec < 60 ? `${Math.round(sec)} с` : `${Math.floor(sec / 60)} хв ${Math.round(sec % 60)} с`;
}

// ---------------------------------------------------------------- назви

export const BODY_UA: Record<string, { name: string; glyph: string }> = {
  Mercury: { name: "Меркурій", glyph: "☿" },
  Venus: { name: "Венера", glyph: "♀" },
  Mars: { name: "Марс", glyph: "♂" },
  Jupiter: { name: "Юпітер", glyph: "♃" },
  Saturn: { name: "Сатурн", glyph: "♄" },
  Uranus: { name: "Уран", glyph: "⛢" },
  Neptune: { name: "Нептун", glyph: "♆" },
  Moon: { name: "Місяць", glyph: "☾" },
  Sun: { name: "Сонце", glyph: "☉" },
};

export const CONSTELLATIONS: Record<string, string> = {
  And: "Андромеда", Ant: "Насос", Aps: "Райський Птах", Aqr: "Водолій", Aql: "Орел", Ara: "Жертовник",
  Ari: "Овен", Aur: "Візничий", Boo: "Волопас", Cae: "Різець", Cam: "Жирафа", Cnc: "Рак",
  CVn: "Гончі Пси", CMa: "Великий Пес", CMi: "Малий Пес", Cap: "Козеріг", Car: "Кіль", Cas: "Кассіопея",
  Cen: "Центавр", Cep: "Цефей", Cet: "Кит", Cha: "Хамелеон", Cir: "Циркуль", Col: "Голуб",
  Com: "Волосся Вероніки", CrA: "Південна Корона", CrB: "Північна Корона", Crv: "Ворон", Crt: "Чаша",
  Cru: "Південний Хрест", Cyg: "Лебідь", Del: "Дельфін", Dor: "Золота Риба", Dra: "Дракон",
  Equ: "Малий Кінь", Eri: "Ерідан", For: "Піч", Gem: "Близнята", Gru: "Журавель", Her: "Геркулес",
  Hor: "Годинник", Hya: "Гідра", Hyi: "Південна Гідра", Ind: "Індіанець", Lac: "Ящірка", Leo: "Лев",
  LMi: "Малий Лев", Lep: "Заєць", Lib: "Терези", Lup: "Вовк", Lyn: "Рись", Lyr: "Ліра",
  Men: "Столова Гора", Mic: "Мікроскоп", Mon: "Єдиноріг", Mus: "Муха", Nor: "Косинець", Oct: "Октант",
  Oph: "Змієносець", Ori: "Оріон", Pav: "Павич", Peg: "Пегас", Per: "Персей", Phe: "Фенікс",
  Pic: "Живописець", Psc: "Риби", PsA: "Південна Риба", Pup: "Корма", Pyx: "Компас", Ret: "Сітка",
  Sge: "Стріла", Sgr: "Стрілець", Sco: "Скорпіон", Scl: "Скульптор", Sct: "Щит", Ser: "Змія",
  Sex: "Секстант", Tau: "Телець", Tel: "Телескоп", Tri: "Трикутник", TrA: "Південний Трикутник",
  Tuc: "Тукан", UMa: "Велика Ведмедиця", UMi: "Мала Ведмедиця", Vel: "Вітрила", Vir: "Діва",
  Vol: "Летюча Риба", Vul: "Лисичка",
};

export const SOURCE_UA: Record<string, string> = {
  user: "вказано вручну",
  exif: "з EXIF",
  default: "за замовчуванням",
};

export const KIND_UA: Record<string, string> = {
  raster: "звичайне зображення",
  raw: "RAW",
  "raw-linear": "лінійний DNG",
};

export function download(name: string, data: Blob | string, type = "application/json") {
  const blob = typeof data === "string" ? new Blob([data], { type }) : data;
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number) {
  let t = 0;
  return (...a: A) => {
    clearTimeout(t);
    t = window.setTimeout(() => fn(...a), ms);
  };
}
