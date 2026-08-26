# Pixovo — MVP Working Report

*A detailed account of what the current build actually does, which functions drive it and why, what shape it's in today, and where it's headed per the team's own roadmap.*

---

## 1. What Pixovo Is

Pixovo is an automated photobook design system. A user uploads a batch of raw photos, the system silently throws out the bad ones (blurry, duplicate, junk/document scans), groups the survivors into a coherent story, generates three distinct themed layout variations, lets the user preview and reshuffle them in an interactive UI, and finally exports a print-ready 300 DPI PDF with proper bleed margins.

It's a two-service app: a **FastAPI/Python backend** (image processing, layout math, persistence) and a **plain React/Vite frontend** (upload UI, preview, export trigger), talking over a REST + Server-Sent-Events API on `localhost:8000` / `localhost:5173` in dev.

---

## 2. End-to-End Scenario — How a Photobook Actually Gets Made

Walking through what happens when a real user drags in 150 photos:

1. **Drop the photos.** [`PhotoUploader.jsx`](frontend/src/components/PhotoUploader.jsx) hands the file list to `PixovoClientDownsampler`, which now runs on a pool of Web Workers ([`downsample.worker.js`](frontend/src/utils/downsample.worker.js)) instead of the main thread — the tab stays responsive while 150 images get decoded and resized. Each photo is downscaled to a ≤512px JPEG thumbnail via `OffscreenCanvas`, and a client-side `photo_id` (`px_<random>_<timestamp>`) is minted for it. The original full-resolution file is kept in memory untouched, earmarked for later.

2. **UI advances immediately.** [`App.jsx`](frontend/src/App.jsx)'s `handlePhotosUploaded` doesn't wait for anything to finish uploading — it flips the screen to the theme-prompt step right away so the user can start typing while ingestion happens in the background.

3. **Thumbnails + metadata go to the backend in chunks of 50.** Each chunk is POSTed to `/api/photobook/ingest` ([`main.py:160`](backend/app/main.py:160)). The very first chunk gets back a `session_id`, which is now persisted to `localStorage` and reused for every subsequent chunk *and* survives a page refresh mid-upload.

4. **The backend filters the batch.** `Phase1FilterEngine.run_phase1_filtering()` ([`filter_engine.py:1001`](backend/app/engine/filter/filter_engine.py:1001)) runs off the event loop in a CPU-sized thread pool and executes, per photo:
   - **Exposure guard** — rejects true blackout/whiteout frames while protecting artistic low-key/high-key shots.
   - **Junk/document detector** — QR codes, scanned receipts, screenshots get rejected; faces, natural detail shots, and B&W art photos are protected.
   - **Focal sharpness (Tenengrad + Laplacian)** — rejects out-of-focus shots, with a relaxed threshold for dim/low-key solo photos.
   - **Burst dedupe** — near-identical consecutive shots (matched by dual perceptual hash: background "shell" + subject "core") get thinned to one survivor, unless a group shot needs a backup with better face confidence.
   - **Event clustering (DBSCAN)** — survivors are grouped into chronological/geographic "story chapters."
   - Every survivor also gets a `hero_score` (composition/resolution/face quality/sharpness) that later decides which photo gets the big double-page-spread treatment.

5. **Survivors get batch-committed to SQLite.** `SessionStore.save_photos_batch()` ([`session_store.py:115`](backend/app/db/session_store.py:115)) does one atomic `executemany` insert per chunk rather than 50 separate writes — this is what makes 1000-photo sessions viable.

