"""
Cover selection for photobook variations.

Stage 1.5. Previously `solver.py` did:

    cover_img = photos[0].url if photos else "/uploads/sample_placeholder.jpg"

— the same photo for all three variations, chosen by upload order, ignoring the
`hero_score` the filter engine computes for exactly this purpose. Combined with a
single-URL schema and a frontend that tiled that one URL four times for its
"collage" style, every variation showed one arbitrary photo repeated.

This module ranks by hero_score across the whole session and hands each
variation its own non-overlapping set, which is what makes the three read as
different books rather than three palettes over one image.
"""

from typing import Any, Dict, List

from app.config import logger
from app.schemas.photobook import CoverPhoto, PhotoMeta

# Each style declares how many photos it needs and what shape suits it.
# The backend owns this because only the producer knows what it supplied.
COVER_STYLES: List[Dict[str, Any]] = [
    {"name": "SPLIT_BANNER", "photo_count": 2, "prefers": "LANDSCAPE"},
    {"name": "HERO_BAND", "photo_count": 1, "prefers": "LANDSCAPE"},
    {"name": "COLLAGE_2X2", "photo_count": 4, "prefers": "ANY"},
]

# 2 + 1 + 4. Below this, variations must share photos.
MIN_PHOTOS_FOR_DISTINCT_COVERS = sum(s["photo_count"] for s in COVER_STYLES)

_ASPECT_BONUS_STRONG = 15.0
_ASPECT_BONUS_WEAK = 5.0
_ASPECT_BONUS_ANY = 8.0


def _aspect_affinity(photo: PhotoMeta, prefers: str) -> float:
    """
    How well a photo's shape suits a cover style, as a 0-15 point bonus added to
    hero_score. Deliberately smaller than the hero_score range so quality still
    dominates shape — a great portrait beats a mediocre landscape.
    """
    ar = photo.aspect_ratio or 1.33

    if prefers == "ANY":
        return _ASPECT_BONUS_ANY
    if prefers == "LANDSCAPE":
        if ar >= 1.35:
            return _ASPECT_BONUS_STRONG
        return _ASPECT_BONUS_WEAK if ar >= 1.0 else 0.0
    # PORTRAIT
    if ar <= 0.8:
        return _ASPECT_BONUS_STRONG
    return _ASPECT_BONUS_WEAK if ar <= 1.0 else 0.0


def _cover_photo(photo: PhotoMeta) -> CoverPhoto:
    return CoverPhoto(
        photo_id=photo.id,
        # Thumbnail, never the original. Three full-resolution photos in the
        # carousel is a large part of the cover lag.
        url=photo.thumbnail_url or photo.url,
        aspect_ratio=photo.aspect_ratio or 1.33,
        hero_score=photo.hero_score or 0.0,
    )


def select_covers(
    photos: List[PhotoMeta], variation_count: int = 3
) -> List[Dict[str, Any]]:
    """
    Picks a distinct cover set per variation, best-hero-first, no photo reused.

    Fully deterministic: every sort breaks ties on photo_id, so identical input
    always produces identical covers. The engine contains no `random`, and the
    golden tests depend on that holding.

    Ranking note: `is_event_cover_hero` from the filter engine is deliberately
    NOT used as the primary key. Filtering runs per ingest chunk, so that flag is
    chunk-local — a 1,000-photo session has one "event hero" per chunk-event.
    `hero_score` is computed per photo independently and is therefore the only
    globally comparable signal.
    """
    if not photos:
        logger.warning("[CoverSelect] No photos supplied; returning empty cover sets.")
        return [
            {"cover_style": COVER_STYLES[i % len(COVER_STYLES)]["name"], "cover_photos": []}
            for i in range(variation_count)
        ]

    if len(photos) < MIN_PHOTOS_FOR_DISTINCT_COVERS:
        logger.warning(
            f"[CoverSelect] Only {len(photos)} photos for {variation_count} variations "
            f"({MIN_PHOTOS_FOR_DISTINCT_COVERS} needed for fully distinct covers); "
            f"some photos will be reused across covers."
        )

    # Global quality ranking. Ties: more faces wins, then photo_id for stability.
    ranked = sorted(
        photos,
        key=lambda p: (-(p.hero_score or 0.0), -(p.face_count or 0), p.id),
    )

    used: set = set()
    cover_sets: List[Dict[str, Any]] = []

    for var_idx in range(variation_count):
        style = COVER_STYLES[var_idx % len(COVER_STYLES)]
        need = int(style["photo_count"])
        prefers = str(style["prefers"])

        # Re-rank the unused pool for THIS style's shape preference.
        candidates = sorted(
            (p for p in ranked if p.id not in used),
            key=lambda p: (
                -((p.hero_score or 0.0) + _aspect_affinity(p, prefers)),
                -(p.face_count or 0),
                p.id,
            ),
        )
        chosen = candidates[:need]

        # Degrade rather than fail when the pool is exhausted: pad by cycling
        # through the global ranking, so a style always receives exactly the
        # photo count it declares. Returning a short list would push the
        # special-casing into the UI, which is where the original bug lived.
        if len(chosen) < need:
            pad_index = 0
            while len(chosen) < need and ranked:
                chosen.append(ranked[pad_index % len(ranked)])
                pad_index += 1

        used.update(p.id for p in chosen)
        cover_sets.append({
            "cover_style": style["name"],
            "cover_photos": [_cover_photo(p) for p in chosen],
        })

    logger.info(
        "[CoverSelect] "
        + " | ".join(
            f"{cs['cover_style']}: "
            + ", ".join(f"{cp.photo_id}({cp.hero_score:.0f})" for cp in cs["cover_photos"])
            for cs in cover_sets
        )
    )
    return cover_sets
