"""
SQLite-backed persistent store for Pixovo Sessions, Photos, and Async Jobs.
Provides thread-safe operations, automatic table creation, and crash-resilience.
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
from app.schemas.photobook import PhotoMeta, JobStatusResponse, PhotobookVariation
from app.config import logger

DB_PATH = Path(__file__).resolve().parent.parent.parent / "pixovo_session.db"

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
    # Enable WAL mode for high-concurrency non-blocking reads & writes
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema if it doesn't already exist."""
    conn = get_db_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                filename TEXT,
                url TEXT,
                preview_url TEXT,
                original_url TEXT,
                thumbnail_url TEXT,
                original_synced INTEGER DEFAULT 0,
                width INTEGER DEFAULT 1200,
                height INTEGER DEFAULT 900,
                aspect_ratio REAL DEFAULT 1.33,
                dominant_colors_json TEXT,
                score REAL DEFAULT 0.8,
                blur_score REAL,
                face_count INTEGER DEFAULT 0,
                shell_phash TEXT,
                core_phash TEXT,
                is_hero_candidate INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                session_id TEXT,
                status TEXT,
                progress INTEGER DEFAULT 0,
                message TEXT,
                variations_json TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_photos_session ON photos(session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id)
        """)
    conn.close()
    logger.info(f"[DB] Initialized persistent SQLite database at: {DB_PATH}")

# Initialize on module import
init_db()

class SessionStore:
    """Thread-safe CRUD operations for Photos and Jobs."""

    @staticmethod
    def save_photo(photo: PhotoMeta, session_id: Optional[str] = None) -> None:
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO photos (
                        id, session_id, filename, url, preview_url, original_url, thumbnail_url,
                        original_synced, width, height, aspect_ratio, dominant_colors_json,
                        score, blur_score, face_count, shell_phash, core_phash, is_hero_candidate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    photo.id,
                    session_id,
                    photo.filename,
                    photo.url,
                    photo.preview_url or photo.url,
                    photo.original_url,
                    photo.thumbnail_url,
                    1 if photo.original_synced else 0,
                    photo.width,
                    photo.height,
                    photo.aspect_ratio,
                    json.dumps(photo.dominant_colors or []),
                    photo.score,
                    photo.blur_score,
                    photo.face_count,
                    photo.shell_phash,
                    photo.core_phash,
                    1 if photo.is_hero_candidate else 0
                ))
        finally:
            conn.close()

    @staticmethod
    def save_photos_batch(photos: List[PhotoMeta], session_id: Optional[str] = None) -> None:
        """Atomic batch insert using executemany for high performance with up to 1000+ photos."""
        if not photos:
            return
        conn = get_db_connection()
        try:
            records = [
                (
                    photo.id,
                    session_id,
                    photo.filename,
                    photo.url,
                    photo.preview_url or photo.url,
                    photo.original_url,
                    photo.thumbnail_url,
                    1 if photo.original_synced else 0,
                    photo.width,
                    photo.height,
                    photo.aspect_ratio,
                    json.dumps(photo.dominant_colors or []),
                    photo.score,
                    photo.blur_score,
                    photo.face_count,
                    photo.shell_phash,
                    photo.core_phash,
                    1 if photo.is_hero_candidate else 0
                )
                for photo in photos
            ]
            with conn:
                conn.executemany("""
                    INSERT OR REPLACE INTO photos (
                        id, session_id, filename, url, preview_url, original_url, thumbnail_url,
                        original_synced, width, height, aspect_ratio, dominant_colors_json,
                        score, blur_score, face_count, shell_phash, core_phash, is_hero_candidate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, records)
        finally:
            conn.close()

    @staticmethod
    def get_photo(photo_id: str) -> Optional[PhotoMeta]:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
            row = cur.fetchone()
            if not row:
                return None
            return PhotoMeta(
                id=row["id"],
                filename=row["filename"],
                url=row["url"],
                preview_url=row["preview_url"],
                original_url=row["original_url"],
                thumbnail_url=row["thumbnail_url"],
                original_synced=bool(row["original_synced"]),
                width=row["width"],
                height=row["height"],
                aspect_ratio=row["aspect_ratio"],
                dominant_colors=json.loads(row["dominant_colors_json"] or "[]"),
                score=row["score"],
                blur_score=row["blur_score"],
                face_count=row["face_count"],
                shell_phash=row["shell_phash"],
                core_phash=row["core_phash"],
                is_hero_candidate=bool(row["is_hero_candidate"])
            )
        finally:
            conn.close()

    @staticmethod
    def get_photos(photo_ids: List[str]) -> List[PhotoMeta]:
        """
        Retrieves photos in safe batches of 500 to prevent SQLite 999 parameter placeholder limits
        when fetching large sessions (e.g. 1000 photos).
        """
        if not photo_ids:
            return []
        conn = get_db_connection()
        try:
            results = {}
            chunk_size = 500
            cur = conn.cursor()
            for i in range(0, len(photo_ids), chunk_size):
                chunk = photo_ids[i:i + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"SELECT * FROM photos WHERE id IN ({placeholders})", chunk)
                rows = cur.fetchall()
                for row in rows:
                    results[row["id"]] = PhotoMeta(
                        id=row["id"],
                        filename=row["filename"],
                        url=row["url"],
                        preview_url=row["preview_url"],
                        original_url=row["original_url"],
                        thumbnail_url=row["thumbnail_url"],
                        original_synced=bool(row["original_synced"]),
                        width=row["width"],
                        height=row["height"],
                        aspect_ratio=row["aspect_ratio"],
                        dominant_colors=json.loads(row["dominant_colors_json"] or "[]"),
                        score=row["score"],
                        blur_score=row["blur_score"],
                        face_count=row["face_count"],
                        shell_phash=row["shell_phash"],
                        core_phash=row["core_phash"],
                        is_hero_candidate=bool(row["is_hero_candidate"])
                    )
            return [results[pid] for pid in photo_ids if pid in results]
        finally:
            conn.close()

    @staticmethod
    def get_session_photos(session_id: str) -> List[PhotoMeta]:
        """Retrieves all photos persisted under a given session_id."""
        if not session_id:
            return []
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM photos WHERE session_id = ? ORDER BY created_at ASC", (session_id,))
            rows = cur.fetchall()
            return [
                PhotoMeta(
                    id=row["id"],
                    filename=row["filename"],
                    url=row["url"],
                    preview_url=row["preview_url"],
                    original_url=row["original_url"],
                    thumbnail_url=row["thumbnail_url"],
                    original_synced=bool(row["original_synced"]),
                    width=row["width"],
                    height=row["height"],
                    aspect_ratio=row["aspect_ratio"],
                    dominant_colors=json.loads(row["dominant_colors_json"] or "[]"),
                    score=row["score"],
                    blur_score=row["blur_score"],
                    face_count=row["face_count"],
                    shell_phash=row["shell_phash"],
                    core_phash=row["core_phash"],
                    is_hero_candidate=bool(row["is_hero_candidate"])
                )
                for row in rows
            ]
        finally:
            conn.close()

    @staticmethod
    def mark_original_synced(photo_id: str, original_url: str) -> None:
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("""
                    UPDATE photos 
                    SET original_url = ?, original_synced = 1 
                    WHERE id = ?
                """, (original_url, photo_id))
        finally:
            conn.close()

    @staticmethod
    def count_photos() -> int:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as count FROM photos")
            return cur.fetchone()["count"]
        finally:
            conn.close()

    @staticmethod
    def save_job(job: JobStatusResponse, session_id: Optional[str] = None) -> None:
        conn = get_db_connection()
        try:
            result_json = None
            if job.result and job.result.variations:
                result_json = json.dumps({
                    "theme_name": job.result.theme_name,
                    "variations": [v.dict() for v in job.result.variations]
                })
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO jobs (
                        job_id, session_id, status, progress, message, variations_json, error_message, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    job.job_id,
                    session_id,
                    job.status,
                    job.progress,
                    job.message,
                    result_json,
                    getattr(job, "error", None)
                ))
        finally:
            conn.close()

    @staticmethod
    def get_job(job_id: str) -> Optional[JobStatusResponse]:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            
            result_obj = None
            if row["variations_json"]:
                raw_data = json.loads(row["variations_json"])
                variations = [PhotobookVariation(**v) for v in raw_data.get("variations", [])]
                result_obj = GenerateVariationsResponse(
                    theme_name=raw_data.get("theme_name", "Devotional / Temple"),
                    variations=variations
                )
            
            return JobStatusResponse(
                job_id=row["job_id"],
                status=row["status"],
                progress=row["progress"],
                message=row["message"],
                result=result_obj
            )
        finally:
            conn.close()

    @staticmethod
    def count_jobs() -> int:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as count FROM jobs")
            return cur.fetchone()["count"]
        finally:
            conn.close()
