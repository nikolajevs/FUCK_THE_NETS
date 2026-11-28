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

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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
OUT = Path("data/rimi-eveikals-offers.json")
PAGE_SIZE = 100
MAX_PAGES = 120
PAGE_DELAY = 0.4
# Rimi needs ~3 s to render each 100-card page, so a few pages are fetched at
# once; this keeps the scrape at ~1-2 minutes instead of ~6.
PAGE_WORKERS = 4
RETRIES = 3

PRICE_TEXT_RE = re.compile(r"(?<![\d.,])([\d.,]+)\s*€")

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
    refs = []
    for element in BeautifulSoup(html_text, "html.parser").select("[data-page]"):
        try:
            refs.append(int(element.get("data-page")))
        except (TypeError, ValueError):
            continue
    return max(refs, default=1)


def _element_price(element) -> float | None:
    if element is None:
        return None
    match = PRICE_TEXT_RE.search(element.get_text(" ", strip=True))
    return _money(match.group(1)) if match else None


def _gtm_product(card_element) -> dict:
    raw = card_element.get("data-gtm-eec-product")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_cards(html_text: str) -> list[dict]:
    """Extract raw promotion cards from one server-rendered listing page."""
    cards: list[dict] = []
    soup = BeautifulSoup(html_text, "html.parser")
    card_elements = soup.select("li.product-grid__item")
    if not card_elements:
        # Keep working if Rimi changes the grid wrapper but retains product IDs.
        card_elements = soup.select("[data-product-code]")

    for element in card_elements:
        product_element = (
            element if element.has_attr("data-product-code")
            else element.select_one("[data-product-code]")
        )
        if product_element is None:
            continue
        code = str(product_element.get("data-product-code") or "").strip()
        if not code:
            continue

        name_element = element.select_one(".card__name")
        url_element = element.select_one(".card__url[href]")
        image_element = (
            element.select_one(".card__image-wrapper img[src]")
            or element.select_one("img[src]")
        )
        label_element = element.select_one(".price-label")
        label_text = ""
        if label_element is not None:
            label_text = " ".join((
                str(label_element.get("title") or ""),
                label_element.get_text(" ", strip=True),
            )).casefold()
        has_card_label = bool(re.search(r"mans\s*rimi|mansrimi|paldies", label_text))
        label_price = None
        if has_card_label:
            major = label_element.select_one(".price-label__price .major")
            cents = label_element.select_one(".price-label__price .cents")
            if major and cents:
                label_price = _money(major.get_text(strip=True) + "." + cents.get_text(strip=True))

        gtm = _gtm_product(product_element)
        cards.append({
            "code": code,
            "name": name_element.get_text(" ", strip=True) if name_element else "",
            "url": url_element.get("href") if url_element else None,
            "image": image_element.get("src") if image_element else None,
            "category": gtm.get("category"),
            "brand": gtm.get("brand"),
            "hasCardLabel": has_card_label,
            "cardPrice": label_price,
            "price": _element_price(element.select_one(".card__price")),
            "oldPrice": _element_price(element.select_one(".old-price-tag")),
        })
    return cards


def card_to_offer(card: dict) -> dict | None:
    """Convert a raw card to a feed offer; ``None`` for alcoholic products."""
    if is_alcohol(card.get("category")):
        return None

    name = card.get("name") or ""
    if card.get("hasCardLabel"):
        sale = card.get("cardPrice")
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
        "store": "rimi-eveikals",
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
            # A plain substring check: parse_cards() parses the page anyway,
            # and a full BeautifulSoup pass here doubled the per-page cost.
            if 'data-product-code="' in r.text:
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

    local = threading.local()

    def fetch_in_worker(page: int) -> str:
        # requests.Session is not thread-safe; each worker keeps its own.
        if not hasattr(local, "sess"):
            local.sess = session()
        time.sleep(PAGE_DELAY)
        return fetch_page(local.sess, page)

    remaining = range(2, pages + 1)
    pool = ThreadPoolExecutor(max_workers=PAGE_WORKERS)
    try:
        # map() yields in page order, so cards keep the listing order.
        for page, text in zip(remaining, pool.map(fetch_in_worker, remaining)):
            fresh = [c for c in parse_cards(text) if c["code"] not in seen]
            for c in fresh:
                seen.add(c["code"])
            cards.extend(fresh)
            print(f"  page {page}: {len(fresh)} new cards")
            if not fresh:
                break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
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
        "store": "rimi-eveikals",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "campaigns": CAMPAIGNS,
        "catalogues": [],
        "offers": offers,
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
