# Pixovo v3.9 — Clean Architecture Reference

Pixovo is an enterprise-grade automated photobook design system. It takes user-uploaded photos, cleans & curates them, creates intelligent story themes, solves optimal layouts, previews them in an interactive 3D virtualized UI, and exports print-ready 300 DPI PDFs.

---

## 🏗️ High-Level System Architecture

```
[ FRONTEND (React + Vite) ]
  ├── 1. Client Downsampler (512px WebP + EXIF parsing in browser Web Workers)
  ├── 2. 2-Tier Progressive Ingestion (Lightweight preview foreground + HD background sync)
  ├── 3. Virtualized Spread Viewport (Windowed rendering @ 60 FPS on 1,000 photos)
  └── 4. 3D Cover Carousel & Live Performance Telemetry Dashboard
          │
          ▼ (HTTP REST API / JSON + Multipart)
[ BACKEND GATEWAY (FastAPI / Python 3.10+) ]
  ├── 1. ThreadPool Offloaded Filter Engine (Blur detection, pHash de-duplication, flyer rejection)
  ├── 2. Story AI Engine (Chrono & Geo Macro-Clustering + Theme/Caption generation)
  ├── 3. Dynamic DSA Solver Engine (Safe-bounds box fitting, slot aspect-matching, hero placement)
  ├── 4. Pre-Flight Print DPI Validation (Mathematical effective DPI computation & badging)
  ├── 5. SQLite WAL-Mode Session Store (Zero-data-loss persistence across restarts)
  └── 6. High-Res 300 DPI PDF Exporter (Vector ReportLab canvas with 3mm print bleeds)
```

---

## 🔄 The 7-Stage Pipeline Lifecycle

### Stage 1: Browser-Side Downsampling & EXIF Extraction
* **Source:** `frontend/src/utils/client_downsampler.js`, `PhotoUploader.jsx`
* **What happens:**
  1. Raw 4K/8K images are downscaled in browser memory to 512px WebP/JPEG thumbnails (~30KB).
  2. Extracts EXIF capture timestamps, orientation, and GPS coordinates.
  3. Raw original blobs are temporarily backed up into `IndexedDB` (`indexedDB.js`) to guarantee zero client data loss on network drops.

### Stage 2: Phase 1 Gateway Ingestion & Quality Filtering
* **Source:** `backend/app/main.py` (`/api/photobook/ingest`), `backend/app/engine/filter/filter_engine.py`
* **What happens:**
  1. Receives 512px thumbnails + EXIF JSON metadata.
  2. Offloaded to `CPU_WORKER_POOL` to prevent blocking the async event loop:
     - **Integrity Check:** Rejects corrupt or invalid image files.
     - **Laplacian Blur Variance:** Rejects blurry photos ($\text{Score} < 40.0$).
     - **pHash Deduplication:** Filters near-identical burst shots ($\text{Hamming Distance} \le 2$).

### Stage 3: SQLite WAL Persistence & Progressive HD Sync
* **Source:** `backend/app/db/session_store.py`
* **What happens:**
  1. Survived photos are committed to `pixovo_session.db` with `WAL` (Write-Ahead Logging) mode.
  2. **Foreground:** Frontend immediately advances to theme/chatbot prompt without waiting for raw original uploads (<2s).
  3. **Background:** Concurrency-throttled queue streams raw 300 DPI files via `POST /api/upload-originals`.

### Stage 4: Story Intelligence & Macro-Clustering
* **Source:** `backend/app/engine/story_ai.py`
* **What happens:**
  1. Partitions photos chronologically into Story Chapters based on timestamp deltas (>45m) and GPS distance (>5km).
  2. Assigns thematic palettes (e.g. *Warm, Nostalgic, Minimalist, Bold*) and generates dynamic titles.

### Stage 5: DSA Dynamic Layout Solver & Pre-Flight DPI Check
* **Source:** `backend/app/engine/dsa_solver.py`, `color_extractor.py`
* **What happens:**
  1. Calculates exact physical millimeter bounding boxes for Left & Right spreads.
  2. Solves photo aspect ratios into dynamic uncropped templates with zero empty slots.
  3. **Pre-Flight DPI Check:** Calculates effective print DPI for every slot:
     - $\ge 250\text{ DPI} \rightarrow \text{Excellent}$
     - $150 - 249\text{ DPI} \rightarrow \text{Warning}$
     - $< 150\text{ DPI} \rightarrow \text{Alert Badge}$

### Stage 6: Interactive 60 FPS Viewport Preview & Reshuffle
* **Source:** `frontend/src/components/SpreadViewer.jsx`, `BookCarousel3D.jsx`
* **What happens:**
  1. Viewport windowing renders only the 3–4 visible spreads in the active DOM (+2 buffer).
  2. User can click any individual spread or the global reshuffle button to re-run layout variations in ~10ms.

### Stage 7: High-Res 300 DPI Vector PDF Export
* **Source:** `backend/app/engine/pdf_exporter.py`
* **What happens:**
  1. Pulls original full-resolution files matching the spread slots.
  2. Renders 300 DPI vector PDF with 3mm bleed margins, vector clipping masks, and typography.

---

## 📁 Clean File Structure

```
pixovo-clean/
├── backend/
│   ├── app/
│   │   ├── config.py              # Configuration & Loguru logger
│   │   ├── main.py                # FastAPI routes, endpoints & concurrency
│   │   ├── metrics.py             # Telemetry & step timer collector
│   │   ├── db/
│   │   │   └── session_store.py   # SQLite WAL persistent database store
│   │   ├── engine/
│   │   │   ├── color_extractor.py # Theme matrix & palette extraction
│   │   │   ├── dsa_solver.py      # Dynamic page packing & Pre-flight DPI
│   │   │   ├── pdf_exporter.py    # 300 DPI Vector PDF compiler
│   │   │   ├── registry.py        # Template definitions
│   │   │   ├── solver.py          # Multi-variation generator
│   │   │   ├── story_ai.py        # Chrono macro-clustering & Story AI
│   │   │   └── filter/
│   │   │       ├── filter_engine.py # Blur, pHash dedupe & junk gate
│   │   │       └── face_detector/   # Face models
│   │   └── schemas/
│   │       └── photobook.py       # Pydantic schemas
│   ├── requirements.txt           # Python dependencies
│   └── run.py                     # Backend startup script
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx                # State coordinator & upload queue
│   │   ├── main.jsx               # React DOM root
│   │   ├── components/
│   │   │   ├── AIChatbotWidget.jsx
│   │   │   ├── BoilerplateInspector.jsx
│   │   │   ├── BookCarousel3D.jsx
│   │   │   ├── EmotionThemeSelector.jsx
│   │   │   ├── PhotoUploader.jsx
│   │   │   ├── SpreadViewer.jsx
│   │   │   ├── SystemStatsDashboard.jsx
│   │   │   └── ToolbarHeader.jsx
│   │   ├── styles/
│   │   │   └── storymode.css
│   │   └── utils/
│   │       ├── client_downsampler.js
│   │       └── indexedDB.js
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
│
├── ARCHITECTURE.md
└── QUICKSTART.md
```
