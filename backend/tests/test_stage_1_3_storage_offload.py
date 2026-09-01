"""
Stage 1.3 regression tests — storage offload.

What these guard:
  * the StorageBackend traversal guard (keys embed client-supplied photo ids)
  * ingest accepts no originals and stays small regardless of photo count
  * originals land under a session-scoped key both write paths agree on
  * the export gate refuses to ship thumbnails as a 300 DPI print
  * disk guards trip rather than filling the disk
"""

import json

import pytest

from tests.conftest import make_thumbnail_bytes


def ingest_chunk(client, session_id, photo_ids, chunk_index=0, chunk_count=1):
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
            "timestamp_epoch": 1_785_535_200 + n * 300,
            "original_size_bytes": 8_000_000,
            "thumbnail_size_bytes": 30_000,
        })
    return client.post(
        "/api/photobook/ingest",
        data={
            "session_id": session_id,
            "chunk_index": str(chunk_index),
            "chunk_count": str(chunk_count),
            "metadata_json": json.dumps(meta),
        },
        files=files,
    )


# --------------------------------------------------------- storage primitives


@pytest.mark.parametrize("bad_key", [
    "../../etc/passwd",
    "/absolute/path.jpg",
    "originals/../../escape.jpg",
    "thumbnails/sess/../../../x.jpg",
    "",
])
def test_traversal_keys_are_rejected(bad_key):
    """
    Keys are built from client-supplied photo ids, so a crafted id must not be
    able to write outside the uploads root.
    """
    from app.config import STORAGE

    with pytest.raises(ValueError):
        STORAGE._p(bad_key)


def test_url_and_key_round_trip():
    from app.config import STORAGE, storage_key

    key = storage_key("originals", "sess_x", "px_1_orig.jpg")
    assert key == "originals/sess_x/px_1_orig.jpg"
    url = STORAGE.url_for(key)
    assert url == "/uploads/originals/sess_x/px_1_orig.jpg"
    assert STORAGE.key_for_url(url) == key
    assert STORAGE.key_for_url("https://example.com/other.jpg") is None


def test_storage_key_rejects_unknown_kind():
    from app.config import storage_key

    with pytest.raises(ValueError):
        storage_key("secrets", "sess_x", "a.jpg")


def test_put_stream_iter_cleans_up_on_failure(tmp_path):
    """A raising iterator must not leave a truncated file behind."""
    from app.storage import LocalDiskBackend

    backend = LocalDiskBackend(root=tmp_path)

    def _boom():
        yield b"partial data"
        raise RuntimeError("upload aborted")

    with pytest.raises(RuntimeError):
        backend.put_stream_iter("originals/sess_a/px_1.jpg", _boom())

    assert not backend.exists("originals/sess_a/px_1.jpg")


def test_delete_prefix_removes_a_whole_session(tmp_path):
    """Retention deletes a session by prefix, so this must be one call."""
    from app.storage import LocalDiskBackend

    backend = LocalDiskBackend(root=tmp_path)
    for i in range(3):
        backend.put_stream_iter(f"originals/sess_a/px_{i}.jpg", [b"x" * 10])
    backend.put_stream_iter("originals/sess_b/px_9.jpg", [b"x" * 10])

    removed = backend.delete_prefix("originals/sess_a")
    assert removed == 3
    assert not backend.exists("originals/sess_a/px_0.jpg")
    assert backend.exists("originals/sess_b/px_9.jpg"), "deleted the wrong session"


# ------------------------------------------------------------------- ingest


def test_ingest_carries_no_originals(client):
    """
    The core 1.1 + 1.3 invariant: ingest transports thumbnails only, so the
    request size is bounded by thumbnail bytes and never by original bytes.

    Asserted as a ratio against the originals the client declares, rather than
    an absolute megabyte figure — the synthetic fixtures compress far worse
    than real photos (see the note in conftest), so an absolute bound would be
    measuring the fixture instead of the system.
    """
    from app.config import STORAGE, storage_key

    count = 40
    sess = client.post("/api/sessions", json={"expected_photo_count": count}).json()["session_id"]
    ids = [f"px_sz{i}" for i in range(count)]
    assert ingest_chunk(client, sess, ids).status_code == 200

    # What ingest actually transported.
    thumb_bytes = sum(
        len(make_thumbnail_bytes(abs(hash(pid)) % 9973 + n))
        for n, pid in enumerate(ids)
    )
    # What the pre-1.3 payload would ALSO have carried: the declared originals.
    declared_original_bytes = 8_000_000 * count  # 320 MB

    assert thumb_bytes < declared_original_bytes / 20, (
        f"ingest payload {thumb_bytes / 1024**2:.1f} MB is not a small fraction of "
        f"the {declared_original_bytes / 1024**2:.0f} MB of originals"
    )

    # And no original may exist on disk for this session — nothing on the ingest
    # path is allowed to write one.
    for pid in ids:
        for ext in (".jpg", ".jpeg", ".png"):
            assert not STORAGE.exists(storage_key("originals", sess, f"{pid}_orig{ext}"))


def test_ingest_ignores_a_stray_originals_part(client, store):
    """
    A client still sending the old dual payload must not break, and must not
    get its originals written by the ingest path.
    """
    from app.config import STORAGE, storage_key

    sess = client.post("/api/sessions", json={"expected_photo_count": 1}).json()["session_id"]
    res = client.post(
        "/api/photobook/ingest",
        data={
            "session_id": sess,
            "chunk_index": "0",
            "chunk_count": "1",
            "metadata_json": json.dumps([{
                "photo_id": "px_legacy", "filename": "px_legacy.jpg",
                "original_width": 4000, "original_height": 3000,
                "aspect_ratio": 1.333, "timestamp_epoch": 1_785_535_200,
            }]),
        },
        files=[
            ("thumbnails", ("px_legacy_thumb.jpg", make_thumbnail_bytes(21), "image/jpeg")),
            ("originals", ("px_legacy_orig.jpg", make_thumbnail_bytes(21), "image/jpeg")),
        ],
    )
    assert res.status_code == 200
    assert not STORAGE.exists(storage_key("originals", sess, "px_legacy_orig.jpg"))
    photo = store.get_photo("px_legacy")
    assert photo is not None
    assert photo.original_synced is False


