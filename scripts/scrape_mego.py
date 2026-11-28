#!/usr/bin/env python3
"""Collect Mego Latvia promotional offers from weekly booklets and active campaigns."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    from scripts.product_identity import build_product_groups, product_identity
except ModuleNotFoundError:
    from product_identity import build_product_groups, product_identity

try:
    from scripts.booklet_images import prune_images, save_product_photo
except ModuleNotFoundError:
    from booklet_images import prune_images, save_product_photo

OFFERS_PAGE = "https://mego.lv/akcijas-un-piedavajumi/"
WP_API = "https://mego.lv/wp-json/wp/v2/pages?slug=akcijas-un-piedavajumi"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)"
}
OUT = Path("data/mego-offers.json")

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3})(?:[,.]\s*|\s+)(\d{2})(?!\d)")
DISCOUNT_RE = re.compile(r"-\s*(\d{1,2})\s*%")
MONTHS = {
    "janvāris": 1, "janv": 1, "februāris": 2, "febr": 2,
    "marts": 3, "mar": 3, "aprīlis": 4, "apr": 4,
    "maijs": 5, "mai": 5, "jūnijs": 6, "jūn": 6,
    "jūlijs": 7, "jūl": 7, "augusts": 8, "aug": 8,
    "septembris": 9, "sept": 9, "oktobris": 10, "okt": 10,
    "novembris": 11, "nov": 11, "decembris": 12, "dec": 12,
}


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def parse_period(text: str, today: date | None = None) -> tuple[str | None, str | None]:
    """Parse Latvian date ranges like '30.11. – 06.12.' or '30.11.2026 – 06.12.2026'."""
    today = today or date.today()
    text = text.replace("&#8211;", "–").replace("&ndash;", "–")
    m = re.search(
        r"(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?\s*[–—-]\s*(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?",
        text,
    )
    if not m:
        return None, None
    d1, m1, y1, d2, m2, y2 = m.groups()
    year2 = int(y2) if y2 else today.year
    year1 = int(y1) if y1 else (year2 - 1 if int(m2) < int(m1) else year2)
    try:
        start = date(year1, int(m1), int(d1))
        end = date(year2, int(m2), int(d2))
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None, None


def discover_booklets(sess: requests.Session) -> list[dict]:
    """Discover active PDF booklets from the WordPress offers page."""
    r = sess.get(WP_API, timeout=30)
    r.raise_for_status()
    pages = r.json()
    if not pages:
        raise RuntimeError("No offers page found via WP API")
    html = pages[0].get("content", {}).get("rendered", "")
    # Unescape JSON-embedded URLs
    cleaned = html.replace("\\/", "/")

    booklets: list[dict] = []
    # Match promotion cards with PDF source
    for block in re.finditer(
        r'data-promotion="([^"]+)".*?source["\s:]+["\']?(https://mego\.lv/wp-content/uploads/[^"\']+\.pdf)',
        cleaned,
        re.DOTALL | re.IGNORECASE,
    ):
        promo_id = block.group(1)
        pdf_url = block.group(2)
        # Try to find nearby title and period
        title_m = re.search(r"<h3>([^<]+)</h3>", cleaned[max(0, block.start() - 800) : block.start()])
        period_m = re.search(
            r"Aktuālais periods:\s*([^<]+)",
            cleaned[max(0, block.start() - 800) : block.end() + 200],
            re.IGNORECASE,
        )
        name = title_m.group(1).strip() if title_m else promo_id
        period_text = period_m.group(1).strip() if period_m else ""
        valid_from, valid_until = parse_period(period_text)
        booklets.append(
            {
                "id": promo_id,
                "name": name,
                "pdf": pdf_url,
                "validFrom": valid_from,
                "validUntil": valid_until,
                "periodText": period_text,
            }
        )

    # Fallback: any PDF links
    if not booklets:
        for pdf in sorted(set(re.findall(r"https://mego\.lv/wp-content/uploads/[^\s\"<>]+\.pdf", cleaned))):
            booklets.append(
                {
                    "id": Path(pdf).stem.lower(),
                    "name": Path(pdf).stem,
                    "pdf": pdf,
                    "validFrom": None,
                    "validUntil": None,
                    "periodText": "",
                }
            )
    return booklets


def _is_cent_price(token: str) -> float | None:
    """Interpret bare integers like 129 / 149 as euro prices (1.29 / 1.49)."""
    if not re.fullmatch(r"\d{2,4}", token):
        return None
    val = int(token)
    if 15 <= val <= 4999:  # 0.15 – 49.99
        return round(val / 100.0, 2)
    return None


def _is_decimal_price(token: str) -> float | None:
    m = re.fullmatch(r"(\d{1,3})[,.](\d{2})", token)
    if not m:
        return None
    val = float(f"{m.group(1)}.{m.group(2)}")
    if 0.15 < val < 80:
        return val
    return None


def _clean_name(parts: list[str]) -> str:
    """Join and clean product name tokens, removing duplicates from multi-column layout."""
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text).strip(" -–•*:")
    # Drop leading/trailing pure numbers, discounts, colons, stars
    text = re.sub(r"^[\d\s%.,:\-*]+", "", text)
    text = re.sub(r"[\d\s%.,:\-*]+$", "", text)
    text = text.strip()
    # De-duplicate consecutive repeated halves (common in dual-column extraction)
    tokens = text.split()
    n = len(tokens)
    if n >= 4 and n % 2 == 0 and tokens[: n // 2] == tokens[n // 2 :]:
        text = " ".join(tokens[: n // 2])
    # Remove common noise labels and marketing slogans
    for noise in (
        r"ar\s+mego\s+karti?", r"regulārā?\s+cena", r"tikai\s+lietotnē",
        r"produkcijai", r"akcijas\s+cena", r"iepriekšējā\s+cena",
        r"cel\s+galdā", r"pārbaudītas\s+vērtības", r"kā\s+svētkos",
        r"tā\s+ikdienā", r"preces\s+nav\s+pieejamas", r"\*\*",
    ):
        text = re.sub(noise, " ", text, flags=re.IGNORECASE)
    # Strip unit-price fragments left in the name
    text = re.sub(r"€\s*/\s*(?:kg|l|rullis|gab)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"€\s*/|/\s*(?:kg|l)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -–•*:")
    return text[:90]


def _is_size_word(word, words: list[tuple]) -> bool:
    """Recognize dimension/package tokens that can look like cent prices."""
    token = str(word[4]).casefold().strip(".,:")
    units = {"kg", "g", "ml", "l", "cm", "mm", "gab", "gb", "iepak"}
    if token in units:
        return True
    if not re.fullmatch(r"\d+(?:[,.]\d+)?", token):
        return False

    line_words = sorted(
        (
            other for other in words
            if int(other[5]) == int(word[5]) and int(other[6]) == int(word[6])
        ),
        key=lambda other: int(other[7]),
    )
    position = next((i for i, other in enumerate(line_words) if other is word), None)
    if position is None:
        # PDF word tuples can be copied while sorting, so use their stable fields.
        position = next(
            (
                i for i, other in enumerate(line_words)
                if int(other[7]) == int(word[7]) and str(other[4]) == str(word[4])
            ),
            None,
        )
    if position is None:
        return False
    previous = str(line_words[position - 1][4]).casefold().strip(".,:") if position else ""
    following = (
        str(line_words[position + 1][4]).casefold().strip(".,:")
        if position + 1 < len(line_words) else ""
    )
    return following in units | {"x", "×"} or previous in {"x", "×"}


def _extract_from_page(page, page_num: int, booklet: dict, img_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Coordinate-aware extraction for one Mego PDF page."""
    words = page.get_text("words", sort=True)  # x0,y0,x1,y1,word,block,line,word_no
    if not words:
        return [], []

    width = float(page.rect.width)
    height = float(page.rect.height)

    # Identify price tokens with their positions. In Mego leaflets the sale
    # price is large and the crossed-out price is smaller; unit prices and
    # package dimensions are body text and should not become shelf prices.
    price_items: list[tuple[float, float, float, str, float]] = []  # cx, cy, value, raw, height
    seen_prices: set[tuple[float, float, float]] = set()
    for w in words:
        x0, y0, x1, y1, token = w[0], w[1], w[2], w[3], w[4]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        val = None if _is_size_word(w, words) else (_is_decimal_price(token) or _is_cent_price(token))
        if val is not None:
            height = float(y1 - y0)
            key = (round(cx, 1), round(cy, 1), round(val, 2))
            if key not in seen_prices:
                seen_prices.add(key)
                price_items.append((cx, cy, val, token, height))

    # Some PDFs duplicate each text layer at identical coordinates. Exclude
    # small package/unit-price text when this page also has proper price labels.
    if any(item[4] >= 12 for item in price_items):
        price_items = [item for item in price_items if item[4] >= 12]

    if not price_items:
        return [], []

    # Price labels from different brochure columns can be only a few points
    # apart vertically. Cluster x positions first, then group prices by row
    # within each column.
    x_clusters: list[list[float]] = []
    for cx in sorted(item[0] for item in price_items):
        if not x_clusters or cx - x_clusters[-1][-1] > max(24, width * 0.08):
            x_clusters.append([cx])
        else:
            x_clusters[-1].append(cx)
    x_centres = [sum(group) / len(group) for group in x_clusters]
    x_bounds = [0.0] + [(x_centres[i] + x_centres[i + 1]) / 2 for i in range(len(x_centres) - 1)] + [width]
    assigned = [
        (*item, min(range(len(x_centres)), key=lambda index: abs(item[0] - x_centres[index])))
        for item in price_items
    ]

    assigned.sort(key=lambda t: (t[1], t[0]))  # by y then x
    cards: list[list[tuple[float, float, float, str, float, int]]] = []
    for item in assigned:
        placed = False
        for card in cards:
            if item[5] == card[0][5] and abs(item[1] - card[0][1]) < 45:
                card.append(item)
                placed = True
                break
        if not placed:
            cards.append([item])

    offers = []
    reviews = []
    seen: set[str] = set()

    columns = len(x_centres)
    for card in cards:
        # Card centre
        cxs = [p[0] for p in card]
        cys = [p[1] for p in card]
        min_cy, max_cy = min(cys), max(cys)
        column_index = card[0][5]
        column_left = column_index * width / columns
        column_right = (column_index + 1) * width / columns

        # Keep title words inside this brochure column; adjacent columns often
        # share the same vertical price baseline.
        name_tokens: list[tuple[float, str]] = []
        for w in words:
            x0, y0, x1, y1, token = w[0], w[1], w[2], w[3], w[4]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if not column_left <= cx < column_right:
                continue
            if float(x1) < 18:
                continue
            if cy > max_cy + 8:
                continue
            if cy < min_cy - 70:
                continue
            if _is_cent_price(token) or _is_decimal_price(token):
                continue
            token_folded = token.casefold().strip(".,:")
            if re.fullmatch(r"-?\d+%", token) or token_folded in {
                "€", "kg", "g", "l", "ml", "cm", "mm", "gab", "gb", "iepak",
                "/", "=", "x", "×",
            }:
                continue
            if re.fullmatch(r"[\d.,]+", token):
                continue
            # Skip store lists, footers, category markers
            low = token.casefold()
            if any(s in low for s in (
                "veikal", "rīgā", "valmier", "ventspil", "pieejam", "ielā",
                "biķerniek", "ģertrūd", "stacijas", "inženieru", "purvciem",
            )):
                continue
            if re.fullmatch(r"\([A-D]\)", token):
                continue
            name_tokens.append((cy, token))

        name_tokens.sort(key=lambda t: t[0])
        cleaned_tokens = []
        for _, tok in name_tokens:
            if not cleaned_tokens or cleaned_tokens[-1] != tok:
                cleaned_tokens.append(tok)
        name = _clean_name(cleaned_tokens)
        if len(name) < 4:
            continue

        # Price selection: prefer realistic grocery sale prices
        raw_prices = sorted({p[2] for p in card})
        # Discard very high values that are likely unit prices or pack totals misread
        candidates = [p for p in raw_prices if 0.19 <= p <= 25.0]
        if not candidates:
            candidates = raw_prices
        sale = min(candidates)
        higher = [p for p in candidates if p >= sale * 1.15 and p <= sale * 4.0]
        regular = max(higher) if higher else None

        region_text = " ".join(t for _, t in name_tokens).casefold()
        has_card = "mego" in region_text and "kart" in region_text
        only_app = "lietotnē" in region_text or "lietotne" in region_text

        key = f"{name.casefold()}|{sale:.2f}|{page_num}"
        if key in seen:
            continue
        seen.add(key)

        offer = {
            "offerId": f"mego-{booklet['id']}-p{page_num}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}",
            "name": name,
            "price": sale,
            "regularPrice": regular,
            "requiresCard": has_card,
            "onlyInApp": only_app,
            "campaign": booklet["name"],
            "campaignId": booklet["id"],
            "source": booklet["pdf"],
            "pdfPage": page_num + 1,
            "validFrom": booklet.get("validFrom"),
            "validUntil": booklet.get("validUntil"),
            "store": "mego",
        }

        # Quality gate — reject addresses, slogans, empty shells
        folded = name.casefold()
        weak = (
            len(name) < 6
            or re.fullmatch(r"[\d\s%A-Z.,:\-*]+", name)
            or folded in {"veidi", "gab", "produkcijai", "cesvaine", "heinz", "ar mego"}
            or len(re.findall(r"[a-zāčēģīķļņšūž]{3,}", folded)) < 1
            or re.search(r"\b(?:ielā|iela|veikal|rīgā|valmier|ventspil|biķerniek|ģertrūd)\b", folded)
            or re.search(r"\([A-D]\)", name)  # store category markers
            or name.count(",") >= 4  # address lists
            or folded.startswith(("regulār", "ar mego", "tikai ", "cel ", "preces "))
            or "€/" in name
        )
        if weak:
            offer["reviewReason"] = "weak_name"
            reviews.append(offer)
        else:
            if img_dir is not None:
                photo = save_product_photo(page, x_bounds[column_index], x_bounds[column_index + 1],
                                           min_cy - 20, max_cy + 10, min_cy - 90,
                                           img_dir, offer["offerId"])
                if photo:
                    offer["image"] = photo
            offers.append(offer)

    return offers, reviews


