#!/usr/bin/env python3
"""Collect Rimi Latvia (rimi.lv) promotional offers from the e-veikals listing.

Rimi's online store server-renders its full promotion catalogue at
``https://www.rimi.lv/e-veikals/lv/akcijas-piedavajumi`` — every product that
currently carries a price label, paged with ``?currentPage=N&pageSize=100``
(100 cards per page is the server maximum; larger values return an error page).
No PDF parsing is needed.

Two kinds of promotion cards appear in the listing:

- **Mans Rimi card price** — a ``price-label`` block titled "Piedāvājums spēkā
  tikai MansRimi kartes lietotājiem" shows the loyalty price (major + cents
  spans); the plain ``card__price`` next to it is the regular price. These
  offers are flagged ``requiresCard`` with ``cardName: "Mans Rimi"`` and form
  the "Mans Rimi cena" campaign.
- **Regular discount** — ``card__price-wrapper -has-discount`` with the sale
  price in ``card__price`` and the crossed-out regular price in
  ``old-price-tag`` ("Regulārā cena: 2,19 €"). These form the "Akcija"
  campaign. The "Izdevīga cena katru dienu!" badge is marketing copy on top of
  a real discount and does not change the price semantics.

Price formats are inconsistent in the markup: sale prices use a dot decimal
("2.39 €"), crossed-out regular prices use a comma decimal ("2,19 €"); both
are normalized. Unit prices ("Cena par vienību: 7,95 €/kg") are ignored.

Alcoholic products are not part of the feed: the GTM category code embedded in
every card ("SH-1-12-3" etc.) starts with the top-level category, and alcohol
lives under SH-1 (Alkoholiskie dzērieni) and SH-18 (Vīns un šampanietis).
Non-alcoholic beer/cider/wine sit under SH-5-1 and are kept, mirroring top!.

The listing carries no per-product validity dates (they are only shown on
individual product pages), so ``validFrom``/``validUntil`` stay ``null`` rather
than guessing a booklet period for month-long card prices.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

BASE = "https://www.rimi.lv"
OFFERS_PAGE = BASE + "/e-veikals/lv/akcijas-piedavajumi"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/rimi-offers.json")
PAGE_SIZE = 100
MAX_PAGES = 120
PAGE_DELAY = 0.4
RETRIES = 3

CARD_SPLIT_RE = re.compile(r'(?=<li class="product-grid__item">)')
CODE_RE = re.compile(r'data-product-code="(\d+)"')
NAME_RE = re.compile(r'class="card__name">([^<]+)<')
URL_RE = re.compile(r'card__url[^>]*href="([^"]+)"')
IMAGE_RE = re.compile(r'card__image-wrapper.*?<img\s+src="([^"]+)"', re.S)
GTM_RE = re.compile(r"data-gtm-eec-product='([^']+)'")
LABEL_RE = re.compile(r'<div class="price-label"')
LABEL_PRICE_RE = re.compile(r'class="major">(\d+)<.*?class="cents">(\d+)<', re.S)
PRICE_RE = re.compile(r'class="sr-only">\s*([\d.,]+)\s*€')
OLD_PRICE_RE = re.compile(r'Regulārā cena:\s*([\d.,]+)')
PAGE_REF_RE = re.compile(r'data-page="(\d+)"')

# Top-level GTM category codes that hold alcoholic products.
ALCOHOL_CATEGORIES = {"1", "18"}

CAMPAIGNS = [
    {"id": "mans-rimi-cena", "name": "Mans Rimi cena"},
    {"id": "akcija", "name": "Akcija"},
]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _money(value: str | None) -> float | None:
    if not value:
        return None
    try:
        amount = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return round(amount, 2) if amount > 0 else None


def is_alcohol(category: str | None) -> bool:
    parts = str(category or "").split("-")
    return len(parts) >= 2 and parts[1] in ALCOHOL_CATEGORIES


def last_page(html_text: str) -> int:
    refs = [int(x) for x in PAGE_REF_RE.findall(html_text)]
    return max(refs) if refs else 1


def parse_cards(html_text: str) -> list[dict]:
    """Extract raw promotion cards from one server-rendered listing page."""
    cards: list[dict] = []
    for chunk in CARD_SPLIT_RE.split(html_text):
        code_match = CODE_RE.search(chunk)
        if not code_match:
            continue
        name_match = NAME_RE.search(chunk)
        url_match = URL_RE.search(chunk)
        image_match = IMAGE_RE.search(chunk)
        gtm_match = GTM_RE.search(chunk)
        gtm: dict = {}
        if gtm_match:
            try:
                gtm = json.loads(gtm_match.group(1))
            except json.JSONDecodeError:
                gtm = {}
        label_price = LABEL_PRICE_RE.search(chunk) if LABEL_RE.search(chunk) else None
        price_match = PRICE_RE.search(chunk)
        old_match = OLD_PRICE_RE.search(chunk)
        cards.append({
            "code": code_match.group(1),
            "name": html.unescape(name_match.group(1)).strip() if name_match else "",
            "url": html.unescape(url_match.group(1)) if url_match else None,
            "image": html.unescape(image_match.group(1)) if image_match else None,
            "category": gtm.get("category"),
            "brand": gtm.get("brand"),
            "hasCardLabel": label_price is not None,
            "cardPrice": _money(label_price.group(1) + "." + label_price.group(2)) if label_price else None,
            "price": _money(price_match.group(1)) if price_match else None,
            "oldPrice": _money(old_match.group(1)) if old_match else None,
        })
    return cards


def card_to_offer(card: dict) -> dict | None:
    """Convert a raw card to a feed offer; ``None`` for alcoholic products."""
    if is_alcohol(card.get("category")):
        return None

    name = card.get("name") or ""
    if card.get("hasCardLabel") and card.get("cardPrice") is not None:
        sale = card["cardPrice"]
        regular = card.get("price")
        requires_card = True
        campaign_id, campaign = "mans-rimi-cena", "Mans Rimi cena"
    else:
        sale = card.get("price")
        regular = card.get("oldPrice")
        requires_card = False
        campaign_id, campaign = "akcija", "Akcija"
    if regular is not None and sale is not None and regular <= sale + 0.001:
        regular = None

    url = card.get("url") or ""
    source = url if url.startswith("http") else (BASE + url if url else OFFERS_PAGE)
    offer = {
        "offerId": "rimi-" + str(card.get("code")),
        "name": name,
        "brand": card.get("brand") or None,
        "price": sale,
        "regularPrice": regular,
        "requiresCard": requires_card,
        "onlyInApp": False,
        "campaign": campaign,
        "campaignId": campaign_id,
        "source": source,
        "validFrom": None,
        "validUntil": None,
        "store": "rimi",
        "image": card.get("image"),
    }
    if requires_card:
        offer["cardName"] = "Mans Rimi"

    if sale is None:
        offer["reviewReason"] = "missing_price"
    elif len(name) < 4:
        offer["reviewReason"] = "weak_name"
    elif not requires_card and regular is None:
        # A card on the promotion listing without any detectable discount.
        offer["reviewReason"] = "no_discount"
    return offer


def fetch_page(sess: requests.Session, page: int) -> str:
    url = f"{OFFERS_PAGE}?currentPage={page}&pageSize={PAGE_SIZE}"
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = sess.get(url, timeout=90)
            r.raise_for_status()
            if CODE_RE.search(r.text):
                return r.text
            last_error = RuntimeError(f"page {page}: no product cards (bot check?)")
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Rimi listing page {page} failed after {RETRIES} attempts: {last_error}")


def fetch_offers(sess: requests.Session) -> tuple[list[dict], int]:
    first = fetch_page(sess, 1)
    pages = min(last_page(first), MAX_PAGES)
    cards = parse_cards(first)
    print(f"  page 1: {len(cards)} cards, {pages} pages total")
    seen = {c["code"] for c in cards}
    for page in range(2, pages + 1):
        time.sleep(PAGE_DELAY)
        text = fetch_page(sess, page)
        fresh = [c for c in parse_cards(text) if c["code"] not in seen]
        for c in fresh:
            seen.add(c["code"])
        cards.extend(fresh)
        print(f"  page {page}: {len(fresh)} new cards")
        if not fresh:
            break
    return cards, pages


def main() -> None:
    sess = session()
    print("Collecting Rimi e-veikals promotion listing...")
    cards, pages = fetch_offers(sess)
    if not cards:
        raise RuntimeError("No Rimi promotion cards returned")
    print(f"Found {len(cards)} cards on {pages} pages")

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
        "store": "rimi",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "campaigns": CAMPAIGNS,
        "catalogues": [],
        "offers": offers,
        "products": grouped,
        "reviewOffers": reviews,
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(grouped),
            "reviewOffers": len(reviews),
            "catalogues": 0,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} offers ({len(reviews)} review) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Rimi scraper failed: {exc}", file=sys.stderr)
        raise