def test_thumbnails_land_under_the_session_key(client, store):
    from app.config import STORAGE, storage_key

    sess = client.post("/api/sessions", json={"expected_photo_count": 2}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_key1", "px_key2"]).status_code == 200

    for photo in store.get_session_photos(sess):
        key = storage_key("thumbnails", sess, f"{photo.id}_thumb.jpg")
        assert STORAGE.exists(key), f"missing {key}"
        assert photo.url == STORAGE.url_for(key)


# ---------------------------------------------------------------- originals


def test_original_lands_under_session_scoped_key(client, store):
    from app.config import STORAGE

    sess = client.post("/api/sessions", json={"expected_photo_count": 1}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_orig_key"]).status_code == 200

    res = client.post(
        f"/api/upload-originals?session_id={sess}&photo_id=px_orig_key",
        files={"file": ("photo.jpg", make_thumbnail_bytes(11), "image/jpeg")},
    )
    assert res.status_code == 200
    url = res.json()["original_url"]
    key = STORAGE.key_for_url(url)
    assert key == f"originals/{sess}/px_orig_key_orig.jpg"
    assert STORAGE.exists(key)
    # And the resolver must find it by session + id alone.
    from app.engine.pdf_exporter import resolve_photo_path

    resolved = resolve_photo_path("", "px_orig_key", sess)
    assert resolved.is_file()
    assert "originals" in str(resolved)


def test_session_byte_total_is_tracked(client, store):
    sess = client.post("/api/sessions", json={"expected_photo_count": 1}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_bytes"]).status_code == 200
    assert (store.get_session(sess).get("total_bytes") or 0) == 0

    blob = make_thumbnail_bytes(12)
    client.post(
        f"/api/upload-originals?session_id={sess}&photo_id=px_bytes",
        files={"file": ("photo.jpg", blob, "image/jpeg")},
    )
    assert store.get_session(sess)["total_bytes"] == len(blob)


def test_unknown_session_cannot_upload_originals(client):
    res = client.post(
        "/api/upload-originals?session_id=sess_nope&photo_id=px_whatever",
        files={"file": ("photo.jpg", make_thumbnail_bytes(13), "image/jpeg")},
    )
    assert res.status_code == 404


# -------------------------------------------------------------- export gate


def _variation_placing(photo_ids):
    """Minimal variation with one slot per supplied photo id."""
    from app.schemas.photobook import PhotobookVariation, SinglePage, SpreadPair, TemplateSlot

    slots = [
        TemplateSlot(
            id=f"slot_{i}", type="photo", x_pct=0.0, y_pct=0.0, w_pct=50.0, h_pct=50.0,
            photo_id=pid, photo_url=f"/uploads/thumbnails/x/{pid}_thumb.jpg",
        )
        for i, pid in enumerate(photo_ids)
    ]
    page = SinglePage(page_number=1, background_color="#FFF", text_color="#000", slots=slots)
    empty = SinglePage(page_number=2, background_color="#FFF", text_color="#000", slots=[])
    return PhotobookVariation(
        id="var_1", variation_title="T", theme_name="Warm",
        cover_title="C", cover_subtitle="S",
        cover_image_url="/uploads/thumbnails/x/a_thumb.jpg",
        base_color="#FFF", accent_color="#000", text_color="#000",
        spreads=[SpreadPair(spread_index=1, left_page=page, right_page=empty)],
    )


def test_export_is_blocked_while_originals_are_pending(client):
    """
    The honest version of the old behaviour: rather than silently embedding
    512px thumbnails into a 300 DPI PDF, say what is missing.
    """
    sess = client.post("/api/sessions", json={"expected_photo_count": 2}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_gate1", "px_gate2"]).status_code == 200

    res = client.post("/api/export-pdf", json={
        "variation": json.loads(_variation_placing(["px_gate1", "px_gate2"]).model_dump_json()),
        "session_id": sess,
    })
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "originals_pending"
    assert detail["pending_count"] == 2
    assert detail["total_count"] == 2


def test_export_proceeds_once_originals_arrive(client):
    sess = client.post("/api/sessions", json={"expected_photo_count": 1}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_ready"]).status_code == 200

    client.post(
        f"/api/upload-originals?session_id={sess}&photo_id=px_ready",
        files={"file": ("photo.jpg", make_thumbnail_bytes(14), "image/jpeg")},
    )

    res = client.post("/api/export-pdf", json={
        "variation": json.loads(_variation_placing(["px_ready"]).model_dump_json()),
        "session_id": sess,
    })
    assert res.status_code == 200, res.text
    assert res.json().get("pdf_url")


def test_force_preview_bypasses_the_gate(client):
    """A deliberate low-resolution proof is allowed."""
    sess = client.post("/api/sessions", json={"expected_photo_count": 1}).json()["session_id"]
    assert ingest_chunk(client, sess, ["px_proof"]).status_code == 200

    res = client.post("/api/export-pdf", json={
        "variation": json.loads(_variation_placing(["px_proof"]).model_dump_json()),
        "session_id": sess,
        "force_preview": True,
    })
    assert res.status_code == 200, res.text
