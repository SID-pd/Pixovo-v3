# Pixovo — MVP Working Report

*A detailed account of what the current build actually does, which functions drive it and why, what shape it's in today, and where it's headed per the team's own roadmap.*

---

## 1. What Pixovo Is

Pixovo is an automated photobook design system. A user uploads a batch of raw photos, the system silently throws out the bad ones (blurry, duplicate, junk/document scans), groups the survivors into a coherent story, generates three distinct themed layout variations, lets the user preview and reshuffle them in an interactive UI, and finally exports a print-ready 300 DPI PDF with proper bleed margins.

It's a two-service app: a **FastAPI/Python backend** (image processing, layout math, persistence) and a **plain React/Vite frontend** (upload UI, preview, export trigger), talking over a REST API on `localhost:8000` / `localhost:5173` in dev.

---

## 2. End-to-End Scenario — How a Photobook Actually Gets Made

Walking through what happens when a real user drags in 150 photos:

1. **Drop the photos.** [`PhotoUploader.jsx`](frontend/src/components/PhotoUploader.jsx) hands the file list to `PixovoClientDownsampler`, which resizes every photo **on the main browser thread** using an `<img>` + `<canvas>` — despite `ARCHITECTURE.md`'s claim of "Web Workers," there is no worker in the actual code, so a large batch visibly freezes the tab while it downsamples. Each photo is downscaled to a ≤512px JPEG thumbnail, and a client-side `photo_id` (`px_<random>_<timestamp>`) is minted for it. The original full-resolution file is kept in memory untouched, earmarked for later.

2. **UI advances immediately.** [`App.jsx`](frontend/src/App.jsx)'s `handlePhotosUploaded` doesn't wait for anything to finish uploading — it flips the screen to the theme-prompt step right away so the user can start typing while ingestion happens in the background.

3. **Thumbnails + metadata go to the backend in chunks of 50.** Each chunk is POSTed to `/api/photobook/ingest` ([`main.py:126`](backend/app/main.py:126)). The very first chunk gets back a `session_id`, which lives only in a plain JS variable (`lastSessionId`) inside `handlePhotosUploaded` — a page refresh mid-upload loses it, even though IndexedDB still has the queued HD originals waiting to sync.

4. **The backend filters the batch.** `Phase1FilterEngine.run_phase1_filtering()` ([`filter_engine.py:1001`](backend/app/engine/filter/filter_engine.py:1001)) runs off the event loop in a CPU-sized thread pool and executes, per photo:
   - **Exposure guard** — rejects true blackout/whiteout frames while protecting artistic low-key/high-key shots.
   - **Junk/document detector** — QR codes, scanned receipts, screenshots get rejected; faces, natural detail shots, and B&W art photos are protected.
   - **Focal sharpness (Tenengrad + Laplacian)** — rejects out-of-focus shots, with a relaxed threshold for dim/low-key solo photos.
   - **Burst dedupe** — near-identical consecutive shots (matched by dual perceptual hash: background "shell" + subject "core") get thinned to one survivor, unless a group shot needs a backup with better face confidence.
   - **Event clustering (DBSCAN)** — survivors are grouped into chronological/geographic "story chapters."
   - Every survivor also gets a `hero_score` (composition/resolution/face quality/sharpness) that later decides which photo gets the big double-page-spread treatment.

5. **Survivors get batch-committed to SQLite.** `SessionStore.save_photos_batch()` ([`session_store.py:115`](backend/app/db/session_store.py:115)) does one atomic `executemany` insert per chunk rather than 50 separate writes — this is what makes 1000-photo sessions viable.

