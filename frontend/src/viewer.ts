// Переглядач кадру: прев'ю + SVG-накладка (зорі, планети, точки, сітка висоти/азимута).
// Масштабування коліщатком і щипком, перетягування, підписи без накладань.

import type { Job, Result } from "./api";
import { targetCard } from "./cards";
import { exportAnnotatedPng } from "./exportPng";
import { TIER_ZOOM, bodyGlyph, bodyName, bodyRadius, constellationName, labelTier, starRadius, type Layer, type Target } from "./marks";
import { arc, compass, dms, h, hms, icon, num, s } from "./util";
import { altAz, makeWcs, type Wcs } from "./wcs";

const LAYER_UA: Record<Layer, [string, string]> = {
  stars: ["Зорі", "S"],
  bodies: ["Планети", "P"],
  constellations: ["Сузір'я", "C"],
  grid: ["Сітка", "G"],
  detections: ["Точки", "D"],
  labels: ["Підписи", "L"],
};

interface Marker {
  target: Target | null;
  x: number;
  y: number;
  g: SVGGElement;
  label: SVGTextElement | null;
  labelW: number;
  r: number;
  priority: number;
  mag: number;
  /** Рівень підпису: 0 — завжди (власна назва, грецька літера), 1 — номер Флемстіда, 2 — HIP/HR. */
  tier: number;
}

/** Скільки кружечків зір прийнятно на весь кадр: за замовчуванням межа величини підбирається під це. */
const DEFAULT_MAX_STARS = 250;

/** Що зберегти, перемальовуючи той самий кадр (напр. після зміни часу чи місця). */
export interface ViewState {
  sc: number;
  tx: number;
  ty: number;
  magLimit: number;
  layers: Record<Layer, boolean>;
}

const FONT = "500 12px Inter, system-ui, sans-serif";
let measureCtx: CanvasRenderingContext2D | null = null;
function textWidth(t: string) {
  measureCtx ??= document.createElement("canvas").getContext("2d")!;
  measureCtx.font = FONT;
  return measureCtx.measureText(t).width;
}

export class Viewer {
  readonly el: HTMLElement;
  private stage: HTMLElement;
  private img: HTMLImageElement;
  private svg: SVGSVGElement;
  private gridG: SVGGElement;
  private gridLabels: SVGGElement;
  /** Підписи ліній сітки: ставляться на першу видиму точку своєї лінії. */
  private gridLabelEls: { el: SVGTextElement; lines: [number, number][][] }[] = [];
  private constG: SVGGElement;
  private constLabels: { el: SVGTextElement; x: number; y: number; w: number }[] = [];
  private markersG: SVGGElement;
  private popover: HTMLElement;
  private readout: HTMLElement;
  private scaleBar: HTMLElement;
  private markers: Marker[] = [];
  private wcs: Wcs | null;
  private W: number;
  private H: number;
  private sc = 1;
  private tx = 0;
  private ty = 0;
  private fitScale = 1;
  private raf = 0;
  private anim = 0;
  private selected: Marker | null = null;
  private layers: Record<Layer, boolean> = { stars: true, bodies: true, constellations: true, grid: true, detections: false, labels: true };
  magLimit = 6.5;
  /** Сюди зовнішній код додає свої кнопки (права панель, повний екран). */
  readonly tools: HTMLElement = h("div.view-tools");
  private lastSize: [number, number] = [0, 0];
  onSelect: (t: Target | null) => void = () => {};
  private ro: ResizeObserver;

