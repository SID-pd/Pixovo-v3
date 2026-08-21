"""
FastAPI Main Application for Pixovo Template Engine (PTE)
Includes Loguru metrics, Async Job Queue (202 Accepted + Polling),
asyncio.Semaphore(4) concurrency control, 2MB file limit, Pillow Decompression Bomb protection,
and Aspect Ratio clamping (0.33 to 3.0).
"""

import uuid
import time
import asyncio
import shutil
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
    UPLOADS_DIR, UPLOADS_ORIGINALS_DIR, UPLOADS_PREVIEWS_DIR, UPLOADS_THUMBNAILS_DIR, EXPORTS_DIR, logger
)
from app.schemas.photobook import (
    PhotoMeta, GenerateVariationsRequest, GenerateVariationsResponse, JobStatusResponse, SpreadPair, PhotobookVariation
)
from concurrent.futures import ThreadPoolExecutor
from app.db.session_store import SessionStore
from app.engine.color_extractor import extract_dominant_colors
from app.engine.story_ai import generate_story_theme_batch
from app.engine.solver import generate_photobook_variations_engine
from app.engine.pdf_exporter import generate_print_pdf_engine
from app.engine.filter.filter_engine import Phase1FilterEngine

filter_engine = Phase1FilterEngine()
CPU_WORKER_POOL = ThreadPoolExecutor(max_workers=4)

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

# Thread-safe persistent SQLite storage + fast in-memory working cache
PHOTO_STORE: Dict[str, PhotoMeta] = {}
JOBS_STORE: Dict[str, JobStatusResponse] = {}
CONCURRENCY_SEMAPHORE = asyncio.Semaphore(4)
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB limit per file for High-Res original photos

@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("[PTE Backend] Service Started Successfully with SQLite Persistence")
    logger.info(f"[PTE Backend] Max Upload Size: {MAX_FILE_SIZE / (1024*1024)} MB (High-Res Print Support)")
    logger.info(f"[PTE Backend] Concurrency Limit: {CONCURRENCY_SEMAPHORE._value} Workers")
    logger.info(f"[PTE Backend] Persisted Photos: {SessionStore.count_photos()} | Persisted Jobs: {SessionStore.count_jobs()}")
    logger.info("=" * 60)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "Pixovo Template Engine (PTE) High-Scale Backend",
        "concurrency_limit": 4,
        "max_upload_size_mb": 20,
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
            "in_memory_active_jobs": len(JOBS_STORE)
        },
        "performance": summary
    }

