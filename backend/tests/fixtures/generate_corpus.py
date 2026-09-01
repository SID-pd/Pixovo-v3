"""
Deterministic synthetic photo corpus for tests and load runs.

Never uses real user photos. Seeded, so every run produces byte-identical images
and any test built on it is reproducible.

Design note on realism: the first-pass test fixtures were dense random noise
plus hard grid lines, which is close to worst-case for JPEG — they compressed to
~160 KB at 512px versus ~30-40 KB for a real photo thumbnail. That made every
absolute size assertion a measurement of the fixture rather than the system.
The generators here use smooth gradients, soft blobs and mild grain, which
compress in the same ballpark as real photographs.
"""

from __future__ import annotations

import io
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# Share of each kind in a generated corpus. Chosen so the filter engine is
# actually exercised on every gate rather than only the happy path.
COMPOSITION: List[Tuple[str, float]] = [
    ("sharp", 0.58),        # normal photos, varied composition -> survive
    ("burst_dup", 0.15),    # near-identical consecutive frames -> 1 of N survives
    ("blurry", 0.10),       # gaussian blurred -> rejected
    ("low_key", 0.05),      # ARTISTIC dark frames -> must SURVIVE
    ("dark", 0.03),         # true blackout -> rejected
    ("blown", 0.03),        # true whiteout -> rejected
    ("junk_qr", 0.04),      # QR / document scan -> rejected
    ("corrupt", 0.02),      # truncated file -> rejected gracefully
]

# What the pipeline is expected to do with each kind.
#   survive : must pass the filters
#   reject  : must be filtered out
#   partial : burst duplicates — SOME survive by design (1 of N), so neither
#             "survive" nor "reject" is a meaningful per-photo expectation
EXPECTATION = {
    "sharp": "survive",
    "low_key": "survive",
    "burst_dup": "partial",
    "blurry": "reject",
    "dark": "reject",
    "blown": "reject",
    "junk_qr": "reject",
    "corrupt": "reject",
}
EXPECTED_SURVIVORS = {k for k, v in EXPECTATION.items() if v == "survive"}


@dataclass
class CorpusItem:
    photo_id: str
    path: str
    kind: str
    width: int
    height: int
    aspect_ratio: float
    timestamp_epoch: int
    expected_survives: bool
    expectation: str          # survive | reject | partial


def _kind_sequence(count: int, seed: int) -> List[str]:
    """
    Builds the kind list by exact proportion, then shuffles deterministically.
    Proportional construction (rather than per-item random choice) keeps the mix
    stable at small counts, where sampling would otherwise skew badly.
    """
    kinds: List[str] = []
    for name, share in COMPOSITION:
        kinds.extend([name] * max(1, int(round(count * share))))

    if len(kinds) > count:
        kinds = kinds[:count]
    while len(kinds) < count:
        kinds.append("sharp")

    random.Random(seed).shuffle(kinds)
    return kinds


