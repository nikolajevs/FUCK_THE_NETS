"""Extract clean product photos from supermarket booklet PDFs.

Maxima/top! expose product images through their APIs, but Mego and Lidl only
publish PDF booklets. Those PDFs keep a real text layer *and* place each product
photo as a separate embedded image, so a photo can be recovered without any
printed text by matching its placement rectangle to the product card:

- the rectangle must stay inside the card's brochure column (page-wide banners
  and background art span several columns and are rejected);
- its bottom edge must sit at the sale-price block (photos end where the price
  begins), and its top must stay inside the card's vertical band;
- tiny logos/badges are rejected by minimum dimensions.

The largest qualifying image wins. Files are written next to the feed under
``data/<store>-img/<offerId>.<ext>`` and referenced by ``offer.image``.
"""
from __future__ import annotations

from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

MIN_W = 28.0
MIN_H = 34.0
MAX_W = 620


def find_product_photo(page, col_lo: float, col_hi: float,
                       price_y0: float, price_y1: float, band_top: float):
    """Return (xref, ext, width, height) of the best photo for a card, or None."""
    doc = page.parent
    best = None
    for item in page.get_images(full=True):
        xref = item[0]
        width, height = item[2], item[3]
        if width < MIN_W or height < MIN_H or width > MAX_W:
            continue
        for rect in page.get_image_rects(xref):
            if rect.x0 < col_lo - 6 or rect.x1 > col_hi + 6:
                continue
            if not (price_y0 - 10 <= rect.y1 <= price_y1 + 14):
                continue
            if rect.y0 < band_top - 30:
                continue
            area = rect.width * rect.height
            if best is None or area > best[0]:
                best = (area, xref)
    if best is None:
        return None
    info = doc.extract_image(best[1])
    return best[1], info["ext"], info["width"], info["height"]


def save_product_photo(page, col_lo: float, col_hi: float,
                       price_y0: float, price_y1: float, band_top: float,
                       out_dir: Path, offer_id: str) -> str | None:
    """Write the matched photo to ``out_dir`` and return its site-relative URL."""
    found = find_product_photo(page, col_lo, col_hi, price_y0, price_y1, band_top)
    if found is None:
        return None
    xref, ext, _, _ = found
    doc = page.parent
    pix = fitz.Pixmap(doc, xref)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    cs = pix.colorspace
    if cs is None or cs.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "png" if ext == "png" else "jpg"
    target = out_dir / f"{offer_id}.{suffix}"
    try:
        pix.save(target)
    except Exception:
        # Unsupported colorspace/encoder edge cases: skip the photo, keep the offer.
        target.unlink(missing_ok=True)
        return None
    return f"{out_dir.as_posix()}/{offer_id}.{suffix}"


def prune_images(out_dir: Path, keep_urls: set[str]) -> None:
    """Delete photo files no longer referenced by the current feed."""
    if not out_dir.exists():
        return
    keep_names = {Path(url).name for url in keep_urls}
    for file in out_dir.iterdir():
        if file.is_file() and file.name not in keep_names:
            file.unlink(missing_ok=True)