  constructor(
    readonly job: Job,
    readonly result: Result,
  ) {
    this.W = result.image.width;
    this.H = result.image.height;
    this.wcs = result.solution ? makeWcs(result.solution.wcs_header) : null;
    if (!result.horizontal) this.layers.grid = false;
    if (!result.constellations?.length) this.layers.constellations = false;
    if (!result.solved) this.layers.detections = true;
    // Найслабша межа, за якої кружечків не забагато (на ширококутному кадрі їх сотні)
    for (const lim of [6.5, 6, 5.5, 5, 4.5]) {
      this.magLimit = lim;
      if (result.stars.filter((st) => st.mag <= lim).length <= DEFAULT_MAX_STARS) break;
    }

    this.img = h("img.photo", { src: job.links.preview, alt: job.filename, draggable: "false" });
    this.img.style.width = `${this.W}px`;
    this.img.style.height = `${this.H}px`;
    this.gridG = s("g", { class: "grid" });
    this.gridLabels = s("g", { class: "grid-labels" });
    this.constG = s("g", { class: "const" });
    const constLabelsG = s("g", { class: "const-labels" });
    this.markersG = s("g", { class: "markers" });
    this.svg = s("svg", { class: "overlay" }, this.gridG, this.constG, this.gridLabels, constLabelsG, this.markersG);
    this.popover = h("div.popover", { hidden: true });
    this.readout = h("div.readout");
    this.scaleBar = h("div.scalebar");
    this.stage = h("div.stage", { tabindex: "0", "aria-label": "Кадр із накладкою" }, this.img, this.svg, this.popover);

    this.el = h("div.viewer", {}, this.toolbar(), this.stage, this.compassWidget(), this.scaleBar, this.readout);

    this.buildGrid();
    this.buildConstellations(constLabelsG);
    this.buildMarkers();
    this.bindPointer();
    this.applyLayerClasses();

    this.ro = new ResizeObserver(() => {
      const { clientWidth: cw, clientHeight: ch } = this.stage;
      const [pw, ph] = this.lastSize;
      this.lastSize = [cw, ch];
      const wasFit = Math.abs(this.sc / this.fitScale - 1) < 1e-3;
      this.computeFit();
      if (!pw || wasFit) {
        // Перший показ або вид «весь кадр»: вписати заново під новий розмір
        this.fit(false);
      } else {
        // Інакше лишити на місці те, що було в центрі (панелі згортаються з боків)
        this.tx += (cw - pw) / 2;
        this.ty += (ch - ph) / 2;
        this.schedule();
      }
    });
    this.ro.observe(this.stage);
  }

  getView(): ViewState {
    return { sc: this.sc, tx: this.tx, ty: this.ty, magLimit: this.magLimit, layers: { ...this.layers } };
  }

  /** Відновити вид після перемальовування (викликати, коли переглядач уже на сторінці). */
  setView(v: ViewState) {
    for (const l of Object.keys(v.layers) as Layer[]) if (l in this.layers && this.layers[l] !== v.layers[l]) this.toggle(l, v.layers[l]);
    this.magLimit = v.magLimit;
    const range = this.el.querySelector<HTMLInputElement>(".mag input");
    if (range) {
      range.value = String(v.magLimit);
      range.dispatchEvent(new Event("input"));
    }
    requestAnimationFrame(() => {
      this.computeFit();
      [this.sc, this.tx, this.ty] = [v.sc, v.tx, v.ty];
      this.lastSize = [this.stage.clientWidth, this.stage.clientHeight];
      this.schedule();
    });
  }

  destroy() {
    this.ro.disconnect();
    cancelAnimationFrame(this.raf);
    cancelAnimationFrame(this.anim);
  }

  // ------------------------------------------------------------ панель інструментів

