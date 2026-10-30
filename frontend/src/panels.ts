// Бічна панель результату: огляд, список об'єктів, точність, дані.

import type { Job, ObservationUpdate, Result } from "./api";
import { placeForm, type Place } from "./place";
import { skyDome } from "./skydome";
import { BODY_UA, CONSTELLATIONS, KIND_UA, LOCATION_SOURCE_UA, SOURCE_UA, arc, compass, dms, download, h, hms, icon, num, s, when } from "./util";
import { targetName, type Target, type Viewer } from "./viewer";

type Tab = "overview" | "objects" | "accuracy" | "data";
const TABS: [Tab, string][] = [
  ["overview", "Огляд"],
  ["objects", "Об'єкти"],
  ["accuracy", "Точність"],
  ["data", "Дані"],
];

export class ResultPanel {
  readonly el: HTMLElement;
  private body: HTMLElement;
  private tab: Tab = "overview";
  private list: HTMLElement | null = null;
  private search: HTMLInputElement | null = null;
  /** Відкрита форма зміни часу й місця (і що в ній підсвітити першим). */
  private editing: "time" | "place" | null = null;

  constructor(
    private job: Job,
    private r: Result,
    private viewer: Viewer,
    private onDelete: () => void,
    private onObservation: (body: ObservationUpdate, remember: Place | null) => Promise<void>,
  ) {
    const tabs = h("div.tabs", { role: "tablist" },
      TABS.map(([id, name]) => h("button.tab", { role: "tab", "data-tab": id, "aria-selected": String(id === this.tab), onclick: () => this.show(id) }, name)));
    this.body = h("div.panel-body");
    this.el = h("aside.panel", {}, tabs, this.body);
    this.show(this.tab);
  }

