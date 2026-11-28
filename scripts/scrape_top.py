#!/usr/bin/env python3
"""Collect top! (etop.lv) promotional offers from the public promotion-products API.

Unlike Maxima/Mego, the top! chain exposes a structured JSON feed behind its
Angular storefront, so no PDF parsing is needed:

- ``GET /v1/Promotions/GetActivePromotions`` — active booklets with direct PDF
  links and validity periods;
- ``POST /v1/Products/GetPromotionProducts`` ``{"page": N, "pageSize": 200}`` —
  every discounted product with sale/regular/loyalty prices, brand, EAN,
  package weight, product image and per-product validity dates.

Price semantics observed in the feed:

- ``discountType 3`` (A1/LA/S1 booklets): ``price`` is the sale price,
  ``regPriceMin..regPriceMax`` the regular price range across store formats;
- ``discountType 9`` (TC, "top! cena"): ``price`` is the regular price and
  ``loyaltyPrice`` the lower "mans top!" card price;
- ``discountType 21`` (PP, private-label "Zemā cena") and ``18`` (ZG, lowest
  price guarantee): everyday low prices without a crossed-out regular price.

Alcoholic products are not part of the feed (only 0% beer/cider variants are),
so no age-restricted price advertising is reproduced.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

try:
    from scripts.common import write_feed, parse_utc, riga_date, riga_offset
except ModuleNotFoundError:
    from common import write_feed, parse_utc, riga_date, riga_offset

BASE = "https://etop.lv"
OFFERS_PAGE = BASE + "/lv/visi-akcijas-produkti"
BOOKLETS_API = BASE + "/v1/Promotions/GetActivePromotions"
PRODUCTS_API = BASE + "/v1/Products/GetPromotionProducts"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
OUT = Path("data/top-offers.json")
PAGE_SIZE = 200
MAX_PAGES = 40

# Campaigns that are not tied to a booklet PDF.
DESIGNATION_CAMPAIGNS = {
    "TC": ("top-cena", "top! cena"),
    "PP": ("zema-cena", "Zemā cena"),
    "ZG": ("zemaka-cena", "Zemākā cena"),
}
BOOKLET_FALLBACKS = {
    "A1": ("top-buklets", "top! buklets"),
    "S1": ("top-buklets", "top! buklets"),
    "LA": ("lielo-veikalu-top-buklets", "Lielo veikalu top! buklets"),
    "LAJ": ("lielo-veikalu-top-buklets", "Lielo veikalu top! buklets"),
}

UNIT_TOKEN_RE = re.compile(r"(\d+(?:[.,]\d+)?)(KG|G|ML|L|GAB|GB|CM|MM|DEN)$", re.IGNORECASE)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# --- Europe/Riga local dates (riga_offset / riga_date live in common.py) --------

def offer_period(start_iso: str | None, end_iso: str | None) -> tuple[str | None, str | None]:
    """Local validity dates; the API end instant is exclusive (00:00 Riga)."""
    start = parse_utc(start_iso)
    end = parse_utc(end_iso)
    valid_from = (start + riga_offset(start)).date().isoformat() if start else None
    valid_until = ((end + riga_offset(end)) - timedelta(seconds=1)).date().isoformat() if end else None
    return valid_from, valid_until


# --- display helpers ----------------------------------------------------------

def display_name(raw: str | None) -> str:
    """Title-case the ALL-CAPS feed names, keeping units and brand acronyms."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    parts: list[str] = []
    for token in text.split(" "):
        if "&" in token or "'" in token:
            parts.append(token)  # C&T, O'BYTE, B&J, NUTELLA&GO …
            continue
        unit = UNIT_TOKEN_RE.fullmatch(token)
        if unit:
            parts.append(unit.group(1) + unit.group(2).lower())
        else:
            parts.append(token[:1].upper() + token[1:].lower())
    return " ".join(parts)


def _money(value) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return round(float(value), 2)
    return None


# --- API collection -----------------------------------------------------------

def fetch_booklets(sess: requests.Session) -> list[dict]:
    r = sess.get(BOOKLETS_API, timeout=30)
    r.raise_for_status()
    booklets: list[dict] = []
    for item in r.json() or []:
        paper = item.get("storeNewspaper") or {}
        preview = item.get("previewImage") or {}
        booklets.append({
            "id": item.get("url") or item.get("id"),
            "name": item.get("name") or "top! buklets",
            "pdf": paper.get("fileUrl"),
            "preview": preview.get("fileUrl"),
            "validFrom": riga_date(item.get("startDate")),
            "validUntil": riga_date(item.get("endDate")),
        })
    return booklets


def fetch_products(sess: requests.Session) -> list[dict]:
    products: list[dict] = []
    seen: set[str] = set()
    page = 1
    while page <= MAX_PAGES:
        r = sess.post(PRODUCTS_API, json={"page": page, "pageSize": PAGE_SIZE}, timeout=60)
        r.raise_for_status()
        data = r.json()
        items = data.get("list") or []
        fresh = [p for p in items if p.get("id") and p["id"] not in seen]
        for p in fresh:
            seen.add(p["id"])
            products.append(p)
        print(f"  page {page}: {len(items)} items ({len(fresh)} new), hasMore={data.get('hasMore')}")
        if not items or not data.get("hasMore") or not fresh:
            break
        page += 1
    return products