  private toolbar() {
    const chips = (Object.keys(LAYER_UA) as Layer[]).map((l) => {
      const [name, key] = LAYER_UA[l];
      const disabled = (l === "grid" && !this.result.horizontal) || (l === "constellations" && !this.result.constellations?.length) || (l === "bodies" && !this.result.bodies.length) || (l === "stars" && !this.result.stars.length);
      return h(`button.chip.layer-${l}`, {
        "data-layer": l,
        "aria-pressed": String(this.layers[l]),
        title: `${name} (${key})`,
        disabled,
        onclick: () => this.toggle(l),
      }, h("i.swatch"), name);
    });
    const mag = h("input", { type: "range", min: "0", max: "6.5", step: "0.5", value: String(this.magLimit), "aria-label": "Найслабші зорі" });
    const magOut = h("output", {}, `${num(this.magLimit, 1)}m`);
    mag.addEventListener("input", () => {
      this.magLimit = Number(mag.value);
      magOut.textContent = `${num(this.magLimit, 1)}m`;
      this.schedule();
    });
    const zoomBtn = (name: string, title: string, fn: () => void) => h("button.icon-btn", { title, onclick: fn }, icon(name));
    return h("div.toolbar", {},
      h("div.chips", {}, chips),
      this.result.stars.length ? h("label.mag", { title: "Показувати зорі до цієї зоряної величини" }, h("span.mag-label", {}, "до"), mag, magOut) : null,
      h("div.zoom", {},
        zoomBtn("minus", "Віддалити (−)", () => this.zoomBy(1 / 1.6)),
        zoomBtn("fit", "Весь кадр (0)", () => this.fit()),
        zoomBtn("plus", "Наблизити (+)", () => this.zoomBy(1.6)),
        this.tools,
      ),
    );
  }

  toggle(l: Layer, on?: boolean) {
    const btn = this.el.querySelector<HTMLButtonElement>(`[data-layer="${l}"]`);
    if (btn?.disabled) return;
    this.layers[l] = on ?? !this.layers[l];
    btn?.setAttribute("aria-pressed", String(this.layers[l]));
    this.applyLayerClasses();
    this.schedule();
  }

  private applyLayerClasses() {
    for (const l of Object.keys(this.layers) as Layer[]) this.svg.classList.toggle(`hide-${l}`, !this.layers[l]);
  }

  private compassWidget() {
    const hz = this.result.horizontal;
    // Без часу зйомки напрямку на зеніт немає — порожній компас лише заважав би
    if (!hz) return null;
    const zen = hz?.zenith_direction_deg;
    const svg = s("svg", { viewBox: "-50 -50 100 100", "aria-hidden": "true" },
      s("circle", { r: "44", class: "ring" }),
      ...Array.from({ length: 12 }, (_, i) => s("line", { x1: "0", y1: "-44", x2: "0", y2: i % 3 ? "-40" : "-36", transform: `rotate(${i * 30})`, class: "tick" })),
      zen != null ? s("g", { transform: `rotate(${zen})`, class: "zen" },
        s("path", { d: "M0 -38 L7 -18 L0 -23 L-7 -18 Z" }),
        s("line", { x1: "0", y1: "-20", x2: "0", y2: "14" }),
      ) : null,
      s("circle", { r: "3", class: "hub" }),
    );
    const lines: (string | HTMLElement)[] = [];
    if (hz) lines.push(h("b", {}, `${compass(hz.center.az_deg)} · ${num(hz.center.az_deg, 0)}°`), h("span", {}, `висота ${num(hz.center.alt_refracted_deg, 1)}°`));
    if (zen != null) lines.push(h("small", {}, "стрілка — до зеніту"));
    else if (hz) lines.push(h("small", {}, "кадр дивиться в зеніт"));
    return h("div.compass", { title: "Куди дивилася камера" }, svg, h("div", {}, lines));
  }

  // ------------------------------------------------------------ побудова накладки

  private buildGrid() {
    const hz = this.result.horizontal;
    if (!hz) return;
    const CARD: Record<number, string> = { 0: "Пн", 90: "Сх", 180: "Пд", 270: "Зх" };
    for (const g of hz.grid) {
      const cls = g.kind === "alt" ? (g.value === 0 ? "horizon" : "alt") : CARD[g.value] ? "az cardinal" : "az";
      for (const line of g.lines) {
        const d = "M" + line.map(([x, y]) => `${x} ${y}`).join("L");
        this.gridG.append(s("path", { d, class: cls }));
      }
      const text = g.kind === "alt" ? (g.value === 0 ? "горизонт" : `${g.value}°`) : CARD[g.value] ?? `${g.value}°`;
      const el = s("text", { class: `glabel ${cls}` }, text);
      this.gridLabelEls.push({ el, lines: g.lines });
      this.gridLabels.append(el);
    }
  }