  show(tab: Tab) {
    this.tab = tab;
    this.el.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.getAttribute("data-tab") === tab)));
    this.list = this.search = null;
    const content = { overview: () => this.overview(), objects: () => this.objects(), accuracy: () => this.accuracy(), data: () => this.data() }[tab]();
    this.body.replaceChildren(...content);
    this.body.scrollTop = 0;
  }

  focusSearch() {
    if (this.tab !== "objects") this.show("objects");
    this.search?.focus();
  }

  highlight(t: Target | null) {
    if (!this.list) return;
    this.list.querySelectorAll(".obj.active").forEach((e) => e.classList.remove("active"));
    if (!t) return;
    const row = Array.from(this.list.children).find((e) => (e as HTMLElement & { _item?: unknown })._item === t.item);
    row?.classList.add("active");
    row?.scrollIntoView({ block: "nearest" });
  }

  // ------------------------------------------------------------ огляд

  private overview(): Node[] {
    const { r } = this;
    const out: Node[] = [];
    const hz = r.horizontal;
    const sol = r.solution;

    if (!r.solved) {
      out.push(h("div.callout.bad", {}, h("b", {}, "Небо не розпізнано"), h("p", {}, r.warnings.join(" ") || "Спробуйте інший кадр або підкажіть поле зору.")));
    }

    const obsv = r.observation;
    const edit = (focus: "time" | "place") => {
      this.editing = focus;
      this.show("overview");
    };
    if (r.solved && this.editing) {
      out.push(placeForm({
        timeUtc: obsv.time_utc,
        place: obsv.location,
        placeUnknown: obsv.location.source === "default",
        focus: this.editing,
        onSubmit: (body, remember) => this.onObservation(body, remember),
        onCancel: () => {
          this.editing = null;
          this.show("overview");
        },
      }));
    } else if (r.solved && !obsv.time_utc) {
      out.push(h("div.callout.warn", {},
        h("b", {}, "Коли знято фото?"),
        h("p", {}, "У EXIF немає часу з часовим поясом. Без нього не порахувати планети, Місяць, висоти й горизонт."),
        h("button.btn", { onclick: () => edit("time") }, "Вказати час і місце")));
    } else if (r.solved && obsv.location.source === "default") {
      out.push(h("div.callout.warn", {},
        h("b", {}, "Де знято фото?"),
        h("p", {}, `У EXIF немає GPS, тож висоти, азимути, горизонт і Місяць пораховано для місця за замовчуванням (${num(obsv.location.lat, 4)}°, ${num(obsv.location.lon, 4)}°). Якщо знімали деінде, вкажіть місце — небо перерахується за секунду, а зорі на кадрі лишаться ті самі.`),
        h("button.btn", { onclick: () => edit("place") }, icon("pin"), "Вказати місце")));
    }

    if (hz) {
      const dome = skyDome(r);
      out.push(h("section.hero", {},
        dome,
        h("div.hero-text", {},
          h("p.eyebrow", {}, "Камера дивилася"),
          h("p.big", {}, `на ${compass(hz.center.az_deg)}`, h("small", {}, ` ${num(hz.center.az_deg, 1)}°`)),
          h("p.big", {}, `${num(hz.center.alt_refracted_deg, 1)}°`, h("small", {}, " над горизонтом")),
          h("p.muted", {}, `висоти в кадрі від ${num(hz.alt_range_deg[0], 1)}° до ${num(hz.alt_range_deg[1], 1)}°`),
        )));
    }

    if (sol) {
      out.push(h("div.facts", {},
        fact("Центр кадру", h("span.mono", {}, hms(sol.center.ra_deg), h("br"), dms(sol.center.dec_deg))),
        fact("Поле зору", `${num(sol.fov_deg[0], 2)}° × ${num(sol.fov_deg[1], 2)}°`),
        fact("Масштаб", `${num(sol.scale_arcsec_per_px, 1)}″/px`),
        fact("Похибка (rms)", h("span", { class: quality(sol.residual_arcsec.rms, sol.scale_arcsec_per_px).cls }, arc(sol.residual_arcsec.rms))),
      ));
    }

    // Сузір'я за кількістю зір у кадрі
    const cons = new Map<string, number>();
    for (const st of r.stars) if (st.constellation) cons.set(st.constellation, (cons.get(st.constellation) ?? 0) + 1);
    if (cons.size) {
      out.push(h("h4", {}, "Сузір'я в кадрі"),
        h("div.tags", {}, [...cons.entries()].sort((a, b) => b[1] - a[1]).map(([c, n]) =>
          r.constellations?.some((k) => k.id === c)
            ? h("button.tag", { title: `${n} зір до ${num(6.5, 1)}m · показати лінії`, onclick: () => this.viewer.focusConstellation(c) }, CONSTELLATIONS[c] ?? c, h("i", {}, n))
            : h("span.tag", { title: `${n} зір до ${num(6.5, 1)}m` }, CONSTELLATIONS[c] ?? c, h("i", {}, n)))));
    }

    if (r.bodies.length) {
      out.push(h("h4", {}, "Сонячна система"),
        h("div.tags", {}, r.bodies.map((b) =>
          h("button.tag.body-tag", { onclick: () => this.viewer.flyTo({ kind: "body", item: b }) }, `${BODY_UA[b.name]?.glyph ?? ""} ${BODY_UA[b.name]?.name ?? b.name}`))));
    }

    const brightest = r.stars.filter((st) => st.proper_name).slice(0, 6);
    if (brightest.length) {
      out.push(h("h4", {}, "Найяскравіші зорі"),
        h("div.tags", {}, brightest.map((st) =>
          h("button.tag", { onclick: () => this.viewer.flyTo({ kind: "star", item: st }) }, st.name, h("i", {}, `${num(st.mag, 1)}m`)))));
    }

    const obs = r.observation;
    const cam = obs.camera;
    out.push(h("div.section-head", {}, h("h4", {}, "Зйомка"),
      r.solved && !this.editing ? h("button.link", { onclick: () => edit("time") }, "Змінити час і місце") : null),
    h("dl.kv", {},
      kv("Час", obs.time_utc ? `${when(obs.time_utc)}` : "невідомий", obs.time_source ? SOURCE_UA[obs.time_source] : null),
      kv("Місце", `${num(obs.location.lat, 4)}°, ${num(obs.location.lon, 4)}°, ${num(obs.location.elevation_m, 0)} м`, LOCATION_SOURCE_UA[obs.location.source]),
      cam.model ? kv("Камера", [cam.make, cam.model].filter(Boolean).join(" ")) : null,
      cam.exposure_s != null ? kv("Експозиція", [
        cam.exposure_s >= 1 ? `${num(cam.exposure_s, 1)} с` : `1/${Math.round(1 / cam.exposure_s)} с`,
        cam.iso ? `ISO ${cam.iso}` : null,
        cam.focal_length_mm ? `${num(cam.focal_length_mm, 1)} мм` : null,
        cam.focal_length_35mm ? `(${Math.round(cam.focal_length_35mm)} мм екв.)` : null,
      ].filter(Boolean).join(" · ")) : null,
      kv("Кадр", `${r.image.width} × ${r.image.height}, ${KIND_UA[r.image.kind] ?? r.image.kind}`),
    ));
    return out;
  }

  // ------------------------------------------------------------ об'єкти

  private objects(): Node[] {
    const { r } = this;
    const targets: Target[] = [
      ...r.bodies.map((item) => ({ kind: "body" as const, item })),
      ...r.stars.map((item) => ({ kind: "star" as const, item })),
    ];
    this.search = h("input.search", { type: "search", placeholder: "Назва, позначення, сузір'я…", "aria-label": "Пошук об'єкта" });
    this.list = h("ul.objlist");
    const count = h("p.muted.small");

    const fill = () => {
      const q = this.search!.value.trim().toLowerCase();
      const match = (t: Target) => {
        if (!q) return true;
        const hay = t.kind === "body"
          ? `${t.item.name} ${BODY_UA[t.item.name]?.name ?? ""}`
          : `${t.item.name} ${t.item.designation ?? ""} ${t.item.constellation ?? ""} ${CONSTELLATIONS[t.item.constellation ?? ""] ?? ""} ${t.item.hip ? `hip ${t.item.hip}` : ""}`;
        return hay.toLowerCase().includes(q);
      };
      const shown = targets.filter(match);
      count.textContent = q ? `Знайдено: ${shown.length}` : `${r.bodies.length ? `${r.bodies.length} тіл Сонячної системи, ` : ""}${r.stars.length} зір до 6,5m`;
      this.list!.replaceChildren(...shown.slice(0, 300).map((t) => this.objRow(t)));
    };
    this.search.addEventListener("input", fill);
    this.search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") (this.list!.firstElementChild as HTMLElement | null)?.click();
      if (e.key === "Escape") this.search!.blur();
    });
    fill();
    return [this.search, count, this.list];
  }

  private objRow(t: Target): HTMLElement {
    let glyph: Node;
    let sub: string;
    let right: string;
    if (t.kind === "body") {
      glyph = h("span.glyph.body", {}, BODY_UA[t.item.name]?.glyph ?? "●");
      sub = t.item.nearest_detection ? `збіг з точкою: ${arc(t.item.nearest_detection.offset_arcsec)}` : "планета";
      right = t.item.mag != null ? `${num(t.item.mag, 1)}m` : "";
    } else {
      const st = t.item;
      const sz = Math.max(3, 10 - st.mag * 1.2);
      glyph = h("span.glyph", {}, s("svg", { viewBox: "-6 -6 12 12" }, s("circle", { r: (sz / 2).toFixed(1), class: st.detection ? "m" : "u" })));
      sub = [st.proper_name ? st.designation : null, st.constellation ? CONSTELLATIONS[st.constellation] ?? st.constellation : null].filter(Boolean).join(" · ");
      right = `${num(st.mag, 1)}m`;
    }
    const alt = t.item.alt_refracted_deg;
    const li = h("li.obj", { tabindex: "0", onclick: () => this.viewer.flyTo(t) },
      glyph,
      h("div", {}, h("b", {}, targetName(t)), h("small", {}, sub)),
      h("div.r", {}, h("span", {}, right), alt != null ? h("small", {}, `${num(alt, 0)}°`) : null));
    li.addEventListener("keydown", (e) => e.key === "Enter" && this.viewer.flyTo(t));
    (li as HTMLElement & { _item?: unknown })._item = t.item;
    return li;
  }

  // ------------------------------------------------------------ точність

  private accuracy(): Node[] {
    const sol = this.r.solution;
    if (!sol) return [h("p.muted", {}, "Розв'язку немає, тож і оцінювати нічого.")];
    const res = sol.residual_arcsec;
    const q = quality(res.rms, sol.scale_arcsec_per_px);
    const out: Node[] = [
      h("div.callout", { class: `callout ${q.cls}` }, h("b", {}, q.text),
        h("p", {}, `${sol.matched_stars} зір каталогу збіглися зі знайденими точками. Середня похибка ${arc(res.rms)} — це ${num(res.rms / sol.scale_arcsec_per_px, 2)} пікселя кадру.`)),
      h("div.stats", {},
        stat("rms", arc(res.rms)), stat("медіана", arc(res.median)), stat("90 %", arc(res.p90)), stat("макс.", arc(res.max))),
    ];

    const dist = sol.distortion;
    if (dist?.cv_rms_arcsec != null) {
      out.push(h("h4", {}, "Модель кадру"),
        h("p.small.muted", {}, "Кілька моделей дисторсії об'єктива перевірено на зорях, яких підгонка не бачила. Обрано найпростішу з тих, що не помітно гірші за найкращу."),
        modelChart(dist.candidates, sol.scale_arcsec_per_px));
    }

    const offs = this.r.stars.filter((st) => st.detection && st.detection.in_fit !== false).map((st) => st.detection!.offset_arcsec);
    if (offs.length > 2) out.push(h("h4", {}, "Відхилення зір від каталогу"), histogram(offs, res.median));

    if (this.r.bodies.some((b) => b.nearest_detection)) {
      out.push(h("h4", {}, "Планети: розрахунок проти кадру"),
        h("table.tbl", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Тіло"), h("th", {}, "Відхилення"), h("th", {}, "px"))),
          h("tbody", {}, this.r.bodies.filter((b) => b.nearest_detection).map((b) =>
            h("tr", {}, h("td", {}, BODY_UA[b.name]?.name ?? b.name), h("td", {}, arc(b.nearest_detection!.offset_arcsec)), h("td", {}, num(b.nearest_detection!.offset_px, 1)))))));
    }

    out.push(h("h4", {}, "Розв'язання"), h("dl.kv", {},
      kv("Розв'язувач", sol.solver, `${num(sol.solve_ms, 0)} мс`),
      kv("Модель", dist ? dist.model : sol.sip_order ? `TAN-SIP, порядок ${sol.sip_order}` : "TAN"),
      dist?.cv_rms_arcsec != null ? kv("Похибка на нових зорях", arc(dist.cv_rms_arcsec), "перехресна перевірка") : null,
      kv("Напрямок «вгору»", `${num(sol.up_position_angle_deg, 1)}° від півночі через схід`),
    ));

    const t = this.r.timings;
    const total = Object.values(t).reduce((a, b) => a + b, 0);
    const STAGE: Record<string, string> = { load_s: "читання", detect_s: "пошук зір", solve_s: "розв'язання", refine_s: "уточнення", sky_s: "планети й сітка" };
    out.push(h("h4", {}, `Час обробки: ${num(total, 2)} с`),
      h("div.timing", {}, Object.entries(t).map(([k, v], i) =>
        h("i", { style: `flex:${Math.max(v, total * 0.02)};--c:${i}`, title: `${STAGE[k] ?? k}: ${num(v, 2)} с` }))),
      h("div.timing-legend", {}, Object.entries(t).map(([k, v], i) => h("span", { style: `--c:${i}` }, `${STAGE[k] ?? k} ${num(v, 2)} с`))));
    return out;
  }

  // ------------------------------------------------------------ дані

  private data(): Node[] {
    const { r, job } = this;
    const base = job.filename.replace(/\.[^.]+$/, "");
    const out: Node[] = [];
    if (r.warnings.length) out.push(h("h4", {}, "Попередження"), h("ul.warnings", {}, r.warnings.map((w) => h("li", {}, w))));
    const exportBtn = h("button.btn", {}, icon("download"), "Кадр із підписами (PNG)");
    exportBtn.addEventListener("click", async () => {
      exportBtn.setAttribute("disabled", "");
      try {
        download(`${base}-annotated.png`, await this.viewer.exportPng());
      } finally {
        exportBtn.removeAttribute("disabled");
      }
    });
    out.push(h("h4", {}, "Завантажити"), h("div.downloads", {},
      exportBtn,
      h("button.btn", { onclick: () => download(`${base}.result.json`, JSON.stringify(r, null, 2)) }, icon("download"), "Результат (JSON)"),
      r.solution ? h("button.btn", { onclick: () => download(`${base}.wcs.txt`, fitsHeader(r.solution!.wcs_header), "text/plain") }, icon("download"), "WCS-заголовок") : null,
      h("a.btn", { href: job.links.original }, icon("download"), "Оригінал"),
    ));
    out.push(h("h4", {}, "Задача"), h("dl.kv", {},
      kv("Файл", job.filename),
      kv("Створено", when(job.created)),
      kv("Готово", when(job.finished)),
      kv("Ідентифікатор", h("code", {}, job.id)),
    ));
    const del = h("button.btn.danger", {}, icon("trash"), "Видалити задачу");
    del.addEventListener("click", () => confirm(`Видалити «${job.filename}» разом із результатом?`) && this.onDelete());
    out.push(h("div.danger-zone", {}, del));
    return out;
  }
}

