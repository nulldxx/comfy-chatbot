"""Layout and compositing for ``/character-sheet`` (``/api/character-sheet``).

A character sheet is seven image2image runs over one head-and-shoulders
portrait — a large portrait, three tall full-body views and three small facial
expressions — pasted onto a white 16:9 canvas sized for reference-to-video
models such as MiniMax H3. This module is the Pillow-only geometry; it never
imports the app, so it is testable standalone (like ``face_multi.py``).
See ADR/character-sheet.md.
"""

from PIL import Image

SHEET_SIZE = (3840, 2160)
BACKGROUND = (255, 255, 255)

# key -> generation size (w, h) sent as <WIDTH>/<HEIGHT>, panel box (x, y, w, h)
# on the sheet, and whether near-white borders are trimmed before fitting.
# Order is the generation order. Canvas: 40px margin and gutters; the left half
# is the portrait over three expressions, the right half three full-body views.
PANELS = {
    "portrait": {"label": "Portrait",   "size": (1536, 1536), "box": (40, 40, 1560, 1560),   "trim": False},
    "front":    {"label": "Full body",  "size": (768, 1920),  "box": (1640, 40, 693, 2080),  "trim": True},
    "side":     {"label": "Side view",  "size": (768, 1920),  "box": (2373, 40, 693, 2080),  "trim": True},
    "rear":     {"label": "Rear view",  "size": (768, 1920),  "box": (3106, 40, 693, 2080),  "trim": True},
    "neutral":  {"label": "Neutral",    "size": (1024, 1024), "box": (40, 1640, 480, 480),   "trim": False},
    "smile":    {"label": "Smiling",    "size": (1024, 1024), "box": (580, 1640, 480, 480),  "trim": False},
    "shocked":  {"label": "Shocked",    "size": (1024, 1024), "box": (1120, 1640, 480, 480), "trim": False},
}

# Anything at or above this on every channel counts as background when trimming.
WHITE_THRESHOLD = 245
TRIM_PAD = 0.03


def trim_white(img, threshold=WHITE_THRESHOLD, pad=TRIM_PAD):
    """Crop near-white borders off ``img``, keeping ``pad`` (a fraction of the
    content's larger side) of margin. An all-white image is returned as is."""
    rgb = img.convert("RGB")
    # A pixel is "content" if any channel is below the threshold.
    mask = rgb.point(lambda v: 255 if v < threshold else 0).convert("L")
    # convert("L") averages channels, so a pixel dark in only one channel is
    # still non-zero; getbbox() treats any non-zero as content.
    bbox = mask.getbbox()
    if bbox is None:
        return rgb
    l, t, r, b = bbox
    p = int(round(max(r - l, b - t) * pad))
    l, t = max(0, l - p), max(0, t - p)
    r, b = min(rgb.width, r + p), min(rgb.height, b + p)
    return rgb.crop((l, t, r, b))


def fit_into(canvas, img, box):
    """Scale ``img`` to fit inside ``box`` (x, y, w, h) keeping its aspect
    ratio, and paste it centred on ``canvas`` (in place)."""
    x, y, w, h = box
    scale = min(w / img.width, h / img.height)
    nw = max(1, int(round(img.width * scale)))
    nh = max(1, int(round(img.height * scale)))
    patch = img.convert("RGB")
    if (nw, nh) != patch.size:
        patch = patch.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas.paste(patch, (x + (w - nw) // 2, y + (h - nh) // 2))
    return canvas


def compose_sheet(panel_paths):
    """Composite ``{key: path}`` onto a white SHEET_SIZE canvas. Keys missing
    from ``panel_paths`` leave their box blank."""
    canvas = Image.new("RGB", SHEET_SIZE, BACKGROUND)
    for key, spec in PANELS.items():
        path = panel_paths.get(key)
        if path is None:
            continue
        with Image.open(path) as im:
            img = im.convert("RGB")
        if spec["trim"]:
            img = trim_white(img)
        fit_into(canvas, img, spec["box"])
    return canvas


def default_output_size(width, height, megapixels=1.0, multiple=32):
    """The size an edit workflow would render ``width``×``height`` at when no
    explicit size is given: scaled to ``megapixels`` and snapped to ``multiple``.

    Mirrors the Klein template's ``ImageScaleToTotalPixels(megapixels=1)`` so a
    template that gained ``<WIDTH>``/``<HEIGHT>`` keeps its old output size for
    the plain ✨ image2image button.
    """
    scale = (megapixels * 1_000_000 / (width * height)) ** 0.5
    w = max(multiple, int(round(width * scale / multiple)) * multiple)
    h = max(multiple, int(round(height * scale / multiple)) * multiple)
    return w, h