  private buildConstellations(labelsG: SVGGElement) {
    for (const c of this.result.constellations ?? []) {
      const d = c.lines.map((line) => "M" + line.map(([x, y]) => `${x} ${y}`).join("L")).join("");
      this.constG.append(s("path", { d, "data-con": c.id }));
      const name = constellationName(c.id);
      const el = s("text", { "text-anchor": "middle", "data-con": c.id }, name);
      labelsG.append(el);
      this.constLabels.push({ el, x: c.label[0], y: c.label[1], w: textWidth(name.toUpperCase()) + name.length * 2 + 10 });
    }
  }

  /** Показати сузір'я: увімкнути шар, підсвітити лінії й наблизитися до підпису. */
  focusConstellation(id: string) {
    const c = this.result.constellations?.find((k) => k.id === id);
    if (!c) return;
    if (!this.layers.constellations) this.toggle("constellations", true);
    this.svg.querySelectorAll(`[data-con="${id}"]`).forEach((e) => {
      e.classList.remove("hl");
      void (e as SVGElement).getBoundingClientRect(); // перезапустити анімацію
      e.classList.add("hl");
    });
    const pts = c.lines.flat();
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const { clientWidth: cw, clientHeight: ch } = this.stage;
    const top = this.topInset();
    const sc = this.clampScale(Math.min(cw / ((x1 - x0) * 1.3 || 1), (ch - top) / ((y1 - y0) * 1.3 || 1), this.fitScale * 4));
    this.animateTo(sc, cw / 2 - ((x0 + x1) / 2) * sc, top + (ch - top) / 2 - ((y0 + y1) / 2) * sc, true);
  }

  private buildMarkers() {
    const r = this.result;
    const add = (m: Omit<Marker, "labelW">) => {
      this.markers.push({ ...m, labelW: m.label ? textWidth(m.label.textContent ?? "") + 6 : 0 });
      this.markersG.append(m.g);
    };

    // Знайдені точки: найслабший шар, без підписів
    const fluxes = r.detections.map((d) => d[2]).sort((a, b) => b - a);
    const fRef = fluxes[Math.min(fluxes.length - 1, 5)] || 1;
    r.detections.forEach(([x, y, f]) => {
      const rad = Math.max(2.5, Math.min(7, 4 + Math.log10(f / fRef) * 2));
      const g = s("g", { class: "det" }, s("circle", { r: rad.toFixed(1) }));
      add({ target: null, x, y, g, label: null, r: rad, priority: 1e9, mag: 99, tier: 0 });
    });

    // Зорі каталогу
    for (const st of r.stars) {
      const rad = starRadius(st.mag);
      const label = s("text", { x: (rad + 4).toFixed(1), y: "4" }, st.name);
      const g = s("g", { class: `star${st.detection ? " matched" : ""}${st.proper_name ? " named" : ""}`, tabindex: "-1" },
        s("circle", { r: rad.toFixed(1) }), label);
      add({ target: { kind: "star", item: st }, x: st.x, y: st.y, g, label, r: rad, priority: st.mag - (st.proper_name ? 1.5 : 0), mag: st.mag, tier: labelTier(st) });
    }

    // Планети, Місяць, Сонце: над зорями
    for (const b of r.bodies) {
      const rad = bodyRadius(b.name);
      const nd = b.nearest_detection;
      const label = s("text", { x: (rad + 5).toFixed(1), y: "5" }, bodyName(b.name));
      const g = s("g", { class: `body body-${b.name.toLowerCase()}` },
        nd ? s("line", { class: "offset", x1: "0", y1: "0", "data-dx": nd.x - b.x, "data-dy": nd.y - b.y }) : null,
        s("circle", { r: String(rad) }),
        s("text", { class: "glyph", y: "5", "text-anchor": "middle" }, bodyGlyph(b.name)),
        label);
      add({ target: { kind: "body", item: b }, x: b.x, y: b.y, g, label, r: rad, priority: -100 + (b.mag ?? 0), mag: -99, tier: 0 });
    }
  }

