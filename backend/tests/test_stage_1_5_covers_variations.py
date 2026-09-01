"""
Stage 1.5 regression tests — covers and variation distinctness.

The reported defect: "only the main cover is being shown, and that too is just a
dummy." Four compounding causes:

  1. solver.py: `cover_img = photos[0].url` — same upload-order photo for all
     three variations; hero_score never consulted.
  2. PhotobookVariation.cover_image_url was a single str — the API could not
     express a multi-photo cover at all.
  3. BookCarousel3D's "4-photo collage" rendered that one URL four times.
  4. When photo resolution failed, photos[0] became a sample_N placeholder.

These tests assert the observable outcome: three variations, distinct real
hero-ranked covers, correct photo count per style, and structurally different
books. The engine has no `random`, so exact assertions are legitimate.
"""

import pytest

from app.schemas.photobook import PhotoMeta


def make_photo(pid: str, hero: float, ar: float = 1.5, faces: int = 1) -> PhotoMeta:
    """A PhotoMeta as ingest would produce it after Stage 1.2."""
    return PhotoMeta(
        id=pid,
        filename=f"{pid}.jpg",
        url=f"/uploads/thumbnails/sess_test/{pid}_thumb.jpg",
        thumbnail_url=f"/uploads/thumbnails/sess_test/{pid}_thumb.jpg",
        preview_url=f"/uploads/thumbnails/sess_test/{pid}_thumb.jpg",
        original_url=None,
        original_synced=False,
        width=4000,
        height=int(4000 / ar),
        aspect_ratio=ar,
        hero_score=hero,
        face_count=faces,
        shell_phash=f"{abs(hash(pid)) % (16**16):016x}",
        core_phash=f"{abs(hash(pid + 'c')) % (16**16):016x}",
        dominant_colors=["#112233", "#445566", "#778899"],
        timestamp_epoch=1_785_535_200 + int(pid[-2:] if pid[-2:].isdigit() else 0) * 600,
    )


@pytest.fixture
def photos_30():
    """
    30 photos with descending hero scores and mixed aspect ratios, spread over
    time so chaptering produces several chapters.
    """
    out = []
    for i in range(30):
        ar = [1.6, 1.4, 0.75, 1.0][i % 4]
        out.append(make_photo(f"px_c{i:02d}", hero=95.0 - i * 2.0, ar=ar, faces=(i % 3)))
    return out


@pytest.fixture
def ai_batch():
    from app.engine.story_ai import generate_story_theme_batch

    return generate_story_theme_batch("Family trip to the temple", total_photos=30)


# ------------------------------------------------------------ cover selector


def test_selector_returns_correct_photo_count_per_style(photos_30):
    from app.engine.cover_selector import select_covers

    sets = select_covers(photos_30, variation_count=3)
    expected = {"SPLIT_BANNER": 2, "HERO_BAND": 1, "COLLAGE_2X2": 4}

    assert len(sets) == 3
    for cs in sets:
        assert len(cs["cover_photos"]) == expected[cs["cover_style"]]


def test_selector_never_reuses_a_photo(photos_30):
    from app.engine.cover_selector import select_covers

    sets = select_covers(photos_30, variation_count=3)
    ids = [cp.photo_id for cs in sets for cp in cs["cover_photos"]]
    assert len(ids) == len(set(ids)), f"photo reused across covers: {ids}"


def test_selector_picks_the_best_hero_first(photos_30):
    """Cover selection must consult hero_score, not array order."""
    from app.engine.cover_selector import select_covers

    best = max(photos_30, key=lambda p: p.hero_score)
    sets = select_covers(photos_30, variation_count=3)
    assert sets[0]["cover_photos"][0].photo_id == best.id


def test_selector_ignores_array_order(photos_30):
    """
    The old bug was `photos[0]`. Reversing the input must not change the covers,
    because ranking is by hero_score.
    """
    from app.engine.cover_selector import select_covers

    forward = select_covers(photos_30, variation_count=3)
    reverse = select_covers(list(reversed(photos_30)), variation_count=3)

    ids_f = [cp.photo_id for cs in forward for cp in cs["cover_photos"]]
    ids_r = [cp.photo_id for cs in reverse for cp in cs["cover_photos"]]
    assert ids_f == ids_r, "cover choice depends on input order"


def test_selector_uses_thumbnails_not_originals(photos_30):
    """
    Three full-resolution photos in the carousel is a large part of the cover
    lag the user reported.
    """
    from app.engine.cover_selector import select_covers

    for cs in select_covers(photos_30, variation_count=3):
        for cp in cs["cover_photos"]:
            assert "/thumbnails/" in cp.url, cp.url
            assert "/originals/" not in cp.url


def test_selector_is_deterministic(photos_30):
    from app.engine.cover_selector import select_covers

    a = select_covers(photos_30, variation_count=3)
    b = select_covers(photos_30, variation_count=3)
    assert [[cp.photo_id for cp in cs["cover_photos"]] for cs in a] == \
           [[cp.photo_id for cp in cs["cover_photos"]] for cs in b]


def test_selector_prefers_landscape_for_landscape_styles(photos_30):
    """HERO_BAND prefers landscape; with plenty of candidates it should get one."""
    from app.engine.cover_selector import select_covers

    sets = select_covers(photos_30, variation_count=3)
    hero_band = next(cs for cs in sets if cs["cover_style"] == "HERO_BAND")
    assert hero_band["cover_photos"][0].aspect_ratio >= 1.0


