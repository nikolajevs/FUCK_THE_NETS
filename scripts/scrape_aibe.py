#!/usr/bin/env python3
"""Collect AIBE Latvia (aibe.lv) promotional offers.

AIBE server-renders its current featured promotions on
https://aibe.lv/akcijas/ as WordPress cards grouped by category
("Zivju produkti", "Gaļas Produkti", "Piena produkti", "Bakaleja",
"Nepārtikas preces"). Each card is::

    <div class="bestOffers-block call-popup" data-id="92324" data-post-type="akcijos">
      <p>-23%</p>                                  (discount-percent-num)
      <p class="discount-price-now-euro">1</p>      sale euros
      <p class="discount-price-now-cent">99<span>€</span></p>  sale cents
      <p class="discount-price-was-this">2.60€</p>  struck-through regular price
      <p class="price_quantity-text">1 kg - 6.63 €</p>  unit price (ignored)
      <img data-src="https://aibe.lv/wp-content/uploads/...png">  product photo
      <p class="bestOffers-block-discountProduct-name">Rakveres cīsiņi, Rīgas miesnieks, 300 g</p>

The category header ``<h4 class="top-block-title">`` and the per-section
validity ``Akcija ir spēkā no 24.11 - 14.12`` precede each group of cards.
The day/month range carries no year; the year is taken from the akciju avīze
PDF filename (``Nr.16-24.11.2026-14.12.2026.pdf``) published on
https://aibe.lv/akciju-avizes/ via a DearPDF flipbook, which is also recorded
as a catalogue link. The full newspaper is image-only, so the structured
``/akcijas/`` cards are the verified source.

Product names follow "Product, Brand, size"; the brand is the last non-size
comma segment (Rīgas miesnieks, Kārums, Cesvaines piens…). Offers carry no
loyalty-card marker (``requiresCard=False``).
"""
from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

import requests

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

BASE = "https://aibe.lv"
OFFERS_PAGE = BASE + "/akcijas/"
AVIZES_PAGE = BASE + "/akciju-avizes/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/aibe-offers.json")
RETRIES = 3

CARD_SPLIT_RE = re.compile(r'(?=<div class="bestOffers-block call-popup")')
ID_RE = re.compile(r'data-id="(\d+)"\s+data-post-type="akcijos"')
NAME_RE = re.compile(r'class="bestOffers-block-discountProduct-name">\s*([^<]+?)\s*</p>')
EURO_RE = re.compile(r'class="discount-price-now-euro">\s*(\d+)\s*</p>')
CENT_RE = re.compile(r'class="discount-price-now-cent">\s*(\d{1,2})')
WAS_RE = re.compile(r'class="discount-price-was-this">\s*([\d.,]+)\s*€')
DISCOUNT_RE = re.compile(r'class="discount-percent-num">\s*<p>\s*-(\d{1,2})%')
IMAGE_RE = re.compile(r'class="bestOffers-block-discountProduct-imgWrapper">.*?data-src="([^"]+)"', re.S)
CATEGORY_RE = re.compile(r'class="top-block-title">\s*([^<]+?)\s*</h4>')
VALIDITY_RE = re.compile(r'Akcija ir spēkā no\s*(\d{1,2}\.\d{1,2})\s*-\s*(\d{1,2}\.\d{1,2})')
DEARPDF_RE = re.compile(r'"source":"(https?:\\?/\\?/[^"]+?\.pdf)"')
FILEDATE_RE = re.compile(r'(\d{2}\.\d{2}\.(\d{4}))-(\d{2}\.\d{2}\.(\d{4}))')

CAMPAIGNS = [{"id": "ipasie-piedavajumi", "name": "Īpašie piedāvājumi"}]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _money(value: str | None) -> float | None:
    if not value:
        return None
    try:
        amount = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return round(amount, 2) if amount > 0 else None


