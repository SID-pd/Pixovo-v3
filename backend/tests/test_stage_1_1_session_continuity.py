"""
Stage 1.1 regression tests — session continuity & chunked ingest.

Guards the exit criteria in docs/plans/M1-1.1-session-continuity.md:
  * one stable session across many ingest chunks
  * chunk replay is idempotent (no duplicate photos, no inflated counters)
  * sessions are isolated from each other
  * originals are session-scoped and cross-session writes are refused
  * a completed job round-trips through SQLite

These are the behaviours that, when broken, silently produce a photobook from
the wrong photos. Keep them green.
"""

import json

import pytest

from tests.conftest import make_thumbnail_bytes


def build_chunk_request(session_id, photo_ids, chunk_index, chunk_count):
    """Assembles the multipart body for one /api/photobook/ingest chunk."""
    files, meta = [], []
    for n, pid in enumerate(photo_ids):
        files.append((
            "thumbnails",
            (f"{pid}_thumb.jpg", make_thumbnail_bytes(abs(hash(pid)) % 10_000 + n), "image/jpeg"),
        ))
        meta.append({
            "photo_id": pid,
            "filename": f"{pid}.jpg",
            "original_width": 4000,
            "original_height": 3000,
            "aspect_ratio": 1.333,
            "orientation": "LANDSCAPE",
            "timestamp": "2026-08-01T10:00:00.000Z",
            "timestamp_epoch": 1_785_535_200 + n * 60,
            "original_size_bytes": 8_000_000,
            "thumbnail_size_bytes": 30_000,
        })
    data = {
        "session_id": session_id,
        "chunk_index": str(chunk_index),
        "chunk_count": str(chunk_count),
        "metadata_json": json.dumps(meta),
    }
    return data, files


def ingest(client, session_id, photo_ids, chunk_index=0, chunk_count=1):
    data, files = build_chunk_request(session_id, photo_ids, chunk_index, chunk_count)
    return client.post("/api/photobook/ingest", data=data, files=files)


@pytest.fixture
def session(client):
    res = client.post("/api/sessions", json={"expected_photo_count": 6})
    assert res.status_code == 201
    return res.json()["session_id"]


# ---------------------------------------------------------------- creation


def test_create_session_returns_unguessable_token(client):
    res = client.post("/api/sessions", json={"expected_photo_count": 10})
    assert res.status_code == 201
    body = res.json()
    # 8 hex chars was brute-forceable; a token_urlsafe(24) is not.
    assert len(body["session_id"]) > 20
    assert isinstance(body["chunk_size"], int)


def test_oversized_session_rejected_before_any_upload(client):
    res = client.post("/api/sessions", json={"expected_photo_count": 5000})
    assert res.status_code == 413


def test_ingest_requires_known_session(client):
    res = ingest(client, "sess_does_not_exist", ["px_x1"])
    assert res.status_code == 404


# ------------------------------------------------------------- continuity


def test_multiple_chunks_land_under_one_session(client, store, session):
    r1 = ingest(client, session, ["px_c1a", "px_c1b", "px_c1c"], 0, 2)
    r2 = ingest(client, session, ["px_c2a", "px_c2b", "px_c2c"], 1, 2)
    assert r1.status_code == 200 and r2.status_code == 200

    d1, d2 = r1.json(), r2.json()
    assert d1["session_id"] == d2["session_id"] == session
    assert d1["is_final_chunk"] is False
    assert d2["is_final_chunk"] is True
    assert d2["session_received"] == 6

    photos = store.get_session_photos(session)
    assert len(photos) == 6
    assert store.get_session(session)["status"] == "ready"


def test_ingest_does_not_claim_originals_are_synced(client, store, session):
    """
    Ingest no longer receives originals, so it must not mark them synced.
    Claiming original_synced=True here is what let mark_original_synced()
    overwrite an already-reported URL and break PDF export.
    """
    ingest(client, session, ["px_syn1", "px_syn2"], 0, 1)
    photos = store.get_session_photos(session)
    assert photos
    assert all(p.original_synced is False for p in photos)
    assert all(p.original_url is None for p in photos)