def extract_offers_from_pdf(pdf_path: Path, booklet: dict, img_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Extract offers from Mego PDF using word coordinates. Returns (verified, review)."""
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF parsing")

    doc = fitz.open(pdf_path)
    all_offers: list[dict] = []
    all_review: list[dict] = []

    try:
        for page_num, page in enumerate(doc):
            offers, review = _extract_from_page(page, page_num, booklet, img_dir)
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
    print("Discovering Mego booklets...")
    booklets = discover_booklets(sess)
    if not booklets:
        raise RuntimeError("No Mego booklets discovered")

    print(f"Found {len(booklets)} booklet(s):")
    for b in booklets:
        print(f"  - {b['name']} [{b.get('validFrom')} – {b.get('validUntil')}] {b['pdf']}")

    all_offers: list[dict] = []
    all_review: list[dict] = []
    catalogues = []

    for booklet in booklets:
        # Prefer main weekly booklets
        if "zem-zem" in booklet["pdf"].lower() and len(booklets) > 1:
            # Skip long-running static catalogues for first version
            continue
        print(f"Downloading {booklet['pdf']} ...")
        r = sess.get(booklet["pdf"], timeout=60)
        r.raise_for_status()
        pdf_path = Path("tmp/pdfs") / f"mego_{booklet['id']}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(r.content)

        print(f"Parsing {pdf_path.name} ...")
        try:
            offers, review = extract_offers_from_pdf(pdf_path, booklet, Path("data/mego-img"))
        finally:
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass
        print(f"  -> {len(offers)} offers, {len(review)} need review")
        all_offers.extend(offers)
        all_review.extend(review)
        catalogues.append(
            {
                **booklet,
                "verifiedOffers": len(offers),
                "reviewOffers": len(review),
                "complete": len(offers) > 0,
            }
        )

    # Deduplicate by offerId
    unique = {o["offerId"]: o for o in all_offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in all_review}
    review_offers = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())

    # Reuse Maxima product grouping for now
    products = build_product_groups(offers) if offers else []
    prune_images(Path("data/mego-img"), {o["image"] for o in offers if o.get("image")})

    observed_at = datetime.now().astimezone()
    payload = {
        "source": OFFERS_PAGE,
        "store": "mego",
        "updatedAt": observed_at.isoformat(timespec="seconds"),
        "catalogues": catalogues,
        "offers": offers,
        "products": products,
        "reviewOffers": review_offers,
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(products),
            "reviewOffers": len(review_offers),
            "catalogues": len(catalogues),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} offers → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Mego scraper failed: {exc}", file=sys.stderr)
        raise
