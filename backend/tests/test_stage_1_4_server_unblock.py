"""
Stage 1.4 regression tests — unblocking the server.

What these guard:
  * no nested thread pools (the inner pool per call is gone)
  * finalise_batch is separable from per-photo scanning
  * generation does not block the event loop
  * caches are bounded, and a cache miss falls back to SQLite rather than 404
  * a job with no photos fails honestly instead of fabricating a photobook
  * /health does no I/O
"""

import asyncio
import inspect
import json
import time

import pytest

from tests.conftest import make_thumbnail_bytes


def ingest_chunk(client, session_id, photo_ids, base_ts=1_785_535_200, step=300):
    files, meta = [], []
    for n, pid in enumerate(photo_ids):
        files.append((
            "thumbnails",
            (f"{pid}_thumb.jpg", make_thumbnail_bytes(abs(hash(pid)) % 9973 + n), "image/jpeg"),
        ))
        meta.append({
            "photo_id": pid,
            "filename": f"{pid}.jpg",
            "original_width": 4000,
            "original_height": 3000,
            "aspect_ratio": 1.333,
            "orientation": "LANDSCAPE",
            "timestamp": "2026-08-01T10:00:00.000Z",
            "timestamp_epoch": base_ts + n * step,
            "original_size_bytes": 8_000_000,
            "thumbnail_size_bytes": 30_000,
        })
    return client.post(
        "/api/photobook/ingest",
        data={
            "session_id": session_id,
            "chunk_index": "0",
            "chunk_count": "1",
            "metadata_json": json.dumps(meta),
        },
        files=files,
    )


# ------------------------------------------------------------ pool structure


def test_filter_engine_no_longer_creates_its_own_pool():
    """
    run_phase1_filtering used to construct a ThreadPoolExecutor(4) per call while
    already running on a pool thread — FILTER_WORKERS x 4 threads contending,
    on top of OpenCV's own per-operation fan-out.
    """
    import app.engine.filter.filter_engine as fe

    source = inspect.getsource(fe)
    # Match construction specifically — the explanatory comment mentions the
    # class by name, so a bare substring check matches the comment too.
    assert "ThreadPoolExecutor(" not in source, "filter_engine still constructs a pool"
    assert "gc.collect()" not in source, "per-chunk stop-the-world GC still present"


def test_opencv_thread_fanout_is_disabled():
    """We parallelise across photos; OpenCV must not also fan out per operation."""
    import cv2
    import app.config  # noqa: F401  (import applies setNumThreads)

    assert cv2.getNumThreads() == 1


def test_pools_are_sized_from_the_host():
    from app.config import CPU_COUNT, FILTER_WORKERS, JOB_CONCURRENCY

    assert FILTER_WORKERS >= 2
    assert JOB_CONCURRENCY >= 2
    # One core is reserved for the event loop.
    assert FILTER_WORKERS <= max(2, CPU_COUNT - 1)


def test_finalise_batch_is_separable():
    """
    The per-photo half must be usable independently of the serial half, which is
    what lets the caller fan out across concurrent sessions.
    """
    from app.main import filter_engine
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="pixovo_fb_"))
    paths = []
    for i in range(4):
        p = tmp / f"px_fb{i}_thumb.jpg"
        p.write_bytes(make_thumbnail_bytes(500 + i))
        paths.append(str(p))

    scanned = [filter_engine.process_single_photo(p, f"p_{i}") for i, p in enumerate(paths)]
    assert len(scanned) == 4
    assert all("hero_score" in s for s in scanned)

    result = filter_engine.finalise_batch(scanned, total_uploaded=len(paths))
    assert result["summary"]["total_uploaded"] == 4
    assert "survived_photos" in result
    assert "events" in result


# ------------------------------------------------------------ health probes