def _is_size_segment(segment: str) -> bool:
    folded = _fold(segment).strip()
    if not folded:
        return True
    if re.match(r"\d", folded):           # 300 g, 240 g / 190 g, 82%, 1 kg
        return True
    if "sveram" in folded:                 # sverami / sveramas
        return True
    if re.fullmatch(r"[\d.,\s%]+(?:g|kg|ml|l|gab)?", folded):
        return True
    return False


def parse_brand(name: str) -> str | None:
    """AIBE names are "Product, Brand, size"; the brand is the last comma
    segment that is not a package size / by-weight descriptor. Requires at
    least two non-size segments so the product name itself is never a brand.
    """
    segments = [s.strip() for s in name.split(",") if s.strip()]
    while segments and _is_size_segment(segments[-1]):
        segments.pop()
    if len(segments) < 2:
        return None
    brand = segments[-1]
    if len(brand.split()) > 4 or _is_size_segment(brand):
        return None
    return brand


def parse_sale_price(euro: str | None, cent: str | None) -> float | None:
    if euro is None or cent is None:
        return None
    try:
        return round(int(euro) + int(cent) / 100.0, 2)
    except ValueError:
        return None


def parse_cards(html_text: str) -> list[dict]:
    """Extract raw promotion cards, attaching the nearest preceding category
    header and validity range to each card."""
    cards: list[dict] = []
    category = None
    valid_from = valid_until = None
    # Walk tokens (category headers, validity, cards) in document order.
    token_re = re.compile(
        r'(?P<cat>class="top-block-title">\s*([^<]+?)\s*</h4>)'
        r'|(?P<val>Akcija ir spēkā no\s*(\d{1,2}\.\d{1,2})\s*-\s*(\d{1,2}\.\d{1,2}))'
        r'|(?P<card><div class="bestOffers-block call-popup")')
    pending_cat = None
    pending_val = (None, None)
    for match in token_re.finditer(html_text):
        if match.group("cat"):
            pending_cat = html.unescape(match.group(2)).strip()
            continue
        if match.group("val"):
            pending_val = (match.group(4), match.group(5))
            continue
        # card: capture until next card / end
        start = match.start()
        nxt = html_text.find('<div class="bestOffers-block call-popup"', match.end())
        chunk = html_text[start:nxt if nxt != -1 else len(html_text)]
        id_match = ID_RE.search(chunk)
        if not id_match:
            continue
        name_match = NAME_RE.search(chunk)
        euro_match = EURO_RE.search(chunk)
        cent_match = CENT_RE.search(chunk)
        was_match = WAS_RE.search(chunk)
        disc_match = DISCOUNT_RE.search(chunk)
        image_match = IMAGE_RE.search(chunk)
        name = html.unescape(name_match.group(1)).strip() if name_match else ""
        cards.append({
            "id": id_match.group(1),
            "name": name,
            "brand": parse_brand(name),
            "price": parse_sale_price(euro_match.group(1) if euro_match else None,
                                      cent_match.group(1) if cent_match else None),
            "regularPrice": _money(was_match.group(1)) if was_match else None,
            "discountPercent": int(disc_match.group(1)) if disc_match else None,
            "image": html.unescape(image_match.group(1)) if image_match else None,
            "category": pending_cat,
            "validFromRaw": pending_val[0],
            "validUntilRaw": pending_val[1],
        })
    return cards


def _date(dm: str, year: int) -> str | None:
    try:
        day, month = dm.split(".")
        return date(year, int(month), int(day)).isoformat()
    except (ValueError, AttributeError):
        return None


def infer_year(start_dm: str | None, end_dm: str | None, today: date | None = None) -> int:
    """Pick the campaign year for a day/month range that spans new year."""
    today = today or date.today()
    if not start_dm or not end_dm:
        return today.year
    try:
        sd, sm = (int(x) for x in start_dm.split("."))
        ed, em = (int(x) for x in end_dm.split("."))
    except ValueError:
        return today.year
    year = today.year
    end = date(year + (1 if (em, ed) < (sm, sd) else 0), em, ed)
    start = date(end.year if (sm, sd) <= (em, ed) else end.year - 1, sm, sd)
    if (start - today).days > 60:
        start = date(start.year - 1, sm, sd)
        end = date(end.year - 1, em, ed)
    return start.year