  // ------------------------------------------------------------ вид

  /** Відступ згори, щоб кадр не ховався під панеллю інструментів. */
  private topInset() {
    const bar = this.el.querySelector<HTMLElement>(".toolbar");
    return bar ? bar.offsetTop + bar.offsetHeight + 8 : 0;
  }

  private computeFit() {
    const { clientWidth: cw, clientHeight: ch } = this.stage;
    this.fitScale = Math.min(cw / this.W, (ch - this.topInset() - 8) / this.H) * 0.98 || 1;
  }

  fit(animate = true) {
    this.computeFit();
    const { clientWidth: cw, clientHeight: ch } = this.stage;
    const sc = this.fitScale;
    const top = this.topInset();
    this.animateTo(sc, (cw - this.W * sc) / 2, top + (ch - top - this.H * sc) / 2, animate);
  }

  zoomBy(f: number, cx?: number, cy?: number) {
    const { clientWidth: cw, clientHeight: ch } = this.stage;
    cx ??= cw / 2;
    cy ??= ch / 2;
    const sc = this.clampScale(this.sc * f);
    const k = sc / this.sc;
    this.animateTo(sc, cx - (cx - this.tx) * k, cy - (cy - this.ty) * k, true);
  }

  private clampScale(v: number) {
    return Math.min(8, Math.max(this.fitScale * 0.5, v));
  }

  flyTo(t: Target) {
    const m = this.markers.find((mk) => mk.target?.item === t.item);
    if (!m) return;
    if (t.kind === "star" && t.item.mag > this.magLimit) {
      this.magLimit = Math.min(6.5, Math.ceil(t.item.mag * 2) / 2);
      const range = this.el.querySelector<HTMLInputElement>(".mag input");
      if (range) {
        range.value = String(this.magLimit);
        range.dispatchEvent(new Event("input"));
      }
    }
    if (!this.layers[t.kind === "star" ? "stars" : "bodies"]) this.toggle(t.kind === "star" ? "stars" : "bodies", true);
    const { clientWidth: cw, clientHeight: ch } = this.stage;
    const sc = Math.max(this.sc, Math.min(8, this.fitScale * 4));
    this.select(m);
    this.animateTo(sc, cw / 2 - m.x * sc, ch / 2 - m.y * sc, true);
  }