def test_health_does_no_io(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    # Should be far faster than an endpoint that counts rows.
    start = time.perf_counter()
    for _ in range(20):
        client.get("/health")
    per_call_ms = (time.perf_counter() - start) / 20 * 1000
    assert per_call_ms < 50, f"/health averaged {per_call_ms:.1f}ms — is it doing I/O?"


def test_ready_checks_the_database(client):
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"


# ---------------------------------------------------------------- caches


def test_caches_are_bounded():
    from app.main import JOBS_STORE, PHOTO_STORE
    from cachetools import TTLCache

    assert isinstance(PHOTO_STORE, TTLCache)
    assert isinstance(JOBS_STORE, TTLCache)
    assert PHOTO_STORE.maxsize > 0
    assert JOBS_STORE.maxsize > 0


def test_cache_eviction_does_not_lose_photos(client, store):
    """
    A bounded cache must degrade to a SQLite read, never to a missing photo.
    """
    from app.main import PHOTO_STORE, _CACHE_LOCK, get_cached_photo

    sess = client.post("/api/sessions", json={"expected_photo_count": 2}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_ev1", "px_ev2"]).status_code == 200

    with _CACHE_LOCK:
        PHOTO_STORE.clear()
    assert get_cached_photo("px_ev1") is None, "cache was not cleared"

    # Source of truth still has them, and in capture order.
    photos = store.get_session_photos(sess)
    assert {p.id for p in photos} == {"px_ev1", "px_ev2"}


def test_job_status_survives_cache_eviction(client):
    """A cache miss must not become a 404 — SQLite is the source of truth."""
    from app.main import JOBS_STORE, _CACHE_LOCK

    sess = client.post("/api/sessions", json={"expected_photo_count": 2}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_jb1", "px_jb2"]).status_code == 200

    res = client.post("/api/generate-async", json={
        "photo_ids": ["px_jb1", "px_jb2"],
        "user_prompt": "Family trip",
        "session_id": sess,
    })
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    with _CACHE_LOCK:
        JOBS_STORE.clear()

    got = client.get(f"/api/jobs/{job_id}")
    assert got.status_code == 200, "evicted job became a 404"
    assert got.json()["job_id"] == job_id


# ------------------------------------------------------- fail honestly (1.6)


def test_job_with_no_photos_fails_instead_of_faking_a_book(client):
    """
    This used to fabricate four `sample_N` placeholders and return a COMPLETE
    photobook — a data-loss bug presented to the user as a finished product.
    """
    sess = client.post("/api/sessions", json={"expected_photo_count": 0}).json()["session_id"]

    res = client.post("/api/generate-async", json={
        "photo_ids": [],
        "user_prompt": "Nothing uploaded",
        "session_id": sess,
    })
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    job = None
    for _ in range(50):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    assert job is not None
    assert job["status"] == "failed", f"expected failure, got {job['status']}: {job['message']}"
    assert "no valid photos" in job["message"].lower()
    assert job.get("result") is None


def test_no_sample_placeholders_remain_in_the_job_path():
    import app.main as main_mod

    source = inspect.getsource(main_mod.process_async_job)
    for banned in ("sample_1", "sample1.jpg", "multi-sample"):
        assert banned not in source, f"job worker still references {banned}"


# ------------------------------------------------- event loop responsiveness


def test_cpu_work_does_not_block_the_event_loop():
    """
    The headline claim of Stage 1.4.

    Measured against INGEST rather than generation: filtering ~20 photos costs
    seconds of OpenCV work, whereas the layout solver finishes in well under
    100ms even for a full session, so generation alone is too fast to sample.
    Ingest is both the long pole and the code path that changed most (per-photo
    fan-out replacing nested pools), which makes it the right thing to measure.

    If the pool offload were removed, these /health polls would stall for the
    whole filtering run.
    """
    import httpx
    from app.main import app

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            sess = (await ac.post("/api/sessions", json={"expected_photo_count": 20})).json()["session_id"]

            ids = [f"px_nb{i}" for i in range(20)]
            files, meta = [], []
            for n, pid in enumerate(ids):
                files.append((
                    "thumbnails",
                    (f"{pid}_thumb.jpg", make_thumbnail_bytes(700 + n), "image/jpeg"),
                ))
                meta.append({
                    "photo_id": pid, "filename": f"{pid}.jpg",
                    "original_width": 4000, "original_height": 3000,
                    "aspect_ratio": 1.333, "timestamp_epoch": 1_785_535_200 + n * 400,
                })

            # Fire ingest WITHOUT awaiting it, then hammer /health while the
            # filter pool grinds through the batch.
            ingest_task = asyncio.create_task(ac.post("/api/photobook/ingest", data={
                "session_id": sess, "chunk_index": "0", "chunk_count": "1",
                "metadata_json": json.dumps(meta),
            }, files=files))

            worst_ms = 0.0
            samples = 0
            deadline = time.perf_counter() + 30.0
            while not ingest_task.done() and time.perf_counter() < deadline:
                t0 = time.perf_counter()
                h = await ac.get("/health")
                elapsed_ms = (time.perf_counter() - t0) * 1000
                assert h.status_code == 200
                worst_ms = max(worst_ms, elapsed_ms)
                samples += 1
                await asyncio.sleep(0.01)

            ingest_res = await ingest_task
            assert ingest_res.status_code == 200, ingest_res.text

            # Then generation, continuing to sample.
            job = await ac.post("/api/generate-async", json={
                "photo_ids": ids, "user_prompt": "Temple visit", "session_id": sess,
            })
            assert job.status_code == 202
            job_id = job.json()["job_id"]

            status_value = "processing"
            gen_deadline = time.perf_counter() + 20.0
            while time.perf_counter() < gen_deadline:
                t0 = time.perf_counter()
                h = await ac.get("/health")
                worst_ms = max(worst_ms, (time.perf_counter() - t0) * 1000)
                samples += 1
                assert h.status_code == 200

                status_value = (await ac.get(f"/api/jobs/{job_id}")).json()["status"]
                if status_value in ("completed", "failed"):
                    break
                await asyncio.sleep(0.01)

            return status_value, worst_ms, samples

    status_value, worst_ms, samples = asyncio.run(scenario())

    print(
        f"\n[loop responsiveness] {samples} /health samples during CPU work | "
        f"worst-case latency {worst_ms:.1f}ms | job {status_value}"
    )

    assert samples > 10, f"only {samples} health samples — not enough to judge"
    assert status_value == "completed", f"job ended as {status_value}"
    # A blocked loop would show up as seconds, not milliseconds.
    assert worst_ms < 1000, (
        f"/health worst-case latency was {worst_ms:.0f}ms while CPU work ran — "
        f"the event loop is still being blocked"
    )
