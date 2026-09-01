"""
Stage 1.7 regression tests — the load-critical paths.

Fast unit-level guards on the things that break specifically at scale. The
20-user load run itself lives in tests/load/run_load_test.py and is not part of
the normal suite (it takes minutes).

Covered:
  * SQLite 999-bound-parameter chunking (the bug from commit 652e09e)
  * DSA slot invariants: no overlap, within bounds, real coverage
  * the synthetic corpus contract — every filter gate behaves as the manifest says
  * ingest scales linearly in chunk count, not quadratically
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.schemas.photobook import PhotoMeta
from tests.conftest import make_thumbnail_bytes


# --------------------------------------------------- SQLite parameter limits


def _meta(pid: str) -> PhotoMeta:
    return PhotoMeta(
        id=pid,
        filename=f"{pid}.jpg",
        url=f"/uploads/thumbnails/sess_bulk/{pid}_thumb.jpg",
        thumbnail_url=f"/uploads/thumbnails/sess_bulk/{pid}_thumb.jpg",
        aspect_ratio=1.5,
        hero_score=50.0,
        shell_phash="a1b2c3d4e5f60718",
        core_phash="0718f6e5d4c3b2a1",
        timestamp_epoch=1_785_535_200,
    )


def test_get_photos_chunks_past_the_999_parameter_limit(store):
    """
    SQLite caps bound parameters per statement (999 by default). A single
    `IN (...)` clause with 1,500 ids raises OperationalError — this is the exact
    failure that broke 1,000-photo sessions in commit 652e09e.
    """
    photos = [_meta(f"px_bulk{i:05d}") for i in range(1500)]
    store.save_photos_batch(photos, session_id="sess_bulk")

    ids = [p.id for p in photos]
    fetched = store.get_photos(ids)

    assert len(fetched) == 1500
    # Order must follow the requested id list, not database order.
    assert [p.id for p in fetched] == ids


def test_get_photos_preserves_order_and_skips_unknown(store):
    photos = [_meta(f"px_ord{i:04d}") for i in range(10)]
    store.save_photos_batch(photos, session_id="sess_ord")

    requested = ["px_ord0005", "px_ord0001", "px_missing", "px_ord0009"]
    fetched = store.get_photos(requested)
    assert [p.id for p in fetched] == ["px_ord0005", "px_ord0001", "px_ord0009"]


def test_batch_insert_of_1000_photos_is_one_transaction(store):
    """
    Naive per-photo INSERT is what originally broke at scale. This asserts the
    batch path completes in a time only achievable with a single transaction.
    """
    photos = [_meta(f"px_speed{i:05d}") for i in range(1000)]

    t0 = time.perf_counter()
    store.save_photos_batch(photos, session_id="sess_speed")
    elapsed = time.perf_counter() - t0

    assert len(store.get_session_photos("sess_speed")) == 1000
    # 1,000 separate transactions on spinning-rust-era SQLite would be seconds.
    assert elapsed < 5.0, f"batch insert of 1000 took {elapsed:.2f}s"


# ------------------------------------------------------- DSA slot invariants


def _photos_for_layout(n: int) -> list:
    return [
        PhotoMeta(
            id=f"px_lay{i}", filename=f"px_lay{i}.jpg",
            url=f"/uploads/thumbnails/s/px_lay{i}_thumb.jpg",
            thumbnail_url=f"/uploads/thumbnails/s/px_lay{i}_thumb.jpg",
            aspect_ratio=[1.5, 0.667, 1.0][i % 3],
            width=4000, height=3000,
            hero_score=70.0 - i,
            shell_phash=f"{i:016x}", core_phash=f"{i + 7:016x}",
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("photo_count", [1, 2, 3, 4])
@pytest.mark.parametrize("family", ["BALANCED", "DOMINANT", "ALTERNATING"])
def test_slots_never_overlap_and_stay_in_bounds(photo_count, family):
    """
    The differentiated IP. Overlapping or out-of-bounds slots mean photos printed
    on top of each other or bleeding off the page — and this is the invariant
    Stage 1.5's pacing change and any future 4-6 photo collage work could break.
    """
    from app.engine.dsa_solver import build_dsa_spread_pair

    spread = build_dsa_spread_pair(
        spread_idx=1,
        photos=_photos_for_layout(photo_count),
        caption="A CAPTION",
        theme_name="Warm",
        family_variant=family,
        family_variant_seed=3,
    )

    for page in (spread.left_page, spread.right_page):
        boxes = [
            (s.x_pct, s.y_pct, s.w_pct, s.h_pct)
            for s in page.slots
            if s.type == "photo"
        ]

        for x, y, w, h in boxes:
            assert w > 0 and h > 0, f"degenerate slot {w}x{h}"
            assert -0.01 <= x <= 100.01, f"x out of bounds: {x}"
            assert -0.01 <= y <= 100.01, f"y out of bounds: {y}"
            assert x + w <= 100.01, f"slot exceeds width: {x} + {w}"
            assert y + h <= 100.01, f"slot exceeds height: {y} + {h}"

        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                ax, ay, aw, ah = a
                bx, by, bw, bh = b
                # Allow a hairline touch; anything more is a real overlap.
                overlap_x = min(ax + aw, bx + bw) - max(ax, bx)
                overlap_y = min(ay + ah, by + bh) - max(ay, by)
                assert not (overlap_x > 0.5 and overlap_y > 0.5), (
                    f"slots overlap by {overlap_x:.2f}x{overlap_y:.2f}: {a} vs {b}"
                )


def test_dpi_badges_use_the_documented_thresholds():
    """excellent >= 250, warning 150-249, alert < 150."""
    from app.engine.dsa_solver import build_dsa_spread_pair

    # A tiny source photo in a large slot must be flagged.
    small = PhotoMeta(
        id="px_small", filename="px_small.jpg",
        url="/uploads/thumbnails/s/px_small_thumb.jpg",
        thumbnail_url="/uploads/thumbnails/s/px_small_thumb.jpg",
        width=300, height=200, aspect_ratio=1.5, hero_score=40.0,
    )
    spread = build_dsa_spread_pair(1, [small], "C", "Warm", "BALANCED", 0)

    badges = [
        s.dpi_quality
        for page in (spread.left_page, spread.right_page)
        for s in page.slots
        if s.type == "photo" and s.dpi_quality
    ]
    assert badges, "no DPI badge computed"
    assert all(b in ("excellent", "warning", "alert") for b in badges)
    assert "alert" in badges, f"a 300x200 photo should alert, got {badges}"


# ----------------------------------------------------------- corpus contract


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    from tests.fixtures.generate_corpus import generate_corpus

    out = tmp_path_factory.mktemp("corpus_contract")
    return generate_corpus(out, count=100, seed=42)


def test_corpus_sizes_are_photo_realistic(corpus):
    """
    Fixtures must compress like photographs. The first-pass noise fixtures came
    out at ~160 KB per 512px thumbnail versus ~30-40 KB for a real one, which
    silently invalidated every absolute size assertion built on them.
    """
    from tests.fixtures.generate_corpus import corpus_stats

    stats = corpus_stats(corpus)
    assert 15.0 <= stats["mean_kb"] <= 90.0, f"unrealistic mean size: {stats['mean_kb']} KB"
    assert stats["max_kb"] <= 150.0


def test_corpus_is_deterministic(tmp_path):
    from tests.fixtures.generate_corpus import generate_corpus

    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a = generate_corpus(a_dir, count=30, seed=7)
    b = generate_corpus(b_dir, count=30, seed=7)

    assert [x.kind for x in a] == [x.kind for x in b]
    for x, y in zip(a, b):
        assert Path(x.path).read_bytes() == Path(y.path).read_bytes(), (
            f"{x.photo_id} differs between runs — corpus is not reproducible"
        )


def test_corpus_contract_matches_filter_behaviour(corpus):
    """
    The corpus is only useful if the filters actually treat each kind as the
    manifest claims. If this drifts, every test built on the corpus is testing
    something other than what it says.
    """
    from collections import defaultdict

    from app.engine.filter.filter_engine import finalise_scanned_batch, scan_photo

    kind_by_file = {Path(m.path).name: m.kind for m in corpus}
    scanned = [scan_photo(m.path, f"p_{i}") for i, m in enumerate(corpus)]
    result = finalise_scanned_batch(scanned, total_uploaded=len(corpus))

    survived = {p["filename"] for p in result["survived_photos"]}
    tally = defaultdict(lambda: {"survived": 0, "total": 0})
    for p in result["all_scanned_photos"]:
        kind = kind_by_file.get(p["filename"], "?")
        tally[kind]["total"] += 1
        if p["filename"] in survived:
            tally[kind]["survived"] += 1

    # Must survive. `sharp` allows some burst-dedupe losses, which are correct.
    for kind, floor in (("sharp", 0.85), ("low_key", 1.0)):
        t = tally[kind]
        assert t["total"] > 0, f"corpus produced no {kind} photos"
        rate = t["survived"] / t["total"]
        assert rate >= floor, f"{kind}: only {t['survived']}/{t['total']} survived"

    # Must be rejected.
    for kind in ("blurry", "dark", "blown", "junk_qr", "corrupt"):
        t = tally[kind]
        assert t["total"] > 0, f"corpus produced no {kind} photos"
        assert t["survived"] == 0, f"{kind}: {t['survived']}/{t['total']} wrongly survived"

    # Burst duplicates: some survive by design, but not all.
    burst = tally["burst_dup"]
    assert burst["total"] > 0
    assert 0 < burst["survived"] < burst["total"], (
        f"burst dedupe did nothing: {burst['survived']}/{burst['total']} survived"
    )


# ------------------------------------------------------------- ingest scaling


def test_ingest_cost_scales_linearly_in_chunks(client):
    """
    Ingest must be O(n) in photos, not O(n^2). The original ID-resolution did a
    linear scan of the metadata list per survived photo; at 1,000 photos that
    compounds. Compares per-photo cost between a small and a larger chunk.
    """
    def ingest(n: int, tag: str) -> float:
        sess = client.post("/api/sessions", json={"expected_photo_count": n}).json()["session_id"]
        files, meta = [], []
        for i in range(n):
            pid = f"px_{tag}{i:04d}"
            files.append(("thumbnails", (f"{pid}_thumb.jpg", make_thumbnail_bytes(i), "image/jpeg")))
            meta.append({
                "photo_id": pid, "filename": f"{pid}.jpg",
                "original_width": 4000, "original_height": 3000,
                "aspect_ratio": 1.333, "timestamp_epoch": 1_785_535_200 + i * 60,
            })
        t0 = time.perf_counter()
        res = client.post("/api/photobook/ingest", data={
            "session_id": sess, "chunk_index": "0", "chunk_count": "1",
            "metadata_json": json.dumps(meta),
        }, files=files)
        elapsed = time.perf_counter() - t0
        assert res.status_code == 200, res.text
        return elapsed / n

    per_photo_small = ingest(8, "sml")
    per_photo_large = ingest(32, "lrg")

    # Quadratic behaviour would make the larger batch dramatically worse per
    # photo. Generous bound — this is a shape check, not a benchmark.
    assert per_photo_large < per_photo_small * 3.0, (
        f"per-photo cost grew from {per_photo_small * 1000:.0f}ms to "
        f"{per_photo_large * 1000:.0f}ms — ingest may be super-linear"
    )
