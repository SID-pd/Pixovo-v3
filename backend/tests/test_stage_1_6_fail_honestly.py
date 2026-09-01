"""
Stage 1.6 regression tests — fail honestly.

Every placeholder path is gone. A missing photo must now produce a clear error,
never a plausible-looking book made of stock images that a user could pay to
print.

Sites removed:
  * process_async_job's four `sample_N` photos          (Stage 1.4)
  * solver.py `photos[0] or sample_placeholder` cover   (Stage 1.5)
  * /api/variations/reshuffle reading the global cache  (here)
  * pdf_exporter's unresolvable-photo placeholder       (here)
  * config.ensure_sample_placeholders() at import time  (here)
"""

import inspect
import json
from pathlib import Path

import pytest

from tests.conftest import make_thumbnail_bytes


def ingest_chunk(client, session_id, photo_ids):
    files, meta = [], []
    for n, pid in enumerate(photo_ids):
        files.append((
            "thumbnails",
            (f"{pid}_thumb.jpg", make_thumbnail_bytes(abs(hash(pid)) % 9973 + n), "image/jpeg"),
        ))
        meta.append({
            "photo_id": pid, "filename": f"{pid}.jpg",
            "original_width": 4000, "original_height": 3000,
            "aspect_ratio": 1.333, "timestamp_epoch": 1_785_535_200 + n * 400,
        })
    return client.post("/api/photobook/ingest", data={
        "session_id": session_id, "chunk_index": "0", "chunk_count": "1",
        "metadata_json": json.dumps(meta),
    }, files=files)


# ------------------------------------------------------- placeholder removal


def test_config_no_longer_writes_placeholders_on_import():
    """
    ensure_sample_placeholders() wrote 15 JPEGs on every process start and every
    test collection — an import-time side effect backing fallbacks that are gone.
    """
    import app.config as config_mod

    assert not hasattr(config_mod, "ensure_sample_placeholders")

    for name in ("sample1.jpg", "sample2.jpg", "sample_placeholder.jpg"):
        assert not (config_mod.UPLOADS_DIR / name).exists(), (
            f"{name} was regenerated on import"
        )


def test_pdf_resolver_raises_instead_of_returning_a_placeholder():
    """
    Returning sample_placeholder.jpg meant an unresolvable photo was silently
    swapped for a stock image inside a print-resolution PDF.
    """
    from app.engine.pdf_exporter import resolve_photo_path

    with pytest.raises(FileNotFoundError) as exc:
        resolve_photo_path("/uploads/thumbnails/sess_nope/px_ghost_thumb.jpg", "px_ghost", "sess_nope")

    assert "px_ghost" in str(exc.value)


def test_solver_produces_no_placeholder_output():
    """
    Asserted behaviourally rather than by grepping source: the explanatory
    comments name the removed literals, so a substring check matches the comment
    that documents the fix.
    """
    from app.engine.solver import generate_photobook_variations_engine
    from app.schemas.photobook import PhotoMeta

    photos = [
        PhotoMeta(
            id=f"px_ph{i}", filename=f"px_ph{i}.jpg",
            url=f"/uploads/thumbnails/sess_t/px_ph{i}_thumb.jpg",
            thumbnail_url=f"/uploads/thumbnails/sess_t/px_ph{i}_thumb.jpg",
            aspect_ratio=1.5, hero_score=80.0 - i,
            shell_phash=f"{i:016x}", core_phash=f"{i + 99:016x}",
            timestamp_epoch=1_785_535_200 + i * 600,
        )
        for i in range(10)
    ]

    # Empty AI batch forces every default path to be exercised.
    for var in generate_photobook_variations_engine(photos, {"variations": []}):
        assert "sample" not in var.cover_image_url.lower()
        assert "KANPUR" not in var.cover_title.upper()
        for cp in var.cover_photos:
            assert not cp.photo_id.startswith("sample")
            assert "sample" not in cp.url.lower()
        for spread in var.spreads:
            for page in (spread.left_page, spread.right_page):
                for slot in page.slots:
                    if slot.photo_url:
                        assert "sample" not in slot.photo_url.lower()


# --------------------------------------------------- reshuffle session scoping


def test_reshuffle_requires_a_session(client):
    """
    It used to read `list(PHOTO_STORE.values())` — the entire process-wide cache
    — so at 20 concurrent users a reshuffle could pull another user's photos into
    your book.
    """
    sess = client.post("/api/sessions", json={"expected_photo_count": 8}).json()["session_id"]
    assert ingest_chunk(client, sess, [f"px_rs{i}" for i in range(8)]).status_code == 200

    job = client.post("/api/generate-async", json={
        "photo_ids": [f"px_rs{i}" for i in range(8)],
        "user_prompt": "Trip", "session_id": sess,
    })
    job_id = job.json()["job_id"]

    # Wait for completion.
    import time
    for _ in range(50):
        if client.get(f"/api/jobs/{job_id}").json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    # Missing session_id must be refused, not silently served from the cache.
    res = client.post("/api/variations/reshuffle", json={
        "job_id": job_id, "seed_offset": 1,
    })
    assert res.status_code == 400
    assert "session_id" in res.json()["detail"].lower()


def test_reshuffle_only_uses_its_own_session_photos(client, store):
    """Two sessions; reshuffling one must never place the other's photos."""
    import time

    s1 = client.post("/api/sessions", json={"expected_photo_count": 8}).json()["session_id"]
    s2 = client.post("/api/sessions", json={"expected_photo_count": 8}).json()["session_id"]
    ids1 = [f"px_own{i}" for i in range(8)]
    ids2 = [f"px_other{i}" for i in range(8)]
    assert ingest_chunk(client, s1, ids1).status_code == 200
    assert ingest_chunk(client, s2, ids2).status_code == 200

    job = client.post("/api/generate-async", json={
        "photo_ids": ids1, "user_prompt": "Trip", "session_id": s1,
    })
    job_id = job.json()["job_id"]
    for _ in range(50):
        if client.get(f"/api/jobs/{job_id}").json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    res = client.post("/api/variations/reshuffle", json={
        "job_id": job_id, "seed_offset": 2, "session_id": s1,
    })
    assert res.status_code == 200, res.text

    placed = set()
    for var in res.json()["variations"]:
        for cp in var.get("cover_photos", []):
            placed.add(cp["photo_id"])
        for spread in var["spreads"]:
            for page in (spread["left_page"], spread["right_page"]):
                for slot in page["slots"]:
                    if slot.get("photo_id"):
                        placed.add(slot["photo_id"])

    leaked = placed & set(ids2)
    assert not leaked, f"reshuffle leaked photos from another session: {leaked}"
    assert placed <= set(ids1), f"unexpected photo ids: {placed - set(ids1)}"


def test_reshuffle_with_no_session_photos_is_refused(client):
    """An empty session must 409, not produce a placeholder book."""
    import time

    sess = client.post("/api/sessions", json={"expected_photo_count": 4}).json()["session_id"]
    ids = [f"px_rsx{i}" for i in range(4)]
    assert ingest_chunk(client, sess, ids).status_code == 200

    job = client.post("/api/generate-async", json={
        "photo_ids": ids, "user_prompt": "Trip", "session_id": sess,
    })
    job_id = job.json()["job_id"]
    for _ in range(50):
        if client.get(f"/api/jobs/{job_id}").json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    empty = client.post("/api/sessions", json={"expected_photo_count": 0}).json()["session_id"]
    res = client.post("/api/variations/reshuffle", json={
        "job_id": job_id, "seed_offset": 1, "session_id": empty,
    })
    assert res.status_code == 409
