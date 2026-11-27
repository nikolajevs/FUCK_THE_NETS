#!/usr/bin/env python3
"""Collect ELVI Latvia (elvi.lv) promotional offers from the akcijas avīze listing.

ELVI's weekly newspaper ("Akcijas avīze") is published as an issuu flipbook, but
the chain also server-renders the promoted products on its own site:

- ``https://elvi.lv/akcijas-avize/`` renders the first batch of product cards
  and the campaign validity header ("Spēkā no 2026-11-11 līdz 2026-12-01");
- the remaining cards are loaded by the theme's ``offers.js`` via
  ``POST https://elvi.lv/wp-admin/admin-ajax.php`` with ``action=loadMore``,
  ``offset=N`` (steps of 16) and an empty ``cat`` (empty = all categories).
  The response is the same ``<li data-id="...">`` card markup; the end of the
  list is a small script that hides the load-more button.

The issuu flipbook itself is image-only (no public PDF download, no text
layer), so it is recorded as a catalogue link rather than parsed.

Card markup (WordPress theme elvi2019):

- ``<li data-id="70697">`` — the product id, also used for deduplication;
- ``a.product-pic`` links to the product page and carries the lazy image
  (``data-src``) plus an optional "Jaunums" badge;
- ``div.price`` comes in three variants:
  * ``price default`` — one ``col discount`` with an optional struck-through
    ``<span>`` regular price (sometimes a range "3.19€ - 3.39€", the lower
    bound is kept as the conservative regular price) and a ``<p>`` sale price;
    ``<p class="single">`` means a lone promo price with no reference price;
  * ``price two-plus`` — ``col old`` (struck-through regular price) next to
    ``col discount`` holding the per-unit price valid when buying 2+; a
    ``tooltip moreless`` badge carries the quantity ("2+"). These offers keep
    the price and expose ``multiBuy`` so the site can label the condition;
  * ``price percent`` — only a percentage ("−25%") with no absolute price;
    such cards go to ``reviewOffers`` (the comparator never guesses a price);
- ``tooltip card`` / ``tooltip card_plus3`` icons mark loyalty-card prices
  (ELVI Klienta karte / 3+ karte) and set ``requiresCard``;
- ``div.title`` holds the product name (published in upper case) and a detail
  span "Brand; 400 g; 5,98 €/kg" — brand, package size and unit price. The
  package is appended to the name when missing so product identity keeps
  sizes apart; unit prices and deposit notes never become brand or price.

ELVI does not publish alcoholic drinks in the online newspaper (the category
list only contains non-alcoholic ones), so no alcohol filter is needed.
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

BASE = "https://elvi.lv"
OFFERS_PAGE = BASE + "/akcijas-avize/"
AJAX_URL = BASE + "/wp-admin/admin-ajax.php"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/elvi-offers.json")
PAGE_SIZE = 16
MAX_PAGES = 60
PAGE_DELAY = 0.4
RETRIES = 3

CARD_SPLIT_RE = re.compile(r'(?=<li data-id="\d+")')
ID_RE = re.compile(r'<li data-id="(\d+)"')
URL_RE = re.compile(r'href="([^"]+)"[^>]*class="product-pic"')
IMAGE_RE = re.compile(r'<img[^>]*?(?:data-src|src)="([^"]+)"')
NAME_RE = re.compile(r'<div class="title">\s*<a[^>]*>([^<]*)</a>', re.S)
DETAIL_RE = re.compile(
    r'<div class="title">\s*<a[^>]*>[^<]*</a>\s*<span>(.*?)</span>', re.S)
PRICE_BLOCK_RE = re.compile(r'class="price ([a-z-]+)"(.*?)(?=<div class="title">)', re.S)
COL_RE = re.compile(r'class="col ([a-z-]+)"(.*?)</div>', re.S)
SPAN_PRICE_RE = re.compile(r'<span>\s*([^<]+?)\s*</span>')
P_PRICE_RE = re.compile(r'<p[^>]*>\s*([\d.,]+)\s*€\s*</p>')
PERCENT_RE = re.compile(r'<p[^>]*>\s*[−\-]?\s*(\d+)\s*%\s*</p>')
MONEY_RE = re.compile(r'([\d.,]+)\s*€')
TOOLTIP_RE = re.compile(r'class="tooltip ([a-z_0-9]+)"([^>]*)>(.*?)</div>', re.S)
TITLE_ATTR_RE = re.compile(r'title="([^"]*)"')
VALIDITY_RE = re.compile(
    r'Spēkā no\s*(\d{4}-\d{2}-\d{2})\s*līdz\s*(\d{4}-\d{2}-\d{2})')
ISSUU_RE = re.compile(r'href="(https://issuu\.com/[^"]+)"')
ISSUU_NR_RE = re.compile(r'nr[._-]*(\d+)', re.I)
UNIT_PRICE_RE = re.compile(r'([\d.,]+)\s*€\s*/\s*(?:kg|l|g|ml|gab\.?)', re.I)
PACKAGE_RE = re.compile(
    r'(?<![a-z0-9])(\d+(?:[,.]\d+)?)\s*(kg|g|ml|l|gab\.?)(?![a-z])', re.I)
DEPOSIT_RE = re.compile(r'\(\s*\+?\s*[\d.,]+\s*€\s*depozīts\s*\)', re.I)
END_OF_LIST_RE = re.compile(r'load-more"\)\.hide\(\)')

CAMPAIGNS = [
    {"id": "akcijas-avize", "name": "Akcijas avīze"},
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


def parse_validity(html_text: str) -> tuple[str | None, str | None]:
    match = VALIDITY_RE.search(html_text)
    return (match.group(1), match.group(2)) if match else (None, None)


def parse_catalogues(html_text: str, valid_from: str | None,
                     valid_until: str | None) -> list[dict]:
    """Record the issuu flipbook as a catalogue link (image-only, not parsed)."""
    catalogues: list[dict] = []
    for url in dict.fromkeys(ISSUU_RE.findall(html_text)):
        url = html.unescape(url)
        slug = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        nr_match = ISSUU_NR_RE.search(slug)
        nr = nr_match.group(1) if nr_match else None
        name = f"ELVI akciju avīze Nr. {nr}" if nr else "ELVI akciju avīze"
        cat_id = f"elvi-akciju-avize-nr-{nr}" if nr else "elvi-akciju-avize"
        catalogues.append({
            "id": cat_id,
            "name": name,
            "source": url,
            "validFrom": valid_from,
            "validUntil": valid_until,
        })
    return catalogues


def parse_detail(span: str) -> tuple[str | None, str | None]:
    """Split "Brand; 400 g; 5,98 €/kg" into brand and package size.

    Unit prices and deposit notes are stripped; they never become brand text.
    """
    text = html.unescape(span or "").strip()
    if not text:
        return None, None
    text = DEPOSIT_RE.sub("", text)
    brand: str | None = None
    package: str | None = None
    for segment in text.split(";"):
        segment = UNIT_PRICE_RE.sub("", segment).strip(" ,")
        pack_match = PACKAGE_RE.search(segment)
        if pack_match and package is None:
            package = f"{pack_match.group(1)} {pack_match.group(2).rstrip('.').lower()}"
            segment = segment[:pack_match.start()] + segment[pack_match.end():]
        leftover = segment.strip(" ,")
        if leftover and brand is None:
            brand = leftover
    return brand, package


def _norm_size(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(",", ".").casefold()).strip()


def parse_cards(html_text: str) -> list[dict]:
    """Extract raw promotion cards from one SSR page or AJAX response."""
    cards: list[dict] = []
    for chunk in CARD_SPLIT_RE.split(html_text):
        id_match = ID_RE.search(chunk)
        if not id_match:
            continue
        url_match = URL_RE.search(chunk)
        image_match = IMAGE_RE.search(chunk)
        name_match = NAME_RE.search(chunk)
        detail_match = DETAIL_RE.search(chunk)
        price_match = PRICE_BLOCK_RE.search(chunk)

        variant = price_match.group(1) if price_match else None
        price_html = price_match.group(2) if price_match else ""
        cols = {kind: body for kind, body in COL_RE.findall(price_html)}
        discount_col = cols.get("discount", "")
        old_col = cols.get("old", "")

        sale_match = P_PRICE_RE.search(discount_col) or P_PRICE_RE.search(price_html)
        regular_span = SPAN_PRICE_RE.search(discount_col) or SPAN_PRICE_RE.search(old_col)
        regular_values = [_money(v) for v in MONEY_RE.findall(regular_span.group(1))] \
            if regular_span else []
        regular_values = [v for v in regular_values if v is not None]
        percent_match = PERCENT_RE.search(price_html)

        card_tooltip = None
        card_name = None
        multi_buy = None
        for kind, attrs, body in TOOLTIP_RE.findall(price_html):
            title_match = TITLE_ATTR_RE.search(attrs)
            title = html.unescape(title_match.group(1)) if title_match else ""
            if kind == "card":
                card_tooltip = title or "ELVI Klienta karte"
                card_name = "ELVI Klienta karte"
            elif kind == "card_plus3":
                card_tooltip = title or "3+ karte"
                card_name = "3+ karte"
            elif kind == "moreless":
                label = re.sub(r"<[^>]+>", "", body).strip()
                multi_buy = label or "2+"
        if variant == "two-plus" and not multi_buy:
            multi_buy = "2+"

        brand, package = parse_detail(detail_match.group(1) if detail_match else "")
        name = html.unescape(name_match.group(1)).strip() if name_match else ""
        if package and _norm_size(package) not in _norm_size(name):
            name = f"{name} {package}".strip()

        cards.append({
            "id": id_match.group(1),
            "name": name,
            "brand": brand,
            "url": html.unescape(url_match.group(1)) if url_match else None,
            "image": html.unescape(image_match.group(1)) if image_match else None,
            "variant": variant,
            "price": _money(sale_match.group(1)) if sale_match else None,
            "regularPrice": min(regular_values) if regular_values else None,
            "percent": int(percent_match.group(1)) if percent_match else None,
            "isNew": "new-product" in chunk,
            "cardName": card_name,
            "cardTooltip": card_tooltip,
            "multiBuy": multi_buy,
        })
    return cards


def card_to_offer(card: dict, valid_from: str | None,
                  valid_until: str | None) -> dict:
    """Convert a raw card to a feed offer (ambiguous cards keep reviewReason)."""
    name = card.get("name") or ""
    sale = card.get("price")
    regular = card.get("regularPrice")
    if regular is not None and sale is not None and regular <= sale + 0.001:
        regular = None

    url = card.get("url") or ""
    source = url if url.startswith("http") else (BASE + url if url else OFFERS_PAGE)
    offer = {
        "offerId": "elvi-" + str(card.get("id")),
        "name": name,
        "brand": card.get("brand"),
        "price": sale,
        "regularPrice": regular,
        "requiresCard": card.get("cardName") is not None,
        "onlyInApp": False,
        "campaign": "Akcijas avīze",
        "campaignId": "akcijas-avize",
        "source": source,
        "validFrom": valid_from,
        "validUntil": valid_until,
        "store": "elvi",
        "image": card.get("image"),
    }
    if card.get("cardName"):
        offer["cardName"] = card["cardName"]
    if card.get("multiBuy"):
        offer["multiBuy"] = card["multiBuy"]

    if sale is None:
        offer["reviewReason"] = "percent_only" if card.get("percent") else "missing_price"
    elif len(name) < 4:
        offer["reviewReason"] = "weak_name"
    return offer


def fetch_page(sess: requests.Session, url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = sess.get(url, timeout=60)
            r.raise_for_status()
            if ID_RE.search(r.text) or VALIDITY_RE.search(r.text):
                return r.text
            last_error = RuntimeError("offers page: no product cards (bot check?)")
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ELVI offers page failed after {RETRIES} attempts: {last_error}")


def fetch_ajax_page(sess: requests.Session, offset: int) -> str:
    data = {
        "action": "loadMore",
        "offset": str(offset),
        "cat": "",
        "small_elvi": "",
        "big_elvi": "",
        "latvia_product": "",
        "moreless_count": "",
    }
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = sess.post(AJAX_URL, data=data, timeout=60)
            r.raise_for_status()
            if ID_RE.search(r.text) or END_OF_LIST_RE.search(r.text):
                return r.text
            last_error = RuntimeError(f"offset {offset}: unexpected AJAX response")
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ELVI AJAX offset {offset} failed after {RETRIES} attempts: {last_error}")


def fetch_offers(sess: requests.Session) -> tuple[list[dict], str | None, str | None, list[dict]]:
    first = fetch_page(sess, OFFERS_PAGE)
    valid_from, valid_until = parse_validity(first)
    catalogues = parse_catalogues(first, valid_from, valid_until)
    cards = parse_cards(first)
    seen = {c["id"] for c in cards}
    print(f"  offers page: {len(cards)} cards, valid {valid_from} .. {valid_until}")
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        time.sleep(PAGE_DELAY)
        text = fetch_ajax_page(sess, offset)
        fresh = [c for c in parse_cards(text) if c["id"] not in seen]
        for c in fresh:
            seen.add(c["id"])
        cards.extend(fresh)
        print(f"  ajax offset {offset}: {len(fresh)} new cards")
        if not fresh:
            break
    return cards, valid_from, valid_until, catalogues


def main() -> None:
    sess = session()
    print("Collecting ELVI akcijas avīze listing...")
    cards, valid_from, valid_until, catalogues = fetch_offers(sess)
    if not cards:
        raise RuntimeError("No ELVI promotion cards returned")
    print(f"Found {len(cards)} cards")

    offers: list[dict] = []
    reviews: list[dict] = []
    for card in cards:
        offer = card_to_offer(card, valid_from, valid_until)
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
        "store": "elvi",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "campaigns": CAMPAIGNS,
        "catalogues": catalogues,
        "offers": offers,
        "products": grouped,
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
        print(f"ELVI scraper failed: {exc}", file=sys.stderr)
        raise