// ---------------------------------------------------------------- дрібниці

function fact(k: string, v: Node | string) {
  return h("div.fact", {}, h("span", {}, k), h("b", {}, v));
}

function kv(k: string, v: Node | string, note?: string | null) {
  return h("div", {}, h("dt", {}, k), h("dd", {}, v, note ? h("small", {}, ` · ${note}`) : null));
}

function stat(k: string, v: string) {
  return h("div.stat", {}, h("b", {}, v), h("span", {}, k));
}

/** Якість у пікселях кадру: так порівнюються кадри з різним масштабом. */
function quality(rms: number, scale: number): { cls: string; text: string } {
  const px = rms / scale;
  if (px < 0.7) return { cls: "good", text: "Відмінна точність" };
  if (px < 1.5) return { cls: "good", text: "Добра точність" };
  if (px < 3) return { cls: "warn", text: "Помірна точність" };
  return { cls: "bad", text: "Груба точність" };
}

function histogram(values: number[], median: number): SVGSVGElement {
  const W = 300;
  const H = 90;
  const max = Math.max(...values);
  const bins = Math.min(24, Math.max(6, Math.round(Math.sqrt(values.length) * 2)));
  const step = max / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const v of values) counts[Math.min(bins - 1, Math.floor(v / step))]++;
  const top = Math.max(...counts);
  const bw = W / bins;
  const mx = (median / (step * bins)) * W;
  return s("svg", { class: "hist", viewBox: `0 0 ${W} ${H + 16}`, role: "img", "aria-label": "Розподіл відхилень" },
    counts.map((c, i) => s("rect", { x: (i * bw + 1).toFixed(1), y: (H - (c / top) * H).toFixed(1), width: (bw - 2).toFixed(1), height: ((c / top) * H).toFixed(1), rx: "2" },
      s("title", {}, `${arc(i * step)}–${arc((i + 1) * step)}: ${c}`))),
    s("line", { x1: mx.toFixed(1), x2: mx.toFixed(1), y1: "0", y2: String(H), class: "median" }),
    s("text", { x: "0", y: String(H + 13) }, "0″"),
    s("text", { x: String(W), y: String(H + 13), "text-anchor": "end" }, arc(max)),
    s("text", { x: (mx + 4).toFixed(1), y: "10", class: "median-l" }, `медіана ${arc(median)}`),
  );
}

