#!/usr/bin/env python3
"""Collect Lidl Latvia (lidl.lv) promotional offers from weekly booklet PDFs.

Lidl's Latvian storefront does not expose a structured product feed (the leaflet
API returns ``products: []`` and ``showProductDetails: false``), so offers are
read from the booklet PDF text layer with word coordinates, the same approach
used for Mego.

Discovery:

- the booklet landing page ``https://www.lidl.lv/c/lv-LV/akciju-bukleti/s10024390``
  links to every active leaflet viewer ``/l/lv/akciju-buklets/<slug>/ar/0``;
- for each slug the public leaflet API
  ``GET https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier=<slug>&region_id=0&region_code=0``
  returns the booklet name, validity dates and a direct ``pdfUrl``.

Extraction (per PDF page):

- the large sale price (text height >= 28 pt) anchors a product card;
- columns are clustered from the anchor x positions; within a column the product
  name is the block of words above (and to the left of) the anchor, below the
  previous anchor;
- a small decimal to the right of the anchor is the crossed-out regular price
  (unit prices such as ``1 kg = 16,14 €`` sit inside the name block and are
  ignored);
- ``Ar Lidl Plus`` near the card flags ``requiresCard`` (card name ``Lidl Plus``).

Only cards with a clean name and a finite sale price are promoted to ``offers``;
ambiguous cards (empty/short/merged names, marketing copy, date banners) go to
``reviewOffers`` and are not shown on the site. Regular prices are kept only when
confidently detected, otherwise ``null`` — the comparator never guesses a
discount from a unit price.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import requests

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

try:
    from scripts.booklet_images import prune_images, save_product_photo
except ModuleNotFoundError:
    from booklet_images import prune_images, save_product_photo

BASE = "https://www.lidl.lv"
BOOKLETS_PAGE = BASE + "/c/lv-LV/akciju-bukleti/s10024390"
LEAFLET_API = "https://endpoints.leaflets.schwarz/v4/flyer"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/lidl-offers.json")

DEC_RE = re.compile(r"^(\d{1,3})[,.](\d{2})$")
DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.")
NOISE_TOK = {
    "€", "/", "=", "ar", "lidl", "plus", "no", "vai", "gab.", "kg", "g", "ml", "l",
    "mazg.", "slāņi", "loksnes", "iepak.", "gab", "cm", "mm", "den", "x", "×", "un",
}
NOISE_SUB = (
    "pirmdien", "nedēļ", "ierobežots", "www.", "lidl.lv", "atlaide", "nesummējas",
    "piemērota", "lētākajam", "izvietotas", "stendā", "produktu", "izlasei", "precei",
    "pērkot", "atlasītajam", "atlasīto", "nenokavē", "esmaspäev", "kuni", "vērtīgi",
    "ietaupīt", "cena", "lētāk", "piedāvājums", "spēkā", "izmantojot", "lietotni",
    "konkursa", "uzvarētājs", "pasaulē", "novērtēta", "marm", "sulīgiem", "challenge",
    "izlase", "akcija", "svaigi", "augļi", "dārzeņi",
)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def dec_val(token: str) -> float | None:
    m = DEC_RE.match(token)
    if not m:
        return None
    value = float(f"{m.group(1)}.{m.group(2)}")
    return value if 0.10 < value < 200 else None


def _cluster(values: list[float], gap: float) -> list[float]:
    groups: list[list[float]] = []
    for v in sorted(values):
        if not groups or v - groups[-1][-1] > gap:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [sum(g) / len(g) for g in groups]


def discover_booklets(sess: requests.Session) -> list[dict]:
    """Find active leaflet slugs and resolve their metadata + PDF via the API."""
    r = sess.get(BOOKLETS_PAGE, timeout=30)
    r.raise_for_status()
    slugs = sorted(set(re.findall(r"/l/lv/akciju-buklets/([a-z0-9-]+)/ar/0", r.text)))
    booklets: list[dict] = []
    for slug in slugs:
        try:
            ar = sess.get(LEAFLET_API, params={
                "flyer_identifier": slug, "region_id": "0", "region_code": "0",
            }, timeout=30)
            ar.raise_for_status()
            flyer = (ar.json() or {}).get("flyer") or {}
        except Exception as exc:
            print(f"  ! {slug}: {exc}", file=sys.stderr)
            continue
        if not flyer.get("pdfUrl") or not flyer.get("isActive"):
            continue
        booklets.append({
            "id": slug,
            "name": flyer.get("name") or slug,
            "title": flyer.get("title") or "",
            "pdf": flyer["pdfUrl"],
            "preview": flyer.get("thumbnailUrl"),
            "validFrom": flyer.get("offerStartDate") or flyer.get("startDate"),
            "validUntil": flyer.get("offerEndDate") or flyer.get("endDate"),
            "status": flyer.get("status"),
        })
    return booklets


def _clean_name(parts: list[str]) -> str:
    text = re.sub(r"\s+", " ", " ".join(parts)).strip(" -–•*:,+")
    tokens = text.split()
    # collapse consecutive duplicate words (PILOS PILOS -> PILOS)
    dedup: list[str] = []
    for tok in tokens:
        if not dedup or dedup[-1].casefold() != tok.casefold():
            dedup.append(tok)
    return " ".join(dedup)[:90]


def extract_page(page, page_num: int, booklet: dict, img_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    words = page.get_text("words")
    if not words:
        return [], []
    text = page.get_text("text")
    if "Product name" in text or text.count("XXX") >= 3 or "x,xx" in text:
        return [], []

    width = float(page.rect.width)
    anchors = []
    for w in words:
        x0, y0, x1, y1, tok = w[:5]
        value = dec_val(tok)
        if value is not None and (y1 - y0) >= 28:
            anchors.append({"cx": (x0 + x1) / 2, "x0": x0, "x1": x1,
                            "y0": y0, "y1": y1, "v": value})
    if len(anchors) < 2:
        return [], []

    cols = _cluster([a["cx"] for a in anchors], 60)
    xb = [0] + [(cols[i] + cols[i + 1]) / 2 for i in range(len(cols) - 1)] + [width]
    for a in anchors:
        a["col"] = next(i for i in range(len(cols)) if xb[i] <= a["cx"] < xb[i + 1])

    offers: list[dict] = []
    reviews: list[dict] = []
    seen: set[str] = set()
    for ci in range(len(cols)):
        col_anchors = sorted([a for a in anchors if a["col"] == ci], key=lambda a: a["y0"])
        x_lo, x_hi = xb[ci], xb[ci + 1]
        prev_bottom = 0.0
        for a in col_anchors:
            sale = a["v"]
            name_parts: list[tuple[float, float, str]] = []
            regular = None
            has_plus = False
            for w in words:
                x0, y0, x1, y1, tok = w[:5]
                wx, wy = (x0 + x1) / 2, (y0 + y1) / 2
                h = y1 - y0
                if not (x_lo <= wx < x_hi):
                    continue
                low = tok.casefold()
                if low == "plus" and prev_bottom <= wy < a["y1"] + 30:
                    has_plus = True
                value = dec_val(tok)
                if (value is not None and 10 <= h <= 13.5 and x0 >= a["x1"] - 4
                        and a["y0"] - 12 <= wy <= a["y1"] and sale * 1.08 < value < sale * 4):
                    regular = max(regular or 0, value)
                above = prev_bottom - 2 <= wy < a["y0"] - 2
                left = wx < a["x0"] - 2 and a["y0"] - 2 <= wy <= a["y1"]
                if above or left:
                    if value is not None or re.fullmatch(r"-?\d+%?", tok) or re.fullmatch(r"[\d.,]+", tok):
                        continue
                    if DATE_RE.search(tok) or h < 9.5 or h >= 17:
                        continue
                    if low in NOISE_TOK or re.fullmatch(r"\d+", tok):
                        continue
                    if any(s in low for s in NOISE_SUB):
                        continue
                    name_parts.append((wy, wx, tok))
            name_parts.sort()
            name = _clean_name([t for _, _, t in name_parts])

            key = f"{name.casefold()}|{sale:.2f}|{page_num}"
            if key in seen:
                continue
            seen.add(key)
            offer = {
                "offerId": f"lidl-{booklet['id']}-p{page_num}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}",
                "name": name,
                "price": sale,
                "regularPrice": regular,
                "requiresCard": has_plus,
                "onlyInApp": False,
                "campaign": booklet["name"],
                "campaignId": booklet["id"],
                "source": booklet["pdf"],
                "pdfPage": page_num + 1,
                "validFrom": booklet.get("validFrom"),
                "validUntil": booklet.get("validUntil"),
                "store": "lidl",
            }
            if has_plus:
                offer["cardName"] = "Lidl Plus"

            folded = name.casefold()
            name_words = name.split()
            words_count = len(name_words)
            lowered = [w.casefold() for w in name_words]
            has_dup = len(lowered) != len(set(lowered))
            brand_caps = len(re.findall(r"\b[A-ZĀČĒĢĪĶĻŅŠŪŽ]{3,}\b", name))
            weak = (
                len(name) < 6
                or len(re.findall(r"[a-zāčēģīķļņšūž]{3,}", folded)) < 2
                or words_count > 7
                or has_dup
                or brand_caps >= 2
                or folded.startswith(("www", "no ", "ar ", "cena"))
                or DATE_RE.search(name)
                or name.count(",") >= 3
            )
            if weak:
                offer["reviewReason"] = "weak_name"
                reviews.append(offer)
            else:
                if img_dir is not None:
                    photo = save_product_photo(page, x_lo, x_hi, a["y0"], a["y1"],
                                               prev_bottom, img_dir, offer["offerId"])
                    if photo:
                        offer["image"] = photo
                offers.append(offer)
            prev_bottom = a["y1"]
    return offers, reviews


def extract_offers_from_pdf(pdf_path: Path, booklet: dict, img_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF parsing")
    doc = fitz.open(pdf_path)
    all_offers: list[dict] = []
    all_review: list[dict] = []
    try:
        for page_num, page in enumerate(doc):
            offers, review = extract_page(page, page_num, booklet, img_dir)
            all_offers.extend(offers)
            all_review.extend(review)
    finally:
        doc.close()
    return all_offers, all_review


def main() -> None:
    if fitz is None:
        print("PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)
    sess = session()
    print("Discovering Lidl booklets...")
    booklets = discover_booklets(sess)
    if not booklets:
        raise RuntimeError("No Lidl booklets discovered")
    today = date.today().isoformat()
    for b in booklets:
        print(f"  - {b['name']} / {b['title']} [{b['validFrom']} – {b['validUntil']}] {b['status']}")

    all_offers: list[dict] = []
    all_review: list[dict] = []
    catalogues = []
    for booklet in booklets:
        # Skip booklets whose offer window has not started or already ended.
        if booklet.get("validFrom") and booklet["validFrom"] > today:
            continue
        if booklet.get("validUntil") and booklet["validUntil"] < today:
            continue
        print(f"Downloading {booklet['pdf']} ...")
        r = sess.get(booklet["pdf"], timeout=240)
        r.raise_for_status()
        pdf_path = Path("tmp/pdfs") / f"lidl_{booklet['id']}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(r.content)
        try:
            offers, review = extract_offers_from_pdf(pdf_path, booklet, Path("data/lidl-img"))
        finally:
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass
        print(f"  -> {len(offers)} offers, {len(review)} need review")
        all_offers.extend(offers)
        all_review.extend(review)
        catalogues.append({
            "id": booklet["id"],
            "name": booklet["name"],
            "title": booklet["title"],
            "pdf": booklet["pdf"],
            "preview": booklet["preview"],
            "validFrom": booklet.get("validFrom"),
            "validUntil": booklet.get("validUntil"),
            "verifiedOffers": len(offers),
            "reviewOffers": len(review),
            "complete": len(offers) > 0,
        })

    unique = {o["offerId"]: o for o in all_offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in all_review}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())
    products = build_product_groups(offers) if offers else []
    prune_images(Path("data/lidl-img"), {o["image"] for o in offers if o.get("image")})

    payload = {
        "source": BOOKLETS_PAGE,
        "store": "lidl",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogues": catalogues,
        "offers": offers,
        "products": products,
        "reviewOffers": reviews,
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(products),
            "reviewOffers": len(reviews),
            "catalogues": len(catalogues),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} offers ({len(reviews)} review) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Lidl scraper failed: {exc}", file=sys.stderr)
        raise