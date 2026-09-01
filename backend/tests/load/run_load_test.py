"""
Pixovo load harness — the Stage 1.7 exit gate.

Drives N concurrent simulated users through the full pipeline (session ->
chunked ingest -> generate -> poll) against the ASGI app in-process, while
sampling /health to measure event-loop responsiveness.

    python -m tests.load.run_load_test --users 20 --photos 1000
    python -m tests.load.run_load_test --users 4 --photos 60 --quick

Isolation: writes to a throwaway uploads dir and database, never the dev ones.
Set PIXOVO_POOL=process to benchmark the process pool instead of threads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Redirect storage BEFORE importing app.* — config and session_store both do
# work at import time.
_TMP = Path(tempfile.mkdtemp(prefix="pixovo_load_"))
os.environ.setdefault("PIXOVO_DB_PATH", str(_TMP / "load.db"))
os.environ.setdefault("PIXOVO_UPLOADS_DIR", str(_TMP / "uploads"))
os.environ.setdefault("PIXOVO_EXPORTS_DIR", str(_TMP / "exports"))
# Keep log noise out of the timing numbers.
os.environ.setdefault("LOGURU_LEVEL", "WARNING")

import httpx  # noqa: E402

from tests.fixtures.generate_corpus import corpus_stats, generate_corpus  # noqa: E402

try:
    import psutil
except ImportError:
    psutil = None


def pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def chunks(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class LoadCorpus:
    """Thumbnail bytes held in memory once and shared by every simulated user."""

    def __init__(self, root: Path, count: int, seed: int = 42):
        self.items = generate_corpus(root, count=count, seed=seed)
        self.stats = corpus_stats(self.items)
        self.blobs: List[bytes] = [Path(i.path).read_bytes() for i in self.items]

    def payload_for(self, user_idx: int, photos: int, chunk_size: int) -> List[Dict[str, Any]]:
        """
        Per-user photo list. photo_ids are namespaced per user so sessions stay
        disjoint even though the image bytes are shared.
        """
        out = []
        for n in range(photos):
            src = n % len(self.items)
            item = self.items[src]
            pid = f"px_u{user_idx:03d}_{n:05d}"
            out.append({
                "photo_id": pid,
                "bytes": self.blobs[src],
                "meta": {
                    "photo_id": pid,
                    "filename": f"{pid}.jpg",
                    "original_width": item.width,
                    "original_height": item.height,
                    "aspect_ratio": item.aspect_ratio,
                    "timestamp_epoch": item.timestamp_epoch + n,
                    "original_size_bytes": 8_000_000,
                    "thumbnail_size_bytes": len(self.blobs[src]),
                },
            })
        return out


async def simulate_user(
    client: httpx.AsyncClient,
    user_idx: int,
    corpus: LoadCorpus,
    photos: int,
    chunk_size: int,
) -> Dict[str, Any]:
    t: Dict[str, Any] = {"user": user_idx, "photos_requested": photos}
    t0 = time.perf_counter()

    try:
        res = await client.post("/api/sessions", json={"expected_photo_count": photos})
        if res.status_code != 201:
            t["error"] = f"session {res.status_code}: {res.text[:120]}"
            return t
        session_id = res.json()["session_id"]
        t["session_id"] = session_id

        payload = corpus.payload_for(user_idx, photos, chunk_size)
        batches = chunks(payload, chunk_size)
        survived = 0

        for ci, batch in enumerate(batches):
            files = [
                ("thumbnails", (f"{p['photo_id']}_thumb.jpg", p["bytes"], "image/jpeg"))
                for p in batch
            ]
            r = await client.post(
                "/api/photobook/ingest",
                data={
                    "session_id": session_id,
                    "chunk_index": str(ci),
                    "chunk_count": str(len(batches)),
                    "metadata_json": json.dumps([p["meta"] for p in batch]),
                },
                files=files,
                timeout=300.0,
            )
            if r.status_code != 200:
                t["error"] = f"ingest chunk {ci} -> {r.status_code}: {r.text[:120]}"
                return t
            survived = r.json().get("session_survived", survived)

        t["ingest_s"] = time.perf_counter() - t0
        t["survived"] = survived

        r = await client.post("/api/generate-async", json={
            "photo_ids": [p["photo_id"] for p in payload],
            "user_prompt": "Family trip to the temple",
            "session_id": session_id,
        })
        if r.status_code != 202:
            t["error"] = f"generate {r.status_code}: {r.text[:120]}"
            return t
        job_id = r.json()["job_id"]

        deadline = time.perf_counter() + 600.0
        while time.perf_counter() < deadline:
            job = (await client.get(f"/api/jobs/{job_id}", timeout=60.0)).json()
            if job["status"] == "completed":
                t["total_s"] = time.perf_counter() - t0
                t["generate_s"] = t["total_s"] - t["ingest_s"]
                t["spreads"] = sum(len(v["spreads"]) for v in job["result"]["variations"])
                t["variations"] = len(job["result"]["variations"])
                covers = [
                    cp["photo_id"]
                    for v in job["result"]["variations"]
                    for cp in v.get("cover_photos", [])
                ]
                t["cover_photos"] = len(covers)
                t["cover_photos_distinct"] = len(set(covers))
                return t
            if job["status"] == "failed":
                t["error"] = f"job failed: {job['message'][:120]}"
                return t
            await asyncio.sleep(0.25)

        t["error"] = "job timed out after 600s"
        return t

    except Exception as exc:  # noqa: BLE001 — a harness must report, not crash
        t["error"] = f"{type(exc).__name__}: {exc}"
        return t


async def sample_health(client: httpx.AsyncClient, stop: asyncio.Event, out: List[float]) -> None:
    """
    Event-loop responsiveness — the single most diagnostic metric here. If CPU
    work is running on the loop instead of the pool, this is where it shows.
    """
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            await client.get("/health", timeout=10.0)
            out.append((time.perf_counter() - t0) * 1000.0)
        except Exception:
            out.append(10_000.0)
        await asyncio.sleep(0.1)


async def sample_memory(stop: asyncio.Event, out: List[float]) -> None:
    if psutil is None:
        return
    proc = psutil.Process()
    while not stop.is_set():
        out.append(proc.memory_info().rss / 1024 ** 3)
        await asyncio.sleep(0.5)


def dir_size_gb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 ** 3


async def run(users: int, photos: int, chunk_size: int, corpus_size: int) -> int:
    from app.config import CPU_COUNT, FILTER_WORKERS, JOB_CONCURRENCY, POOL_KIND
    from app.main import app

    print(f"Generating synthetic corpus ({corpus_size} photos)...", flush=True)
    corpus = LoadCorpus(_TMP / "corpus", count=corpus_size)
    print(f"  {corpus.stats}", flush=True)

    health_ms: List[float] = []
    rss_gb: List[float] = []
    stop = asyncio.Event()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://load") as client:
        health_task = asyncio.create_task(sample_health(client, stop, health_ms))
        mem_task = asyncio.create_task(sample_memory(stop, rss_gb))

        print(f"Running {users} users x {photos} photos...", flush=True)
        wall0 = time.perf_counter()
        results = await asyncio.gather(*[
            simulate_user(client, i, corpus, photos, chunk_size) for i in range(users)
        ])
        wall = time.perf_counter() - wall0

        stop.set()
        await asyncio.gather(health_task, mem_task, return_exceptions=True)

    ok = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]

    ingest = [r["ingest_s"] for r in ok if "ingest_s" in r]
    total = [r["total_s"] for r in ok if "total_s" in r]
    gen = [r["generate_s"] for r in ok if "generate_s" in r]
    photos_done = sum(r.get("photos_requested", 0) for r in ok)

    def row(label: str, vals: List[float], unit: str = "s") -> str:
        if not vals:
            return f"{label:<24} (no data)"
        return (
            f"{label:<24} p50 {pct(vals, 50):7.2f}{unit}  "
            f"p95 {pct(vals, 95):7.2f}{unit}  max {max(vals):7.2f}{unit}"
        )

    print("\n" + "=" * 72)
    print("PIXOVO LOAD TEST")
    print("=" * 72)
    print(f"Users: {users} | Photos/user: {photos} | Chunk: {chunk_size}")
    print(f"Machine: {CPU_COUNT} cores | filter workers {FILTER_WORKERS} "
          f"({POOL_KIND} pool) | job concurrency {JOB_CONCURRENCY}")
    if psutil is not None:
        print(f"RAM: {psutil.virtual_memory().total / 1024 ** 3:.1f} GB")
    print("-" * 72)
    print(row("Ingest (per user)", ingest))
    print(row("Generate (per user)", gen))
    print(row("Total (per user)", total))
    print(row("/health latency", health_ms, "ms"))
    print("-" * 72)
    print(f"{'Wall clock':<24} {wall:.1f}s")
    if photos_done and wall:
        thr = photos_done / wall
        print(f"{'Throughput':<24} {thr:.1f} photos/sec "
              f"({thr / max(1, FILTER_WORKERS):.1f} per worker)")
    if rss_gb:
        print(f"{'Peak RSS':<24} {max(rss_gb):.2f} GB")
    print(f"{'Peak disk':<24} {dir_size_gb(_TMP / 'uploads'):.2f} GB")
    print(f"{'Health samples':<24} {len(health_ms)}")
    print(f"{'Failed sessions':<24} {len(failed)} / {users}")

    if ok:
        distinct = [r.get("cover_photos_distinct", 0) for r in ok]
        counts = [r.get("cover_photos", 0) for r in ok]
        spreads = [r.get("spreads", 0) for r in ok]
        print(f"{'Cover photos/user':<24} {statistics.median(counts):.0f} "
              f"({statistics.median(distinct):.0f} distinct)")
        print(f"{'Spreads/user':<24} {statistics.median(spreads):.0f}")

    for f in failed[:5]:
        print(f"  FAIL user {f['user']}: {f['error']}")

    # Gate: the numbers that decide whether the target is met.
    print("-" * 72)
    gate_health = pct(health_ms, 99) if health_ms else 0.0
    checks = [
        ("no failed sessions", len(failed) == 0, f"{len(failed)} failed"),
        ("/health p99 < 200ms", gate_health < 200.0, f"p99 {gate_health:.0f}ms"),
        ("all users got 3 variations",
         all(r.get("variations") == 3 for r in ok) and bool(ok),
         "variation count mismatch"),
    ]
    for name, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + ("" if passed else f" — {detail}"))

    all_passed = all(c[1] for c in checks)
    print("=" * 72)
    print("RESULT:", "PASS" if all_passed else "FAIL")
    return 0 if all_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Pixovo load harness")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--photos", type=int, default=1000)
    parser.add_argument("--chunk", type=int, default=40)
    parser.add_argument("--corpus", type=int, default=200,
                        help="distinct synthetic photos generated; users cycle through them")
    parser.add_argument("--quick", action="store_true", help="4 users x 60 photos")
    parser.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = parser.parse_args()

    users, photos = args.users, args.photos
    corpus_size = args.corpus
    if args.quick:
        users, photos, corpus_size = 4, 60, 100

    try:
        return asyncio.run(run(users, photos, args.chunk, min(corpus_size, max(photos, 20))))
    finally:
        if args.keep:
            print(f"\nTemp dir kept: {_TMP}")
        else:
            shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