@app.post("/api/photobook/ingest")
async def ingest_photobook_dual_payload(
    thumbnails: Optional[List[UploadFile]] = File(default=[]),
    originals: Optional[List[UploadFile]] = File(default=[]),
    metadata_json: Optional[str] = Form(default="[]")
):
    """
    Phase 1 Ingestion Gateway Endpoint:
    Receives Client Dual-Payload:
    - 512px WebP/JPEG thumbnails (for AI filtering & UI previews)
    - Full High-Res original files (for 300 DPI PDF print compilation)
    - Structured EXIF metadata JSON (photo_id, aspect_ratio, timestamp, GPS)
    """
    import json
    start_time = time.perf_counter()
    session_id = f"sess_{uuid.uuid4().hex[:8]}"

    thumbnails = thumbnails or []
    originals = originals or []

    if not thumbnails and not originals and (not metadata_json or metadata_json == "[]"):
        logger.warning("[Ingest Error] Ingestion called with empty payload.")
        raise HTTPException(status_code=400, detail="No photos or metadata received in ingestion payload.")

    try:
        # 1. Validate & parse metadata JSON
        try:
            metadata_list = json.loads(metadata_json) if metadata_json else []
        except Exception as e:
            logger.error(f"[Ingest Error] Malformed metadata JSON: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {str(e)}")

        # 2. Setup Session-Specific Storage Directories
        session_originals_dir = UPLOADS_ORIGINALS_DIR / session_id
        session_thumbnails_dir = UPLOADS_THUMBNAILS_DIR / session_id
        session_originals_dir.mkdir(parents=True, exist_ok=True)
        session_thumbnails_dir.mkdir(parents=True, exist_ok=True)

        metadata_map = {m["photo_id"]: m for m in metadata_list if isinstance(m, dict) and "photo_id" in m}

        # 3. Save 512px Thumbnails
        saved_thumb_paths = []
        for thumb_file in thumbnails:
            thumb_bytes = await thumb_file.read()
            thumb_filename = thumb_file.filename
            target_path = session_thumbnails_dir / thumb_filename
            with open(target_path, "wb") as f:
                f.write(thumb_bytes)
            saved_thumb_paths.append(str(target_path))

        # 4. Save High-Res Originals
        saved_orig_map = {}
        for orig_file in originals:
            orig_bytes = await orig_file.read()
            orig_filename = orig_file.filename
            target_orig_path = session_originals_dir / orig_filename
            with open(target_orig_path, "wb") as f:
                f.write(orig_bytes)
            saved_orig_map[orig_filename] = str(target_orig_path)

        # 5. Run Phase 1 Filtering Engine on Thumbnails (Offloaded to CPU pool to keep event loop 100% responsive)
        loop = asyncio.get_running_loop()
        filter_result = await loop.run_in_executor(
            CPU_WORKER_POOL,
            filter_engine.run_phase1_filtering,
            saved_thumb_paths
        )

        # Step 5b: Verify suspected QR on High-Res Original if available (handles client downsample compression loss)
        for item in filter_result.get("survived_photos", []):
            matching_orig_path = None
            for orig_name, orig_path in saved_orig_map.items():
                if any(k in orig_name for k in [item.get("photo_id", ""), item.get("filename", "")] if k):
                    matching_orig_path = orig_path
                    break
            if matching_orig_path:
                try:
                    import cv2
                    orig_bgr = cv2.imread(matching_orig_path)
                    if orig_bgr is not None and hasattr(cv2, "QRCodeDetector"):
                        qr_det = cv2.QRCodeDetector()
                        retval, decoded, _, _ = qr_det.detectAndDecodeMulti(orig_bgr)
                        if retval and any(decoded):
                            item["status"] = "REJECTED_JUNK"
                            item["reject_reason"] = f"Junk QR Code in Original ({decoded[0][:25]}...)"
                except Exception:
                    pass

        # Segregate Survived vs Rejected Photos
        survived_photos_list = [p for p in filter_result.get("all_scanned_photos", []) if p.get("status") == "PASSED"]
        rejected_photos_list = [p for p in filter_result.get("all_scanned_photos", []) if p.get("status") != "PASSED"]

        # 6. Populate PhotoMeta and PHOTO_STORE for survived photos only
        survived_photos_meta: List[PhotoMeta] = []
        for item in survived_photos_list:
            p_id = None
            for meta in metadata_list:
                if isinstance(meta, dict) and meta.get("photo_id") and (meta.get("photo_id") in item.get("filename", "") or meta.get("filename") == item.get("filename")):
                    p_id = meta.get("photo_id")
                    break
            if not p_id:
                p_id = item.get("photo_id") or item.get("filename", "").replace("_thumb.jpg", "").replace("_thumb.png", "").replace("_thumb.webp", "")

            meta = metadata_map.get(p_id, {})
            orig_name = meta.get("filename", f"{p_id}.jpg")
            thumb_filename = f"{p_id}_thumb.jpg"
            orig_filename = f"{p_id}_orig_{orig_name}"
            
            web_thumb_url = f"/uploads/thumbnails/{session_id}/{thumb_filename}"
            web_orig_url = f"/uploads/originals/{session_id}/{orig_filename}"
            
            photo_meta = PhotoMeta(
                id=str(p_id),
                filename=str(orig_name),
                url=web_thumb_url,
                preview_url=web_thumb_url,
                original_url=web_orig_url,
                thumbnail_url=web_thumb_url,
                original_synced=True,
                width=int(meta.get("original_width") or 1200),
                height=int(meta.get("original_height") or 900),
                aspect_ratio=float(item.get("aspect_ratio") or meta.get("aspect_ratio") or 1.33),
                dominant_colors=["#2C3E50", "#ECF0F1"]
            )
            PHOTO_STORE[p_id] = photo_meta
            survived_photos_meta.append(photo_meta)

        # Atomic Batch Persistence in SQLite (1000+ photos saved in a single transaction)
        SessionStore.save_photos_batch(survived_photos_meta, session_id=session_id)

        # Attach web preview URLs to filter results
        for p in filter_result.get("all_scanned_photos", []):
            p_filename = p.get("filename", "")
            p["web_url"] = f"/uploads/thumbnails/{session_id}/{p_filename}"

        # Explicit GC for large photo sets (avoids memory accumulation during peak 1000-photo uploads)
        if len(metadata_list) > 100:
            import gc
            gc.collect()

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"[Phase 1 Gateway] Session {session_id}: Ingested {len(metadata_list)} photos, Survived: {len(survived_photos_meta)}, Rejected: {len(rejected_photos_list)} in {elapsed_ms:.2f}ms")
        from app.metrics import MetricsCollector
        MetricsCollector.record("Phase 1 Ingestion & Filtering", elapsed_ms, {
            "photos_count": len(metadata_list),
            "survived": len(survived_photos_meta),
            "rejected": len(rejected_photos_list)
        })

        # Update summary counts
        summary = filter_result.get("summary", {})
        summary["total_uploaded"] = len(metadata_list)
        summary["total_survived"] = len(survived_photos_meta)
        summary["total_rejected"] = len(rejected_photos_list)

        return {
            "status": "success",
            "session_id": session_id,
            "summary": summary,
            "total_ingested": len(metadata_list),
            "total_survived": len(survived_photos_meta),
            "total_rejected": len(rejected_photos_list),
            "photos": [p.dict() if hasattr(p, "dict") else p.model_dump() for p in survived_photos_meta],
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
    if req.job_id not in JOBS_STORE:
        raise HTTPException(status_code=404, detail="Job ID not found")
        
    job = JOBS_STORE[req.job_id]
    if not job.result:
        raise HTTPException(status_code=400, detail="Job result not ready")

    from app.engine.solver import generate_photobook_variations_engine
    # Retrieve photos and ai batch result
    sample_photos = list(PHOTO_STORE.values()) if PHOTO_STORE else [
        PhotoMeta(id="p1", url="/uploads/sample_placeholder.jpg", aspect_ratio=1.33, dominant_colors=["#FAF9F6"])
    ]
    ai_batch_result = {
        "variations": [
            {"variation_id": f"var_{i+1}", "variation_title": v.variation_title, "theme_name": v.theme_name, "cover_title": v.cover_title, "cover_subtitle": v.cover_subtitle}
            for i, v in enumerate(job.result.variations)
        ]
    }

    new_variations = generate_photobook_variations_engine(sample_photos, ai_batch_result, variant_seed_offset=req.seed_offset)
    job.result.variations = new_variations
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
    photo_id: str,
    file: UploadFile = File(...)
):
    """
    Phase 2 (Background HD Sync):
    Receives 20MB original print files in background parallel stream.
    Saves original untouched file to /uploads/originals/ and updates PhotoMeta in PHOTO_STORE.
    """
    if photo_id not in PHOTO_STORE:
        # Create fallback photo_id if not present
        logger.info(f"[Background HD Sync] photo_id {photo_id} not in store. Registering new photo.")
    
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Original HD file exceeds 20MB limit.")

    file_ext = Path(file.filename).suffix or ".jpg"
    orig_filename = f"{photo_id}_orig{file_ext}"
    orig_path = UPLOADS_ORIGINALS_DIR / orig_filename

    with open(orig_path, "wb") as buffer:
        buffer.write(content)

    orig_url_path = f"/uploads/originals/{orig_filename}"

    if photo_id in PHOTO_STORE:
        PHOTO_STORE[photo_id].original_url = orig_url_path
        PHOTO_STORE[photo_id].original_synced = True
    SessionStore.mark_original_synced(photo_id, orig_url_path)

    logger.info(f"[Background HD Sync] Successfully synced original HD file for {photo_id} ({len(content)/(1024*1024):.2f}MB)")
    return {
        "status": "success",
        "photo_id": photo_id,
        "original_url": orig_url_path,
        "synced": True
    }