6. **Original HD files stream up in the background,** three at a time, each one first cached in IndexedDB (so a dropped connection doesn't lose it) and removed only after the backend confirms receipt via `/api/upload-originals`.

7. **User submits a theme prompt** ("Friends at ISKCON Temple", "Family Memories", etc.). `App.jsx` POSTs to `/api/generate-async`, gets a `202 Accepted` + `job_id` back immediately, and opens an `EventSource` against `/api/jobs/{job_id}/stream` — the backend now pushes progress updates as they happen instead of the client polling every 500ms.

8. **The backend does the actual "designing" in the background** (`process_async_job` → `_run_job`, [`main.py:561`](backend/app/main.py:561), gated by a CPU-sized `asyncio.Semaphore`):
   - `generate_story_theme_batch()` ([`story_ai.py`](backend/app/engine/story_ai.py)) picks a category, 3 distinct themes from a 20-theme palette matrix, and writes cover titles/captions. (Currently a fast local rules engine — see §4.)
   - `generate_photobook_variations_engine()` ([`solver.py`](backend/app/engine/solver.py)) partitions photos into macro chapters, clusters them 2-4 at a time into spread-sized chunks, and for each chunk calls `build_dsa_spread_pair()` ([`dsa_solver.py:394`](backend/app/engine/dsa_solver.py:394)) to compute exact `(x_pct, y_pct, w_pct, h_pct)` slot geometry — deciding which photo is "dominant," which layout family fits the photo count, and whether a caption needs a text slot.
   - Each slot also gets a pre-flight **effective DPI** badge (excellent/warning/alert) computed from the photo's native resolution vs. its physical slot size at 300 DPI, so print-quality problems surface before export, not after.
   - Three complete variations come out the other end.

9. **Preview.** `BookCarousel3D.jsx` lets the user flip through the 3 variations; `SpreadViewer.jsx` virtualizes rendering so only the visible spreads (+2 buffer) are in the DOM, even for a 100-spread book. The user can reshuffle a single spread or the whole set.

10. **Export.** `handleExportPDF` calls `/api/export-pdf`, which runs `generate_print_pdf_engine()` ([`pdf_exporter.py`](backend/app/engine/pdf_exporter.py)) — a raw ReportLab canvas draws each spread at 300 DPI with 3mm bleed, embedding the original JPEGs' compressed data directly (no re-compression pass, so no generational quality loss) and clip-masking each photo to its slot. Out comes a downloadable PDF.

That's the whole loop — upload → filter → cluster → theme → lay out → preview → print.

---

## 3. The Functions Doing the Real Work, and Why Each One Exists

| Function / Module | Purpose | Why it's built this way |
|---|---|---|
| `PixovoClientDownsampler.processBatch()` [(client_downsampler.js)](frontend/src/utils/client_downsampler.js) | Resizes raw photos to ≤512px thumbnails in the browser before upload | Uploading 150 full-res 8K photos (could be 20MB each) just to filter them would be enormous wasted bandwidth; a 512px thumbnail is all the filter engine needs to make its decisions |
| `Phase1FilterEngine.run_phase1_filtering()` [(filter_engine.py:1001)](backend/app/engine/filter/filter_engine.py:1001) | Runs the 6-layer quality gate (exposure/junk/blur/dedupe/cluster/hero-score) | Manually curating hundreds of photos is the actual tedious part of making a photobook; automating "throw out the bad ones" is the product's core value |
| `SessionStore.save_photos_batch()` [(session_store.py:115)](backend/app/db/session_store.py:115) | Atomic batch SQLite insert for survivors | A naive per-photo `INSERT` loop is what originally broke at scale (the "1000 photos" fix in commit `652e09e`) — one transaction for 50-1000 rows is dramatically faster |
| `SessionStore.get_photos()` [(session_store.py:187)](backend/app/db/session_store.py:187) | Fetches photos in chunks of 500 | SQLite caps bound parameters at 999 per statement — fetching 1000+ photo IDs in one `IN (...)` clause would just error out |
| `generate_story_theme_batch()` [(story_ai.py)](backend/app/engine/story_ai.py) | Picks a category + 3 themes + captions from a user's free-text prompt | Turns "Friends at ISKCON Temple" into structured design decisions (palette, tone, titles) without the user ever touching a design tool |
| `partition_macro_chapters` / `cluster_photos_2tier_engine` [(dsa_solver.py)](backend/app/engine/dsa_solver.py) | Groups the flat photo list into chronological chapters, then into spread-sized visual chunks | A photobook that's just "photo 1, photo 2, photo 3..." in upload order isn't a story; grouping by time/place/subject-similarity is what makes it read as one |
| `build_dsa_spread_pair()` [(dsa_solver.py:394)](backend/app/engine/dsa_solver.py:394) | Computes exact percentage-based bounding boxes for every photo slot on a spread | This is the actual "layout designer" — it decides sizes/positions so photos of different aspect ratios tile a page with no gaps and no arbitrary cropping |
| Pre-flight DPI check (inside `build_dsa_spread_pair`) | Flags any slot where the source photo doesn't have enough resolution for its printed size | Catches "this photo will look blurry in print" *before* the user pays for a PDF, not after |
| `generate_print_pdf_engine()` [(pdf_exporter.py)](backend/app/engine/pdf_exporter.py) | Compiles the chosen variation into a vector 300 DPI PDF with bleed | The actual deliverable — everything upstream exists to produce this file correctly |
| `_job_event_stream()` / SSE endpoint [(main.py)](backend/app/main.py) | Pushes job progress to the browser as it changes | Generation takes several seconds; the user needs live feedback, and server-push is cheaper than the client hammering a status endpoint every 500ms |
| `BoundedCache` [(main.py)](backend/app/main.py) | Caps `PHOTO_STORE`/`JOBS_STORE` in-memory dict size with LRU eviction | Without this the process's memory grows for its entire uptime; SQLite is already the durable fallback, so evicting old entries is safe |
| `sweep_expired_sessions()` [(cleanup.py)](backend/app/cleanup.py) | Deletes old session directories/DB rows/export PDFs on startup | Nothing previously deleted anything — disk usage and the DB would grow forever otherwise |

---

## 4. Where Things Stand Right Now

This section reflects the state **after** a recent hardening pass (dead-endpoint removal, SSE, worker-based downsampling, bounded caches, retention sweep, and initial test coverage — see git history for specifics).

**Solid:**
- The filtering → clustering → layout pipeline is genuinely sophisticated and works end-to-end.
- 1000-photo sessions are now handled correctly at the DB layer (batched writes, chunked reads).
- Job status uses SSE push; client downsampling runs off the main thread; concurrency limits scale with the host machine instead of a hardcoded `4`.
- A retention sweep now actually deletes expired session data instead of accumulating forever.
- `backend/tests/` has real coverage (22 tests) of the SQLite batch layer, filter engine thresholds, and layout solver geometry — previously zero.

**Still true, worth knowing:**
- **No authentication.** `session_id` is the only access boundary, and it's guessable. Anyone with a session_id can read another session's `/uploads`.
- **Gemini AI is present but off by default.** `generate_story_theme_batch()` currently always uses a fast local rules-based fallback (keyword matching against a 20-theme matrix), not a live LLM call — the toggle is now an explicit env var (`PIXOVO_ENABLE_GEMINI`) rather than a silent hardcoded flag, but the behavior hasn't changed. See §5, item 4.1.
- **Single SQLite file, single machine.** Fine for the current scale; would need to move to Postgres to run multiple backend instances behind a load balancer.
- **Local-dev/demo deployment only.** No Dockerfile, no CI, `run.py` now supports a non-reload multi-worker mode but there's still no process manager/orchestration around it.
- **Documentation drift.** `ARCHITECTURE.md` still lists `EmotionThemeSelector.jsx` (removed as dead code) and doesn't yet reflect the SSE/worker/cleanup changes — worth a refresh pass.

---

## 5. Where It's Going — The Team's Own Roadmap

Pulled from [`TODO.md`](TODO.md), organized by theme:

### 5.1 Strip developer/debug surface before real users see it
- Remove the `BoilerplateInspector` tab and lock down `/api/templates`, `/api/palettes`, `/api/categories`.
- Gate `/api/stats` and `SystemStatsDashboard.jsx` behind admin auth instead of exposing server internals on the main toolbar.
- Replace hardcoded mock sample photos (`sample_1`, etc.) with a real validation error when nobody has valid surviving photos.
- Clean out legacy scratch directories and verification scripts.

### 5.2 Move storage to the cloud
- Pre-signed S3/GCS upload URLs so the browser uploads thumbnails *and* originals directly to object storage — bypassing the FastAPI server entirely for file bytes.
- Optional Lambda/Cloud Function hooks for EXIF extraction on upload.
- Serve previews from a CDN (CloudFront/S3) instead of the local `/uploads/` static mount.

### 5.3 Push print quality further
- CMYK color space + ICC profile support (FOGRA39/GRACoL) for real commercial press output, not just RGB PDF.
- Face-aware smart cropping so a slot crop never clips a head.
- Stream PDF pages to disk incrementally instead of buffering every page's bitmaps in RAM — needed for books beyond ~100 pages.
- Direct order-submission integration with a print-on-demand fulfillment API (Prodigi, Gelato, etc.).

### 5.4 Make the AI and layout solver smarter
- **Turn Gemini back on** using the tiered chapter-summary prompting already sketched out in `story_ai.py`'s docstring: instead of sending 1,000 individual photo descriptions (which blows the token budget and triggers 429s), send 10-15 *chapter summaries* and let Gemini reason at that level while the DSA solver still handles per-photo placement.
- Expand the layout solver beyond today's 1-3 photo spreads to dynamic 4-6 photo collages.
- Let users drag-and-drop swap photos between slots interactively in `SpreadViewer.jsx`, rather than only "reshuffle the whole spread."

### 5.5 Production hardening
- Migrate SQLite → PostgreSQL and add Redis, enabling a horizontally-scaled, multi-worker deployment (this is the point where the current SQLite-file architecture stops being enough).
- Add IP-based rate limiting (`slowapi`) on upload/generation routes.
- ~~Automated upload cleanup job~~ — **now implemented** as the startup retention sweep (`cleanup.py`); the team's original plan called for a 24-hour TTL specifically, current default is 14 days (`PIXOVO_RETENTION_DAYS`), worth revisiting once real usage patterns are known.

Notably absent from the team's own TODO but worth flagging alongside it: **authentication/authorization** isn't mentioned anywhere in the roadmap, despite being the single biggest gap before this could safely serve real users with real photos. Worth raising with the team explicitly rather than assuming it's implied by "production hardening."

---

## 6. One-Paragraph Summary

Pixovo today is a working local-dev MVP: drop in photos, get back a filtered, story-clustered, professionally laid-out, print-ready photobook PDF, entirely through automated computer vision and layout math — no live LLM call currently in the loop despite the plumbing being ready for one. The core pipeline (filter → cluster → theme → lay out → export) is the most mature part of the codebase and genuinely works at the 1000-photo scale it was built for. What's missing to go from "working demo" to "product real users can hit" is entirely in the surrounding infrastructure: authentication, cloud storage, a database that isn't a single file, and the security/ops hardening the team has already scoped out in their own TODO — none of which touches the actual photobook-design logic that makes this project interesting.
