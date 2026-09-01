"""
Stage 1.2 regression tests — the filter engine -> PhotoMeta -> solver contract.

The bug these guard: `process_single_photo()` computes hero_score, shell/core
pHash, capture time, GPS, blur, face count and dominant colours, but ingest
built PhotoMeta from only id/filename/urls/dimensions. Four consumers were
therefore running on defaults:

  * cluster_photos_2tier_engine  -> empty pHash  -> distance 99 -> array order
  * partition_macro_chapters     -> no timestamp -> fixed-size chunks
  * cover selection              -> hero_score 0 -> photos[0] (the dummy cover)
  * colour theming               -> two hardcoded hex values for every photo

These tests assert the *downstream behaviour*, not just the field values,
because field values that nothing reads are worth nothing.
"""

import json

import pytest

from tests.conftest import make_thumbnail_bytes


# Distinct-looking images so pHash, colours and hero scores genuinely differ.
def _varied_thumbnail(seed):
    return make_thumbnail_bytes(seed * 7919)


def ingest_photos(client, session_id, specs, chunk_index=0, chunk_count=1):
    """
    specs: list of dicts with keys photo_id, seed, and optionally
    timestamp_epoch / latitude / longitude.
    """
    files, meta = [], []
    for spec in specs:
        pid = spec["photo_id"]
        files.append((
            "thumbnails",
            (f"{pid}_thumb.jpg", _varied_thumbnail(spec["seed"]), "image/jpeg"),
        ))
        meta.append({
            "photo_id": pid,
            "filename": f"{pid}.jpg",
            "original_width": spec.get("width", 4000),
            "original_height": spec.get("height", 3000),
            "aspect_ratio": spec.get("aspect_ratio", 1.333),
            "orientation": "LANDSCAPE",
            "timestamp": "2026-08-01T10:00:00.000Z",
            "timestamp_epoch": spec.get("timestamp_epoch", 1_785_535_200),
            "original_size_bytes": 8_000_000,
            "thumbnail_size_bytes": 30_000,
        })
    data = {
        "session_id": session_id,
        "chunk_index": str(chunk_index),
        "chunk_count": str(chunk_count),
        "metadata_json": json.dumps(meta),
    }
    return client.post("/api/photobook/ingest", data=data, files=files)


@pytest.fixture
def populated_session(client, store):
    """A session of 8 visually distinct photos with staggered capture times."""
    sess = client.post("/api/sessions", json={"expected_photo_count": 8}).json()["session_id"]
    base = 1_785_535_200
    specs = [
        {"photo_id": f"px_dc{i}", "seed": i + 1, "timestamp_epoch": base + i * 300}
        for i in range(8)
    ]
    res = ingest_photos(client, sess, specs)
    assert res.status_code == 200, res.text
    photos = store.get_session_photos(sess)
    assert photos, "no photos survived filtering — fixture images are not passing the quality gate"
    return sess, photos


# ------------------------------------------------------- field-level contract


def test_phashes_are_populated(populated_session):
    _, photos = populated_session
    missing = [p.id for p in photos if not p.shell_phash or not p.core_phash]
    assert not missing, f"photos with empty pHash: {missing}"


def test_hero_scores_are_real_and_vary(populated_session):
    _, photos = populated_session
    scores = [p.hero_score for p in photos]
    assert max(scores) > 0, "every hero_score is 0 — cover selection cannot rank"
    # `score` is the normalised mirror and must agree.
    for p in photos:
        assert p.score == pytest.approx((p.hero_score or 0.0) / 100.0, abs=1e-3)


def test_layout_role_is_assigned(populated_session):
    """Roles come from compute_hero_score()'s three-way split."""
    _, photos = populated_session
    valid = {"DOUBLE_PAGE_HERO", "FULL_PAGE_HERO", "STANDARD_FRAME"}
    roles = {p.layout_role for p in photos}
    assert roles <= valid, f"unexpected layout_role values: {roles - valid}"


def test_capture_time_is_carried_through(populated_session):
    _, photos = populated_session
    stamps = [p.timestamp_epoch for p in photos]
    assert all(s and s > 0 for s in stamps), "timestamp_epoch not populated"
    assert len(set(stamps)) > 1, "all photos share one timestamp — chaptering cannot split on time"


def test_dominant_colours_are_per_photo(populated_session):
    _, photos = populated_session
    palettes = {tuple(p.dominant_colors) for p in photos}
    assert len(palettes) > 1, f"all photos share one palette: {palettes}"
    for p in photos:
        assert len(p.dominant_colors) == 3
        for hex_colour in p.dominant_colors:
            assert hex_colour.startswith("#") and len(hex_colour) == 7
    # The old hardcoded pair must be gone.
    assert ("#2C3E50", "#ECF0F1") not in {tuple(x[:2]) for x in palettes}


def test_quality_signals_are_carried_through(populated_session):
    _, photos = populated_session
    assert any(p.blur_score for p in photos)
    assert any(p.contrast_score for p in photos)
    assert all(p.tenengrad_score is not None for p in photos)


# ------------------------------------------------------------- persistence