def test_chunk_replay_is_idempotent(client, store, session):
    ids = ["px_rep1", "px_rep2", "px_rep3"]
    ingest(client, session, ids, 0, 2)
    before = len(store.get_session_photos(session))

    replay = ingest(client, session, ids, 0, 2)
    after = len(store.get_session_photos(session))

    assert replay.status_code == 200
    assert before == after, "replayed chunk duplicated photos"
    assert replay.json()["session_survived"] == after, "counters inflated on replay"


def test_get_session_rehydrates_photos(client, store, session):
    ingest(client, session, ["px_reh1", "px_reh2"], 0, 1)
    res = client.get(f"/api/sessions/{session}")
    assert res.status_code == 200
    assert len(res.json()["photos"]) == len(store.get_session_photos(session))


def test_get_unknown_session_is_404(client):
    assert client.get("/api/sessions/sess_nope").status_code == 404


# -------------------------------------------------------------- isolation


def test_sessions_hold_disjoint_photo_sets(client, store):
    s1 = client.post("/api/sessions", json={"expected_photo_count": 2}).json()["session_id"]
    s2 = client.post("/api/sessions", json={"expected_photo_count": 2}).json()["session_id"]
    ingest(client, s1, ["px_iso1a", "px_iso1b"], 0, 1)
    ingest(client, s2, ["px_iso2a", "px_iso2b"], 0, 1)

    p1 = {p.id for p in store.get_session_photos(s1)}
    p2 = {p.id for p in store.get_session_photos(s2)}
    assert p1 and p2
    assert p1.isdisjoint(p2)


# ---------------------------------------------------------------- originals


def test_original_upload_is_session_scoped(client, store, session):
    ingest(client, session, ["px_orig1"], 0, 1)
    res = client.post(
        f"/api/upload-originals?session_id={session}&photo_id=px_orig1",
        files={"file": ("orig.jpg", make_thumbnail_bytes(3), "image/jpeg")},
    )
    assert res.status_code == 200
    # Must agree with the path ingest builds, or PDF export cannot find the file.
    assert f"/uploads/originals/{session}/" in res.json()["original_url"]
    assert store.get_photo("px_orig1").original_synced is True


def test_cross_session_original_upload_is_refused(client, session):
    ingest(client, session, ["px_victim"], 0, 1)
    other = client.post("/api/sessions", json={"expected_photo_count": 1}).json()["session_id"]

    res = client.post(
        f"/api/upload-originals?session_id={other}&photo_id=px_victim",
        files={"file": ("orig.jpg", make_thumbnail_bytes(4), "image/jpeg")},
    )
    assert res.status_code == 403


def test_original_upload_for_unknown_photo_is_404(client, session):
    res = client.post(
        f"/api/upload-originals?session_id={session}&photo_id=px_never_ingested",
        files={"file": ("orig.jpg", make_thumbnail_bytes(5), "image/jpeg")},
    )
    assert res.status_code == 404


# --------------------------------------------------------------------- jobs


def test_completed_job_round_trips_through_sqlite(store, session):
    """
    get_job() referenced GenerateVariationsResponse without importing it, so
    recovering any completed job from SQLite raised NameError.

    The variation list must be non-empty: save_job() only writes
    `variations_json` when `job.result.variations` is truthy, and the NameError
    only fired on the read path when that column was non-null. An empty-variation
    fixture would pass without exercising the bug at all.
    """
    from app.schemas.photobook import (
        GenerateVariationsResponse,
        JobStatusResponse,
        PhotobookVariation,
    )

    variation = PhotobookVariation(
        id="var_1",
        variation_title="Warm Style 1",
        theme_name="Warm",
        cover_title="TEST BOOK",
        cover_subtitle="2026",
        cover_image_url="/uploads/thumbnails/x/px_1_thumb.jpg",
        base_color="#FAF9F6",
        accent_color="#D4A373",
        text_color="#1F2937",
        spreads=[],
    )
    job = JobStatusResponse(
        job_id="job_roundtrip",
        status="completed",
        progress=100,
        message="done",
        result=GenerateVariationsResponse(theme_name="Warm", variations=[variation]),
    )
    store.save_job(job, session_id=session)

    loaded = store.get_job("job_roundtrip")
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.result is not None, "result column was not persisted"
    assert loaded.result.theme_name == "Warm"
    assert len(loaded.result.variations) == 1
    assert loaded.result.variations[0].id == "var_1"
