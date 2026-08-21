from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class PhotoMeta(BaseModel):
    id: str
    filename: str
    url: str  # Alias for preview_url (used by frontend for smooth 60fps rendering)
    preview_url: Optional[str] = None  # ~1200px WebP for canvas/editor UI
    original_url: Optional[str] = None # Original high-res file for 300 DPI print export
    thumbnail_url: Optional[str] = None # ~256px WebP for photo tray / grid
    original_synced: bool = False      # Track background HD print asset sync status
    width: int = 1200
    height: int = 900
    aspect_ratio: float = 1.33
    dominant_colors: List[str] = Field(default_factory=list)
    score: Optional[float] = 0.8
    blur_score: Optional[float] = None
    face_count: Optional[int] = 0
    shell_phash: Optional[str] = ""
    core_phash: Optional[str] = ""
    is_hero_candidate: Optional[bool] = False

class TemplateSlot(BaseModel):
    id: str
    type: str  # "photo" or "text"
    x_pct: float
    y_pct: float
    w_pct: float
    h_pct: float
    target_aspect: Optional[float] = 1.0
    role: Optional[str] = "photo"
    photo_id: Optional[str] = None
    photo_url: Optional[str] = None
    text_content: Optional[str] = None
    effective_dpi: Optional[float] = None
    dpi_quality: Optional[str] = "excellent"  # "excellent" (>=250), "warning" (150-249), "alert" (<150)

class TemplateModel(BaseModel):
    id: str
    name: str
    photo_count: int
    text_condition: str  # "no_text", "short_text", "long_text"
    slots: List[TemplateSlot]

class SinglePage(BaseModel):
    page_number: int
    background_color: str
    text_color: str
    slots: List[TemplateSlot]
    title_caption: Optional[str] = None

class SpreadPair(BaseModel):
    spread_index: int
    left_page: SinglePage
    right_page: SinglePage

class PhotobookVariation(BaseModel):
    id: str
    variation_title: str
    theme_name: str
    cover_title: str
    cover_subtitle: str
    cover_image_url: str
    base_color: str
    accent_color: str
    text_color: str
    spreads: List[SpreadPair]

class GenerateVariationsRequest(BaseModel):
    photo_ids: List[str]
    user_prompt: str = ""

class GenerateVariationsResponse(BaseModel):
    theme_name: str
    variations: List[PhotobookVariation]

class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # "processing", "completed", "failed"
    progress: int  # 0 to 100
    message: str
    result: Optional[GenerateVariationsResponse] = None
