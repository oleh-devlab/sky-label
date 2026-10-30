// Типи й виклики HTTP API бекенда (див. astro_search/server.py і README).

export type JobStatus = "queued" | "running" | "done" | "failed";

export interface Health {
  status: "ok" | "no_catalog";
  version: string;
  solvers: Record<string, boolean>;
  catalog_stars: number;
  ephemeris: string;
  default_location: { lat: number; lon: number; elevation_m: number };
  queue_length: number;
}

export interface Job {
  id: string;
  filename: string;
  original: string;
  status: JobStatus;
  created: string;
  started: string | null;
  finished: string | null;
  error: string | null;
  params: Record<string, string | number>;
  links: { self: string; preview: string; original: string };
  result?: Result | null;
}

export interface Star {
  name: string;
  proper_name: string | null;
  designation: string | null;
  constellation: string | null;
  hip: number | null;
  hr: number | null;
  mag: number;
  x: number;
  y: number;
  ra_deg: number;
  dec_deg: number;
  detection: { index: number; offset_arcsec: number } | null;
  alt_deg?: number;
  alt_refracted_deg?: number;
  az_deg?: number;
}

export interface Body {
  name: string;
  x: number;
  y: number;
  ra_deg: number;
  dec_deg: number;
  distance_au: number;
  alt_deg: number;
  alt_refracted_deg: number;
  az_deg: number;
  mag: number | null;
  nearest_detection: { index: number; x: number; y: number; offset_px: number; offset_arcsec: number } | null;
}

export interface GridLine {
  kind: "alt" | "az";
  value: number;
  lines: [number, number][][];
}

export interface Solution {
  solver: string;
  solve_ms: number;
  solver_info: Record<string, unknown>;
  center: { ra_deg: number; dec_deg: number };
  fov_deg: [number, number];
  scale_arcsec_per_px: number;
  up_position_angle_deg: number;
  matched_stars: number;
  residual_arcsec: { rms: number; median: number; p90: number; max: number };
  sip_order: number | null;
  wcs_header: Record<string, string | number | boolean>;
}

export interface Camera {
  exposure_s: number | null;
  focal_length_mm: number | null;
  focal_length_35mm: number | null;
  iso: number | null;
  make: string | null;
  model: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  gps_elevation_m: number | null;
  local_time_without_offset: string | null;
}

export interface Result {
  version: number;
  image: { width: number; height: number; kind: string };
  observation: {
    time_utc: string | null;
    time_source: "user" | "exif" | null;
    location: { lat: number; lon: number; elevation_m: number; source: "user" | "exif" | "default" };
    camera: Camera;
  };
  solved: boolean;
  solution: Solution | null;
  stars: Star[];
  bodies: Body[];
  horizontal: {
    center: { alt_deg: number; alt_refracted_deg: number; az_deg: number };
    alt_range_deg: [number, number];
    zenith_direction_deg: number | null;
    grid: GridLine[];
  } | null;
  detections: [number, number, number][];
  warnings: string[];
  timings: Record<string, number>;
}

export class ApiError extends Error {}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      msg = (await r.json()).error ?? msg;
    } catch {
      /* не JSON */
    }
    throw new ApiError(msg);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

export const api = {
  health: () => call<Health>("/api/health"),
  jobs: () => call<Job[]>("/api/jobs"),
  job: (id: string) => call<Job>(`/api/jobs/${id}`),
  remove: (id: string) => call<void>(`/api/jobs/${id}`, { method: "DELETE" }),

  /** Завантаження через XHR, бо fetch не повідомляє про прогрес відправлення. */
  upload(form: FormData, onProgress: (fraction: number) => void): Promise<Job> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/jobs");
      xhr.responseType = "json";
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        if (xhr.status === 202) resolve(xhr.response as Job);
        else reject(new ApiError(xhr.response?.error ?? `${xhr.status} ${xhr.statusText}`));
      };
      xhr.onerror = () => reject(new ApiError("Сервер недоступний"));
      xhr.send(form);
    });
  },
};