def test_all_fields_survive_a_database_round_trip(populated_session, store):
    """Guards against updating some read paths and not others."""
    _, photos = populated_session
    probe = photos[0]

    via_get_photo = store.get_photo(probe.id)
    via_get_photos = store.get_photos([probe.id])[0]

    for field in (
        "timestamp_epoch", "latitude", "longitude", "hero_score", "layout_role",
        "is_event_cover_hero", "tenengrad_score", "contrast_score",
        "shell_phash", "core_phash", "dominant_colors", "score", "blur_score",
        "face_count",
    ):
        expected = getattr(probe, field)
        assert getattr(via_get_photo, field) == expected, f"get_photo lost {field}"
        assert getattr(via_get_photos, field) == expected, f"get_photos lost {field}"


def test_session_photos_are_returned_in_capture_order(client, store):
    """
    Chunked ingest means insertion order is upload order. Chaptering needs
    chronological order, so get_session_photos must sort on capture time.
    """
    sess = client.post("/api/sessions", json={"expected_photo_count": 4}).json()["session_id"]
    base = 1_785_535_200
    # Deliberately ingest newest-first.
    specs = [
        {"photo_id": "px_ord_d", "seed": 41, "timestamp_epoch": base + 3000},
        {"photo_id": "px_ord_c", "seed": 42, "timestamp_epoch": base + 2000},
        {"photo_id": "px_ord_b", "seed": 43, "timestamp_epoch": base + 1000},
        {"photo_id": "px_ord_a", "seed": 44, "timestamp_epoch": base},
    ]
    assert ingest_photos(client, sess, specs).status_code == 200

    photos = store.get_session_photos(sess)
    stamps = [p.timestamp_epoch for p in photos]
    assert stamps == sorted(stamps), f"not chronological: {stamps}"


def test_migration_is_idempotent():
    """init_db() runs on every import and must not fail on an existing table."""
    from app.db.session_store import get_db_connection, _ensure_photo_columns

    # Do NOT close this connection: since Stage 1.4 it is thread-local and
    # reused, so closing it would break every subsequent caller on this thread.
    conn = get_db_connection()
    with conn:
        _ensure_photo_columns(conn)
        _ensure_photo_columns(conn)  # second pass must be a no-op
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(photos)")}
    assert {"timestamp_epoch", "hero_score", "layout_role", "contrast_score"} <= cols


# ------------------------------------------------- downstream consumer wiring


def test_clustering_receives_usable_phashes(populated_session):
    """
    compute_phash_distance returns 99 when either hash is empty. If the contract
    is intact, real photo pairs must produce distances below that sentinel.
    """
    from app.engine.dsa_solver import compute_phash_distance

    _, photos = populated_session
    distances = [
        compute_phash_distance(a.shell_phash, b.shell_phash)
        for i, a in enumerate(photos)
        for b in photos[i + 1:]
    ]
    assert distances
    assert any(d < 99 for d in distances), "every pHash distance is the 99 no-data sentinel"


def test_chaptering_splits_on_a_real_time_gap(client, store):
    """
    Three tight clusters separated by 2-hour gaps must produce 3 chapters.
    With timestamp_epoch missing this returned ceil(n / max_per_chapter) instead.
    """
    from app.engine.story_ai import partition_macro_chapters

    sess = client.post("/api/sessions", json={"expected_photo_count": 9}).json()["session_id"]
    base = 1_785_535_200
    two_hours = 7200
    specs = []
    seed = 100
    for cluster in range(3):
        for n in range(3):
            seed += 1
            specs.append({
                "photo_id": f"px_ch{cluster}_{n}",
                "seed": seed,
                "timestamp_epoch": base + cluster * two_hours + n * 60,
            })
    assert ingest_photos(client, sess, specs).status_code == 200

    photos = store.get_session_photos(sess)
    assert len(photos) >= 6, "too few survivors to test chaptering"

    chapters = partition_macro_chapters(photos)
    assert len(chapters) == 3, (
        f"expected 3 chapters from 3 time-separated clusters, got {len(chapters)} "
        f"with sizes {[len(c['photos']) for c in chapters]}"
    )


def test_chaptering_does_not_split_a_tight_cluster(client, store):
    """The inverse: photos minutes apart must stay in one chapter."""
    from app.engine.story_ai import partition_macro_chapters

    sess = client.post("/api/sessions", json={"expected_photo_count": 6}).json()["session_id"]
    base = 1_785_535_200
    specs = [
        {"photo_id": f"px_tight{i}", "seed": 200 + i, "timestamp_epoch": base + i * 120}
        for i in range(6)
    ]
    assert ingest_photos(client, sess, specs).status_code == 200

    photos = store.get_session_photos(sess)
    chapters = partition_macro_chapters(photos)
    assert len(chapters) == 1, f"tight cluster was split into {len(chapters)} chapters"


def test_generation_end_to_end_uses_restored_signals(client, store, populated_session):
    """
    Full path still works and the solver sees the restored fields. Cover quality
    itself is Stage 1.5; here we only assert the pipeline runs and produces
    three variations from real photos.
    """
    from app.engine.solver import generate_photobook_variations_engine
    from app.engine.story_ai import generate_story_theme_batch

    _, photos = populated_session
    ai_batch = generate_story_theme_batch("Family trip", total_photos=len(photos))
    variations = generate_photobook_variations_engine(photos, ai_batch)

    assert len(variations) == 3
    for var in variations:
        assert var.spreads, f"variation {var.id} has no spreads"
        # Stage 1.5 replaces this with hero-ranked, per-variation covers.
        assert var.cover_image_url
        assert "sample" not in var.cover_image_url.lower(), "cover fell back to a placeholder"
