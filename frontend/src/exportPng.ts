// Кадр у повній роздільності з накладкою, як її зараз видно (шари, межа величини).

import type { Result } from "./api";
import { bodyName, bodyRadius, constellationName, starRadius, type Layer } from "./marks";
import { num } from "./util";

type Ctx = CanvasRenderingContext2D;

function strokePolylines(ctx: Ctx, lines: [number, number][][]) {
  for (const line of lines) {
    ctx.beginPath();
    line.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.stroke();
  }
}

function circle(ctx: Ctx, x: number, y: number, r: number) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.stroke();
}

export async function exportAnnotatedPng(img: HTMLImageElement, r: Result,
  opts: { layers: Record<Layer, boolean>; magLimit: number }): Promise<Blob> {
  const { layers, magLimit } = opts;
  const { width: W, height: H } = r.image;
  await img.decode().catch(() => {});
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d")!;
  ctx.drawImage(img, 0, 0, W, H);
  const u = Math.max(1, Math.max(W, H) / 1600); // одиниця товщини/шрифту
  ctx.lineJoin = "round";

  const hz = r.horizontal;
  if (layers.grid && hz) {
    for (const g of hz.grid) {
      const horizon = g.kind === "alt" && g.value === 0;
      ctx.strokeStyle = horizon ? "rgba(251,191,36,.9)" : g.kind === "alt" ? "rgba(125,211,252,.45)" : "rgba(196,181,253,.4)";
      ctx.lineWidth = (horizon ? 2.2 : 1) * u;
      ctx.setLineDash(horizon ? [] : [6 * u, 5 * u]);
      strokePolylines(ctx, g.lines);
    }
    ctx.setLineDash([]);
  }
  const cons = layers.constellations ? r.constellations ?? [] : [];
  ctx.strokeStyle = "rgba(147,197,253,.55)";
  ctx.lineWidth = 1.6 * u;
  for (const k of cons) strokePolylines(ctx, k.lines);

  // Підписи без накладань, як і на екрані: першими йдуть найважливіші
  const placed: number[][] = [];
  const label = (t: string, x: number, y: number, color: string, force = false, center = false) => {
    ctx.font = `600 ${13 * u}px system-ui, sans-serif`;
    const w = ctx.measureText(t).width;
    if (center) x -= w / 2;
    const box = [x, y - 12 * u, x + w, y + 3 * u];
    if (!force && placed.some((p) => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) return;
    placed.push(box);
    ctx.lineWidth = 3.5 * u;
    ctx.strokeStyle = "rgba(2,6,23,.85)";
    ctx.strokeText(t, x, y);
    ctx.fillStyle = color;
    ctx.fillText(t, x, y);
  };
  if (layers.labels)
    for (const k of cons) label(constellationName(k.id).toUpperCase(), k.label[0], k.label[1], "rgba(191,219,254,.8)", false, true);
  if (layers.detections) {
    ctx.strokeStyle = "rgba(94,234,212,.6)";
    ctx.lineWidth = u;
    for (const [x, y] of r.detections) circle(ctx, x, y, 4 * u);
  }
  if (layers.bodies) {
    for (const b of r.bodies) {
      const rad = bodyRadius(b.name) * u;
      ctx.strokeStyle = "#fbbf24";
      ctx.lineWidth = 2 * u;
      circle(ctx, b.x, b.y, rad);
      if (layers.labels) label(bodyName(b.name), b.x + rad + 5 * u, b.y + 5 * u, "#fde68a");
    }
  }
  if (layers.stars) {
    for (const st of r.stars) {
      if (st.mag > magLimit) continue;
      const rad = starRadius(st.mag) * u;
      ctx.strokeStyle = st.detection ? "rgba(186,230,253,.95)" : "rgba(186,230,253,.5)";
      ctx.lineWidth = 1.4 * u;
      circle(ctx, st.x, st.y, rad);
      if (layers.labels && (st.proper_name || st.mag < 4)) label(st.name, st.x + rad + 4 * u, st.y + 4 * u, "#e0f2fe");
    }
  }
  // Підпис унизу
  const obs = r.observation;
  const foot = ["SkyLabel", obs.time_utc ? new Date(obs.time_utc).toISOString().replace(".000Z", " UTC").replace("T", " ") : null,
    `${num(obs.location.lat, 4)}°, ${num(obs.location.lon, 4)}°`].filter(Boolean).join("  ·  ");
  label(foot, 12 * u, H - 12 * u, "rgba(226,232,240,.9)", true);
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("toBlob"))), "image/png"));
}
