"""Image maths for multi-face face-detail (``/api/face-detail-multi``).

The user paints up to four face masks. For each one we cut a square out of
the source image around the mask (plus a margin), face-detail that square on
its own, and paste the result back with an alpha ramp across the margin band
so no square edge shows. This module is the Pillow-only geometry and
compositing; it never imports the app, so it is testable standalone.
"""

from PIL import Image, ImageDraw

DETAIL_SIZE = 1024


def mask_bbox(mask):
    """Bounding box ``(l, t, r, b)`` (exclusive r/b) of the mask's non-black
    pixels, or ``None`` for an empty mask."""
    return mask.convert("L").point(lambda v: 255 if v > 127 else 0).getbbox()


def square_box(bbox, margin, width, height):
    """A square ``(l, t, r, b)`` that fully covers ``bbox`` plus ``margin`` on
    every side, kept inside a ``width`` x ``height`` image.

    The side is capped at the shorter image dimension, so a face larger than
    that is covered as far as a square can cover it. The square is centred on
    the bbox, then shifted (not shrunk) back inside the image at an edge.
    """
    l, t, r, b = bbox
    side = max(r - l, b - t) + 2 * max(0, int(margin))
    side = max(1, min(side, width, height))
    cx = (l + r) / 2
    cy = (t + b) / 2
    left = int(round(cx - side / 2))
    top = int(round(cy - side / 2))
    left = min(max(left, 0), width - side)
    top = min(max(top, 0), height - side)
    return (left, top, left + side, top + side)


def feather_alpha(box, bbox):
    """L-mode alpha mask for pasting the square ``box`` back.

    Fully opaque over the mask's ``bbox`` (in image coords), falling linearly
    to 0 at the square's edge across whatever band lies between them. A side
    with no band (bbox touches the square edge, or margin 0) is hard.
    """
    l, t, r, _ = box
    side = r - l
    alpha = Image.new("L", (side, side), 0)
    draw = ImageDraw.Draw(alpha)
    # Inner box relative to the square, clipped to it.
    il = min(max(bbox[0] - l, 0), side)
    it = min(max(bbox[1] - t, 0), side)
    ir = min(max(bbox[2] - l, 0), side)
    ib = min(max(bbox[3] - t, 0), side)
    band = max(il, it, side - ir, side - ib)
    if band <= 0:
        return Image.new("L", (side, side), 255)
    # Concentric rectangles from the outer edge inwards; each step i moves
    # every side towards the inner box by its own fraction of the gap, so an
    # uneven band (square shifted at the image edge) still ramps smoothly.
    for i in range(band + 1):
        f = i / band
        x0 = round(il * f)
        y0 = round(it * f)
        x1 = round(side - (side - ir) * f) - 1
        y1 = round(side - (side - ib) * f) - 1
        if x1 < x0 or y1 < y0:
            continue
        draw.rectangle((x0, y0, x1, y1), fill=round(255 * f))
    return alpha


def prepare_crop(image, box, target=DETAIL_SIZE):
    """Crop ``box`` from ``image`` and upscale it to ``target`` if smaller.

    Returns the RGB crop; larger crops are sent at native size.
    """
    crop = image.convert("RGB").crop(box)
    side = box[2] - box[0]
    if side < target:
        crop = crop.resize((target, target), Image.Resampling.LANCZOS)
    return crop


def paste_back(base, processed, box, alpha):
    """Resize ``processed`` to ``box`` and paste it onto ``base`` (in place)
    through ``alpha``."""
    side = box[2] - box[0]
    patch = processed.convert(base.mode)
    if patch.size != (side, side):
        patch = patch.resize((side, side), Image.Resampling.LANCZOS)
    base.paste(patch, box[:2], alpha)
    return base