def parse_catalogue(html_text: str) -> tuple[list[dict], int | None]:
    """Record the DearPDF akciju avīze as a catalogue link; return its year."""
    match = DEARPDF_RE.search(html_text)
    if not match:
        return [], None
    url = match.group(1).replace("\\/", "/")
    year = None
    valid_from = valid_until = None
    fd = FILEDATE_RE.search(url)
    if fd:
        year = int(fd.group(2))
        valid_from = _date(fd.group(1)[:5], year)
        valid_until = _date(fd.group(3)[:5], int(fd.group(4)))
    name = "AIBE akciju avīze"
    nr = re.search(r'Nr\.?\s*(\d+)', url)
    if nr:
        name += " Nr. " + nr.group(1)
    return [{
        "id": "aibe-avize",
        "name": name,
        "source": url,
        "validFrom": valid_from,
        "validUntil": valid_until,
    }], year


def card_to_offer(card: dict, year: int) -> dict:
    sale = card.get("price")
    regular = card.get("regularPrice")
    if regular is not None and sale is not None and regular <= sale + 0.001:
        regular = None
    offer = {
        "offerId": "aibe-" + str(card.get("id")),
        "name": card.get("name") or "",
        "brand": card.get("brand"),
        "price": sale,
        "regularPrice": regular,
        "discountPercent": card.get("discountPercent"),
        "requiresCard": False,
        "onlyInApp": False,
        "campaign": "Īpašie piedāvājumi",
        "campaignId": "ipasie-piedavajumi",
        "category": card.get("category"),
        "source": OFFERS_PAGE,
        "validFrom": _date(card.get("validFromRaw"), year) if card.get("validFromRaw") else None,
        "validUntil": _date(card.get("validUntilRaw"), year) if card.get("validUntilRaw") else None,
        "store": "aibe",
        "image": card.get("image"),
    }
    if sale is None:
        offer["reviewReason"] = "missing_price"
    elif len(offer["name"]) < 4:
        offer["reviewReason"] = "weak_name"
    return offer


def fetch(sess: requests.Session, url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = sess.get(url, timeout=60)
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            last_error = exc
        import time
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"AIBE page {url} failed after {RETRIES} attempts: {last_error}")


def main() -> None:
    sess = session()
    print("Collecting AIBE akcijas cards...")
    offers_html = fetch(sess, OFFERS_PAGE)
    cards = parse_cards(offers_html)
    if not cards:
        raise RuntimeError("No AIBE promotion cards returned")
    print(f"Found {len(cards)} cards")

    catalogues: list[dict] = []
    year = None
    try:
        avizes_html = fetch(sess, AVIZES_PAGE)
        catalogues, year = parse_catalogue(avizes_html)
    except RuntimeError as exc:
        print(f"  avīze page unavailable ({exc}); inferring year", file=sys.stderr)
    if year is None:
        year = infer_year(cards[0].get("validFromRaw"), cards[0].get("validUntilRaw"))

    offers: list[dict] = []
    reviews: list[dict] = []
    for card in cards:
        offer = card_to_offer(card, year)
        if offer.get("reviewReason"):
            reviews.append(offer)
        else:
            offers.append(offer)

    unique = {o["offerId"]: o for o in offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in reviews}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())
    grouped = build_product_groups(offers) if offers else []

    payload = {
        "source": OFFERS_PAGE,
        "store": "aibe",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "campaigns": CAMPAIGNS,
        "catalogues": catalogues,
        "offers": offers,
        "reviewOffers": reviews,
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(grouped),
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
        print(f"AIBE scraper failed: {exc}", file=sys.stderr)
        raise