def resolve_campaign(product: dict, booklets: list[dict]) -> tuple[str, str, str]:
    """Return (campaignId, campaignLabel, sourceUrl) for a feed product."""
    base = str(product.get("promoDesignation") or "").split("+")[0].upper()
    if base in DESIGNATION_CAMPAIGNS:
        campaign_id, label = DESIGNATION_CAMPAIGNS[base]
        return campaign_id, label, OFFERS_PAGE

    # Booklet offers end at local midnight, while the booklet itself stays
    # valid through that day — match on local dates with a 0–1 day tolerance.
    valid_from, valid_until = offer_period(product.get("discountStartDate"), product.get("discountEndDate"))
    if valid_until:
        end_date = date.fromisoformat(valid_until)
        for booklet in booklets:
            if not booklet.get("validUntil"):
                continue
            if valid_from and booklet.get("validFrom") and booklet["validFrom"] != valid_from:
                continue
            delta = (date.fromisoformat(booklet["validUntil"]) - end_date).days
            if 0 <= delta <= 1:
                return booklet["id"], booklet["name"], booklet.get("pdf") or OFFERS_PAGE

    campaign_id, label = BOOKLET_FALLBACKS.get(base, ("akcija", "Akcija"))
    return campaign_id, label, OFFERS_PAGE


def product_to_offer(product: dict, booklets: list[dict]) -> dict:
    name = display_name(product.get("name"))
    price = _money(product.get("discountedPrice")) or _money(product.get("price"))
    loyalty = _money(product.get("loyaltyPrice"))
    requires_card = loyalty is not None and price is not None and loyalty < price - 0.001
    if requires_card:
        sale, regular = loyalty, price
    else:
        sale = price
        reg_min = _money(product.get("regPriceMin"))
        regular = reg_min if reg_min is not None and sale is not None and reg_min > sale + 0.001 else None

    valid_from, valid_until = offer_period(product.get("discountStartDate"), product.get("discountEndDate"))
    campaign_id, campaign_label, source = resolve_campaign(product, booklets)

    offer = {
        "offerId": "top-" + str(product.get("keyCode") or product.get("id")),
        "name": name,
        "brand": display_name(product.get("brand")) or None,
        "price": sale,
        "regularPrice": regular,
        "requiresCard": requires_card,
        "onlyInApp": False,
        "campaign": campaign_label,
        "campaignId": campaign_id,
        "source": source,
        "validFrom": valid_from,
        "validUntil": valid_until,
        "store": "top",
        "image": product.get("normalImageUrl") or product.get("thumbnailImageUrl"),
        "ean": product.get("ean"),
    }
    if requires_card:
        offer["cardName"] = "mans top!"

    if sale is None:
        offer["reviewReason"] = "missing_price"
    elif len(name) < 4:
        offer["reviewReason"] = "weak_name"
    return offer


def main() -> None:
    sess = session()
    print("Discovering top! booklets...")
    booklets = fetch_booklets(sess)
    for b in booklets:
        print(f"  - {b['name']} [{b['validFrom']} – {b['validUntil']}] {b['pdf']}")

    print("Collecting top! promotion products...")
    products = fetch_products(sess)
    if not products:
        raise RuntimeError("No top! promotion products returned")
    print(f"Found {len(products)} products")

    offers: list[dict] = []
    reviews: list[dict] = []
    for product in products:
        offer = product_to_offer(product, booklets)
        if offer.get("reviewReason"):
            reviews.append(offer)
        else:
            offers.append(offer)

    unique = {o["offerId"]: o for o in offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in reviews}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())

    grouped = build_product_groups(offers) if offers else []

    catalogues = []
    for booklet in booklets:
        matched = [o for o in offers if o["campaignId"] == booklet["id"]]
        matched_rev = [o for o in reviews if o["campaignId"] == booklet["id"]]
        catalogues.append({
            "id": booklet["id"],
            "name": booklet["name"],
            "pdf": booklet["pdf"],
            "preview": booklet["preview"],
            "validFrom": booklet["validFrom"],
            "validUntil": booklet["validUntil"],
            "verifiedOffers": len(matched),
            "reviewOffers": len(matched_rev),
            "complete": len(matched) > 0,
        })
    campaigns = [
        {"id": campaign_id, "name": label}
        for campaign_id, label in DESIGNATION_CAMPAIGNS.values()
    ]

    payload = {
        "source": OFFERS_PAGE,
        "store": "top",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "campaigns": campaigns,
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

    write_feed(OUT, payload)
    print(f"Saved {len(offers)} offers ({len(reviews)} review) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Top scraper failed: {exc}", file=sys.stderr)
        raise