# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

astro-search is a local, offline web service for night-sky photos: it finds stars on a frame, plate-solves it, labels stars, planets, the Moon and constellations, computes altitude/azimuth and reports accuracy. Backend is Python (aiohttp); frontend is Vite + TypeScript without a framework. README.md documents the API and the `result.json` schema. IDEAS.md records verified results, open questions and the plan; update its status lines when you finish something it lists.

The UI, docs, code comments and API error messages are in Ukrainian. Keep them in Ukrainian. Commit messages are in English.

## Commands

Development without Docker (Linux, Python 3.10+). astrometry.net is optional; without it only tetra3rs solves.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/prepare_data.py --out ./catalog          # downloads HYG + constellation lines, builds tetra3.db (~15 s)
(cd frontend && npm ci && npm run build)                 # page into frontend/dist
ASTRO_CATALOG_DIR=./catalog python -m astro_search       # server on 127.0.0.1:8080
ASTRO_CATALOG_DIR=./catalog python -m astro_search.cli photo.jpg --time 2024-11-01T22:30:01+02:00 --out result.json
```

`astro_search.cli` writes `preview.jpg` into the current directory by default (it is git-ignored).

- Frontend with hot reload: run the server, then `cd frontend && npm run dev` (Vite at `http://localhost:5173/static/`, `/api` proxied to `127.0.0.1:8080` or `$ASTRO_API`).
- Type-check the frontend: `cd frontend && npm run typecheck`. `npm run build` type-checks too.
- Unit tests (no catalog or network needed): `pip install -r requirements-dev.txt && python -m pytest`. Run a single test with `python -m pytest tests/test_distortion.py::test_choose_prefers_plain_tan_without_distortion`. They cover projection helpers, the distortion fit and model choice, location priority and observation warnings, and API parameter validation.
- Accuracy check after touching detection, solving or refinement: `ASTRO_CATALOG_DIR=./catalog python scripts/check_accuracy.py`. It renders 16 synthetic frames from known WCS (no distortion, barrel, the wavy distortion measured on a real Galaxy S24 Ultra frame, narrow field; stars everywhere, in one half, one quarter, bright only), runs detection → tetra3rs → refine and measures error over the whole frame, including the empty parts. It exits 1 if any case leaves its threshold in `LIMITS`.
- There is no configured linter.
- Docker: `docker build -t astro-search .` and `docker run --rm -p 127.0.0.1:8080:8080 -v astro-search-data:/data astro-search`. Docker Hub is blocked from the Claude cloud environment, so the image cannot be built there; verify the steps by running them directly (venv, `prepare_data.py`, `scripts/entrypoint.sh`).

Configuration is env vars, all `ASTRO_*`, read in `astro_search/config.py`. The README has the full table.

## Architecture

### Processing pipeline (`astro_search/pipeline.py: process`)

1. `ingest.load_image` reads JPG/PNG/HEIC through Pillow, RAW through rawpy and linear DNG through tifffile. It returns a grayscale array, an RGB preview of the **same pixel size**, and EXIF metadata. All pixel coordinates in results are 0-based, x right, y down, identical to the preview. FITS `CRPIX` is 1-based.
2. `stars.detect` subtracts background, keeps peaks above 5σ (MAD) and centroids them. For raster images it also drops strongly coloured points.
3. Initial solve, TAN only: `solve.solve_tetra3` first, then `solve.solve_astrometry` as fallback.
4. `solve.refine` matches HYG catalog stars to detections (mutual nearest neighbour), clips outliers and fits the distortion model. Thresholds and the next matching radius use a median-based σ (`_robust_sigma`), and the radius never grows past the initial one. With an rms-based σ, false pairs inflated the radius until refinement diverged.
5. `distortion.py` fits TAN plus distortion by linear least squares in normalized coordinates. Candidate models run from plain TAN through cubic + radial r⁴/r⁶ to a full quintic. 5-fold cross-validation picks the simplest model within 5 % of the best, and the result is exported as standard TAN-SIP. Avoid `astropy.wcs.utils.fit_wcs_from_points` for refinement:
   - with `proj_point="center"` it averages RA, which breaks near the celestial pole;
   - it fits polynomials in raw pixels, so it is ill-conditioned above SIP order 3.

   It is still used only for the initial TAN mapping, with an explicit tangent point.
6. Labels and overlays:
   - stars ≤ 6.5m in the frame;
   - `detection.in_fit: false` for bright, saturated stars or tight doubles that were found on the frame but excluded from the fit;
   - constellation lines (`sky.constellation_lines`): great-circle segments sampled and projected through the WCS, clipped to the frame.
7. `_apply_observation` computes everything that depends on time and place: star alt/az, bodies (`sky.bodies`, JPL DE421, topocentric) and the horizon grid (`sky.horizontal`). Without a capture time these are left empty.

