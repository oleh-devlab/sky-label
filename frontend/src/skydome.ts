// Мініатюрне небо: азимутальна проєкція з зенітом у центрі.
// Показує, куди дивилася камера: контур кадру, зорі й планети з кадру.

import type { Result } from "./api";
import { BODY_UA, s } from "./util";

const R = 100;

/** Висота/азимут → точка на диску. Північ угорі, схід ліворуч (як дивитися на небо знизу). */
function project(alt: number, az: number): [number, number] {
  const r = (R * (90 - Math.max(-10, alt))) / 90;
  const a = (az * Math.PI) / 180;
  return [-r * Math.sin(a), -r * Math.cos(a)];
}

/** Афінне наближення пікселі → площина диска за зорями з відомими висотою й азимутом. */
function fitFrame(r: Result): [number, number][] | null {
  const pts = r.stars.filter((st) => st.alt_deg != null && st.az_deg != null);
  if (pts.length < 4) return null;
  // Нормальні рівняння для u = a·x + b·y + c (і окремо для v)
  const M = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  const bu = [0, 0, 0];
  const bv = [0, 0, 0];
  for (const st of pts) {
    const [u, v] = project(st.alt_deg!, st.az_deg!);
    const row = [st.x, st.y, 1];
    for (let i = 0; i < 3; i++) {
      for (let j = 0; j < 3; j++) M[i][j] += row[i] * row[j];
      bu[i] += row[i] * u;
      bv[i] += row[i] * v;
    }
  }
  const cu = solve3(M, bu);
  const cv = solve3(M, bv);
  if (!cu || !cv) return null;
  const { width: W, height: H } = r.image;
  const outline: [number, number][] = [];
  const edge = (x0: number, y0: number, x1: number, y1: number) => {
    for (let i = 0; i < 8; i++) {
      const x = x0 + ((x1 - x0) * i) / 8;
      const y = y0 + ((y1 - y0) * i) / 8;
      outline.push([cu[0] * x + cu[1] * y + cu[2], cv[0] * x + cv[1] * y + cv[2]]);
    }
  };
  edge(0, 0, W, 0);
  edge(W, 0, W, H);
  edge(W, H, 0, H);
  edge(0, H, 0, 0);
  return outline;
}

function solve3(M: number[][], b: number[]): number[] | null {
  const det = (m: number[][]) =>
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  const d = det(M);
  if (Math.abs(d) < 1e-12) return null;
  return [0, 1, 2].map((k) => det(M.map((row, i) => row.map((v, j) => (j === k ? b[i] : v)))) / d);
}

export function skyDome(r: Result): SVGSVGElement | null {
  const hz = r.horizontal;
  if (!hz) return null;
  const svg = s("svg", { class: "skydome", viewBox: "-122 -122 244 244", role: "img", "aria-label": "Напрямок кадру на небі" });
  const defs = s("defs", {},
    s("radialGradient", { id: "dome-bg" },
      s("stop", { offset: "0", "stop-color": "#1e2a5a" }),
      s("stop", { offset: "1", "stop-color": "#0a0f24" })),
  );
  svg.append(defs, s("circle", { r: String(R), class: "dome", fill: "url(#dome-bg)" }));
  for (const alt of [30, 60]) svg.append(s("circle", { r: String((R * (90 - alt)) / 90), class: "dome-alt" }));
  for (let az = 0; az < 360; az += 45) {
    const [x, y] = project(0, az);
    svg.append(s("line", { x1: "0", y1: "0", x2: x.toFixed(1), y2: y.toFixed(1), class: "dome-az" }));
  }
  for (const [az, t] of [[0, "Пн"], [90, "Сх"], [180, "Пд"], [270, "Зх"]] as const) {
    const [x, y] = project(-9, az);
    svg.append(s("text", { x: x.toFixed(1), y: (y + 4).toFixed(1), class: "dome-card", "text-anchor": "middle" }, t));
  }

  const outline = fitFrame(r);
  if (outline) {
    svg.append(s("path", { d: "M" + outline.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join("L") + "Z", class: "dome-frame" }));
  }
  for (const st of r.stars) {
    if (st.alt_deg == null || st.mag > 5) continue;
    const [x, y] = project(st.alt_deg, st.az_deg!);
    svg.append(s("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: Math.max(0.6, 2.2 - st.mag * 0.35).toFixed(2), class: "dome-star" }));
  }
  for (const b of r.bodies) {
    const [x, y] = project(b.alt_deg, b.az_deg);
    svg.append(s("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: "3", class: "dome-body" }, s("title", {}, BODY_UA[b.name]?.name ?? b.name)));
  }
  const [cx, cy] = project(hz.center.alt_deg, hz.center.az_deg);
  svg.append(
    s("line", { x1: "0", y1: "0", x2: cx.toFixed(1), y2: cy.toFixed(1), class: "dome-ray" }),
    s("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: "3.2", class: "dome-center" }),
    s("circle", { r: "1.6", class: "dome-zenith" }),
  );
  return svg;
}
