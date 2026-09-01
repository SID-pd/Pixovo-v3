"""
FastAPI Main Application for Pixovo Template Engine (PTE)
Includes Loguru metrics, Async Job Queue (202 Accepted + Polling),
asyncio.Semaphore(4) concurrency control, 2MB file limit, Pillow Decompression Bomb protection,
and Aspect Ratio clamping (0.33 to 3.0).
"""

import os
import uuid
import time
import asyncio
import shutil
import secrets
from pathlib import Path
from typing import List, Dict, Any, Optional

from PIL import Image
# 1. Protection against decompression bombs (max 50M pixels)
Image.MAX_IMAGE_PIXELS = 50_000_000

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import (
    UPLOADS_DIR, UPLOADS_ORIGINALS_DIR, UPLOADS_PREVIEWS_DIR, UPLOADS_THUMBNAILS_DIR, EXPORTS_DIR, logger,
    MAX_PHOTOS_PER_SESSION, INGEST_CHUNK_SIZE,
    STORAGE, storage_key, MAX_BYTES_PER_SESSION, GLOBAL_DISK_WATERMARK,
    FILTER_WORKERS, JOB_CONCURRENCY, POOL_KIND,
    PHOTO_CACHE_SIZE, JOB_CACHE_SIZE, CACHE_TTL_SECONDS
)
from app.schemas.photobook import (
    PhotoMeta, GenerateVariationsRequest, GenerateVariationsResponse, JobStatusResponse, SpreadPair, PhotobookVariation
)
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from cachetools import TTLCache
from app.db.session_store import SessionStore
from app.engine.color_extractor import extract_dominant_colors
from app.engine.story_ai import generate_story_theme_batch
from app.engine.solver import generate_photobook_variations_engine
from app.engine.pdf_exporter import generate_print_pdf_engine
from app.engine.filter.filter_engine import (
    Phase1FilterEngine, scan_photo, finalise_scanned_batch
)

filter_engine = Phase1FilterEngine()

# Stage 1.4: one pool, sized from the host, replacing a hardcoded 4 that also
# had filter_engine constructing its own inner pool of 4 per call.
# POOL_KIND is settled by measurement in Stage 1.7 — OpenCV releases the GIL,
# PIL and imagehash largely do not.
CPU_WORKER_POOL = (
    ProcessPoolExecutor(max_workers=FILTER_WORKERS)
    if POOL_KIND == "process"
    else ThreadPoolExecutor(max_workers=FILTER_WORKERS, thread_name_prefix="pixovo-cpu")
)

app = FastAPI(
    title="Pixovo Template Engine (PTE)",
    description="High-Scale Fail-Safe Story Mode Photobook Engine",
    version="2.1.0"
)