6. **Original HD files stream up in the background,** three at a time, each one first cached in IndexedDB (so a dropped connection doesn't lose it) and removed only after the backend confirms receipt via `/api/upload-originals`.

7. **User submits a theme prompt** ("Friends at ISKCON Temple", "Family Memories", etc.). `App.jsx` POSTs to `/api/generate-async`, gets a `202 Accepted` + `job_id` back immediately, and starts polling `GET /api/jobs/{job_id}` on a plain `setInterval` every **500ms** until the job completes or fails.

8. **The backend does the actual "designing" in the background** (`process_async_job`, [`main.py:706`](backend/app/main.py:706), gated by a hardcoded `asyncio.Semaphore(4)`):
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
| `pollJobStatus()` [(App.jsx)](frontend/src/App.jsx) | Fetches `/api/jobs/{id}` every 500ms until the job resolves | Generation takes several seconds and the client needs live progress; a fixed-interval poll is the simplest way to get it, at the cost of a steady stream of requests per active user |
| `PHOTO_STORE` / `JOBS_STORE` [(main.py)](backend/app/main.py) | Plain in-memory dicts caching photos/jobs, backed by SQLite on miss | Fast-path reads without a DB round-trip for the common case; they currently have no eviction, so they grow for the life of the process |

---

## 4. Where Things Stand Right Now

This is the state of the code on disk as of this report. A hardening pass (dead-endpoint removal, SSE push, worker-based downsampling, bounded caches, a retention sweep, and test coverage) was implemented and verified earlier in this session, but is **no longer present in the working tree** — the repo has reverted to its pre-hardening state (only `pixovo_session.db`'s contents differ from the last commit). Nothing here reflects that work; it's a plain read of what's actually in the files right now.

**Solid:**
- The filtering → clustering → layout pipeline is genuinely sophisticated and works end-to-end.
- 1000-photo sessions are handled correctly at the DB layer: `SessionStore.save_photos_batch()` does one atomic `executemany` insert, and `get_photos()` chunks reads into groups of 500 to stay under SQLite's 999-parameter limit.
- The DSA layout solver and DPI pre-flight check are the most mature, differentiated part of the codebase.

**Known gaps, as currently in the code:**
- **No authentication.** `session_id` is the only access boundary, and it's a guessable 8-hex-char string. Anyone with (or guessing) a session_id can read another session's `/uploads` directly — the static mount has no access check.
- **Two endpoints are broken.** `POST /api/curate-photos` and `POST /api/curate-and-generate` ([main.py:381](backend/app/main.py:381), [main.py:473](backend/app/main.py:473)) import `app.engine.selection.curation_pipeline.MasterCurationPipeline`, which doesn't exist anywhere in the repo — both 500 on every call. Unused by the frontend, but live in the API surface.
- **Gemini AI is wired but hardcoded off.** `story_ai.py:208` sets `ENABLE_GEMINI_API = False` directly in code — `generate_story_theme_batch()` always uses the local rules-based fallback regardless of whether `GEMINI_API_KEY` is set.
- **CORS allows `"*"`** alongside an explicit origin allowlist ([main.py:47-53](backend/app/main.py:47)) — the wildcard makes the allowlist redundant.
- **Client downsampling runs on the main thread**, not a Web Worker as `ARCHITECTURE.md` claims — a large batch upload will visibly freeze the tab.
- **Job status is 500ms polling**, not push — `App.jsx`'s `pollJobStatus` hits `/api/jobs/{id}` on a fixed interval.
- **Concurrency limits are hardcoded** (`ThreadPoolExecutor(max_workers=4)`, `asyncio.Semaphore(4)`), independent of the host machine's actual CPU count.
- **`PHOTO_STORE`/`JOBS_STORE` have no eviction** — they grow in memory for the life of the process.
- **`EmotionThemeSelector.jsx` is unused dead code**, not imported anywhere.
- **Nothing deletes old data.** No retention sweep exists — uploads, exports, and DB rows accumulate indefinitely.
- **Zero test coverage.** `pytest` is a listed dependency; there are no test files anywhere in the repo.
- **Real photo data is committed to git**: `backend/pixovo_session.db` (containing actual uploaded photo blob references) and `backend/venv/` (a full virtualenv) are both tracked.
- **Single SQLite file, single machine, local-dev only.** No Dockerfile, no CI; `run.py` hardcodes `reload=True`, which is a dev-only flag being used as the de facto production entrypoint.
- **Documentation drift.** `ARCHITECTURE.md` describes Web Workers and other behavior that doesn't match the current code — treat it as aspirational/historical rather than a literal spec.

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
- Automated upload cleanup job with a 24-hour retention TTL on temporary session files and exported PDFs — nothing currently deletes anything, so this is still fully open.

Notably absent from the team's own TODO but worth flagging alongside it: **authentication/authorization** isn't mentioned anywhere in the roadmap, despite being the single biggest gap before this could safely serve real users with real photos. Worth raising with the team explicitly rather than assuming it's implied by "production hardening."

---

## 6. One-Paragraph Summary

Pixovo today is a working local-dev MVP: drop in photos, get back a filtered, story-clustered, professionally laid-out, print-ready photobook PDF, entirely through automated computer vision and layout math — no live LLM call currently in the loop despite the plumbing being ready for one. The core pipeline (filter → cluster → theme → lay out → export) is the most mature part of the codebase and genuinely works at the 1000-photo scale it was built for. Everything around that core is still in its original, unhardened state as of this report: no auth, two endpoints that 500 on every call, main-thread downsampling, fixed-interval polling instead of push, hardcoded concurrency limits, unbounded in-memory caches, no data retention, and zero test coverage. What's missing to go from "working demo" to "product real users can hit" is entirely in that surrounding infrastructure — none of it touches the actual photobook-design logic that makes this project interesting.