async def process_async_job(job_id: str, photo_ids: List[str], user_prompt: str):
    """Background async worker bounded by CONCURRENCY_SEMAPHORE with fail-safe recovery."""
    job_start = time.perf_counter()
    logger.info(f"[JobWorker] Starting job {job_id} for prompt: '{user_prompt}' with {len(photo_ids)} photos.")

    async with CONCURRENCY_SEMAPHORE:
        try:
            # Step 1: Update progress (30%)
            if job_id in JOBS_STORE:
                JOBS_STORE[job_id].progress = 30
                JOBS_STORE[job_id].message = "Analyzing photo colors and story context..."
                SessionStore.save_job(JOBS_STORE[job_id])
            await asyncio.sleep(0.1)

            # Load photos from in-memory cache first, fall back to SQLite SessionStore
            photos = [PHOTO_STORE[pid] for pid in photo_ids if pid in PHOTO_STORE]
            if len(photos) < len(photo_ids):
                db_photos = SessionStore.get_photos(photo_ids)
                if db_photos:
                    photos = db_photos
                    for p in db_photos:
                        PHOTO_STORE[p.id] = p

            if not photos:
                logger.warning(f"[JobWorker] No matching photos in store for job {job_id}. Using multi-sample fallbacks.")
                photos = [
                    PhotoMeta(
                        id="sample_1", filename="sample1.jpg", url="/uploads/sample1.jpg",
                        width=1200, height=900, aspect_ratio=1.33,
                        dominant_colors=["#FAF9F6", "#D4A373", "#3C3D37"]
                    ),
                    PhotoMeta(
                        id="sample_2", filename="sample2.jpg", url="/uploads/sample2.jpg",
                        width=1200, height=900, aspect_ratio=1.33,
                        dominant_colors=["#E1EBF0", "#4A6B82", "#1C2B36"]
                    ),
                    PhotoMeta(
                        id="sample_3", filename="sample3.jpg", url="/uploads/sample3.jpg",
                        width=1200, height=900, aspect_ratio=1.33,
                        dominant_colors=["#F0E1EB", "#824A6B", "#361C2B"]
                    ),
                    PhotoMeta(
                        id="sample_4", filename="sample4.jpg", url="/uploads/sample4.jpg",
                        width=1200, height=900, aspect_ratio=1.33,
                        dominant_colors=["#EBE1F0", "#6B4A82", "#2B1C36"]
                    )
                ]

            # Step 2: Gemini AI Theme batch (60%)
            if job_id in JOBS_STORE:
                JOBS_STORE[job_id].progress = 60
                JOBS_STORE[job_id].message = "Running Gemini AI theme & caption engine..."
                SessionStore.save_job(JOBS_STORE[job_id])
            ai_batch = generate_story_theme_batch(user_prompt, total_photos=len(photos))
            await asyncio.sleep(0.1)

            # Step 3: Cost-Solver Layout Engine (90%)
            if job_id in JOBS_STORE:
                JOBS_STORE[job_id].progress = 90
                JOBS_STORE[job_id].message = "Solving optimal layout templates & background swatches..."
                SessionStore.save_job(JOBS_STORE[job_id])
            variations = generate_photobook_variations_engine(photos, ai_batch)

            # Step 4: Job Completed (100%)
            elapsed_ms = (time.perf_counter() - job_start) * 1000
            if job_id in JOBS_STORE:
                JOBS_STORE[job_id].progress = 100
                JOBS_STORE[job_id].status = "completed"
                JOBS_STORE[job_id].message = "Photobook variations generated successfully!"
                JOBS_STORE[job_id].result = GenerateVariationsResponse(
                    theme_name=ai_batch.get("primary_theme", "Devotional / Temple"),
                    variations=variations
                )
                SessionStore.save_job(JOBS_STORE[job_id])
            from app.metrics import MetricsCollector
            MetricsCollector.record("Full Variation Pipeline (AI + Solver)", elapsed_ms, {
                "job_id": job_id,
                "photos_count": len(photos),
                "variations_count": len(variations)
            })
            logger.info(f"[Metrics] Job {job_id} COMPLETED successfully in {elapsed_ms:.2f}ms | {len(variations)} Variations Generated.")
        except Exception as e:
            elapsed_ms = (time.perf_counter() - job_start) * 1000
            logger.error(f"[JobWorker] Job {job_id} FAILED after {elapsed_ms:.2f}ms: {e}", exc_info=True)
            from app.metrics import MetricsCollector
            MetricsCollector.record("Failed Job Execution", elapsed_ms, {"job_id": job_id, "error": str(e)})
            if job_id in JOBS_STORE:
                JOBS_STORE[job_id].status = "failed"
                JOBS_STORE[job_id].message = f"Photobook generation encountered an error: {str(e)}"
                SessionStore.save_job(JOBS_STORE[job_id])

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
    JOBS_STORE[job_id] = initial_job
    SessionStore.save_job(initial_job)

    background_tasks.add_task(
        process_async_job, job_id, payload.photo_ids, payload.user_prompt
    )

    return initial_job

