# 📌 Pixovo v3.9 — Future Action Plan & Technical TODOs

This document outlines the high-priority engineering tasks, optimizations, and production hardening steps to be implemented in subsequent phases.

---

## 🧹 1. Clean-up & Removal of Developer/Debug Functionalities

Before rolling out to production end-users, strip out internal development inspection views and mock fallback data:

- [ ] **Remove Boilerplate Inspector Tab:**
  - Remove `BoilerplateInspector.jsx` and its navigation trigger in `ToolbarHeader.jsx`.
  - Disable raw template JSON inspection endpoints (`/api/templates`, `/api/palettes`, `/api/categories`) from public access.
- [ ] **Gate Diagnostic Telemetry Dashboard (`SystemStatsDashboard.jsx`):**
  - Restrict `/api/stats` and `SystemStatsDashboard.jsx` behind an admin authentication route (e.g. `/admin/stats` or basic auth header) rather than exposing server internals on the main UI toolbar.
- [ ] **Purge Hardcoded Mock Sample Photos:**
  - Remove mock placeholder fallback photos (e.g., `sample_1`, `sample_2`, `sample3.jpg` in `main.py` and `solver.py`) and replace with explicit user validation errors (`"No valid surviving photos uploaded to generate album"`).
- [ ] **Remove Scratch & Verification Code:**
  - Clean up legacy root directories (`Filter/`, `Template_Engine_under_dev/`, `backend/verify_*.py`).

---

## ☁️ 2. Direct-to-S3 / Cloud Storage Optimization

Eliminate server disk I/O and network bandwidth bottlenecks by switching to direct cloud storage:

- [ ] **S3 / GCS Pre-Signed Upload Handshake:**
  - Implement backend endpoint `POST /api/storage/presigned-urls` generating time-limited pre-signed PUT URLs.
  - Update `client_downsampler.js` and `App.jsx` to upload both 512px previews and 300 DPI original print files directly from the user's browser to Amazon S3 / Google Cloud Storage.
- [ ] **S3 Event-Driven Webhooks / Lambda Thumbnail Resizing (Optional):**
  - Trigger automated image validation and EXIF metadata extraction via AWS Lambda / Cloud Functions on S3 upload events.
- [ ] **Direct S3 Previews in SpreadViewer:**
  - Point image `src` URLs directly to AWS CloudFront / S3 CDN endpoints with caching headers rather than serving through local FastAPI `/uploads/` static mount.

---

## 🖨️ 3. Print Engine Research & Upgrades (300 DPI Vector PDF)

Enhance commercial print quality, color management, and bleed accuracy:

- [ ] **CMYK Color Space Support & ICC Profiles:**
  - Add CMYK color conversion (`PDF/X-1a` or `PDF/X-4` standard) with embedded ICC color profiles (e.g. *FOGRA39* or *GRACoL*) for commercial offset and digital presses.
- [ ] **Face & Salience-Aware Smart Cropping in PDF:**
  - Integrate lightweight face bounding boxes into `pdf_exporter.py` so that photo slot cropping never clips heads or faces when aspect ratios don't match the slot.
- [ ] **Page-by-Page Streaming PDF Compilation:**
  - Modify `pdf_exporter.py` to stream pages onto disk incrementally rather than buffering all 100+ uncompressed bitmaps in server RAM simultaneously.
- [ ] **Print Lab Webhook Integration:**
  - Implement automated order submission to commercial print-on-demand APIs (e.g., Prodigi, Gelato, or local fulfillment labs).

---

## 🧠 4. AI & Layout Solver Optimizations

Enhance intelligence while keeping token costs low and latency under 2 seconds:

- [ ] **Activate 2-Tier Hierarchical AI Macro-Clustering:**
  - When re-enabling Gemini API, activate the structured cluster summary flow documented in `story_ai.py` (sending 10–15 chapter summaries instead of 1,000 photo prompts).
- [ ] **Dynamic Multi-Photo Spreads (4–6 Photos per Page):**
  - Expand `dsa_solver.py` page packing algorithm to support dynamic collage layouts with 4, 5, and 6 photos per page.
- [ ] **User Slot Drag-and-Drop Reordering:**
  - Implement interactive canvas photo swaps in `SpreadViewer.jsx` allowing users to swap photos between slots or spreads interactively.

---

## 🔒 5. Production Hardening & Security

- [ ] **Migrate Persistent Storage to PostgreSQL & Redis:**
  - Upgrade from SQLite WAL mode to managed PostgreSQL (Amazon RDS) and Redis for distributed horizontally-scaled worker clusters.
- [ ] **IP-Based Rate Limiting & Auth:**
  - Implement `slowapi` rate limiting on upload and layout generation routes to protect against Denial of Service (DoS).
- [ ] **Automated Upload Cleanup Job:**
  - Set a 24-hour retention TTL on temporary session files and exported PDFs to prevent storage bloat.