/** Похибка кожної моделі на перехресній перевірці (логарифмічна шкала: різниця буває в рази). */
function modelChart(cands: { model: string; cv_rms_arcsec: number; chosen: boolean }[], scale: number): HTMLElement {
  const vals = cands.map((c) => c.cv_rms_arcsec);
  const lo = Math.log(Math.min(...vals) * 0.8);
  const hi = Math.log(Math.max(...vals));
  return h("div.models", {}, cands.map((c) =>
    h(`div.model${c.chosen ? ".chosen" : ""}`, { title: `${num(c.cv_rms_arcsec / scale, 2)} px` },
      h("span", {}, c.model),
      h("i", { style: `--w:${(((Math.log(c.cv_rms_arcsec) - lo) / (hi - lo || 1)) * 100).toFixed(1)}%` }),
      h("b", {}, arc(c.cv_rms_arcsec)))));
}

function fitsHeader(hdr: Record<string, string | number | boolean>): string {
  const card = (k: string, v: string | number | boolean) => {
    const val = typeof v === "string" ? `'${v}'` : typeof v === "boolean" ? (v ? "T" : "F") : String(v);
    return `${k.padEnd(8)}= ${val.padStart(20)}`;
  };
  return Object.entries(hdr).map(([k, v]) => card(k, v)).concat(["END"]).join("\n") + "\n";
}
