"""
Double-Page Spread Boilerplate Registry for Pixovo Template Engine (PTE)
Each template defines non-overlapping photo & text slots for the ENTIRE double spread (width 0.0 to 1.0).
Spine Gutter is at x = 0.50 (Left Page: 0.04 to 0.46, Right Page: 0.54 to 0.96).
Zero overlapping, zero cross patterns, zero container overflow.
"""

from typing import List, Dict, Any

SPREAD_TEMPLATES_REGISTRY: List[Dict[str, Any]] = [
    # --- 1 PHOTO SPREAD (Hero Right + Left Journal) ---
    {
        "id": "tpl_1p_hero_right_journal",
        "name": "Hero Right with Left Journal Title",
        "photo_count": 1,
        "text_condition": "short_text",
        "slots": [
            {
                "id": "photo_1",
                "type": "photo",
                "x_pct": 0.55,
                "y_pct": 0.12,
                "w_pct": 0.40,
                "h_pct": 0.76,
                "target_aspect": 1.0,
                "render_mode": "cover",
                "role": "main_hero"
            },
            {
                "id": "text_1",
                "type": "text",
                "x_pct": 0.06,
                "y_pct": 0.42,
                "w_pct": 0.38,
                "h_pct": 0.16,
                "role": "journal_title"
            }
        ]
    },
    {
        "id": "tpl_1p_hero_left_clean",
        "name": "Hero Left Clean Spread",
        "photo_count": 1,
        "text_condition": "no_text",
        "slots": [
            {
                "id": "photo_1",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.12,
                "w_pct": 0.38,
                "h_pct": 0.76,
                "target_aspect": 1.0,
                "render_mode": "cover",
                "role": "main_hero"
            }
        ]
    },

    # --- 2 PHOTO SPREADS ---
    {
        "id": "tpl_2p_split_facing_balanced",
        "name": "Balanced Facing Pair (1 Left + 1 Right)",
        "photo_count": 2,
        "text_condition": "short_text",
        "slots": [
            {
                "id": "photo_1",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.12,
                "w_pct": 0.38,
                "h_pct": 0.68,
                "target_aspect": 1.1,
                "render_mode": "cover",
                "role": "left_photo"
            },
            {
                "id": "photo_2",
                "type": "photo",
                "x_pct": 0.56,
                "y_pct": 0.12,
                "w_pct": 0.38,
                "h_pct": 0.68,
                "target_aspect": 1.1,
                "render_mode": "cover",
                "role": "right_photo"
            },
            {
                "id": "text_1",
                "type": "text",
                "x_pct": 0.06,
                "y_pct": 0.84,
                "w_pct": 0.88,
                "h_pct": 0.08,
                "role": "footer_caption"
            }
        ]
    },
    {
        "id": "tpl_2p_left_stacked_duo",
        "name": "Stacked Vertical Duo Left Page",
        "photo_count": 2,
        "text_condition": "no_text",
        "slots": [
            {
                "id": "photo_1",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.08,
                "w_pct": 0.38,
                "h_pct": 0.40,
                "target_aspect": 1.5,
                "render_mode": "cover",
                "role": "top_photo"
            },
            {
                "id": "photo_2",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.52,
                "w_pct": 0.38,
                "h_pct": 0.40,
                "target_aspect": 1.5,
                "render_mode": "cover",
                "role": "bottom_photo"
            }
        ]
    },

    # --- 3 PHOTO SPREADS ---
    {
        "id": "tpl_3p_hero_left_stacked_right",
        "name": "Hero Left + Stacked Duo Right",
        "photo_count": 3,
        "text_condition": "short_text",
        "slots": [
            {
                "id": "photo_1",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.08,
                "w_pct": 0.38,
                "h_pct": 0.84,
                "target_aspect": 0.85,
                "render_mode": "cover",
                "role": "hero_left"
            },
            {
                "id": "photo_2",
                "type": "photo",
                "x_pct": 0.56,
                "y_pct": 0.08,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.4,
                "render_mode": "cover",
                "role": "right_top"
            },
            {
                "id": "photo_3",
                "type": "photo",
                "x_pct": 0.56,
                "y_pct": 0.53,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.4,
                "render_mode": "cover",
                "role": "right_bot"
            },
            {
                "id": "text_1",
                "type": "text",
                "x_pct": 0.56,
                "y_pct": 0.48,
                "w_pct": 0.38,
                "h_pct": 0.04,
                "role": "middle_accent"
            }
        ]
    },
    {
        "id": "tpl_3p_stacked_left_hero_right",
        "name": "Stacked Duo Left + Hero Right",
        "photo_count": 3,
        "text_condition": "short_text",
        "slots": [
            {
                "id": "photo_1",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.08,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.4,
                "render_mode": "cover",
                "role": "left_top"
            },
            {
                "id": "photo_2",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.53,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.4,
                "render_mode": "cover",
                "role": "left_bot"
            },
            {
                "id": "photo_3",
                "type": "photo",
                "x_pct": 0.56,
                "y_pct": 0.08,
                "w_pct": 0.38,
                "h_pct": 0.84,
                "target_aspect": 0.85,
                "render_mode": "cover",
                "role": "hero_right"
            },
            {
                "id": "text_1",
                "type": "text",
                "x_pct": 0.06,
                "y_pct": 0.48,
                "w_pct": 0.38,
                "h_pct": 0.04,
                "role": "left_middle_caption"
            }
        ]
    },

    # --- 4 PHOTO SPREADS ---
    {
        "id": "tpl_4p_2x2_split_grid",
        "name": "Balanced 2x2 Grid Across Double Spread",
        "photo_count": 4,
        "text_condition": "short_text",
        "slots": [
            {
                "id": "photo_1",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.08,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.2,
                "render_mode": "cover",
                "role": "g1_left_top"
            },
            {
                "id": "photo_2",
                "type": "photo",
                "x_pct": 0.06,
                "y_pct": 0.53,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.2,
                "render_mode": "cover",
                "role": "g2_left_bot"
            },
            {
                "id": "photo_3",
                "type": "photo",
                "x_pct": 0.56,
                "y_pct": 0.08,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.2,
                "render_mode": "cover",
                "role": "g3_right_top"
            },
            {
                "id": "photo_4",
                "type": "photo",
                "x_pct": 0.56,
                "y_pct": 0.53,
                "w_pct": 0.38,
                "h_pct": 0.39,
                "target_aspect": 1.2,
                "render_mode": "cover",
                "role": "g4_right_bot"
            },
            {
                "id": "text_1",
                "type": "text",
                "x_pct": 0.06,
                "y_pct": 0.48,
                "w_pct": 0.88,
                "h_pct": 0.04,
                "role": "middle_accent_line"
            }
        ]
    }
]

def get_matching_spread_templates(photo_count: int, used_ids: set = None) -> List[Dict[str, Any]]:
    """Returns candidate double-page spread templates matching photo count with non-repetitive selection."""
    candidates = [t for t in SPREAD_TEMPLATES_REGISTRY if t["photo_count"] == photo_count]
    if not candidates:
        candidates = sorted(SPREAD_TEMPLATES_REGISTRY, key=lambda t: abs(t["photo_count"] - photo_count))

    if used_ids:
        unused = [c for c in candidates if c["id"] not in used_ids]
        if unused:
            return unused
            
    return candidates
