#!/usr/bin/env python3
"""Collect LaTS Latvia (latts.lv / e-latts.lv) promotional offers.

LaTS publishes its monthly newspaper ("Akciju avīze"), the charity low-price
list ("Zīļuka zemās cenas") and the loyalty-card list ("Ar Draugu karti lētāk!")
as image-only flipbooks on https://www.latts.lv/akciju-avize — the linked PDFs
have no text layer, so they are recorded as catalogue links rather than parsed
(the same approach Elvi uses for its issuu flipbook).

The promoted products themselves are server-rendered by the chain's online
store, which is the structured source this scraper reads:

- ``GET https://www.e-latts.lv/products.sale.ppl`` — the "Akcijas" listing,
  40 product cards per page, paginated with ``?p=N`` (the highest page number
  is linked from the first page);
- each card is ``<div class="-oProduct" -id="29100">`` carrying:
  * ``a.-oTitle`` — the full product name (size and brand already included)
    and the product page URL ``/slug.<id>.p``;
  * ``div.-oThumb img`` — the product image (``/i/....jpeg``);
  * ``div.-oPrice`` with a ``-price="0.89"`` attribute — the sale price;
  * ``div.-eOld`` — the struck-through regular price;
  * ``div.-ePerUnit`` — the unit price (never used as a product price);
  * ``div.-iDepozit`` — a refundable container deposit, kept out of the price;
  * ``th.-oGroupPath`` — the category path ("Dzērieni/Limonādes/..."), used to
    drop alcoholic products (top-level "Alkoholiskie dzērieni");
  * ``ins.-akc`` — the percentage badge (the site recomputes it from prices).

The listing publishes general online sale prices: it carries no per-card
loyalty marker (the basket-key price template is identical for every product)
and no per-product validity dates, so offers keep ``requiresCard=False`` and
``validFrom/validUntil=None``; the newspaper period is attached to the
catalogue links instead. The separate "Ar Draugu karti lētāk!" card prices live
only in the image-only flipbook and are surfaced as a catalogue link.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

BASE = "https://www.e-latts.lv"
OFFERS_PAGE = BASE + "/products.sale.ppl"
CATALOGUE_PAGE = "https://www.latts.lv/akciju-avize"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/latts-offers.json")
MAX_PAGES = 60
PAGE_DELAY = 0.4
RETRIES = 3

CARD_SPLIT_RE = re.compile(r'(?=<div class="-oProduct")')
ID_RE = re.compile(r'class="-oProduct"\s+-id="(\d+)"')
TITLE_RE = re.compile(r'class="-oTitle[^"]*"\s+href="([^"]+)"[^>]*>([^<]*)</a>')
IMAGE_RE = re.compile(r'class="-oThumb[^"]*".*?<img[^>]*src="([^"]+)"', re.S)
PRICE_RE = re.compile(r'class="-oPrice[^"]*"\s+-price="([\d.]+)"')
OLD_RE = re.compile(r'class="-eOld">\s*([\d.,]+)\s*<')
GROUP_RE = re.compile(r'class="-oGroupPath">([^<]+)<')
BRAND_RE = re.compile(r'class="-brand"[^>]*>([^<]*)<')
PAGE_RE = re.compile(r'products\.sale\.ppl\?p=(\d+)')

VALIDITY_RE = re.compile(
    r'Akcijas periods:\s*(\d{2}\.\d{2}\.\d{4})\.?\s*-\s*(\d{2}\.\d{2}\.\d{4})')
CATALOGUE_TOKEN_RE = re.compile(r'<h2[^>]*>(.*?)</h2>|href="([^"]+\.pdf)"', re.S)
TAG_RE = re.compile(r"<[^>]+>")

# Top-level category that holds alcoholic products; non-alcoholic beer/cider/
# wine sit under other top-levels ("Dzērieni", "Bezalkoholisk...") and are kept.
ALCOHOL_TOP = "alkoholiskie dzerieni"

CAMPAIGNS = [
    {"id": "akcijas", "name": "Akcijas"},
]


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


def _absolute(url: str | None) -> str | None:
    if not url:
        return None
    url = html.unescape(url)
    if url.startswith("http"):
        return url
    return BASE + url if url.startswith("/") else BASE + "/" + url


def _date(value: str) -> str:
    day, month, year = value.split(".")
    return f"{year}-{month}-{day}"


def is_alcohol(category: str | None) -> bool:
    first = _fold(category or "").split("/")[0].strip()
    return first == ALCOHOL_TOP


def last_page(html_text: str) -> int:
    refs = [int(x) for x in PAGE_RE.findall(html_text)]
    return max(refs) if refs else 1


# Trailing name segments that are descriptors, not brands.
BRAND_TAIL_STOP = {"sveramas", "sverami"}


def _is_brand_tail(tail: str) -> bool:
    """LaTS names follow "Product, Brand", so the last comma segment is usually
    the brand (Roshen, Santa Maria, Latvijas Maiznieks…). Reject the few tails
    that are clearly not brands: package sizes ("250ml"), counts/colour notes
    ("4 dazādas krāsas") and by-weight descriptors ("sveramas").
    """
    folded = _fold(tail)
    if not tail or not re.search(r"[a-z0-9]", folded):
        return False
    if re.match(r"\d", folded):  # sizes and counts start with a digit
        return False
    if folded in BRAND_TAIL_STOP or "dazadas krasas" in folded:
        return False
    return True


def parse_brand(name: str, brand_html: str | None) -> str | None:
    """Prefer the explicit -brand label; fall back to the trailing name segment."""
    brand = html.unescape(brand_html or "").strip()
    if brand:
        return brand
    if "," in name:
        tail = name.rsplit(",", 1)[1].strip()
        if _is_brand_tail(tail):
            return tail
    return None


def parse_cards(html_text: str) -> list[dict]:
    """Extract raw promotion cards from one server-rendered listing page."""
    cards: list[dict] = []
    for chunk in CARD_SPLIT_RE.split(html_text):
        id_match = ID_RE.search(chunk)
        if not id_match:
            continue
        title_match = TITLE_RE.search(chunk)
        image_match = IMAGE_RE.search(chunk)
        price_match = PRICE_RE.search(chunk)
        old_match = OLD_RE.search(chunk)
        group_match = GROUP_RE.search(chunk)
        brand_match = BRAND_RE.search(chunk)
        name = html.unescape(title_match.group(2)).strip() if title_match else ""
        cards.append({
            "id": id_match.group(1),
            "name": name,
            "url": _absolute(title_match.group(1)) if title_match else None,
            "image": _absolute(image_match.group(1)) if image_match else None,
            "price": _money(price_match.group(1)) if price_match else None,
            "regularPrice": _money(old_match.group(1)) if old_match else None,
            "category": html.unescape(group_match.group(1)).strip() if group_match else None,
            "brand": parse_brand(name, brand_match.group(1) if brand_match else None),
        })
    return cards


def card_to_offer(card: dict) -> dict | None:
    """Convert a raw card to a feed offer; ``None`` for alcoholic products."""
    if is_alcohol(card.get("category")):
        return None

    name = card.get("name") or ""
    sale = card.get("price")
    regular = card.get("regularPrice")
    if regular is not None and sale is not None and regular <= sale + 0.001:
        regular = None

    offer = {
        "offerId": "latts-" + str(card.get("id")),
        "name": name,
        "brand": card.get("brand") or None,
        "price": sale,
        "regularPrice": regular,
        "requiresCard": False,
        "onlyInApp": False,
        "campaign": "Akcijas",
        "campaignId": "akcijas",
        "source": card.get("url") or OFFERS_PAGE,
        "validFrom": None,
        "validUntil": None,
        "store": "latts",
        "image": card.get("image"),
    }

    # A lone sale price (no struck-through reference) is still a published
    # promotional price on the Akcijas listing — keep it, like Elvi's p.single,
    # rather than hiding it; the site simply shows no discount percentage.
    if sale is None:
        offer["reviewReason"] = "missing_price"
    elif len(name) < 4:
        offer["reviewReason"] = "weak_name"
    return offer


def parse_catalogues(html_text: str) -> list[dict]:
    """Record the image-only flipbooks as catalogue links with their period.

    The avīze page lists three sections (LaTS akciju avīze, Zīļuka zemās cenas,
    Ar Draugu karti lētāk!), each an ``<h2>`` heading followed by a PDF link;
    every PDF is associated with the nearest preceding heading.
    """
    validity = VALIDITY_RE.search(html_text)
    valid_from = _date(validity.group(1)) if validity else None
    valid_until = _date(validity.group(2)) if validity else None

    catalogues: list[dict] = []
    seen: set[str] = set()
    heading = "LaTS akciju avīze"
    for match in CATALOGUE_TOKEN_RE.finditer(html_text):
        if match.group(1) is not None:
            heading = TAG_RE.sub("", match.group(1)).replace("&nbsp;", " ")
            heading = html.unescape(heading).strip() or heading
            continue
        url = html.unescape(match.group(2))
        if url in seen:
            continue
        seen.add(url)
        slug = re.sub(r"[^a-z0-9]+", "-", _fold(heading)).strip("-") or "avize"
        catalogues.append({
            "id": "latts-" + slug,
            "name": heading,
            "source": url,
            "validFrom": valid_from,
            "validUntil": valid_until,
        })
    return catalogues


def fetch_page(sess: requests.Session, page: int) -> str:
    url = OFFERS_PAGE if page == 1 else f"{OFFERS_PAGE}?p={page}"
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = sess.get(url, timeout=90)
            r.raise_for_status()
            if ID_RE.search(r.text):
                return r.text
            last_error = RuntimeError(f"page {page}: no product cards (bot check?)")
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LaTS listing page {page} failed after {RETRIES} attempts: {last_error}")


def fetch_offers(sess: requests.Session, *, page_fetcher=None) -> tuple[list[dict], int]:
    """Collect every listing page; a failed page degrades gracefully.

    The first page already renders 40 cards. A later page that cannot be
    fetched (e.g. a datacenter IP block) only ends pagination with a warning
    while the collected cards are kept; the scrape fails only with no cards.
    """
    page_fetcher = page_fetcher or fetch_page
    first = page_fetcher(sess, 1)
    pages = min(last_page(first), MAX_PAGES)
    cards = parse_cards(first)
    print(f"  page 1: {len(cards)} cards, {pages} pages total")
    seen = {c["id"] for c in cards}
    for page in range(2, pages + 1):
        time.sleep(PAGE_DELAY)
        try:
            text = page_fetcher(sess, page)
        except RuntimeError as exc:
            if not cards:
                raise
            print(f"  pagination stopped ({exc}); keeping {len(cards)} cards",
                  file=sys.stderr)
            break
        fresh = [c for c in parse_cards(text) if c["id"] not in seen]
        for c in fresh:
            seen.add(c["id"])
        cards.extend(fresh)
        print(f"  page {page}: {len(fresh)} new cards")
        if not fresh:
            break
    return cards, pages


def fetch_catalogues(sess: requests.Session) -> list[dict]:
    try:
        r = sess.get(CATALOGUE_PAGE, timeout=60)
        r.raise_for_status()
        return parse_catalogues(r.text)
    except requests.RequestException as exc:
        print(f"  catalogue page unavailable ({exc}); keeping offers only",
              file=sys.stderr)
        return []


def main() -> None:
    sess = session()
    print("Collecting LaTS e-veikals Akcijas listing...")
    cards, pages = fetch_offers(sess)
    if not cards:
        raise RuntimeError("No LaTS promotion cards returned")
    print(f"Found {len(cards)} cards on {pages} pages")
    catalogues = fetch_catalogues(sess)

    offers: list[dict] = []
    reviews: list[dict] = []
    alcohol = 0
    for card in cards:
        offer = card_to_offer(card)
        if offer is None:
            alcohol += 1
        elif offer.get("reviewReason"):
            reviews.append(offer)
        else:
            offers.append(offer)
    print(f"Skipped {alcohol} alcoholic products")

    unique = {o["offerId"]: o for o in offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in reviews}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())

    grouped = build_product_groups(offers) if offers else []

    payload = {
        "source": OFFERS_PAGE,
        "store": "latts",
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
        print(f"LaTS scraper failed: {exc}", file=sys.stderr)
        raise