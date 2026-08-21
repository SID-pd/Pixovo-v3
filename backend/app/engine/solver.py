"""
Cost-Function Layout Solver & Photobook Generator for PTE
Calculates non-overlapping Double-Page Spread layouts using DSA Bounded Engine.
Applies 20 Canonical Themes with 5 Semantic Color Roles (background, surface, primary, accent, text).
Supports exact 3 persistent saved book variations & on-click spread reshuffling.
"""

from typing import List, Dict, Any, Set
from app.schemas.photobook import (
    PhotoMeta, TemplateSlot, SinglePage, SpreadPair, PhotobookVariation
)
from app.engine.dsa_solver import build_dsa_spread_pair, reshuffle_single_spread_engine, LAYOUT_FAMILIES, cluster_photos_2tier_engine
from app.engine.story_ai import partition_macro_chapters
from app.engine.color_extractor import THEME_PALETTES, calculate_yiq_text_color

def generate_photobook_variations_engine(
    photos: List[PhotoMeta],
    ai_batch_result: Dict[str, Any],
    variant_seed_offset: int = 0
) -> List[PhotobookVariation]:
    """
    Generates exactly 3 distinct persistent Photobook Variations applying
    2-Tier Intelligent Clustering (Macro Chapters + Micro pHash Spread Matching) and DSA Solver.
    """
    variations_data = ai_batch_result.get("variations", [])
    result_variations: List[PhotobookVariation] = []

    default_themes = ["Warm", "Elegant", "Minimal"]

    # Tier 1: Macro Temporal + GPS Story Chapters
    macro_chapters = partition_macro_chapters(photos)

    for var_idx in range(3):
        if var_idx < len(variations_data):
            var_info = variations_data[var_idx]
            theme_name = var_info.get("theme_name", default_themes[var_idx])
        else:
            var_info = {}
            theme_name = default_themes[var_idx]

        palette = THEME_PALETTES.get(theme_name, THEME_PALETTES["Warm"])

        captions = var_info.get("captions", [
            "THE JOURNEY BEGINS AT FIRST LIGHT",
            "BLUE SKIES OVER SACRED SPIRES",
            "TOGETHER IN THE SOFT AFTERNOON",
            "GENTLE SPIRITS IN THE SANCTUARY",
            "A GLOWING END TO THE DAY"
        ])
        
        family_variant = LAYOUT_FAMILIES[(var_idx + variant_seed_offset) % len(LAYOUT_FAMILIES)]
        spreads: List[SpreadPair] = []
        spread_idx = 1

        # Tier 2: Micro-Clustering (Shell & Core pHash Visual Similarity per Chapter)
        for ch in macro_chapters:
            ch_photos = ch.get("photos", [])
            photo_chunks = cluster_photos_2tier_engine(ch_photos, chunk_size=2 if len(ch_photos) <= 4 else 3)

            for chunk in photo_chunks:
                caption = captions[(spread_idx - 1) % len(captions)]
                spread = build_dsa_spread_pair(
                    spread_idx=spread_idx,
                    photos=chunk,
                    caption=caption,
                    theme_name=theme_name,
                    family_variant=family_variant,
                    family_variant_seed=variant_seed_offset + spread_idx
                )
                spreads.append(spread)
                spread_idx += 1

        cover_img = photos[0].url if photos else "/uploads/sample_placeholder.jpg"
        
        variation = PhotobookVariation(
            id=f"var_{var_idx + 1}",
            variation_title=var_info.get("variation_title", f"{theme_name} Style {var_idx + 1}"),
            theme_name=theme_name,
            cover_title=var_info.get("cover_title", "KANPUR TEMPLE VISIT"),
            cover_subtitle=var_info.get("cover_subtitle", "2025 • MEMORIES"),
            cover_image_url=cover_img,
            base_color=palette["background"],
            accent_color=palette["accent"],
            text_color=palette["text"],
            spreads=spreads
        )
        result_variations.append(variation)

    return result_variations