  private animateTo(sc: number, tx: number, ty: number, animate: boolean) {
    cancelAnimationFrame(this.anim);
    if (!animate || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      [this.sc, this.tx, this.ty] = [sc, tx, ty];
      this.schedule();
      return;
    }
    const from = [this.sc, this.tx, this.ty];
    const t0 = performance.now();
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / 420);
      const e = 1 - (1 - p) ** 3;
      // Масштаб інтерполюється логарифмічно, щоб наближення було рівномірним
      this.sc = Math.exp(Math.log(from[0]) + (Math.log(sc) - Math.log(from[0])) * e);
      const k = (this.sc - from[0]) / (sc - from[0] || 1);
      const kk = sc === from[0] ? e : k;
      this.tx = from[1] + (tx - from[1]) * kk;
      this.ty = from[2] + (ty - from[2]) * kk;
      this.render();
      if (p < 1) this.anim = requestAnimationFrame(step);
    };
    this.anim = requestAnimationFrame(step);
  }

  private schedule() {
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(() => this.render());
  }

  private render() {
    const { sc, tx, ty } = this;
    const { clientWidth: cw, clientHeight: ch } = this.stage;
    this.img.style.transform = `translate(${tx}px, ${ty}px) scale(${sc})`;
    this.img.style.imageRendering = sc > 2 ? "pixelated" : "";
    this.svg.setAttribute("viewBox", `0 0 ${cw} ${ch}`);
    this.gridG.setAttribute("transform", `matrix(${sc} 0 0 ${sc} ${tx} ${ty})`);
    this.constG.setAttribute("transform", `matrix(${sc} 0 0 ${sc} ${tx} ${ty})`);

    // Маркери: позиції в екранних координатах, підписи — жадібно без накладань
    const placed: number[][] = [];
    const overlaps = (b: number[]) => placed.some((p) => b[0] < p[2] && b[2] > p[0] && b[1] < p[3] && b[3] > p[1]);
    const visible: Marker[] = [];
    for (const m of this.markers) {
      const x = tx + m.x * sc;
      const y = ty + m.y * sc;
      const isStar = m.target?.kind === "star";
      const on = x > -40 && y > -40 && x < cw + 40 && y < ch + 40 && (!isStar || m.mag <= this.magLimit);
      m.g.style.display = on ? "" : "none";
      if (!on) continue;
      m.g.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
      visible.push(m);
      if (m.target?.kind === "body") {
        const ln = m.g.querySelector("line.offset");
        if (ln) {
          ln.setAttribute("x2", String(Number(ln.getAttribute("data-dx")) * sc));
          ln.setAttribute("y2", String(Number(ln.getAttribute("data-dy")) * sc));
        }
      }
    }
    // Порядок: підписи планет, назви сузір'їв, потім кола маркерів (щоб їх не затуляли
    // підписи зір) і нарешті підписи зір. Назви сузір'їв перевіряються лише з підписами планет.
    const constOn = this.layers.constellations && this.layers.labels;
    const bodiesFirst = visible.filter((m) => m.label && m.target?.kind === "body");
    const zoomRel = sc / this.fitScale;
    const placeLabel = (m: Marker) => {
      const layerOn = m.target?.kind === "star" ? this.layers.stars : this.layers.bodies;
      // Номери Флемстіда й HIP — лише при наближенні, інакше вони засипають кадр
      let show = this.layers.labels && layerOn && (m.tier === 0 || m === this.selected || zoomRel >= TIER_ZOOM[m.tier]);
      if (show) {
        const x = tx + m.x * sc + m.r + 3;
        const y = ty + m.y * sc;
        const box = [x, y - 8, x + m.labelW, y + 8];
        show = m === this.selected || !overlaps(box);
        if (show) placed.push(box);
      }
      m.label!.style.display = show ? "" : "none";
    };
    bodiesFirst.forEach(placeLabel);
    for (const c of this.constLabels) {
      const x = tx + c.x * sc;
      const y = ty + c.y * sc;
      const box = [x - c.w / 2, y - 9, x + c.w / 2, y + 7];
      const show = constOn && x > 0 && y > 0 && x < cw && y < ch && !overlaps(box);
      if (show) {
        placed.push(box);
        c.el.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
      }
      c.el.style.display = show ? "" : "none";
    }
    if (this.layers.labels) {
      // Спершу обведення маркерів, щоб підписи не затуляли самі об'єкти
      for (const m of visible) {
        if (m.target === null && !this.layers.detections) continue;
        if (m.target?.kind === "star" && !this.layers.stars) continue;
        if (m.target?.kind === "body" && !this.layers.bodies) continue;
        if (m.target) {
          const x = tx + m.x * sc;
          const y = ty + m.y * sc;
          placed.push([x - m.r, y - m.r, x + m.r, y + m.r]);
        }
      }
    }
    const withLabels = visible.filter((m) => m.label && m.target?.kind !== "body").sort((a, b) => a.priority - b.priority);
    withLabels.forEach(placeLabel);

    this.renderGridLabels(cw, ch);
    this.renderScaleBar();
    this.positionPopover();
  }

  private renderGridLabels(cw: number, ch: number) {
    if (!this.layers.grid) return;
    const { sc, tx, ty } = this;
    for (const { el: lbl, lines } of this.gridLabelEls) {
      let pos: [number, number] | null = null;
      // Перша точка лінії, що лежить у видимій частині, з відступом від країв
      outer: for (const line of lines)
        for (const [x, y] of line) {
          const sx = tx + x * sc;
          const sy = ty + y * sc;
          if (sx > 24 && sy > 70 && sx < cw - 60 && sy < ch - 40) {
            pos = [sx, sy];
            break outer;
          }
        }
      lbl.style.display = pos ? "" : "none";
      if (pos) lbl.setAttribute("transform", `translate(${pos[0].toFixed(1)} ${pos[1].toFixed(1)})`);
    }
  }

  private renderScaleBar() {
    const sol = this.result.solution;
    if (!sol) {
      this.scaleBar.hidden = true;
      return;
    }
    // Підібрати «круглу» довжину шкали близько 120 px
    const arcsecPerScreenPx = sol.scale_arcsec_per_px / this.sc;
    const target = arcsecPerScreenPx * 120;
    const steps = [10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 18000, 36000, 72000];
    const v = steps.reduce((best, x) => (Math.abs(Math.log(x / target)) < Math.abs(Math.log(best / target)) ? x : best));
    const px = v / arcsecPerScreenPx;
    this.scaleBar.hidden = false;
    this.scaleBar.replaceChildren(h("i", { style: `width:${px.toFixed(0)}px` }), h("span", {}, arc(v)));
  }

  // ------------------------------------------------------------ вибір

  private select(m: Marker | null) {
    this.selected?.g.classList.remove("selected");
    this.selected = m;
    m?.g.classList.add("selected");
    this.popover.hidden = !m;
    if (m?.target) this.popover.replaceChildren(...this.popoverContent(m.target));
    this.onSelect(m?.target ?? null);
    this.schedule();
  }

  selectTarget(t: Target | null) {
    this.select(t ? this.markers.find((m) => m.target?.item === t.item) ?? null : null);
  }

  private positionPopover() {
    const m = this.selected;
    if (!m) return;
    const { clientWidth: cw, clientHeight: ch } = this.stage;
    const x = this.tx + m.x * this.sc;
    const y = this.ty + m.y * this.sc;
    const pw = this.popover.offsetWidth;
    const ph = this.popover.offsetHeight;
    let left = x + m.r + 14;
    if (left + pw > cw - 8) left = x - m.r - 14 - pw;
    const top = Math.min(Math.max(8, y - ph / 2), ch - ph - 8);
    this.popover.style.transform = `translate(${Math.max(8, left)}px, ${top}px)`;
    this.popover.classList.toggle("offscreen", x < 0 || y < 0 || x > cw || y > ch);
  }

  private popoverContent(t: Target): Node[] {
    return [h("button.close", { "aria-label": "Закрити", onclick: () => this.select(null) }, "×"), ...targetCard(t)];
  }

  // ------------------------------------------------------------ миша, дотики

  private bindPointer() {
    const pts = new Map<number, { x: number; y: number }>();
    let moved = 0;
    let pinch: { d: number; sc: number; mx: number; my: number; tx: number; ty: number } | null = null;
    const rel = (e: PointerEvent | WheelEvent | MouseEvent) => {
      const r = this.stage.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    this.stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      cancelAnimationFrame(this.anim);
      const p = rel(e);
      const f = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0018));
      const sc = this.clampScale(this.sc * f);
      const k = sc / this.sc;
      this.tx = p.x - (p.x - this.tx) * k;
      this.ty = p.y - (p.y - this.ty) * k;
      this.sc = sc;
      this.schedule();
    }, { passive: false });

    this.stage.addEventListener("pointerdown", (e) => {
      if ((e.target as Element).closest(".popover")) return;
      this.stage.setPointerCapture(e.pointerId);
      pts.set(e.pointerId, rel(e));
      moved = 0;
      cancelAnimationFrame(this.anim);
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), sc: this.sc, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, tx: this.tx, ty: this.ty };
      }
      this.stage.classList.add("grabbing");
    });

    this.stage.addEventListener("pointermove", (e) => {
      const p = rel(e);
      this.updateReadout(p.x, p.y);
      const prev = pts.get(e.pointerId);
      if (!prev) return;
      pts.set(e.pointerId, p);
      if (pinch && pts.size === 2) {
        const [a, b] = [...pts.values()];
        const sc = this.clampScale(pinch.sc * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d);
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        const k = sc / pinch.sc;
        this.tx = mx - (pinch.mx - pinch.tx) * k;
        this.ty = my - (pinch.my - pinch.ty) * k;
        this.sc = sc;
        moved = 99;
      } else {
        this.tx += p.x - prev.x;
        this.ty += p.y - prev.y;
        moved += Math.abs(p.x - prev.x) + Math.abs(p.y - prev.y);
      }
      this.schedule();
    });

    const end = (e: PointerEvent) => {
      if (!pts.has(e.pointerId)) return;
      pts.delete(e.pointerId);
      if (pts.size < 2) pinch = null;
      if (pts.size === 0) this.stage.classList.remove("grabbing");
      if (e.type === "pointerup" && moved < 5) {
        // Клік: найближчий маркер у межах кількох пікселів
        const p = rel(e);
        this.select(this.hit(p.x, p.y));
      }
    };
    this.stage.addEventListener("pointerup", end);
    this.stage.addEventListener("pointercancel", end);
    this.stage.addEventListener("pointerleave", () => this.readout.classList.remove("on"));
    this.stage.addEventListener("dblclick", (e) => {
      const p = rel(e);
      this.zoomBy(2, p.x, p.y);
    });
  }

  private hit(px: number, py: number): Marker | null {
    let best: Marker | null = null;
    let bestD = Infinity;
    for (const m of this.markers) {
      if (!m.target || m.g.style.display === "none") continue;
      if (!this.layers[m.target.kind === "star" ? "stars" : "bodies"]) continue;
      const d = Math.hypot(this.tx + m.x * this.sc - px, this.ty + m.y * this.sc - py) - m.r;
      const bonus = m.target.kind === "body" ? 6 : 0;
      if (d - bonus < bestD) {
        bestD = d - bonus;
        best = m;
      }
    }
    return bestD < 10 ? best : null;
  }

  private updateReadout(px: number, py: number) {
    const x = (px - this.tx) / this.sc;
    const y = (py - this.ty) / this.sc;
    if (x < 0 || y < 0 || x > this.W || y > this.H) {
      this.readout.classList.remove("on");
      return;
    }
    const parts: Node[] = [h("span", {}, `x ${Math.round(x)} · y ${Math.round(y)}`)];
    const world = this.wcs?.pixToWorld(x, y);
    if (world) {
      parts.push(h("span", {}, `α ${hms(world[0])}`), h("span", {}, `δ ${dms(world[1])}`));
      const obs = this.result.observation;
      if (obs.time_utc) {
        const [alt, az] = altAz(world[0], world[1], new Date(obs.time_utc), obs.location.lat, obs.location.lon);
        parts.push(h("span", {}, `h ≈ ${num(alt, 1)}°`), h("span", {}, `A ≈ ${num(az, 1)}° ${compass(az)}`));
      }
    }
    this.readout.replaceChildren(...parts);
    this.readout.classList.add("on");
  }

  // ------------------------------------------------------------ експорт

  /** Кадр у повній роздільності з накладкою, як її зараз видно (шари, межа величини). */
  exportPng(): Promise<Blob> {
    return exportAnnotatedPng(this.img, this.result, { layers: this.layers, magLimit: this.magLimit });
  }
}
