#!/usr/bin/env python3
"""Collect Vesko Latvia (veskoveikals.lv) promotional offers from its PDF booklet.

Vesko publishes a monthly booklet ("Vesko buklets") linked from the
``#akcijas`` section of https://veskoveikals.lv/ as
``/booklets/<id>/lv/pdf_download`` with a printed validity period
(``02.11.-29.11.2026.``). The PDF keeps a real text layer laid out in three
brochure columns. Each product card stacks:

- an ALL-CAPS product name (≈11 pt) plus small detail lines (≈8 pt) carrying
  the brand, descriptor, package size and a parenthesised unit price;
- a discount badge ``-NN%`` (≈11 pt);
- the sale price as a large cent integer (≈22 pt, e.g. ``099`` → €0.99);
- the struck-through regular price as a slightly smaller cent integer
  (≈20 pt, e.g. ``119`` → €1.19).

Every text run is duplicated at identical coordinates (a shadow layer), so
words are de-duplicated first. The tallest cent token in a card is the sale
price, the next is the regular price; the printed discount badge is used to
validate the pair. Names keep the package size (product identity derives it
from the name) but drop unit prices, discount badges and marketing tags.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
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

BASE = "https://veskoveikals.lv"
HOME = BASE + "/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/vesko-offers.json")
IMG_DIR = Path("data/vesko-img")
RETRIES = 3

BOOKLET_RE = re.compile(
    r'id="bookletModal\d+".*?class="date-title">\s*([^<]+?)\s*</div>.*?'
    r'href="(/booklets/\d+/lv/pdf_download)"', re.S)
PERIOD_RE = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?\s*[–—-]\s*(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?")
CENT_RE = re.compile(r"\d{2,4}")
DISCOUNT_RE = re.compile(r"^-(\d{1,2})%$")
UNIT_TOKEN_RE = re.compile(r"^(kg|g|ml|l|gab\.?|gb|veidi|veids)$", re.I)
NOISE = {"jaunums!", "jaunums", "akcija", "īpaši", "piedāvājumi"}
DATE_TOKEN_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.?[–—-]?$|^\d{1,2}\.\d{1,2}\.\d{4}\.?$")
URL_TOKEN_RE = re.compile(r"www\.|https?:|\.lv\b|\.lua\b", re.I)
# Standalone alcoholic-drink words (non-alcoholic "bezalkoholisk..." is kept).
ALCOHOL_RE = re.compile(
    r"\b(alus|vins|viskijs|degvins|brendijs|konjaks|sampanietis|likieris|"
    r"dzins|roms|tekila|vodka|sidrs|brut|prosecco|kava|lieris)\b")
ALCOHOL_PHRASE_RE = re.compile(r"alkoholisk\w*\s+(dzerien|kokteil)")
PACKAGE_RE = re.compile(r"(?<![a-z0-9])(\d+(?:[,.]\d+)?)\s*(kg|g|ml|l)(?![a-z])", re.I)


def is_alcohol_name(name: str) -> bool:
    folded = _fold(name)
    if "bezalkohol" in folded:
        return False
    if ALCOHOL_PHRASE_RE.search(folded):
        return True
    return bool(ALCOHOL_RE.search(folded))


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def parse_period(text: str, today: date | None = None) -> tuple[str | None, str | None]:
    today = today or date.today()
    m = PERIOD_RE.search(text or "")
    if not m:
        return None, None
    d1, m1, y1, d2, m2, y2 = m.groups()
    year2 = int(y2) if y2 else today.year
    year1 = int(y1) if y1 else (year2 - 1 if int(m2) < int(m1) else year2)
    try:
        return date(year1, int(m1), int(d1)).isoformat(), date(year2, int(m2), int(d2)).isoformat()
    except ValueError:
        return None, None


def discover_booklets(sess: requests.Session) -> list[dict]:
    r = sess.get(HOME, timeout=60)
    r.raise_for_status()
    booklets: list[dict] = []
    seen: set[str] = set()
    for m in BOOKLET_RE.finditer(r.text):
        period_text = m.group(1).strip()
        pdf = urljoin(BASE, m.group(2))
        if pdf in seen:
            continue
        seen.add(pdf)
        bid = re.search(r"/booklets/(\d+)/", pdf).group(1)
        valid_from, valid_until = parse_period(period_text)
        booklets.append({
            "id": "vesko-" + bid,
            "name": "Vesko buklets",
            "pdf": pdf,
            "periodText": period_text,
            "validFrom": valid_from,
            "validUntil": valid_until,
        })
    return booklets


def _dedup(words: list) -> list:
    seen: set = set()
    out = []
    for w in words:
        key = (round(w[0], 1), round(w[1], 1), w[4])
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _cent_value(token: str) -> float | None:
    if not CENT_RE.fullmatch(token):
        return None
    val = int(token)
    if 15 <= val <= 9999:
        return round(val / 100.0, 2)
    return None


def _is_unit_price_number(token: str, next_token: str | None) -> bool:
    """A decimal immediately followed by a euro/unit token is a unit price."""
    if not re.fullmatch(r"\(?\d+[,.]\d+", token):
        return False
    return bool(next_token and "€" in next_token)


def _clean_name(tokens: list[str]) -> str:
    kept: list[str] = []
    for i, tok in enumerate(tokens):
        t = tok.strip()
        if not t:
            continue
        if "€" in t:
            continue
        if DISCOUNT_RE.match(t):
            continue
        if _fold(t) in NOISE:
            continue
        if URL_TOKEN_RE.search(t):
            continue
        if DATE_TOKEN_RE.match(t) or re.fullmatch(r"Nr\.?|\d+\)|[–—-]", t):
            continue
        if len(t) == 1 and t.isupper():      # vertical-text letter artifacts
            continue
        if re.match(r"^\(no\b", t, re.I):    # "(no 17,95 €/kg)" intro
            continue
        nxt = tokens[i + 1].strip() if i + 1 < len(tokens) else None
        if _is_unit_price_number(t, nxt):
            continue
        # bare decimal unit-price leftovers like "(3,96" after €/kg removed
        if re.fullmatch(r"\(\d+[,.]\d+", t):
            continue
        kept.append(t)
    text = " ".join(kept)
    text = re.sub(r"\(\s*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–•*:,()")
    # Title-case ALL-CAPS words, keep mixed-case brand words as printed.
    words = []
    for w in text.split():
        if w.isupper() and len(w) > 1 and re.search(r"[A-ZĀČĒĢĪĶĻŅŠŪŽ]", w):
            words.append(w.capitalize())
        else:
            words.append(w)
    text = " ".join(words)
    text = re.sub(r"^Nr\.?\s*", "", text)        # cover booklet number
    text = re.sub(r"^(kg|g|ml|l)\s+", "", text)  # stray leading unit
    text = re.sub(r"(?<![0-9])\s+(kg|g|ml|l)$", "", text)  # stray bare trailing unit
    text = re.sub(r"\s+(vai|ar)$", "", text)   # trailing conjunction
    return text[:110]


def _price_tokens(words: list) -> list:
    """Large cent integers (sale + regular), de-duplicated by position so the
    overlapping euro/cent partial runs collapse to the full value."""
    raw = []
    for w in words:
        h = w[3] - w[1]
        if h < 18:
            continue
        val = _cent_value(w[4])
        if val is None:
            continue
        raw.append((w[0], w[1], w[2], w[3], w[4], val, h))
    # collapse tokens sharing a position: keep the longest (full) string
    by_pos: dict = {}
    for item in raw:
        key = (round(item[0] / 3), round(item[1] / 3))
        if key not in by_pos or len(item[4]) > len(by_pos[key][4]):
            by_pos[key] = item
    return list(by_pos.values())


def _cluster_columns(items: list, width: float):
    if not items:
        return [], [0.0, width]
    xs = sorted(item[0] for item in items)
    bounds = [0.0]
    centres = []
    group = [xs[0]]
    for x in xs[1:]:
        if x - group[-1] > 55:
            centres.append(sum(group) / len(group))
            group = [x]
        else:
            group.append(x)
    centres.append(sum(group) / len(group))
    for i in range(len(centres) - 1):
        bounds.append((centres[i] + centres[i + 1]) / 2)
    bounds.append(width)
    cols: list[list] = [[] for _ in centres]
    for item in items:
        idx = min(range(len(centres)), key=lambda i: abs(item[0] - centres[i]))
        cols[idx].append(item)
    return cols, bounds


def extract_page(page, page_num: int, booklet: dict, img_dir: Path | None) -> tuple[list, list]:
    words = _dedup(page.get_text("words", sort=True))
    if not words:
        return [], []
    width = float(page.rect.width)
    prices = _price_tokens(words)
    if not prices:
        return [], []
    cols, bounds = _cluster_columns(prices, width)

    offers, reviews = [], []
    seen: set[str] = set()
    for ci, col in enumerate(cols):
        col_lo, col_hi = bounds[ci], bounds[ci + 1]
        col.sort(key=lambda t: t[1])
        # group price tokens into cards (sale+regular close vertically)
        cards: list[list] = []
        for item in col:
            if cards and item[1] - cards[-1][-1][1] < 45:
                cards[-1].append(item)
            else:
                cards.append([item])
        prev_bottom = 0.0
        for card in cards:
            sale_item = max(card, key=lambda t: t[6])
            sale = sale_item[5]
            sale_y0, sale_y1 = sale_item[1], sale_item[3]
            others = [t for t in card if t is not sale_item]
            regular = None
            reg_item = None
            higher = [t for t in others if t[5] > sale + 0.001]
            if higher:
                reg_item = max(higher, key=lambda t: t[6])
                regular = reg_item[5]
            price_bottom = max(t[3] for t in card)
            # discount badge just above the sale price
            badge = None
            for w in words:
                if not (col_lo <= (w[0] + w[2]) / 2 < col_hi):
                    continue
                m = DISCOUNT_RE.match(w[4])
                if m and sale_y0 - 26 <= w[1] <= sale_y0 - 1:
                    badge = int(m.group(1))
                    break
            # name band: above the sale price, below the previous card;
            # capped so an unpriced neighbour cannot leak into the band
            band_top = max(prev_bottom + 6, sale_y0 - 118)
            band_bottom = sale_y0 - 15
            name_tokens = []
            for w in words:
                cx = (w[0] + w[2]) / 2
                h = w[3] - w[1]
                if not (col_lo <= cx < col_hi):
                    continue
                if not (7 <= h <= 12.5):
                    continue
                if not (band_top <= w[1] <= band_bottom):
                    continue
                name_tokens.append((w[1], w[0], w[4]))
            name_tokens.sort(key=lambda t: (round(t[0] / 3), t[1]))
            name = _clean_name([t[2] for t in name_tokens])
            x_span = (max(t[1] for t in name_tokens) - min(t[1] for t in name_tokens)) if name_tokens else 0
            prev_bottom = price_bottom

            if len(name) < 4:
                continue
            if is_alcohol_name(name):
                continue
            key = f"{name.casefold()}|{sale:.2f}|{page_num}"
            if key in seen:
                continue
            seen.add(key)
            offer = {
                "offerId": f"vesko-{booklet['id']}-p{page_num}-{hashlib.sha1(key.encode()).hexdigest()[:8]}",
                "name": name,
                "brand": None,
                "price": sale,
                "regularPrice": regular,
                "requiresCard": False,
                "onlyInApp": False,
                "campaign": booklet["name"],
                "campaignId": booklet["id"],
                "source": booklet["pdf"],
                "pdfPage": page_num + 1,
                "validFrom": booklet.get("validFrom"),
                "validUntil": booklet.get("validUntil"),
                "store": "vesko",
            }
            if badge is not None:
                offer["discountPercent"] = badge
            # validate the sale/regular pair against the printed badge
            mismatch = False
            if regular is not None and badge is not None:
                expected = round((regular - sale) / regular * 100)
                if abs(expected - badge) > 4:
                    mismatch = True
            if regular is None and badge is not None:
                mismatch = True  # badge implies a reference price we missed
            folded = _fold(name)
            sizes = {m[0] for m in PACKAGE_RE.findall(folded)}
            combo = bool(re.search(r"[+/]|\d\s*[x×]\s*\d|\s[x×]\s", name))
            merged = (len(sizes) >= 2 and not combo) or x_span > 100
            first = name.split()[0].casefold() if name.split() else ""
            weak = (
                len(name) < 6
                or len(re.findall(r"[a-zāčēģīķļņšūž]{3,}", folded)) < 1
                or first in {"ar", "vai", "no", "un", "ar,", "vai,", "no,"}
                or first[:1].isdigit()
                or name.count(",") >= 3
                or "€/" in name
            )
            if mismatch:
                offer["reviewReason"] = "price_mismatch"
                reviews.append(offer)
            elif merged:
                offer["reviewReason"] = "merged_name"
                reviews.append(offer)
            elif weak:
                offer["reviewReason"] = "weak_name"
                reviews.append(offer)
            else:
                if img_dir is not None:
                    photo = save_product_photo(page, col_lo, col_hi,
                                               sale_y0, price_bottom, band_top,
                                               img_dir, offer["offerId"])
                    if photo:
                        offer["image"] = photo
                offers.append(offer)
    return offers, reviews


def extract_pdf(pdf_path: Path, booklet: dict, img_dir: Path | None) -> tuple[list, list]:
    if fitz is None:
        raise RuntimeError("PyMuPDF is required for PDF parsing")
    doc = fitz.open(pdf_path)
    all_offers, all_review = [], []
    try:
        for page_num, page in enumerate(doc):
            o, r = extract_page(page, page_num, booklet, img_dir)
            all_offers.extend(o)
            all_review.extend(r)
    finally:
        doc.close()
    return all_offers, all_review


def main() -> None:
    if fitz is None:
        print("PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)
    sess = session()
    print("Discovering Vesko booklets...")
    booklets = discover_booklets(sess)
    if not booklets:
        raise RuntimeError("No Vesko booklets discovered")
    # keep the booklet whose period covers today, else the first (current) one
    # unless its printed period has already ended
    today = date.today().isoformat()
    active = [b for b in booklets if b.get("validFrom") and b.get("validUntil")
              and b["validFrom"] <= today <= b["validUntil"]]
    chosen = active or [b for b in booklets[:1]
                        if not (b.get("validUntil") and b["validUntil"] < today)]
    print(f"Found {len(booklets)} booklet(s); using {len(chosen)}")

    all_offers, all_review, catalogues = [], [], []
    for booklet in chosen:
        print(f"Downloading {booklet['pdf']} [{booklet.get('validFrom')} – {booklet.get('validUntil')}]")
        r = sess.get(booklet["pdf"], timeout=120)
        r.raise_for_status()
        pdf_path = Path("tmp/pdfs") / f"{booklet['id']}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(r.content)
        try:
            offers, review = extract_pdf(pdf_path, booklet, None)
        finally:
            pdf_path.unlink(missing_ok=True)
        print(f"  -> {len(offers)} offers, {len(review)} review")
        all_offers.extend(offers)
        all_review.extend(review)
        catalogues.append({
            "id": booklet["id"], "name": booklet["name"], "source": booklet["pdf"],
            "validFrom": booklet.get("validFrom"), "validUntil": booklet.get("validUntil"),
            "verifiedOffers": len(offers), "reviewOffers": len(review),
            "complete": len(offers) > 0,
        })

    unique = {o["offerId"]: o for o in all_offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in all_review}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())
    products = build_product_groups(offers) if offers else []
    prune_images(IMG_DIR, {o["image"] for o in offers if o.get("image")})

    payload = {
        "source": HOME,
        "store": "vesko",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogues": catalogues,
        "offers": offers,
        "products": products,
        "reviewOffers": reviews,
        "summary": {
            "offerVariants": len(offers), "uniqueProducts": len(products),
            "reviewOffers": len(reviews), "catalogues": len(catalogues),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} offers ({len(reviews)} review) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Vesko scraper failed: {exc}", file=sys.stderr)
        raise