// Точка входу: стан сервера, опитування задач, маршрут #/<id>, клавіші.

import "./styles/base.css";
import "./styles/shell.css";
import "./styles/upload.css";
import "./styles/screens.css";
import "./styles/viewer.css";
import "./styles/panel.css";
import "./styles/overlays.css";
import "./styles/responsive.css";

import { api, type Health, type Job } from "./api";
import { renderJobList } from "./jobList";
import { help, initLayout, narrow, setPanel, setSide, toggleFullscreen, toggleNight, ui, viewerButtons } from "./layout";
import { ResultPanel } from "./panels";
import { setHome } from "./place";
import { failedScreen, notFoundScreen, pendingScreen, updatePending, welcomeScreen } from "./screens";
import { startStarfield } from "./starfield";
import { initUpload, renderLocHint } from "./upload";
import { $, h, num } from "./util";
import type { Layer } from "./marks";
import { Viewer } from "./viewer";

// ---------------------------------------------------------------- стан

let jobs: Job[] = [];
let health: Health | null = null;
let currentId: string | null = null;
let current: { viewer: Viewer; panel: ResultPanel } | null = null;
let pollTimer = 0;

const main = $("#main");
const routeId = () => location.hash.match(/^#\/([0-9a-f]{32})$/)?.[1] ?? null;
const show = (el: HTMLElement) => main.replaceChildren(el);

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
  el.replaceChildren(
    h(`span.pill.${health.status === "ok" ? "good" : "bad"}`, { title: `версія ${health.version}` }, h("i"),
      health.status === "ok" ? `${num(health.catalog_stars, 0)} зір у каталозі` : "немає каталогу"),
    ...Object.entries(health.solvers).map(([name, ok]) =>
      h(`span.pill.${ok ? "good" : "off"}`, { title: ok ? "розв'язувач доступний" : "розв'язувач недоступний" }, h("i"), name)),
    ...(health.queue_length ? [h("span.pill.warn", {}, h("i"), `у черзі: ${health.queue_length}`)] : []),
  );
  renderLocHint();
}

// ---------------------------------------------------------------- задачі

async function refreshJobs() {
  clearTimeout(pollTimer);
  try {
    const prev = new Map(jobs.map((j) => [j.id, j.status]));
    jobs = await api.jobs();
    renderJobList(jobs, currentId);
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

async function removeJob(id: string) {
  await api.remove(id);
  location.hash = "#/";
  refreshJobs();
}

// ---------------------------------------------------------------- головна область

function closeCurrent() {
  current?.viewer.destroy();
  current = null;
}

async function openJob(id: string | null) {
  // Той самий кадр перемальовується (напр. після зміни часу чи місця): зберегти вид і вкладку
  const keep = current && current.viewer.job.id === id ? { view: current.viewer.getView(), tab: current.panel.tab } : null;
  currentId = id;
  if (narrow.matches && ui.side) setSide(false);
  renderJobList(jobs, currentId);
  if (!id) {
    closeCurrent();
    show(welcomeScreen(jobs.filter((j) => j.status === "done").length));
    return;
  }
  let job: Job;
  try {
    job = await api.job(id);
  } catch (e) {
    closeCurrent();
    show(notFoundScreen(String((e as Error).message)));
    return;
  }
  if (currentId !== id) return; // користувач уже перейшов деінде
  closeCurrent();
  if (job.status === "done" && job.result) showResult(job, job.result, keep);
  else if (job.status === "failed") show(failedScreen(job, () => removeJob(job.id)));
  else renderPending(job);
}

function showResult(job: Job, result: NonNullable<Job["result"]>, keep: { view: ReturnType<Viewer["getView"]>; tab: ResultPanel["tab"] } | null) {
  const viewer = new Viewer(job, result);
  const panel = new ResultPanel(job, result, viewer, () => removeJob(job.id), async (body, remember) => {
    if (Object.keys(body).length) await api.observe(job.id, body);
    if (remember) setHome(remember);
    renderLocHint();
    await openJob(job.id);
  });
  viewer.onSelect = (t) => panel.highlight(t);
  viewer.tools.append(...viewerButtons());
  current = { viewer, panel };
  show(h("div.result", {}, viewer.el, panel.el));
  if (keep) {
    viewer.setView(keep.view);
    panel.show(keep.tab);
  }
}

function renderPending(job: Job) {
  const existing = main.querySelector<HTMLElement>(".pending");
  if (existing && existing.dataset.id === job.id) updatePending(existing, job);
  else show(pendingScreen(job));
}

// ---------------------------------------------------------------- клавіші

const LAYER_KEYS: Record<string, Layer> = { s: "stars", p: "bodies", c: "constellations", g: "grid", d: "detections", l: "labels" };

window.addEventListener("keydown", (e) => {
  const t = e.target as HTMLElement;
  if (e.ctrlKey || e.metaKey || e.altKey || t.matches("input, select, textarea") || help().open) return;
  const k = e.key.toLowerCase();
  if (k === "n") return toggleNight();
  if (k === "?") return help().showModal();
  if (k === "[") return setSide(!ui.side);
  if (k === "escape" && narrow.matches && ui.side) return setSide(false);
  if (!current) return;
  if (k === "]") return setPanel(!ui.panel);
  if (k === "f") return toggleFullscreen();
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

initLayout();
initUpload({
  onUploaded: (job) => {
    location.hash = `#/${job.id}`;
    refreshJobs();
  },
  health: () => health,
});
startStarfield($<HTMLCanvasElement>("#starfield"));
window.addEventListener("hashchange", () => openJob(routeId()));
loadHealth();
setInterval(loadHealth, 30000);
refreshJobs().then(() => openJob(routeId()));