@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Polling route returning job processing status & result with DB fallback."""
    if job_id in JOBS_STORE:
        return JOBS_STORE[job_id]
    
    # Fallback to persistent SQLite DB
    db_job = SessionStore.get_job(job_id)
    if db_job:
        JOBS_STORE[job_id] = db_job
        return db_job

    logger.warning(f"[API] Job ID {job_id} not found in memory or database.")
    raise HTTPException(status_code=404, detail="Job ID not found.")

class ExportPDFRequest(BaseModel):
    variation: PhotobookVariation
    page_width_mm: float = 200.0
    page_height_mm: float = 200.0
    bleed_mm: float = 3.0
    dpi: int = 300

@app.post("/api/export-pdf")
def export_print_pdf(req: ExportPDFRequest):
    """
    Compiles 300 DPI High-Res Print PDF/X file from PhotobookVariation layout metadata.
    Fetches HD original photos from /uploads/originals/ and embeds 3mm bleed margins.
    """
    try:
        result = generate_print_pdf_engine(
            variation=req.variation,
            page_width_mm=req.page_width_mm,
            page_height_mm=req.page_height_mm,
            bleed_mm=req.bleed_mm,
            dpi=req.dpi
        )
        return result
    except Exception as e:
        logger.error(f"[Export PDF Error] Failed to generate 300 DPI PDF: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF Generation Error: {str(e)}")
