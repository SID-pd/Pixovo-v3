import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# Base Directories
BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_ENV = BASE_DIR.parent.parent / ".env" # Check root workspace .env
LOCAL_ENV = BASE_DIR / ".env"              # Check local backend .env

# Load environment variables
if LOCAL_ENV.exists():
    load_dotenv(LOCAL_ENV)
elif ROOT_ENV.exists():
    load_dotenv(ROOT_ENV)
else:
    load_dotenv()

# Upload Directories (Unified Dual-Asset Pipeline: Originals vs Thumbnails)
# [PRODUCTION SPEC]:
# - Originals: 300 DPI Print production assets
# - Thumbnails: 512px downsampled AI filtering & UI previews
# Overridable so tests write into a throwaway directory instead of the real
# uploads tree. Read at import time, which is when the mkdir calls below run.
UPLOADS_DIR = Path(os.environ.get("PIXOVO_UPLOADS_DIR") or (BASE_DIR / "app" / "uploads"))
UPLOADS_ORIGINALS_DIR = UPLOADS_DIR / "originals"
UPLOADS_THUMBNAILS_DIR = UPLOADS_DIR / "thumbnails"
UPLOADS_PREVIEWS_DIR = UPLOADS_DIR / "previews"
EXPORTS_DIR = Path(os.environ.get("PIXOVO_EXPORTS_DIR") or (BASE_DIR / "app" / "exports"))

# Centralized Face & Filter Models
FILTER_MODELS_DIR = BASE_DIR / "app" / "engine" / "filter" / "face_detector" / "models"
MODEL_BLAZEFACE = FILTER_MODELS_DIR / "blaze_face_short_range.tflite"
MODEL_YUNET = FILTER_MODELS_DIR / "face_detection_yunet_2023mar.onnx"
MODEL_HAARCASCADE = FILTER_MODELS_DIR / "haarcascade_frontalface_default.xml"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Stage 1.6: `ensure_sample_placeholders()` used to be defined here and called
# at import time, writing 15 placeholder JPEGs (sample1-4 + sample_placeholder,
# in three directories) on every process start and every test collection.
#
# It existed to back fallbacks that have all been removed:
#   - process_async_job's four `sample_N` photos (deleted in Stage 1.4)
#   - solver.py's `photos[0] or sample_placeholder` cover (Stage 1.5)
#   - pdf_exporter's unresolvable-photo placeholder (Stage 1.6)
#
# Nothing reads these files now, and generating them was both an import-time
# side effect and a way for a placeholder to reach a paid print.

# Session Logs Directory
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SESSION_TIMESTAMP = time.strftime("%Y-%m-%d_%H-%M-%S")
SESSION_LOG_FILE = LOGS_DIR / f"session_{SESSION_TIMESTAMP}.log"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ----------------------------------------------------------------------
# Ingestion limits (Stage 1.1)
# ----------------------------------------------------------------------
# Hard ceiling per session. Enforced at POST /api/sessions so an oversized
# batch is rejected before any bytes are uploaded.
MAX_PHOTOS_PER_SESSION = int(os.environ.get("MAX_PHOTOS_PER_SESSION", "1000"))

# Thumbnails per ingest chunk. Kept in config so the client can fetch it and
# the two sides cannot drift. 40 x ~35KB thumbnails is a ~1.5MB request.
INGEST_CHUNK_SIZE = int(os.environ.get("INGEST_CHUNK_SIZE", "40"))

# ----------------------------------------------------------------------
# Concurrency sizing (Stage 1.4)
# ----------------------------------------------------------------------
# Previously ThreadPoolExecutor(max_workers=4) and Semaphore(4) were hardcoded
# regardless of the host, AND filter_engine constructed its own inner pool of 4
# per call — so 4 outer x 4 inner threads competed for however many cores the
# machine actually had, while OpenCV separately fanned out per operation.
CPU_COUNT = os.cpu_count() or 4