ALLOWED_ORIGINS = [
    "https://doorpost-smashing-regime.ngrok-free.dev",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.ngrok-free\.dev|https://.*\.ngrok\.io",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/exports", StaticFiles(directory=str(EXPORTS_DIR)), name="exports")

# Bounded in-memory working caches over the SQLite source of truth.
#
# Stage 1.4: these were plain dicts with no eviction, so they grew for the life
# of the process — at the load target that is 20,000 PhotoMeta objects held
# forever. TTLCache bounds both size and age; every read path already falls back
# to SessionStore on a miss, so eviction is correct and merely slower.
#
# TTLCache is NOT thread-safe and these are touched from pool threads, so all
# access goes through _CACHE_LOCK via the helpers below.
_CACHE_LOCK = threading.Lock()
PHOTO_STORE: TTLCache = TTLCache(maxsize=PHOTO_CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
JOBS_STORE: TTLCache = TTLCache(maxsize=JOB_CACHE_SIZE, ttl=CACHE_TTL_SECONDS)

CONCURRENCY_SEMAPHORE = asyncio.Semaphore(JOB_CONCURRENCY)
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB limit per file for High-Res original photos


def cache_photo(photo: PhotoMeta) -> None:
    with _CACHE_LOCK:
        PHOTO_STORE[photo.id] = photo


def cache_photos(photos: List[PhotoMeta]) -> None:
    with _CACHE_LOCK:
        for photo in photos:
            PHOTO_STORE[photo.id] = photo


def get_cached_photo(photo_id: str) -> Optional[PhotoMeta]:
    with _CACHE_LOCK:
        return PHOTO_STORE.get(photo_id)


def cache_job(job: JobStatusResponse) -> None:
    with _CACHE_LOCK:
        JOBS_STORE[job.job_id] = job


def get_cached_job(job_id: str) -> Optional[JobStatusResponse]:
    with _CACHE_LOCK:
        return JOBS_STORE.get(job_id)

@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("[PTE Backend] Service Started Successfully with SQLite Persistence")
    logger.info(f"[PTE Backend] Max Upload Size: {MAX_FILE_SIZE / (1024*1024)} MB (High-Res Print Support)")
    logger.info(f"[PTE Backend] Concurrency Limit: {CONCURRENCY_SEMAPHORE._value} Workers")
    logger.info(f"[PTE Backend] Persisted Photos: {SessionStore.count_photos()} | Persisted Jobs: {SessionStore.count_jobs()}")
    logger.info("=" * 60)

@app.get("/health")
async def health():
    """
    Liveness probe. Deliberately does no I/O — this is the endpoint the load
    test measures p99 against, so it must reflect event-loop responsiveness and
    nothing else.
    """
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness probe: is the database actually reachable?"""
    try:
        SessionStore.ping()
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"[Ready] Database unavailable: {e}")
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")


@app.get("/")
def read_root():
    """
    Service info. Note this runs two COUNT(*) queries, so it is NOT a liveness
    probe — use /health for that.
    """
    return {
        "status": "online",
        "service": "Pixovo Template Engine (PTE) High-Scale Backend",
        "concurrency_limit": JOB_CONCURRENCY,
        "filter_workers": FILTER_WORKERS,
        "max_upload_size_mb": MAX_FILE_SIZE // (1024 * 1024),
        "active_jobs": SessionStore.count_jobs(),
        "cached_photos": SessionStore.count_photos()
    }

class ClientMetricPayload(BaseModel):
    step_name: str
    elapsed_ms: float
    details: Optional[Dict[str, Any]] = None

@app.post("/api/client-metrics")
def record_client_metric(payload: ClientMetricPayload):
    """Records client-side benchmark metrics (e.g. Browser Canvas Downsampling)."""
    from app.metrics import MetricsCollector
    MetricsCollector.record(payload.step_name, payload.elapsed_ms, payload.details)
    return {"status": "recorded"}

@app.get("/api/stats")
def get_system_stats():
    """Returns real-time server performance statistics, stage latencies, and storage metrics."""
    from app.metrics import MetricsCollector
    summary = MetricsCollector.get_summary()
    return {
        "status": "healthy",
        "system": {
            "concurrency_limit": 4,
            "max_file_size_mb": 20,
            "persisted_photos": SessionStore.count_photos(),
            "persisted_jobs": SessionStore.count_jobs(),
            "in_memory_cached_photos": len(PHOTO_STORE),
            "in_memory_active_jobs": len(JOBS_STORE),
            "photo_cache_max": PHOTO_CACHE_SIZE,
            "filter_workers": FILTER_WORKERS,
            "job_concurrency": JOB_CONCURRENCY,
            "pool_kind": POOL_KIND
        },
        "performance": summary
    }

class CreateSessionRequest(BaseModel):
    expected_photo_count: int = 0


@app.post("/api/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(req: CreateSessionRequest):
    """
    Opens an upload session BEFORE any photo bytes are sent.

    Stage 1.1: previously `session_id` was minted inside the ingest handler, so
    every chunk of a chunked upload produced its own orphaned session. The client
    now calls this once and carries the returned token through every chunk.
    """
    if req.expected_photo_count > MAX_PHOTOS_PER_SESSION:
        raise HTTPException(
            status_code=413,
            detail=f"Maximum {MAX_PHOTOS_PER_SESSION} photos per session (requested {req.expected_photo_count})."
        )

    # Stage 1.3: refuse new sessions once the machine is near its disk ceiling.
    # Telling a user "at capacity" up front is far better than accepting their
    # upload and dying halfway through it with a full disk.
    used = SessionStore.total_bytes_all_sessions()
    if used >= GLOBAL_DISK_WATERMARK:
        logger.error(
            f"[Session] Refusing new session — storage at {used / 1024**3:.1f} GB "
            f"of {GLOBAL_DISK_WATERMARK / 1024**3:.0f} GB watermark"
        )
        raise HTTPException(
            status_code=503,
            detail="Server is at storage capacity. Please try again later."
        )

    session_id = f"sess_{secrets.token_urlsafe(24)}"
    SessionStore.create_session(session_id, req.expected_photo_count)
    logger.info(
        f"[Session] Created {session_id} expecting {req.expected_photo_count} photos "
        f"(chunk size {INGEST_CHUNK_SIZE})"
    )
    return {
        "session_id": session_id,
        "max_photos": MAX_PHOTOS_PER_SESSION,
        "chunk_size": INGEST_CHUNK_SIZE,
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """
    Rehydrates a session after a page refresh. Returns session counters and the
    photos persisted so far so the client can resume without re-uploading.
    """
    session = SessionStore.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session.")
    if session.get("status") == "expired":
        raise HTTPException(status_code=410, detail="Session has expired.")

    SessionStore.touch_session(session_id)
    photos = SessionStore.get_session_photos(session_id)
    return {
        "session_id": session_id,
        "status": session.get("status"),
        "expected_photo_count": session.get("expected_photo_count"),
        "received_photo_count": session.get("received_photo_count"),
        "survived_photo_count": session.get("survived_photo_count"),
        "photos": [p.model_dump() for p in photos],
    }


@app.post("/api/photobook/ingest")
async def ingest_photobook_dual_payload(
    session_id: str = Form(...),
    chunk_index: int = Form(default=0),
    chunk_count: int = Form(default=1),
    thumbnails: Optional[List[UploadFile]] = File(default=[]),
    metadata_json: Optional[str] = Form(default="[]")
):
    """
    Phase 1 Ingestion Gateway — one chunk of a chunked upload.

    Receives 512px thumbnails + structured metadata JSON only. Full-resolution
    originals are NOT accepted here; they stream separately via
    /api/upload-originals so a 1,000-photo session is never one 8 GB request.
    """
    import json
    start_time = time.perf_counter()

    session = SessionStore.get_session(session_id)
    if not session:
        logger.warning(f"[Ingest Error] Unknown session_id: {session_id}")
        raise HTTPException(status_code=404, detail="Unknown or expired session.")
    if session.get("status") == "expired":
        raise HTTPException(status_code=410, detail="Session has expired.")
    SessionStore.touch_session(session_id)

    thumbnails = thumbnails or []

    if not thumbnails and (not metadata_json or metadata_json == "[]"):
        logger.warning(f"[Ingest Error] Empty payload for session {session_id} chunk {chunk_index}.")
        raise HTTPException(status_code=400, detail="No photos or metadata received in ingestion payload.")

    try:
        # 1. Validate & parse metadata JSON
        try:
            metadata_list = json.loads(metadata_json) if metadata_json else []
        except Exception as e:
            logger.error(f"[Ingest Error] Malformed metadata JSON: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {str(e)}")

        metadata_map = {m["photo_id"]: m for m in metadata_list if isinstance(m, dict) and "photo_id" in m}

        # 2. Save 512px thumbnails through the storage backend.
        #
        #    Stage 1.3: writes go through STORAGE rather than touching
        #    UPLOADS_THUMBNAILS_DIR, and stream in 1 MB chunks instead of
        #    `await thumb_file.read()` loading each file whole.
        #
        #    Stage 1.2: each thumbnail's mtime is stamped with the client-reported
        #    capture time of the ORIGINAL file. Canvas downsampling strips EXIF,
        #    so filter_engine.extract_5_signal_metadata() falls back to the file's
        #    mtime — which for a temp file written moments ago is "now" for every
        #    photo. That made every capture time identical and silently disabled
        #    both the filter engine's internal event clustering and the 45-minute
        #    chapter rule.
        saved_thumb_paths = []
        for thumb_file in thumbnails:
            thumb_filename = Path(thumb_file.filename or "").name
            if not thumb_filename:
                logger.warning(f"[Ingest] Skipping thumbnail with no filename in session {session_id}")
                continue

            key = storage_key("thumbnails", session_id, thumb_filename)
            STORAGE.put_stream(key, thumb_file.file)
            target_path = STORAGE.get_path(key)
            if not target_path:
                logger.error(f"[Ingest] Thumbnail vanished after write: {key}")
                continue

            stem = Path(thumb_filename).stem
            client_pid = stem[: -len("_thumb")] if stem.endswith("_thumb") else stem
            client_ts = (metadata_map.get(client_pid) or {}).get("timestamp_epoch")
            if client_ts:
                try:
                    os.utime(target_path, (float(client_ts), float(client_ts)))
                except (OSError, ValueError, TypeError) as e:
                    logger.debug(f"[Ingest] Could not set mtime for {thumb_filename}: {e}")

            saved_thumb_paths.append(target_path)

        # 3. Phase 1 filtering.
        #
        #    Stage 1.4: fanned out PER PHOTO onto the single shared pool, rather
        #    than handing the whole batch to run_phase1_filtering — which used to
        #    construct its own inner ThreadPoolExecutor(4) on top of this one.
        #    Nested pools meant FILTER_WORKERS x 4 threads (plus OpenCV's own
        #    per-op fan-out) contending for far fewer cores. Fanning out here
        #    parallelises across concurrent sessions instead of within one.
        #
        #    Stage 1.1 note: the previous "step 5b" re-ran cv2.imread() over every
        #    full-resolution original here to re-check for QR codes — synchronously,
        #    on the event loop. It is gone along with the originals payload.
        loop = asyncio.get_running_loop()
        # Module-level `scan_photo` rather than the bound method: a
        # ProcessPoolExecutor pickles the callable, and a bound method drags the
        # engine's mediapipe/ONNX handles along with it (unpicklable ctypes
        # pointers). See filter_engine's worker entry points.
        scan_futures = [
            loop.run_in_executor(
                CPU_WORKER_POOL, scan_photo, path, f"p_{idx + 1}"
            )
            for idx, path in enumerate(saved_thumb_paths)
        ]
        scanned_photos = await asyncio.gather(*scan_futures)

        # The cross-photo half (solo-anchor safeguard, burst dedupe, DBSCAN event
        # clustering, hero ranking) is serial and cheap — no decoding, no I/O.
        filter_result = await loop.run_in_executor(
            CPU_WORKER_POOL,
            finalise_scanned_batch,
            list(scanned_photos),
            len(saved_thumb_paths),
        )

        # Segregate Survived vs Rejected Photos
        survived_photos_list = [p for p in filter_result.get("all_scanned_photos", []) if p.get("status") == "PASSED"]
        rejected_photos_list = [p for p in filter_result.get("all_scanned_photos", []) if p.get("status") != "PASSED"]

        # 4. Populate PhotoMeta and PHOTO_STORE for survived photos only.
        #
        #    Stage 1.1: the client-minted photo_id is now the single join key.
        #    Thumbnails arrive named "{photo_id}_thumb.jpg", so the ID is parsed
        #    straight back out of the filename and looked up in metadata_map —
        #    replacing a linear scan of metadata_list per photo plus a chain of
        #    filename-suffix stripping fallbacks.
        survived_photos_meta: List[PhotoMeta] = []
        unmatched: List[str] = []
        exif_time_hits = 0

        for item in survived_photos_list:
            thumb_name = item.get("filename", "")
            p_id = Path(thumb_name).stem
            if p_id.endswith("_thumb"):
                p_id = p_id[: -len("_thumb")]

            meta = metadata_map.get(p_id)
            if not meta:
                unmatched.append(thumb_name)
                continue

            orig_name = meta.get("filename", f"{p_id}.jpg")
            web_thumb_url = STORAGE.url_for(
                storage_key("thumbnails", session_id, f"{p_id}_thumb.jpg")
            )

            # Capture-time precedence (Stage 1.2).
            #
            # `taken_at` is only trustworthy when the filter engine derived it
            # from the image itself. Its `mtime` / `current_time` fallbacks read
            # the thumbnail's filesystem timestamp, which we control and which
            # would otherwise be meaningless. So:
            #   1. real EXIF (or a date parsed from the filename)
            #   2. client-reported lastModified of the original file
            #   3. taken_at from mtime — correct now, because step 3 of this
            #      handler stamped it with the client timestamp
            #   4. unknown (0) -> chaptering degrades to upload order
            TRUSTED_DATE_SOURCES = ("exif_datetime", "filename_regex")
            if item.get("date_source") in TRUSTED_DATE_SOURCES and item.get("taken_at"):
                timestamp_epoch = float(item["taken_at"])
                exif_time_hits += 1
            else:
                timestamp_epoch = (
                    float(meta.get("timestamp_epoch") or 0)
                    or float(item.get("taken_at") or 0)
                    or 0.0
                )

            photo_meta = PhotoMeta(
                id=str(p_id),
                filename=str(orig_name),
                url=web_thumb_url,
                preview_url=web_thumb_url,
                # The original has NOT arrived yet — it streams separately via
                # /api/upload-originals, which sets original_url and flips the
                # synced flag. Claiming original_synced=True here is what caused
                # mark_original_synced() to silently overwrite a URL that had
                # already been reported as synced.
                original_url=None,
                thumbnail_url=web_thumb_url,
                original_synced=False,
                width=int(meta.get("original_width") or item.get("width") or 1200),
                height=int(meta.get("original_height") or item.get("height") or 900),
                aspect_ratio=float(item.get("aspect_ratio") or meta.get("aspect_ratio") or 1.33),

                # ----- Stage 1.2: carry the filter engine's output forward -----
                # Without these, cluster_photos_2tier_engine, partition_macro_chapters
                # and cover selection all run on null inputs.
                timestamp_epoch=timestamp_epoch,
                latitude=item.get("latitude"),
                longitude=item.get("longitude"),
                hero_score=float(item.get("hero_score") or 0.0),
                layout_role=item.get("layout_role") or "STANDARD_FRAME",
                is_event_cover_hero=bool(item.get("is_event_cover_hero", False)),
                is_hero_candidate=bool(item.get("is_event_cover_hero", False)),
                # `score` is kept as a normalised 0-1 mirror of hero_score for any
                # legacy consumer; hero_score is what the solver should read.
                score=round(float(item.get("hero_score") or 0.0) / 100.0, 4),
                blur_score=item.get("blur_score"),
                tenengrad_score=item.get("tenengrad_score"),
                contrast_score=item.get("contrast_score"),
                face_count=int(item.get("face_count") or 0),
                shell_phash=item.get("shell_phash") or "",
                core_phash=item.get("core_phash") or "",
                dominant_colors=item.get("dominant_colors") or ["#2C3E50", "#ECF0F1", "#7F8C8D"],
            )
            cache_photo(photo_meta)
            survived_photos_meta.append(photo_meta)

        if unmatched:
            logger.warning(
                f"[Ingest] Session {session_id} chunk {chunk_index}: "
                f"{len(unmatched)} thumbnails had no matching metadata entry "
                f"(first few: {unmatched[:3]})"
            )

        # Stage 1.2 data-contract diagnostics. These assert the boundary is
        # actually carrying signal rather than defaults — if pHash coverage or
        # hero_score max reads zero, clustering and cover selection are silently
        # back to running on nulls.
        if survived_photos_meta:
            phash_ok = sum(1 for p in survived_photos_meta if p.shell_phash and p.core_phash)
            max_hero = max(p.hero_score or 0.0 for p in survived_photos_meta)
            distinct_palettes = len({tuple(p.dominant_colors) for p in survived_photos_meta})
            logger.info(
                f"[Ingest] Data contract | pHash {phash_ok}/{len(survived_photos_meta)} "
                f"| hero_score max {max_hero:.1f} "
                f"| capture-time EXIF {exif_time_hits}/{len(survived_photos_meta)} "
                f"| distinct palettes {distinct_palettes}"
            )
            if phash_ok == 0:
                logger.error("[Ingest] pHash coverage is ZERO — spread clustering will fall back to array order.")
            if max_hero == 0.0:
                logger.error("[Ingest] All hero_scores are ZERO — cover selection cannot rank photos.")

        # Atomic Batch Persistence in SQLite. INSERT OR REPLACE keyed on the
        # client-minted photo_id makes a replayed chunk converge rather than
        # duplicate, which is what allows the client to retry safely.
        SessionStore.save_photos_batch(survived_photos_meta, session_id=session_id)

        # Session counters are recomputed from the photos table rather than
        # incremented, so a retried chunk does not inflate them.
        session_totals = SessionStore.recount_session(
            session_id, received_delta=len(metadata_list)
        )

        # Attach web preview URLs to filter results
        for p in filter_result.get("all_scanned_photos", []):
            p_filename = p.get("filename", "")
            p["web_url"] = f"/uploads/thumbnails/{session_id}/{p_filename}"

        # Explicit GC for large photo sets (avoids memory accumulation during peak 1000-photo uploads)
        if len(metadata_list) > 100:
            import gc
            gc.collect()

        is_final_chunk = (chunk_index + 1) >= chunk_count
        if is_final_chunk:
            SessionStore.set_session_status(session_id, "ready")

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            f"[Phase 1 Gateway] Session {session_id} chunk {chunk_index + 1}/{chunk_count}: "
            f"received {len(metadata_list)}, survived {len(survived_photos_meta)}, "
            f"rejected {len(rejected_photos_list)} in {elapsed_ms:.2f}ms "
            f"| session totals: {session_totals['survived_photo_count']}/"
            f"{session_totals['received_photo_count']}"
        )
        from app.metrics import MetricsCollector
        MetricsCollector.record("Phase 1 Ingestion & Filtering", elapsed_ms, {
            "photos_count": len(metadata_list),
            "survived": len(survived_photos_meta),
            "rejected": len(rejected_photos_list),
            "chunk_index": chunk_index,
            "chunk_count": chunk_count
        })

        # Update summary counts
        summary = filter_result.get("summary", {})
        summary["total_uploaded"] = len(metadata_list)
        summary["total_survived"] = len(survived_photos_meta)
        summary["total_rejected"] = len(rejected_photos_list)

        return {
            "status": "success",
            "session_id": session_id,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "is_final_chunk": is_final_chunk,
            "summary": summary,
            # Per-chunk counts
            "total_ingested": len(metadata_list),
            "total_survived": len(survived_photos_meta),
            "total_rejected": len(rejected_photos_list),
            # Cumulative session counts, so the client can show overall progress
            # without summing chunk responses itself.
            "session_received": session_totals["received_photo_count"],
            "session_survived": session_totals["survived_photo_count"],
            "photos": [p.model_dump() for p in survived_photos_meta],
            "survived_photos": survived_photos_list,
            "rejected_photos": rejected_photos_list,
            "events": filter_result.get("events", []),
            "layout_groups": filter_result.get("layout_groups", []),
            "metadata": metadata_list
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Ingest Error] Unhandled exception during ingestion: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ingestion server error: {str(exc)}")

@app.get("/api/templates")
def get_all_templates():
    """Returns all 16+ Double-Page Spread boilerplate layout definitions."""
    from app.engine.registry import SPREAD_TEMPLATES_REGISTRY
    return {
        "count": len(SPREAD_TEMPLATES_REGISTRY),
        "templates": SPREAD_TEMPLATES_REGISTRY
    }

@app.get("/api/palettes")
def get_all_palettes():
    """Returns all 20 Canonical Themes with 5 Semantic Color Roles."""
    from app.engine.color_extractor import THEME_PALETTES
    return {
        "count": len(THEME_PALETTES),
        "palettes": THEME_PALETTES
    }

@app.get("/api/categories")
def get_all_categories():
    """Returns Top 10 Combined Categories and Category-to-Theme AI mappings."""
    from app.engine.color_extractor import CATEGORY_THEMES_MAP
    return {
        "count": len(CATEGORY_THEMES_MAP),
        "categories": CATEGORY_THEMES_MAP
    }

class SingleSpreadReshuffleRequest(BaseModel):
    spread: SpreadPair
    theme_name: str
    seed: int = 1

class VariationsReshuffleRequest(BaseModel):
    job_id: str
    seed_offset: int = 1
    # Stage 1.6: required so reshuffle reloads THIS session's photos. It used to
    # read the whole process-wide photo cache, which at 20 concurrent users
    # meant reshuffling could pull another user's photos into your book.
    session_id: Optional[str] = None

@app.post("/api/spreads/reshuffle", response_model=SpreadPair)
def reshuffle_single_spread(req: SingleSpreadReshuffleRequest):
    """Reshuffles a single spread's layout on click using DSA solver engine."""
    t0 = time.perf_counter()
    from app.engine.dsa_solver import reshuffle_single_spread_engine
    result = reshuffle_single_spread_engine(req.spread, req.theme_name, req.seed)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    from app.metrics import MetricsCollector
    MetricsCollector.record("Spread Reshuffle", elapsed_ms, {
        "spread_index": req.spread.spread_index,
        "theme": req.theme_name
    })
    return result

@app.post("/api/variations/reshuffle")
def reshuffle_job_variations(req: VariationsReshuffleRequest):
    """Reshuffles themes & palettes across the 3 saved persistent variations."""
    t0 = time.perf_counter()
    job = get_cached_job(req.job_id) or SessionStore.get_job(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job ID not found")
    if not job.result:
        raise HTTPException(status_code=400, detail="Job result not ready")

    from app.engine.solver import generate_photobook_variations_engine

    # Stage 1.6: load THIS session's photos.
    #
    # This used to be `list(PHOTO_STORE.values())` — the entire process-wide
    # cache — falling back to a single `sample_placeholder.jpg`. Two bugs in one
    # line: a cross-session data leak (another user's photos could land in your
    # reshuffled book) and a placeholder book when the cache was cold.
    if not req.session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id is required to reshuffle variations."
        )

    photos = SessionStore.get_session_photos(req.session_id)
    if not photos:
        raise HTTPException(
            status_code=409,
            detail="No photos found for this session. Cannot reshuffle."
        )

    ai_batch_result = {
        "variations": [
            {"variation_id": f"var_{i+1}", "variation_title": v.variation_title, "theme_name": v.theme_name, "cover_title": v.cover_title, "cover_subtitle": v.cover_subtitle}
            for i, v in enumerate(job.result.variations)
        ]
    }

    new_variations = generate_photobook_variations_engine(photos, ai_batch_result, variant_seed_offset=req.seed_offset)
    job.result.variations = new_variations
    # Persist, so a reshuffle survives a cache eviction or restart.
    cache_job(job)
    SessionStore.save_job(job, session_id=req.session_id)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    from app.metrics import MetricsCollector
    MetricsCollector.record("Variations Reshuffle", elapsed_ms, {
        "job_id": req.job_id,
        "variations_count": len(new_variations)
    })
    return job.result

@app.post("/api/curate-photos")
async def curate_photos_pipeline(
    thumbnails: List[UploadFile] = File(...),
    metadata_json: str = Form(...)
):
    """
    Selection Engine Endpoint:
    Receives client-downsampled 512px thumbnails + EXIF JSON metadata.
    Runs Phase 2 Quality Gate & Phase 3 Face/Aesthetic Engine to curate top candidates,
    populates PHOTO_STORE with PhotoMeta objects, and returns curated photos for DSA solver.
    """
    import json
    import cv2
    import numpy as np
    from app.engine.selection.curation_pipeline import MasterCurationPipeline

    if not thumbnails:
        raise HTTPException(status_code=400, detail="No thumbnails uploaded.")

    try:
        metadata_list = json.loads(metadata_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid metadata_json: {str(e)}")

    metadata_map = {m["photo_id"]: m for m in metadata_list}
    thumbnails_dict = {}

    # Read and decode thumbnails in RAM
    for thumb_file in thumbnails:
        photo_id = thumb_file.filename.replace("_thumb.jpg", "").replace("_thumb.png", "").replace("_thumb.webp", "")
        content = await thumb_file.read()
        nparr = np.frombuffer(content, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img_bgr is not None:
            thumbnails_dict[photo_id] = img_bgr
            # Save preview asset to disk
            preview_filename = f"{photo_id}_thumb.jpg"
            preview_path = UPLOADS_PREVIEWS_DIR / preview_filename
            with open(preview_path, "wb") as f:
                f.write(content)

    # Run Master Curation Pipeline
    pipeline = MasterCurationPipeline()
    curation_result = pipeline.run_curation_pipeline(
        thumbnails_dict=thumbnails_dict,
        metadata_dict=metadata_map,
        target_quota=20
    )

    # Populate PHOTO_STORE with curated PhotoMeta objects
    curated_photos_meta = []
    for item in curation_result["curated_photo_meta"]:
        p_id = item["photo_id"]
        web_url = f"/uploads/previews/{p_id}_thumb.jpg"
        meta_obj = PhotoMeta(
            id=p_id,
            filename=item.get("filename", f"{p_id}.jpg"),
            url=web_url,
            aspect_ratio=item["aspect_ratio"],
            dominant_colors=["#2C3E50", "#ECF0F1"],
            score=item["score"],
            blur_score=item.get("blur_score"),
            face_count=item.get("face_count", 0),
            shell_phash=item.get("shell_phash", ""),
            core_phash=item.get("core_phash", ""),
            is_hero_candidate=item.get("is_hero_candidate", False)
        )
        PHOTO_STORE[p_id] = meta_obj
        curated_photos_meta.append(meta_obj)

    return {
        "status": "success",
        "summary": curation_result["summary"],
        "curated_photo_meta": curated_photos_meta,
        "chapters": curation_result.get("chapters", []),
        "rejected_photos": curation_result["rejected_photos"]
    }

@app.post("/api/curate-and-generate")
async def curate_and_generate_variations(
    thumbnails: List[UploadFile] = File(...),
    metadata_json: str = Form(...),
    user_prompt: str = Form("Family Memories"),
    target_quota: int = Form(20)
):
    """
    End-to-End Handshake Endpoint:
    1. Runs Selection Engine (Client 512px Thumbnails -> Quality Gate -> Face/Aesthetic -> Story Arc Quota).
    2. Populates PHOTO_STORE with curated PhotoMeta objects.
    3. Calls Gemini Story AI (Theme & Caption generation).
    4. Runs DSA Layout Solver (dsa_solver.py) to calculate exact (x, y, w, h) bounds.
    5. Returns 3 complete Photobook Variations ready for instant UI rendering & print export!
    """
    import json
    import cv2
    import numpy as np
    from app.engine.selection.curation_pipeline import MasterCurationPipeline
    from app.engine.story_ai import generate_story_theme_batch
    from app.engine.solver import generate_photobook_variations_engine

    if not thumbnails:
        raise HTTPException(status_code=400, detail="No thumbnails uploaded.")

    try:
        metadata_list = json.loads(metadata_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid metadata_json: {str(e)}")

    metadata_map = {m["photo_id"]: m for m in metadata_list}
    thumbnails_dict = {}

    for thumb_file in thumbnails:
        photo_id = thumb_file.filename.replace("_thumb.jpg", "").replace("_thumb.png", "").replace("_thumb.webp", "")
        content = await thumb_file.read()
        nparr = np.frombuffer(content, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img_bgr is not None:
            thumbnails_dict[photo_id] = img_bgr
            preview_filename = f"{photo_id}_thumb.jpg"
            preview_path = UPLOADS_PREVIEWS_DIR / preview_filename
            with open(preview_path, "wb") as f:
                f.write(content)

    # 1. Selection Engine Curation Pipeline
    pipeline = MasterCurationPipeline()
    curation_result = pipeline.run_curation_pipeline(
        thumbnails_dict=thumbnails_dict,
        metadata_dict=metadata_map,
        target_quota=target_quota
    )

    # 2. Populate PHOTO_STORE with curated PhotoMeta objects
    curated_photos_meta = []
    for item in curation_result["curated_photo_meta"]:
        p_id = item["photo_id"]
        web_url = f"/uploads/previews/{p_id}_thumb.jpg"
        meta_obj = PhotoMeta(
            id=p_id,
            filename=item.get("filename", f"{p_id}.jpg"),
            url=web_url,
            aspect_ratio=item["aspect_ratio"],
            dominant_colors=["#2C3E50", "#ECF0F1"],
            score=item["score"],
            blur_score=item.get("blur_score"),
            face_count=item.get("face_count", 0),
            shell_phash=item.get("shell_phash", ""),
            core_phash=item.get("core_phash", ""),
            is_hero_candidate=item.get("is_hero_candidate", False)
        )
        PHOTO_STORE[p_id] = meta_obj
        curated_photos_meta.append(meta_obj)

    # 3. Gemini Story & Theme AI
    ai_batch_result = generate_story_theme_batch(user_prompt=user_prompt, total_photos=len(curated_photos_meta))

    # 4. DSA Layout Solver Engine
    variations = generate_photobook_variations_engine(
        photos=curated_photos_meta,
        ai_batch_result=ai_batch_result
    )

    # 5. Create Job Record
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    job_response = JobStatusResponse(
        job_id=job_id,
        status="COMPLETED",
        progress=100.0,
        result=GenerateVariationsResponse(
            job_id=job_id,
            status="COMPLETED",
            user_prompt=user_prompt,
            primary_category=ai_batch_result.get("primary_category", "Family"),
            primary_theme=ai_batch_result.get("primary_theme", "Warm"),
            variations=variations
        )
    )
    JOBS_STORE[job_id] = job_response

    return {
        "status": "success",
        "job_id": job_id,
        "summary": curation_result["summary"],
        "curated_photo_meta": curated_photos_meta,
        "chapters": curation_result.get("chapters", []),
        "photobook_variations": variations
    }

@app.post("/api/upload-photos", response_model=List[PhotoMeta])
async def upload_photos(files: List[UploadFile] = File(...)):
    """
    Phase 1 (Fast Preview Upload):
    Receives lightweight 1200px WebP previews (~100KB each).
    Saves previews & thumbnails, extracts colors, and returns PhotoMeta in <100ms!
    UI opens instantly, AI & DSA Solver start immediately.
    """
    start_time = time.perf_counter()
    if not files:
        logger.warning("[Upload] No files provided in upload request.")
        raise HTTPException(status_code=400, detail="No files uploaded.")

    uploaded_photos: List[PhotoMeta] = []

    for file in files:
        if file.content_type and not file.content_type.startswith("image/"):
            logger.warning(f"[Upload Security] Rejected non-image MIME type: {file.content_type} for file: {file.filename}")
            raise HTTPException(status_code=400, detail=f"File {file.filename} is not a valid image.")

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            logger.warning(f"[Upload Security] File {file.filename} exceeded limit: {len(content)/(1024*1024):.2f}MB")
            raise HTTPException(
                status_code=413,
                detail=f"File {file.filename} exceeds hard limit of 20MB."
            )

        photo_id = f"img_{uuid.uuid4().hex[:8]}"
        file_ext = Path(file.filename).suffix or ".webp"
        
        preview_filename = f"{photo_id}_preview.webp"
        thumb_filename = f"{photo_id}_thumb.webp"

        preview_path = UPLOADS_PREVIEWS_DIR / preview_filename
        thumb_path = UPLOADS_THUMBNAILS_DIR / thumb_filename

        # Save preview asset
        with open(preview_path, "wb") as buffer:
            buffer.write(content)

        # Process with Pillow to extract dimensions & create thumbnail
        try:
            with Image.open(preview_path) as img:
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
                width, height = img.size

                thumb_img = img.copy()
                thumb_img.thumbnail((256, 256))
                thumb_img.save(thumb_path, format="WEBP", quality=80)
        except Exception as e:
            logger.error(f"[Upload Error] Failed to read preview image for {file.filename}: {e}")
            width, height = 1200, 900

        raw_ar = width / max(1, height)
        clamped_ar = round(max(0.33, min(3.0, raw_ar)), 2)

        dominant_colors = extract_dominant_colors(str(preview_path), num_colors=3)
        
        preview_url_path = f"/uploads/previews/{preview_filename}"
        thumb_url_path = f"/uploads/thumbnails/{thumb_filename}"

        photo_meta = PhotoMeta(
            id=photo_id,
            filename=file.filename,
            url=preview_url_path,          # Primary UI asset (lightweight WebP)
            preview_url=preview_url_path,  # 1200px preview for canvas
            original_url=None,             # Will be linked when background HD upload completes
            thumbnail_url=thumb_url_path,  # 256px thumbnail for tray
            original_synced=False,         # Pending background HD sync
            width=width,
            height=height,
            aspect_ratio=clamped_ar,
            dominant_colors=dominant_colors
        )

        PHOTO_STORE[photo_id] = photo_meta
        uploaded_photos.append(photo_meta)

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.info(f"[Metrics] Fast Preview Upload: Processed {len(uploaded_photos)} photos in {elapsed_ms:.2f}ms")
    return uploaded_photos

@app.post("/api/upload-originals")
async def upload_originals(
    session_id: str,
    photo_id: str,
    file: UploadFile = File(...)
):
    """
    Phase 2 (Background HD Sync):
    Receives one full-resolution original in the background stream and writes it
    to the session's originals directory.

    Stage 1.1 changes:
    - `session_id` is required and verified against the photo's owning session,
      so originals cannot be written into a session that does not own the photo.
    - Files are written to /uploads/originals/{session_id}/ so this path agrees
      with the one ingest used to build `original_url`. Previously this wrote to
      a flat directory while ingest wrote to a session subdirectory, and
      mark_original_synced() then overwrote the session-scoped URL with the flat
      one — leaving PDF export unable to locate the file.
    - The body is streamed to disk in chunks instead of `await file.read()`,
      which loaded a full 20 MB original into memory per concurrent request.

    Stage 1.3 changes:
    - Bytes go through STORAGE, so switching to S3 needs no change here.
    - A per-session byte cap is enforced mid-copy against the running total on
      sessions.total_bytes.
    """
    session_row = SessionStore.get_session(session_id)
    if not session_row:
        raise HTTPException(status_code=404, detail="Unknown or expired session.")

    owning_session = SessionStore.get_photo_session(photo_id)
    if owning_session is None:
        raise HTTPException(status_code=404, detail=f"Unknown photo_id: {photo_id}")
    if owning_session != session_id:
        logger.warning(
            f"[Background HD Sync] Rejected cross-session write: photo {photo_id} "
            f"belongs to {owning_session}, caller claimed {session_id}"
        )
        raise HTTPException(status_code=403, detail="Photo does not belong to this session.")

    # Per-session disk cap. Checked against the running total on
    # sessions.total_bytes rather than walking storage, which is far too slow to
    # do per request at the load target.
    used_bytes = int(session_row.get("total_bytes") or 0) if session_row else 0
    if used_bytes >= MAX_BYTES_PER_SESSION:
        raise HTTPException(
            status_code=413,
            detail=f"Session storage limit reached ({MAX_BYTES_PER_SESSION // 1024**3} GB)."
        )

    file_ext = Path(file.filename or "").suffix or ".jpg"
    orig_filename = f"{photo_id}_orig{file_ext}"
    key = storage_key("originals", session_id, orig_filename)

    # Enforce the size cap *during* the copy so an oversized upload never fully
    # lands on disk. put_stream_iter cleans up the partial file if this raises.
    bytes_written = 0

    def _capped_chunks():
        nonlocal bytes_written
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"Original HD file exceeds {MAX_FILE_SIZE // (1024 * 1024)}MB limit."
                )
            if used_bytes + bytes_written > MAX_BYTES_PER_SESSION:
                raise HTTPException(
                    status_code=413,
                    detail=f"Session storage limit reached ({MAX_BYTES_PER_SESSION // 1024**3} GB)."
                )
            yield chunk

    orig_url_path = STORAGE.put_stream_iter(key, _capped_chunks())
    SessionStore.add_session_bytes(session_id, bytes_written)

    cached = get_cached_photo(photo_id)
    if cached is not None:
        cached.original_url = orig_url_path
        cached.original_synced = True
        cache_photo(cached)
    SessionStore.mark_original_synced(photo_id, orig_url_path)

    logger.info(
        f"[Background HD Sync] Synced original for {photo_id} in session {session_id} "
        f"({bytes_written / (1024 * 1024):.2f}MB)"
    )
    return {
        "status": "success",
        "session_id": session_id,
        "photo_id": photo_id,
        "original_url": orig_url_path,
        "synced": True
    }

def _update_job(
    job_id: str,
    progress: int,
    message: str,
    status_value: str = "processing",
    result: Optional[GenerateVariationsResponse] = None,
    session_id: Optional[str] = None,
) -> None:
    """Mutates a cached job and persists it. Safe if the cache has evicted it."""
    job = get_cached_job(job_id) or SessionStore.get_job(job_id)
    if job is None:
        logger.warning(f"[JobWorker] Job {job_id} vanished; cannot record '{message}'")
        return
    job.progress = progress
    job.message = message
    job.status = status_value
    if result is not None:
        job.result = result
    cache_job(job)
    SessionStore.save_job(job, session_id=session_id)


def _load_job_photos(session_id: Optional[str], photo_ids: List[str]) -> List[PhotoMeta]:
    """
    Loads the job's photos. Runs on a pool thread — it does SQLite I/O.

    Stage 1.4 Task 6: when session_id is known this is ONE indexed query
    (idx_photos_session), instead of get_photos() chunking the id list into
    `IN (...)` clauses of 500. It also returns capture-time order, which is what
    chaptering needs.
    """
    if session_id:
        photos = SessionStore.get_session_photos(session_id)
        if photos:
            return photos
        logger.warning(f"[JobWorker] Session {session_id} has no persisted photos; falling back to id list.")
    return SessionStore.get_photos(photo_ids) if photo_ids else []


async def process_async_job(
    job_id: str,
    photo_ids: List[str],
    user_prompt: str,
    session_id: Optional[str] = None,
):
    """
    Background worker, bounded by CONCURRENCY_SEMAPHORE.

    Stage 1.4: the photo load, the theme engine and the layout solver all used
    to run synchronously in this coroutine — directly on the event loop. For a
    1,000-photo session the DSA solver blocks for seconds, during which the
    server answers nobody, so one user generating stalled all twenty. Each
    blocking stage is now handed to CPU_WORKER_POOL via run_in_executor.

    The `await asyncio.sleep(0.1)` calls are gone: they existed only to let the
    loop breathe between blocking sections, which run_in_executor makes moot.
    """
    job_start = time.perf_counter()
    logger.info(
        f"[JobWorker] Starting {job_id} | session={session_id} | "
        f"prompt='{user_prompt}' | {len(photo_ids)} photo ids"
    )

    async with CONCURRENCY_SEMAPHORE:
        loop = asyncio.get_running_loop()
        try:
            _update_job(job_id, 20, "Loading photos...", session_id=session_id)
            photos = await loop.run_in_executor(
                CPU_WORKER_POOL, _load_job_photos, session_id, photo_ids
            )
            cache_photos(photos)

            # Stage 1.6: fail honestly. This used to fabricate four `sample_N`
            # placeholder photos and produce a COMPLETE fake photobook — turning
            # a data-loss bug into a silently wrong product a user could pay to
            # print. It also masked exactly the failures the load test exists to
            # find.
            if not photos:
                logger.error(
                    f"[JobWorker] {job_id} has no valid photos "
                    f"(session={session_id}, {len(photo_ids)} ids requested)"
                )
                _update_job(
                    job_id, 100,
                    "No valid photos found for this session. Please upload photos and try again.",
                    status_value="failed", session_id=session_id,
                )
                return

            _update_job(job_id, 45, "Generating story themes...", session_id=session_id)
            ai_batch = await loop.run_in_executor(
                CPU_WORKER_POOL, generate_story_theme_batch, user_prompt, len(photos)
            )

            _update_job(job_id, 70, "Solving optimal layouts...", session_id=session_id)
            variations = await loop.run_in_executor(
                CPU_WORKER_POOL, generate_photobook_variations_engine, photos, ai_batch
            )

            if not variations:
                _update_job(
                    job_id, 100, "Layout solver produced no variations.",
                    status_value="failed", session_id=session_id,
                )
                return

            elapsed_ms = (time.perf_counter() - job_start) * 1000
            _update_job(
                job_id, 100, "Photobook variations generated successfully!",
                status_value="completed",
                result=GenerateVariationsResponse(
                    theme_name=ai_batch.get("primary_theme", "Devotional / Temple"),
                    variations=variations,
                ),
                session_id=session_id,
            )

            from app.metrics import MetricsCollector
            MetricsCollector.record("Full Variation Pipeline (AI + Solver)", elapsed_ms, {
                "job_id": job_id,
                "photos_count": len(photos),
                "variations_count": len(variations),
            })
            logger.info(
                f"[Metrics] Job {job_id} COMPLETED in {elapsed_ms:.2f}ms | "
                f"{len(variations)} variations from {len(photos)} photos."
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - job_start) * 1000
            logger.error(f"[JobWorker] Job {job_id} FAILED after {elapsed_ms:.2f}ms: {e}", exc_info=True)
            from app.metrics import MetricsCollector
            MetricsCollector.record("Failed Job Execution", elapsed_ms, {"job_id": job_id, "error": str(e)})
            _update_job(
                job_id, 100, f"Photobook generation encountered an error: {e}",
                status_value="failed", session_id=session_id,
            )

@app.post("/api/generate-async", status_code=status.HTTP_202_ACCEPTED, response_model=JobStatusResponse)
async def generate_async(payload: GenerateVariationsRequest, background_tasks: BackgroundTasks):
    """Async endpoint returning 202 Accepted and job_id for frontend polling."""
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    logger.info(f"[API] Queuing async job {job_id} for prompt: '{payload.user_prompt}'")

    initial_job = JobStatusResponse(
        job_id=job_id,
        status="processing",
        progress=10,
        message="Job queued for processing...",
        result=None
    )
    cache_job(initial_job)
    SessionStore.save_job(initial_job, session_id=payload.session_id)

    background_tasks.add_task(
        process_async_job, job_id, payload.photo_ids, payload.user_prompt, payload.session_id
    )

    return initial_job

@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Polling route returning job processing status & result with DB fallback."""
    cached = get_cached_job(job_id)
    if cached is not None:
        return cached

    # Fallback to persistent SQLite DB. A cache miss must never become a 404 —
    # the bounded TTLCache evicts, so SQLite is the source of truth.
    db_job = SessionStore.get_job(job_id)
    if db_job:
        cache_job(db_job)
        return db_job

    logger.warning(f"[API] Job ID {job_id} not found in memory or database.")
    raise HTTPException(status_code=404, detail="Job ID not found.")

class ExportPDFRequest(BaseModel):
    variation: PhotobookVariation
    page_width_mm: float = 200.0
    page_height_mm: float = 200.0
    bleed_mm: float = 3.0
    dpi: int = 300
    # Stage 1.3: scopes original resolution to one session (O(1) lookup instead
    # of an rglob across every session's uploads).
    session_id: Optional[str] = None
    # Deliberately export a low-resolution proof before HD originals finish.
    force_preview: bool = False

def collect_placed_photo_ids(variation: PhotobookVariation) -> List[str]:
    """Every photo_id that actually occupies a slot in this variation."""
    placed: List[str] = []
    for spread in variation.spreads:
        for page in (spread.left_page, spread.right_page):
            for slot in page.slots:
                if slot.photo_id and slot.photo_id not in placed:
                    placed.append(slot.photo_id)
    return placed


@app.post("/api/export-pdf")
def export_print_pdf(req: ExportPDFRequest):
    """
    Compiles 300 DPI High-Res Print PDF/X file from PhotobookVariation layout metadata.

    Stage 1.3: originals now upload on demand rather than eagerly, so an export
    can legitimately be requested before every placed photo's HD file has
    arrived. This gate returns 409 with the outstanding count instead of quietly
    substituting 512px thumbnails into a print-resolution PDF — which is what
    the resolver's fallback chain would otherwise do.

    Pass `force_preview=true` to deliberately export a low-resolution proof.
    """
    placed_ids = collect_placed_photo_ids(req.variation)

    if not req.force_preview and placed_ids:
        photos = SessionStore.get_photos(placed_ids)
        found = {p.id: p for p in photos}
        pending = [pid for pid in placed_ids if not (found.get(pid) and found[pid].original_synced)]

        if pending:
            logger.warning(
                f"[Export PDF] Blocked: {len(pending)}/{len(placed_ids)} placed photos "
                f"have no HD original yet (session {req.session_id or 'unknown'})."
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "originals_pending",
                    "pending_count": len(pending),
                    "total_count": len(placed_ids),
                    "message": (
                        f"{len(pending)} of {len(placed_ids)} high-resolution photos are "
                        f"still uploading. Print export will be available shortly."
                    ),
                },
            )

    try:
        result = generate_print_pdf_engine(
            variation=req.variation,
            page_width_mm=req.page_width_mm,
            page_height_mm=req.page_height_mm,
            bleed_mm=req.bleed_mm,
            dpi=req.dpi,
            session_id=req.session_id or ""
        )
        return result
    except Exception as e:
        logger.error(f"[Export PDF Error] Failed to generate 300 DPI PDF: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF Generation Error: {str(e)}")
