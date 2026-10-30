// Що й як позначається на кадрі: об'єкти, шари накладки, назви, розміри маркерів.
// Спільне для переглядача, бічної панелі й експорту PNG.

import type { Body, Star } from "./api";
import { BODY_UA, CONSTELLATIONS } from "./util";

export type Layer = "stars" | "bodies" | "constellations" | "detections" | "grid" | "labels";
export type Target = { kind: "star"; item: Star } | { kind: "body"; item: Body };

export const bodyName = (name: string) => BODY_UA[name]?.name ?? name;
export const bodyGlyph = (name: string) => BODY_UA[name]?.glyph ?? "●";
export const constellationName = (abbr: string) => CONSTELLATIONS[abbr] ?? abbr;

export const targetName = (t: Target) => (t.kind === "body" ? bodyName(t.item.name) : t.item.name);

/** «α UMa · Велика Ведмедиця»: позначення (якщо назва власна) і сузір'я. */
export function starSubtitle(st: Star): string {
  return [st.proper_name ? st.designation : null, st.constellation ? constellationName(st.constellation) : null]
    .filter(Boolean).join(" · ");
}

/** Радіус кружечка зорі в пікселях екрана: що яскравіша, то більший. */
export const starRadius = (mag: number) => Math.max(3, 9 - mag * 1.05);

/** Радіус кружечка планети; Місяць і Сонце — більші. */
export const bodyRadius = (name: string) => (name === "Moon" || name === "Sun" ? 16 : 11);

/** Рівень підпису: 0 — завжди (власна назва, грецька літера), 1 — номер Флемстіда, 2 — HIP/HR. */
export function labelTier(st: Star): number {
  if (st.proper_name) return 0;
  if (/^(HIP|HR) /.test(st.name)) return 2;
  if (/^\d/.test(st.name)) return 1;
  return 0;
}

/** З якого наближення (відносно «весь кадр») показувати підписи кожного рівня. */
export const TIER_ZOOM = [0, 1.8, 3.5];
