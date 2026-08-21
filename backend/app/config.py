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
UPLOADS_DIR = BASE_DIR / "app" / "uploads"
UPLOADS_ORIGINALS_DIR = UPLOADS_DIR / "originals"
UPLOADS_THUMBNAILS_DIR = UPLOADS_DIR / "thumbnails"
UPLOADS_PREVIEWS_DIR = UPLOADS_DIR / "previews"
EXPORTS_DIR = BASE_DIR / "app" / "exports"

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

def ensure_sample_placeholders():
    """Generates aesthetic placeholder image files for sample fallbacks if missing."""
    from PIL import Image, ImageDraw
    samples = [
        ("sample1.jpg", "#D4A373", "#FEFAE0", "SAMPLE 1 - WARM GOLD"),
        ("sample2.jpg", "#4A6B82", "#E1EBF0", "SAMPLE 2 - OCEAN BLUE"),
        ("sample3.jpg", "#824A6B", "#F0E1EB", "SAMPLE 3 - ROSE VELVET"),
        ("sample4.jpg", "#6B4A82", "#EBE1F0", "SAMPLE 4 - SAGE GREEN"),
        ("sample_placeholder.jpg", "#D4A373", "#FEFAE0", "SAMPLE PLACEHOLDER")
    ]
    for name, bg_color, border_color, label in samples:
        main_file = UPLOADS_DIR / name
        orig_file = UPLOADS_ORIGINALS_DIR / name
        prev_file = UPLOADS_PREVIEWS_DIR / name
        
        # Always ensure files exist and are distinct
        try:
            img = Image.new("RGB", (1200, 900), color=bg_color)
            draw = ImageDraw.Draw(img)
            draw.rectangle([30, 30, 1170, 870], outline=border_color, width=12)
            draw.rectangle([60, 60, 1140, 840], outline=border_color, width=4)
            img.save(main_file, format="JPEG", quality=90)
            img.save(orig_file, format="JPEG", quality=95)
            img.save(prev_file, format="JPEG", quality=85)
        except Exception as e:
            pass

ensure_sample_placeholders()

# Session Logs Directory
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SESSION_TIMESTAMP = time.strftime("%Y-%m-%d_%H-%M-%S")
SESSION_LOG_FILE = LOGS_DIR / f"session_{SESSION_TIMESTAMP}.log"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

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