# Reserve a core for the event loop. A fully saturated pool starves request
# handling, and the symptom looks like a network problem rather than CPU
# exhaustion.
FILTER_WORKERS = int(os.environ.get("PIXOVO_FILTER_WORKERS", str(max(2, CPU_COUNT - 1))))
JOB_CONCURRENCY = int(os.environ.get("PIXOVO_JOB_CONCURRENCY", str(max(2, CPU_COUNT // 2))))

# thread | process — settled by measurement in Stage 1.7, not by argument.
# OpenCV releases the GIL; PIL and imagehash largely do not.
POOL_KIND = os.environ.get("PIXOVO_POOL", "thread")

# Bounded working caches. Both have SQLite fallbacks, so a miss is correct and
# merely slower. Unbounded dicts leaked for the life of the process.
PHOTO_CACHE_SIZE = int(os.environ.get("PIXOVO_PHOTO_CACHE", "8000"))
JOB_CACHE_SIZE = int(os.environ.get("PIXOVO_JOB_CACHE", "500"))
CACHE_TTL_SECONDS = int(os.environ.get("PIXOVO_CACHE_TTL", "3600"))

# We parallelise across photos, so OpenCV must not also fan out within each
# operation — otherwise FILTER_WORKERS x CPU_COUNT threads thrash the machine.
try:
    import cv2 as _cv2

    _cv2.setNumThreads(1)
    logger_cv_note = f"cv2.setNumThreads(1) applied (was {CPU_COUNT} default)"
except Exception as _e:  # pragma: no cover
    logger_cv_note = f"cv2 thread limit not applied: {_e}"

# ----------------------------------------------------------------------
# Disk guards (Stage 1.3)
# ----------------------------------------------------------------------
# At the load target (20 users x 1000 photos) eager original upload would need
# ~160 GB. Demand-driven upload brings it to ~40 GB, but the machine still needs
# a hard stop: a demo that dies from a full disk is worse than one that says
# "at capacity".
MAX_BYTES_PER_SESSION = int(os.environ.get("MAX_BYTES_PER_SESSION", str(3 * 1024**3)))    # 3 GB
GLOBAL_DISK_WATERMARK = int(os.environ.get("GLOBAL_DISK_WATERMARK", str(60 * 1024**3)))   # 60 GB

# ----------------------------------------------------------------------
# Storage backend (Stage 1.3)
# ----------------------------------------------------------------------
# Single instance shared by every caller. Swapping to S3 means constructing a
# different backend here; no call site changes.
from app.storage import LocalDiskBackend, StorageBackend, storage_key  # noqa: E402

STORAGE: StorageBackend = LocalDiskBackend(root=UPLOADS_DIR, url_prefix="/uploads")


# Configure Loguru Logger for metrics, stats, and session logging
logger.remove() # Remove default handler

# 1. Console Output (Colorized DEBUG)
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level:7}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="DEBUG",
    colorize=True
)

# 2. Cumulative Rotating Log File
CUMULATIVE_LOG_FILE = LOGS_DIR / "backend_metrics.log"
logger.add(
    str(CUMULATIVE_LOG_FILE),
    rotation="10 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:7} | {name}:{function}:{line} - {message}",
    level="DEBUG"
)

# 3. Dedicated Per-Session Log File
logger.add(
    str(SESSION_LOG_FILE),
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:7} | {name}:{function}:{line} - {message}",
    level="DEBUG"
)

logger.info(f"[Config] New backend session started. Log file: logs/{SESSION_LOG_FILE.name}")
logger.info(f"[Config] Environment loaded. GEMINI_API_KEY present: {bool(GEMINI_API_KEY)}")
logger.info(
    f"[Config] Concurrency: {CPU_COUNT} cores | filter workers {FILTER_WORKERS} "
    f"({POOL_KIND} pool) | job concurrency {JOB_CONCURRENCY} | {logger_cv_note}"
)