def test_selector_degrades_on_tiny_sets():
    """
    Fully distinct covers need 2+1+4 = 7 photos. Below that it must reuse and
    still return complete sets rather than short lists the UI must special-case.
    """
    from app.engine.cover_selector import select_covers

    tiny = [make_photo(f"px_t{i}", hero=50.0 - i) for i in range(3)]
    sets = select_covers(tiny, variation_count=3)

    expected = {"SPLIT_BANNER": 2, "HERO_BAND": 1, "COLLAGE_2X2": 4}
    for cs in sets:
        assert len(cs["cover_photos"]) == expected[cs["cover_style"]]


def test_selector_handles_empty_input():
    from app.engine.cover_selector import select_covers

    sets = select_covers([], variation_count=3)
    assert len(sets) == 3
    assert all(cs["cover_photos"] == [] for cs in sets)


# ------------------------------------------------------- end-to-end variations


def test_covers_are_distinct_and_hero_ranked(photos_30, ai_batch):
    """The headline assertion for the reported defect."""
    from app.engine.solver import generate_photobook_variations_engine

    variations = generate_photobook_variations_engine(photos_30, ai_batch)
    assert len(variations) == 3

    ids = [cp.photo_id for v in variations for cp in v.cover_photos]
    assert len(ids) == len(set(ids)), "a photo appears on more than one cover"
    assert not any(i.startswith("sample") for i in ids), "placeholder reached a cover"

    expected = {"SPLIT_BANNER": 2, "HERO_BAND": 1, "COLLAGE_2X2": 4}
    for v in variations:
        assert len(v.cover_photos) == expected[v.cover_style]
        assert all("/thumbnails/" in cp.url for cp in v.cover_photos)

    best = max(photos_30, key=lambda p: p.hero_score)
    assert variations[0].cover_photos[0].photo_id == best.id


def test_deprecated_cover_image_url_still_mirrors_first_photo(photos_30, ai_batch):
    """Kept populated for one release so nothing breaks mid-migration."""
    from app.engine.solver import generate_photobook_variations_engine

    for v in generate_photobook_variations_engine(photos_30, ai_batch):
        assert v.cover_image_url == v.cover_photos[0].url


def test_no_hardcoded_test_session_titles(photos_30):
    """
    'KANPUR TEMPLE VISIT' / '2025 • MEMORIES' were leftovers from one test
    session and appeared verbatim whenever the AI batch omitted a title.
    """
    from app.engine.solver import generate_photobook_variations_engine

    variations = generate_photobook_variations_engine(photos_30, {"variations": []})
    for v in variations:
        assert "KANPUR" not in v.cover_title.upper()
        assert v.cover_title.strip() != ""
        assert v.cover_subtitle.strip() != ""


def test_variations_differ_structurally(photos_30, ai_batch):
    """
    Same photos in the same pacing means three near-identical books. Each
    variation must have its own spread structure, not just its own palette.
    """
    from app.engine.solver import generate_photobook_variations_engine

    variations = generate_photobook_variations_engine(photos_30, ai_batch)
    signatures = [
        tuple(len(s.left_page.slots) + len(s.right_page.slots) for s in v.spreads)
        for v in variations
    ]
    assert len(set(signatures)) > 1, (
        f"all three variations share one structure: {signatures[0]}"
    )

    # Palettes must still differ too.
    palettes = {(v.base_color, v.accent_color, v.text_color) for v in variations}
    assert len(palettes) > 1


def test_the_story_reads_forward_in_time(photos_30, ai_batch):
    """
    Pacing may change; the narrative arc may not. Reordering chapters would
    destroy the chronology Stage 1.2 restored.

    Asserted as a trend, not photo-by-photo monotonicity: micro-clustering pairs
    visually similar photos into a spread, which deliberately reorders *within*
    a spread. What must hold is that early spreads carry earlier photos.
    """
    from app.engine.solver import generate_photobook_variations_engine

    variations = generate_photobook_variations_engine(photos_30, ai_batch)
    ts = {p.id: p.timestamp_epoch for p in photos_30}

    def placed_order(v):
        out = []
        for spread in v.spreads:
            for page in (spread.left_page, spread.right_page):
                for slot in page.slots:
                    if slot.photo_id and slot.photo_id not in out:
                        out.append(slot.photo_id)
        return out

    for i, v in enumerate(variations):
        stamps = [ts[pid] for pid in placed_order(v) if pid in ts]
        assert len(stamps) >= 9, f"variation {i} placed too few photos to judge"

        third = len(stamps) // 3
        first_mean = sum(stamps[:third]) / third
        last_mean = sum(stamps[-third:]) / third
        assert first_mean < last_mean, (
            f"variation {i} does not read forward in time "
            f"(first third mean {first_mean:.0f} >= last third mean {last_mean:.0f})"
        )


def test_output_is_deterministic(photos_30, ai_batch):
    """No `random` anywhere in the engine — identical input, identical output."""
    from app.engine.solver import generate_photobook_variations_engine

    a = generate_photobook_variations_engine(photos_30, ai_batch)
    b = generate_photobook_variations_engine(photos_30, ai_batch)
    assert [v.model_dump() for v in a] == [v.model_dump() for v in b]


def test_every_placed_photo_has_a_real_id(photos_30, ai_batch):
    """No slot may reference a placeholder or an unknown photo."""
    from app.engine.solver import generate_photobook_variations_engine

    known = {p.id for p in photos_30}
    for v in generate_photobook_variations_engine(photos_30, ai_batch):
        for spread in v.spreads:
            for page in (spread.left_page, spread.right_page):
                for slot in page.slots:
                    if slot.type == "photo" and slot.photo_id:
                        assert slot.photo_id in known, f"unknown photo {slot.photo_id}"
