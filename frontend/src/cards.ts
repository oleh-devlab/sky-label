// Картка об'єкта (зорі чи планети), що відкривається кліком на кадрі.

import type { Body, Star } from "./api";
import { bodyGlyph, bodyName, starSubtitle, type Target } from "./marks";
import { arc, compass, dms, h, hms, num } from "./util";

type Row = [string, string | Node];

function starCard(st: Star): [Node[], Row[]] {
  const rows: Row[] = [
    ["Зоряна величина", `${num(st.mag, 2)}m`],
    ["α / δ (J2000)", `${hms(st.ra_deg)}  ${dms(st.dec_deg)}`],
  ];
  if (st.alt_refracted_deg != null) rows.push(["Висота / азимут", `${num(st.alt_refracted_deg, 2)}° · ${num(st.az_deg!, 1)}° ${compass(st.az_deg!)}`]);
  const found = st.detection && h("span.ok", {}, `знайдена, відхилення ${arc(st.detection.offset_arcsec)}`);
  rows.push(["На кадрі", !found ? h("span.muted", {}, "не знайдена серед точок")
    : st.detection!.in_fit === false
      ? h("span", {}, found, h("br"), h("small.muted", {}, "у підгонку не ввійшла: пересвічена або тісна подвійна"))
      : found]);
  const ids = [st.hip ? `HIP ${st.hip}` : null, st.hr ? `HR ${st.hr}` : null].filter(Boolean).join(" · ");
  if (ids) rows.push(["Каталоги", ids]);
  return [[h("h3", {}, st.name), h("p.sub", {}, starSubtitle(st))], rows];
}

function bodyCard(b: Body): [Node[], Row[]] {
  const rows: Row[] = [];
  if (b.mag != null) rows.push(["Зоряна величина", `${num(b.mag, 2)}m`]);
  rows.push(["Відстань", b.distance_au < 0.01 ? `${num(b.distance_au * 149597870.7, 0)} км` : `${num(b.distance_au, 3)} а. о.`]);
  rows.push(["α / δ", `${hms(b.ra_deg)}  ${dms(b.dec_deg)}`]);
  rows.push(["Висота / азимут", `${num(b.alt_refracted_deg, 2)}° · ${num(b.az_deg, 1)}° ${compass(b.az_deg)}`]);
  const nd = b.nearest_detection;
  rows.push(["Перевірка", nd ? h("span.ok", {}, `найближча точка за ${arc(nd.offset_arcsec)} (${num(nd.offset_px, 1)} px)`) : h("span.muted", {}, "яскравої точки поруч немає")]);
  return [[h("h3", {}, `${bodyGlyph(b.name)} ${bodyName(b.name)}`), h("p.sub", {}, "положення за ефемеридою JPL DE421")], rows];
}

/** Заголовок і таблиця властивостей об'єкта. */
export function targetCard(t: Target): Node[] {
  const [title, rows] = t.kind === "star" ? starCard(t.item) : bodyCard(t.item);
  return [...title, h("dl", {}, rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))];
}
