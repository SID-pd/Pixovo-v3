"""
20 Canonical Album Themes & 5 Semantic Color Roles Engine for PTE
Semantic Roles: background, surface, primary, accent, text
Neutral base + controlled accent approach for professional photobook design.
"""

import time
from typing import List, Tuple, Dict, Any
from PIL import Image
from app.config import logger

# 10 Theme Categories & Curated Typography Pairings
CATEGORY_TYPOGRAPHY_MAP: Dict[str, Dict[str, str]] = {
    "Wedding": {"heading_font": "Playfair Display", "body_font": "Cormorant Garamond", "recommended_theme": "Elegant"},
    "Travel": {"heading_font": "Montserrat", "body_font": "Lato", "recommended_theme": "Coastal"},
    "Celebration": {"heading_font": "Poppins", "body_font": "Quicksand", "recommended_theme": "Vibrant"},
    "Baby": {"heading_font": "Fredoka", "body_font": "Nunito", "recommended_theme": "Pastel"},
    "Family": {"heading_font": "Merriweather", "body_font": "Open Sans", "recommended_theme": "Warm"},
    "Milestones": {"heading_font": "Cinzel", "body_font": "Roboto", "recommended_theme": "Classic"},
    "Memories": {"heading_font": "Lora", "body_font": "Raleway", "recommended_theme": "Nostalgic"},
    "Portraits": {"heading_font": "Inter", "body_font": "Space Grotesk", "recommended_theme": "Editorial"},
    "Minimal": {"heading_font": "Helvetica", "body_font": "Inter", "recommended_theme": "Minimal"},
    "Vintage": {"heading_font": "Special Elite", "body_font": "Old Standard TT", "recommended_theme": "Vintage"}
}

# 20 Canonical Album Themes with 5 Semantic Color Roles
THEME_PALETTES: Dict[str, Dict[str, str]] = {
    "Warm": {
        "background": "#FFF9F2",
        "surface": "#F5E7D6",
        "primary": "#D5B18A",
        "accent": "#A87552",
        "text": "#604638"
    },
    "Elegant": {
        "background": "#FFFFFF",
        "surface": "#F5F0E7",
        "primary": "#D4C1A0",
        "accent": "#A88B5A",
        "text": "#4A443D"
    },
    "Romantic": {
        "background": "#FFF8F8",
        "surface": "#F7E3E7",
        "primary": "#E0AEB8",
        "accent": "#B76B7A",
        "text": "#70434D"
    },
    "Minimal": {
        "background": "#FFFFFF",
        "surface": "#F5F5F2",
        "primary": "#D9D8D3",
        "accent": "#A4A39E",
        "text": "#444340"
    },
    "Classic": {
        "background": "#FFFFFF",
        "surface": "#F2EFE9",
        "primary": "#C8C1B5",
        "accent": "#80796F",
        "text": "#383531"
    },
    "Vintage": {
        "background": "#F7F0E4",
        "surface": "#E9DDC8",
        "primary": "#C3A47D",
        "accent": "#92704F",
        "text": "#5D493B"
    },
    "Nostalgic": {
        "background": "#FAF3E9",
        "surface": "#EFE1D0",
        "primary": "#C9A487",
        "accent": "#9B6F59",
        "text": "#60483E"
    },
    "Coastal": {
        "background": "#FAFEFE",
        "surface": "#E8F5F5",
        "primary": "#9ACED0",
        "accent": "#4E9BA5",
        "text": "#286174"
    },
    "Tropical": {
        "background": "#FFF8E7",
        "surface": "#EEF2D9",
        "primary": "#B5CC8A",
        "accent": "#5C9860",
        "text": "#E39A58"
    },
    "Adventure": {
        "background": "#F8F2E8",
        "surface": "#E8DDCB",
        "primary": "#B89062",
        "accent": "#6C593F",
        "text": "#385044"
    },
    "Nature": {
        "background": "#F8F7EF",
        "surface": "#E6E9DE",
        "primary": "#A9B78E",
        "accent": "#5C7655",
        "text": "#304B3A"
    },
    "Sunset": {
        "background": "#FFF5E9",
        "surface": "#FFE4C5",
        "primary": "#F4A26C",
        "accent": "#D86B50",
        "text": "#824C4A"
    },
    "Festive": {
        "background": "#FFFDF5",
        "surface": "#FFF1D2",
        "primary": "#D94D48",
        "accent": "#C58A2B",
        "text": "#28745B"
    },
    "Playful": {
        "background": "#FFFDF8",
        "surface": "#FFE4D8",
        "primary": "#FFB19D",
        "accent": "#78C5C3",
        "text": "#8B7BB5"
    },
    "Luxury": {
        "background": "#FFFFFF",
        "surface": "#F2EEE4",
        "primary": "#C7A765",
        "accent": "#756346",
        "text": "#242424"
    },
    "Editorial": {
        "background": "#FFFFFF",
        "surface": "#F3F0EB",
        "primary": "#C5BDB4",
        "accent": "#81786F",
        "text": "#292827"
    },
    "Urban": {
        "background": "#F8F8F6",
        "surface": "#E7E7E4",
        "primary": "#B7B7B2",
        "accent": "#6A6A66",
        "text": "#292929"
    },
    "Night": {
        "background": "#171724",
        "surface": "#36365B",
        "primary": "#684A92",
        "accent": "#D45A96",
        "text": "#F5EDFF"
    },
    "Achievement": {
        "background": "#FFFFFF",
        "surface": "#F2F0E8",
        "primary": "#C5A75C",
        "accent": "#536879",
        "text": "#292D32"
    },
    "Monochrome": {
        "background": "#FFFFFF",
        "surface": "#D8D8D8",
        "primary": "#929292",
        "accent": "#454545",
        "text": "#111111"
    }
}