def _base_photo(w: int, h: int, rng: random.Random) -> np.ndarray:
    """
    A photo-like image: smooth colour gradient, a few soft blobs, mild grain.
    Compresses like a photograph, and carries enough gradient structure to clear
    the Tenengrad/Laplacian sharpness gate.
    """
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xn, yn = xx / max(1, w), yy / max(1, h)

    # Two-axis gradient in a random hue direction.
    c1 = np.array([rng.uniform(40, 210) for _ in range(3)], dtype=np.float32)
    c2 = np.array([rng.uniform(40, 210) for _ in range(3)], dtype=np.float32)
    t = (0.6 * xn + 0.4 * yn)[..., None]
    img = c1 * (1.0 - t) + c2 * t

    # Soft blobs stand in for subjects; they give real edges after blurring.
    for _ in range(rng.randint(3, 6)):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        radius = rng.uniform(min(w, h) * 0.08, min(w, h) * 0.28)
        colour = np.array([rng.uniform(20, 235) for _ in range(3)], dtype=np.float32)
        d2 = (xx - cx) ** 2 + (yy - cy) ** 2
        mask = np.exp(-d2 / (2.0 * radius ** 2))[..., None]
        img = img * (1.0 - mask) + colour * mask

    img_u8 = np.clip(img, 0, 255).astype(np.uint8)

    # Crisp-edged shapes plus fine high-frequency detail.
    #
    # Sizing note: gradients and soft blobs alone are too smooth — the focal
    # sharpness gate (Tenengrad + Laplacian, threshold 30) rejected ~30% of
    # nominally "sharp" fixtures. Real photographs carry fine texture, so the
    # detail below is what makes a synthetic frame read as in-focus. It stays
    # scattered and mid-contrast so the junk detector (which wants high edge
    # density AND low entropy AND a white background) is not triggered.
    for _ in range(rng.randint(2, 4)):
        x0, y0 = rng.randint(0, max(1, w - 60)), rng.randint(0, max(1, h - 60))
        x1 = min(w - 1, x0 + rng.randint(30, max(31, w // 6)))
        y1 = min(h - 1, y0 + rng.randint(30, max(31, h // 6)))
        colour = tuple(int(rng.uniform(0, 255)) for _ in range(3))
        cv2.rectangle(img_u8, (x0, y0), (x1, y1), colour, thickness=rng.choice([2, 3, -1]))

    # Fine strokes: thin, high-contrast, scattered — like foliage or fabric.
    for _ in range(rng.randint(60, 110)):
        x0, y0 = rng.randint(0, w - 1), rng.randint(0, h - 1)
        length = rng.randint(4, 14)
        angle = rng.uniform(0, math.tau)
        x1 = int(np.clip(x0 + length * math.cos(angle), 0, w - 1))
        y1 = int(np.clip(y0 + length * math.sin(angle), 0, h - 1))
        shade = int(rng.choice([rng.uniform(0, 60), rng.uniform(195, 255)]))
        cv2.line(img_u8, (x0, y0), (x1, y1), (shade, shade, shade), 1)

    # Small speckle detail — the strongest single contributor to Laplacian variance.
    for _ in range(rng.randint(120, 200)):
        cx, cy = rng.randint(0, w - 1), rng.randint(0, h - 1)
        shade = int(rng.choice([rng.uniform(0, 50), rng.uniform(205, 255)]))
        cv2.circle(img_u8, (cx, cy), rng.randint(1, 2), (shade, shade, shade), -1)

    grain = np.random.default_rng(rng.randint(0, 2**31)).normal(0, 9.0, (h, w, 3))
    return np.clip(img_u8.astype(np.float32) + grain, 0, 255).astype(np.uint8)


def _render(kind: str, w: int, h: int, rng: random.Random) -> Optional[np.ndarray]:
    img = _base_photo(w, h, rng)

    if kind == "sharp":
        return img

    if kind == "blurry":
        # The sharpness gate is `focal_blur < threshold AND tenengrad < 120`, so
        # BOTH metrics must fall. A moderate kernel left the fine speckle detail
        # as soft-but-steep ramps, which kept Tenengrad above 120 and let 40% of
        # these through. An explicit large sigma plus a second pass collapses the
        # gradient energy properly.
        k = max(21, (min(w, h) // 16) * 2 + 1)
        sigma = max(6.0, min(w, h) / 45.0)
        blurred = cv2.GaussianBlur(img, (k, k), sigmaX=sigma, sigmaY=sigma)
        return cv2.GaussianBlur(blurred, (k, k), sigmaX=sigma, sigmaY=sigma)

    if kind == "low_key":
        # Artistic dark frame: mostly near-black but with real subject contrast.
        # The exposure guard is explicitly built to protect these, so a corpus of
        # only extremes could not catch a regression that starts rejecting them.
        dark = (img.astype(np.float32) * 0.16).astype(np.uint8)
        cx, cy = w // 2, h // 2
        r = min(w, h) // 5
        highlight = np.zeros((h, w, 3), np.float32)
        cv2.circle(highlight, (cx, cy), r, (235, 225, 205), -1)
        highlight = cv2.GaussianBlur(highlight, (0, 0), sigmaX=r / 3.0)
        return np.clip(dark.astype(np.float32) + highlight, 0, 255).astype(np.uint8)

    if kind == "dark":
        return np.full((h, w, 3), 3, np.uint8)

    if kind == "blown":
        return np.full((h, w, 3), 252, np.uint8)

    if kind == "junk_qr":
        # Flat white page with a QR-like block grid and text-like rows: low
        # entropy, high edge density, high white ratio.
        page = np.full((h, w, 3), 246, np.uint8)
        cell = max(4, min(w, h) // 40)
        qr_dim = cell * 21
        ox, oy = (w - qr_dim) // 2, (h - qr_dim) // 3
        qrng = random.Random(rng.randint(0, 2**31))
        for i in range(21):
            for j in range(21):
                if qrng.random() < 0.5:
                    cv2.rectangle(
                        page,
                        (ox + j * cell, oy + i * cell),
                        (ox + (j + 1) * cell, oy + (i + 1) * cell),
                        (0, 0, 0), -1,
                    )
        for row in range(6):
            y = oy + qr_dim + 20 + row * max(6, cell)
            if y < h - 4:
                cv2.line(page, (int(w * 0.2), y), (int(w * 0.8), y), (25, 25, 25), 2)
        return page

    if kind == "corrupt":
        return None  # written as a truncated file

    if kind == "burst_dup":
        return img  # the caller nudges a copy of the previous frame

    return img


def _nudge(img: np.ndarray, rng: random.Random) -> np.ndarray:
    """A near-identical frame: tiny shift plus brightness jitter, as a burst would be."""
    h, w = img.shape[:2]
    dx, dy = rng.randint(-3, 3), rng.randint(-3, 3)
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    shifted = cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REFLECT)
    return np.clip(shifted.astype(np.float32) * rng.uniform(0.98, 1.02), 0, 255).astype(np.uint8)


def generate_corpus(
    out_dir: Path,
    count: int = 100,
    seed: int = 42,
    max_dim: int = 512,
    cluster_gap_seconds: int = 7200,
    cluster_count: int = 3,
) -> List[CorpusItem]:
    """
    Writes `count` synthetic 512px thumbnails and returns a manifest.

    Photos are laid out in `cluster_count` temporal clusters separated by
    `cluster_gap_seconds` (default 2h, comfortably over the 45-minute chapter
    threshold), so chaptering has real gaps to split on.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    kinds = _kind_sequence(count, seed)
    base_ts = 1_700_000_000
    per_cluster = max(1, count // cluster_count)

    manifest: List[CorpusItem] = []
    previous: Optional[np.ndarray] = None

    for i, kind in enumerate(kinds):
        # Portrait/landscape/square mix, then scaled to thumbnail size.
        shape = rng.choice([(1.5, "L"), (0.667, "P"), (1.0, "S")])[0]
        if shape >= 1.0:
            w, h = max_dim, max(1, int(max_dim / shape))
        else:
            h, w = max_dim, max(1, int(max_dim * shape))

        cluster = min(i // per_cluster, cluster_count - 1)
        ts = base_ts + cluster * cluster_gap_seconds + (i % per_cluster) * 45

        photo_id = f"px_syn{i:05d}"
        path = out_dir / f"{photo_id}_thumb.jpg"

        if kind == "corrupt":
            # Valid JPEG header, body truncated hard.
            #
            # Truncating to a third still left a decodable top portion — PIL and
            # OpenCV both salvage partial scans, so the file passed as a real
            # photo. Keeping only the header/quantisation tables makes it
            # genuinely undecodable, which is what this fixture is for.
            good = _base_photo(w, h, rng)
            ok, buf = cv2.imencode(".jpg", good, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            data = buf.tobytes()
            path.write_bytes(data[:180])
        else:
            if kind == "burst_dup" and previous is not None and previous.shape[:2] == (h, w):
                img = _nudge(previous, rng)
            else:
                img = _render(kind, w, h, rng)
                if img is None:
                    img = _base_photo(w, h, rng)
            cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            previous = img

        manifest.append(CorpusItem(
            photo_id=photo_id,
            path=str(path),
            kind=kind,
            width=w * 8,        # pretend the original was 8x the thumbnail
            height=h * 8,
            aspect_ratio=round(w / max(1, h), 4),
            timestamp_epoch=ts,
            expected_survives=kind in EXPECTED_SURVIVORS,
            expectation=EXPECTATION.get(kind, "survive"),
        ))

    (out_dir / "manifest.json").write_text(
        json.dumps([asdict(m) for m in manifest], indent=2), encoding="utf-8"
    )
    return manifest


def corpus_stats(manifest: List[CorpusItem]) -> Dict[str, object]:
    """Size and composition summary — used to sanity-check realism."""
    sizes = [Path(m.path).stat().st_size for m in manifest if Path(m.path).exists()]
    counts: Dict[str, int] = {}
    for m in manifest:
        counts[m.kind] = counts.get(m.kind, 0) + 1
    return {
        "count": len(manifest),
        "kinds": counts,
        "expected_survivors": sum(1 for m in manifest if m.expected_survives),
        "mean_kb": round(sum(sizes) / max(1, len(sizes)) / 1024, 1),
        "max_kb": round(max(sizes) / 1024, 1) if sizes else 0,
        "total_mb": round(sum(sizes) / 1024 / 1024, 2),
    }


if __name__ == "__main__":
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description="Generate a synthetic photo corpus.")
    parser.add_argument("--out", default=None, help="output directory")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    target = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="pixovo_corpus_"))
    items = generate_corpus(target, count=args.count, seed=args.seed)
    print(f"Wrote {len(items)} photos to {target}")
    print(json.dumps(corpus_stats(items), indent=2))