Sky ↔ pixel geometry lives in `projection.py`. Sky → pixel conversion must go through `projection.world2pix`, or `projection.project`, which also applies `near_field`. Raw `wcs.all_world2pix` raises `NoConvergence` for the whole array as soon as one far-off point diverges under SIP. The pixel scale comes from `projection.pixel_scale_deg`.

### Time and location

Location priority, in `pipeline._resolve_location`:

1. form `lat`/`lon`;
2. EXIF GPS;
3. the browser's remembered "usual place", sent as `fallback_*` upload fields;
4. the server default (Lviv).

The source is recorded in `observation.location.source` (`user`/`exif`/`fallback`/`default`). Time/location warnings live in `observation.warnings` as well as in the top-level `warnings`.

`POST /api/jobs/{id}/observation` calls `pipeline.reobserve`. It rebuilds the WCS from `solution.wcs_header` and re-runs only `_apply_observation`, without solving again. If you add a time- or location-dependent field, compute it in `_apply_observation` so both paths stay consistent.

### Data (`scripts/prepare_data.py`, run at image build)

- `stars.npz`: HYG 4.1 subset to 8m.
- `constellations.npz`: d3-celestial IAU lines (BSD, pinned commit), stored as **index pairs into `stars.npz`**, so lines follow catalog proper motion. The two files must be built together. The d3-celestial license text is written next to them.
- `tetra3.db`.

`catalog.load_lines` returns `None` for catalogs prepared before the lines existed; code must tolerate that. The ephemeris comes from the `skyfield-data` package. At runtime nothing is downloaded.

### Jobs and server

`jobs.JobManager` keeps one job per directory under `ASTRO_DATA_DIR/jobs/<id>/`: `job.json`, the original, `preview.jpg` and `result.json`. Writes are atomic (tmp + `os.replace`).

Processing runs one job at a time in a single-worker `ProcessPoolExecutor` using the `spawn` context. The worker is warmed up by `pipeline.warmup`: catalog, constellation lines, the tetra3 DB and the ephemeris. A crashed worker is recreated. Queued and running jobs are re-queued on restart. `reobserve` runs in a thread of the main process, so it does not wait behind the queue.

`server.py` serves `index.html` from `ASTRO_WEB_DIR` at `/` and the built assets at `/static/`.

### Docker and frontend build

The Dockerfile is multi-stage. The `node:22` stage runs `npm ci` and a type-check. The final Ubuntu image gets the `node` binary and `frontend/` including `node_modules`.

`scripts/entrypoint.sh` rebuilds the frontend on **every container start**, offline, into `ASTRO_WEB_DIR` (`/app/web`). If that build fails, it keeps the copy built during `docker build`. Backend code is copied at build time, so backend changes need `docker build`; frontend source can be mounted (`-v ./frontend/src:/app/frontend/src:ro`) and picked up on restart.

### Frontend (`frontend/src`)

Vite `base` is `/static/`.

- `main.ts` is a thin entry point: server health, polling `/api/jobs` (fast while anything is queued or running), hash routing `#/<job id>`, and keyboard shortcuts. Re-opening the same job keeps the view (`Viewer.getView/setView`) and the open tab.
- `layout.ts` is the page shell:
  - the left panel, which collapses to a rail and is a drawer on phones;
  - the hideable result panel;
  - fullscreen, night mode, the help dialog and toasts.
- `upload.ts` holds the upload form, the immediate upload (rail "+", start-page button, drag-and-drop when the form is hidden), the "usual place" fallback fields and the hint.
- `screens.ts` builds the welcome, processing, failed and not-found screens. `jobList.ts` renders the frame list and the rail thumbnails.
- `viewer.ts`:
  - the `<img>` is transformed with CSS, and the SVG overlay works in **screen coordinates**. Grid and constellation lines use a group transform with `vector-effect: non-scaling-stroke`. Markers are repositioned every frame.
  - labels are placed greedily without overlap, in this order: body labels, constellation names, marker circles, star labels.
  - star label tiers by zoom: proper names and Bayer letters always, Flamsteed numbers from ×1.8, HIP/HR from ×3.5.
- Pieces split out of the viewer:
  - `marks.ts`: layer and target types, display names, marker radii and label tiers, shared by the viewer, the panel and the PNG export;
  - `cards.ts`: the object popover;
  - `exportPng.ts`: redraws the visible layers at full resolution.
- `panels.ts` holds the result tabs, including the time/place form from `place.ts`.
- `wcs.ts` is a JS TAN-SIP pix→sky plus approximate alt/az, used only for the live cursor readout.
- `storage.ts` wraps localStorage so a blocked storage never breaks the page. Keys: `astro-ui` (panel state), `astro-home` (the usual place), `astro-night` (red night mode).
- `styles/*.css` is one cascade split by area. `main.ts` imports the files in a fixed order, and that order matters.