# Category to Theme mapping matrix
CATEGORY_THEMES_MAP: Dict[str, List[str]] = {
    "Family": ["Warm", "Classic", "Nostalgic"],
    "Travel": ["Coastal", "Tropical", "Adventure", "Nature", "Sunset"],
    "Celebration": ["Elegant", "Romantic", "Festive", "Playful", "Luxury"],
    "Everyday": ["Warm", "Minimal", "Urban"],
    "Portraits": ["Classic", "Minimal", "Editorial", "Monochrome"],
    "Nature": ["Nature", "Coastal", "Sunset"],
    "Lifestyle": ["Warm", "Editorial", "Urban", "Luxury"],
    "Milestones": ["Achievement", "Elegant", "Classic"],
    "Activities": ["Adventure", "Playful", "Night", "Urban"],
    "Memories": ["Nostalgic", "Vintage", "Monochrome", "Warm"]
}

def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    hex_clean = hex_str.lstrip('#')
    return tuple(int(hex_clean[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

def extract_dominant_colors(image_path: str, num_colors: int = 3) -> List[str]:
    """Extracts top num_colors hex strings using fast Pillow quantization (<3ms)."""
    start_time = time.perf_counter()
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((100, 100))
            quantized = img.quantize(colors=num_colors, method=Image.Quantize.FASTOCTREE)
            palette = quantized.getpalette()[:num_colors * 3]
            hex_colors = []
            for i in range(0, len(palette), 3):
                rgb = tuple(palette[i:i+3])
                hex_colors.append(rgb_to_hex(rgb))
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(f"[Metrics] Color extraction completed in {elapsed_ms:.2f}ms | Colors: {hex_colors}")
            return hex_colors
    except Exception as e:
        logger.warning(f"[ColorExtractor] Failed to extract colors from {image_path}: {e}. Returning default palette.")
        return ["#FFF9F2", "#F5E7D6", "#604638"]

def calculate_yiq_text_color(bg_hex: str) -> str:
    """Calculates W3C YIQ brightness contrast score to return #121212 (dark) or #FFFFFF (light)."""
    r, g, b = hex_to_rgb(bg_hex)
    yiq = (r * 299 + g * 587 + b * 114) / 1000
    return "#121212" if yiq >= 128 else "#FFFFFF"

def get_best_background_color(photo_colors: List[str], theme_name: str) -> str:
    """Computes Euclidean RGB distance between photo colors and theme palette semantic roles (background vs surface)."""
    palette_info = THEME_PALETTES.get(theme_name, THEME_PALETTES["Warm"])
    swatches = [palette_info["background"], palette_info["surface"]]

    if not photo_colors:
        return palette_info["background"]

    photo_rgbs = [hex_to_rgb(c) for c in photo_colors if c.startswith('#')]
    if not photo_rgbs:
        return palette_info["background"]

    best_swatch = swatches[0]
    min_dist = float('inf')

    for swatch in swatches:
        s_rgb = hex_to_rgb(swatch)
        dist = min(
            ((s_rgb[0] - p[0])**2 + (s_rgb[1] - p[1])**2 + (s_rgb[2] - p[2])**2)**0.5
            for p in photo_rgbs
        )
        if dist < min_dist:
            min_dist = dist
            best_swatch = swatch

    return best_swatch
