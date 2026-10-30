// Список кадрів у лівій панелі і мініатюри на вузькій смужці згорнутої панелі.

import type { Job } from "./api";
import { $, ago, h } from "./util";

export const STATUS_UA: Record<Job["status"], string> = { queued: "у черзі", running: "обробка", done: "готово", failed: "помилка" };

const finished = (j: Job) => j.status === "done" || j.status === "failed";

/** Мініатюра прев'ю; поки обробка триває (прев'ю ще може не бути) — крутилка. */
function thumbnail(j: Job): HTMLElement {
  if (!finished(j)) return h("span.thumb-ph.spin");
  const img = h("img", { src: j.links.preview, alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(h("span.thumb-ph")), { once: true });
  return img;
}

export function renderJobList(jobs: Job[], currentId: string | null) {
  $("#jobs-count").textContent = jobs.length ? String(jobs.length) : "";
  $("#rail-jobs").replaceChildren(...jobs.map((j) =>
    h("li", {}, h(`a.rail-job.${j.status}${j.id === currentId ? ".active" : ""}`,
      { href: `#/${j.id}`, title: `${j.filename} · ${STATUS_UA[j.status]}` }, thumbnail(j)))));
  $("#jobs").replaceChildren(...(jobs.length ? jobs.map((j) =>
    h("li", {},
      h(`a.job${j.id === currentId ? ".active" : ""}`, { href: `#/${j.id}` },
        h("span.thumb", {}, thumbnail(j)),
        h("span.job-meta", {},
          h("b", { title: j.filename }, j.filename),
          h("small", {}, h(`span.status.${j.status}`, {}, STATUS_UA[j.status]), " · ", ago(j.created))))))
    : [h("li.empty", {}, "Тут з'являться ваші кадри.")]));
}
