#!/usr/bin/env python3
"""Collect Maxima campaigns, merge identical products and maintain price history."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz  # Compatibility fallback for older PyMuPDF releases.
    except ImportError:  # The scraper reports a clear error if the PDF dependency is absent.
        fitz = None

try:
    from scripts.product_identity import build_product_groups, product_identity
except ModuleNotFoundError:  # Running the file directly from the scripts/ directory.
    from product_identity import build_product_groups, product_identity

BROCHURES_URL = "https://www.maxima.lv/bukleti/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)"
}
OUT = Path("data/offers.json")
HISTORY_OUT = Path("data/price-history.json")
PALDIES_PAGE_URL = "https://www.maxima.lv/piedavajumi/"
PALDIES_CATALOG_ID = "maxima-paldies-catalogue"
PALDIES_CATALOG_NAME = "Piedāvājumi ar PALDIES karti"
PALDIES_PAGE_SIZE = 75

CAMPAIGNS = [
    {
        "id": "maxima-weekly",
        "name": "Iknedēļas piedāvājums",
        "url": "https://www.maxima.lv/piedavajumi/",
    },
    {
        "id": "maxima-well-done-premium",
        "name": "Well Done Premium",
        "url": "https://www.maxima.lv/premium",
    },
]

PRICE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,3})(?:[,.]\s*|\s+)(\d{2})(?!\d)")
MULTI_BUY_RE = re.compile(r"\b\d+\s*\+\s*\d+\b|\b(?:pērkot|iegādājoties|saņemot)\b", re.IGNORECASE)
DISCOUNT_RE = re.compile(r"-\s*(\d{1,2})\s*%")
MONTHS = {
    "janvāris": 1, "janv": 1, "februāris": 2, "febr": 2,
    "marts": 3, "mar": 3, "aprīlis": 4, "apr": 4,
    "maijs": 5, "mai": 5, "jūnijs": 6, "jūn": 6,
    "jūlijs": 7, "jūl": 7, "augusts": 8, "aug": 8,
    "septembris": 9, "sept": 9, "septem": 9, "oktobris": 10, "okt": 10,
    "novembris": 11, "nov": 11, "decembris": 12, "dec": 12,
}
MONTH_PATTERN = "|".join(sorted((re.escape(k) for k in MONTHS), key=len, reverse=True))
IGNORED_NAMES = {"not found", "paldies", "image", "maxima"}
IGNORED_PROMOTIONS = {
    "adventes kalendāriem",
    "rotaļlietām (izņemot konstruktoriem lego)",
}


def product_id(name: str, unit: str | None = None) -> str:
    raw = unicodedata.normalize("NFKD", f"{name} {unit or ''}").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:100]


def prices_from(text: str) -> list[float]:
    """Read shelf prices, excluding €/kg and €/l unit prices."""
    cleaned = re.sub(
        r"(?<!\d)\d{1,3}[,.]\s*\d{2}\s*€\s*/\s*(?:kg|l|100\s*g|100\s*ml|gab\.?|pcs?|rull\w*|pāri|pāris)\b",
        " ", text, flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\([^)]*€\s*/\s*[^)]*\)", " ", cleaned)
    # Product sizes and pack quantities can resemble shelf prices:
    # 0,33L -> €0.33, 42gab -> €1.42, S7 17+kg -> €7.17,
    # and age/size ranges such as "3 12+" or "35-38, 39-42".
    cleaned = re.sub(
        r"\bS\s*\d{1,2}\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s*\+?\s*kg\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b\d{1,3}(?:[,.]\s*\d{1,3})?\s*(?:kg|g|ml|l|gab\.?|gb)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b\d{1,2}\s+\d{1,2}\+(?!\d)", " ", cleaned)
    cleaned = re.sub(r"\b\d{2}\s*[-–]\s*\d{2}\b(?!\s*%)", " ", cleaned)
    # Pampers and other diaper packs encode size and weight as "S7 17+kg"
    # or "S5 11-17kg". Without removing this, "7 17" is falsely read as €7.17.
    cleaned = re.sub(
        r"\bS\s*\d{1,2}\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s*\+?\s*kg\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    values = [
        int(euros) + int(cents) / 100
        for euros, cents in PRICE_TOKEN_RE.findall(cleaned)
    ]
    result: list[float] = []
    for value in values:
        if 0.01 <= value <= 9999 and value not in result:
            result.append(round(value, 2))
    return result


def offer_dates(text: str, today: date | None = None) -> tuple[str | None, str | None]:
    """Parse Latvian campaign date ranges, including end-year dates across New Year."""
    today = today or date.today()
    text = " ".join(text.replace("\xa0", " ").split()).casefold()

    match = re.search(
        rf"(?P<d1>\d{{1,2}})\.?\s*(?P<m1>{MONTH_PATTERN})\.?\s*[-–—]\s*"
        rf"(?P<d2>\d{{1,2}})\.?\s*(?P<m2>{MONTH_PATTERN})\.?",
        text,
    )
    if match:
        d1, m1, d2, m2 = match.group("d1", "m1", "d2", "m2")
        month1, month2 = MONTHS[m1.rstrip(".")], MONTHS[m2.rstrip(".")]
    else:
        match = re.search(
            rf"(?P<d1>\d{{1,2}})\.?\s*[-–—]\s*(?P<d2>\d{{1,2}})\.?\s*"
            rf"(?P<m>{MONTH_PATTERN})\.?",
            text,
        )
        if match:
            d1, d2, month_name = match.group("d1", "d2", "m")
            month1 = month2 = MONTHS[month_name.rstrip(".")]
        else:
            match = re.search(
                r"(?P<d1>\d{1,2})\.(?P<m1>\d{1,2})\.?\s*[-–—]\s*"
                r"(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})\.?",
                text,
            )
            if not match:
                return None, None
            d1, m1, d2, m2 = match.group("d1", "m1", "d2", "m2")
            month1, month2 = int(m1), int(m2)

    # In Latvian catalogues the year is often printed only after the end date:
    # "17.11. - 04.01.2027". Treat it as the end year, not the start year.
    end_year_match = re.search(r"\b(20\d{2})\b", text[match.end():match.end() + 12])
    start_year_match = re.search(r"\b(20\d{2})\b", text[:match.start()])
    if end_year_match:
        year2 = int(end_year_match.group(1))
        year1 = year2 - (1 if month2 < month1 else 0)
    elif start_year_match:
        year1 = int(start_year_match.group(1))
        year2 = year1 + (1 if month2 < month1 else 0)
    else:
        year1 = today.year
        year2 = year1 + (1 if month2 < month1 else 0)

    try:
        start_date = date(year1, month1, int(d1))
        end_date = date(year2, month2, int(d2))
    except ValueError:
        return None, None
    if end_date < start_date:
        return None, None
    return start_date.isoformat(), end_date.isoformat()

def campaign_dates_from_soup(
    soup: BeautifulSoup,
    page_text: str,
    campaign_name: str,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    """Prefer dates from the smallest DOM container holding the exact campaign heading."""
    candidates: list[tuple[int, int, tuple[str | None, str | None]]] = []
    for text_node in soup.find_all(
        string=lambda value: value and campaign_name.casefold() in str(value).casefold()
    ):
        node = text_node.parent
        for depth in range(1, 8):
            if not isinstance(node, Tag):
                break
            container_text = node.get_text(" ", strip=True)
            if len(container_text) <= 500:
                title_match = re.search(re.escape(campaign_name), container_text, flags=re.IGNORECASE)
                if title_match:
                    tail = container_text[title_match.end():title_match.end() + 140]
                    dates = offer_dates(tail, today=today)
                    if dates != (None, None):
                        candidates.append((len(container_text), depth, dates))
            node = node.parent
    if candidates:
        return min(candidates, key=lambda item: (item[0], item[1]))[2]

    # The weekly product listing itself may include the current date range.
    matches = list(re.finditer(re.escape(campaign_name), page_text, flags=re.IGNORECASE))
    for match in matches:
        dates = offer_dates(page_text[match.end():match.end() + 100], today=today)
        if dates != (None, None):
            return dates
    return None, None


def campaign_index_metadata(
    soup: BeautifulSoup,
    campaign_name: str,
    today: date | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve dates and brochure URL using campaign headings or nearby brochure links."""
    candidates: list[tuple[int, int, str | None, str | None, str | None]] = []

    # First inspect the heading and its nearest card-like ancestors.
    for heading in soup.find_all(
        lambda tag: isinstance(tag, Tag)
        and tag.name in {"h1", "h2", "h3", "h4", "p"}
        and campaign_name.casefold() in tag.get_text(" ", strip=True).casefold()
    ):
        node: Tag | None = heading
        for depth in range(1, 9):
            if not isinstance(node, Tag):
                break
            container_text = node.get_text(" ", strip=True)
            if len(container_text) <= 900 and campaign_name.casefold() in container_text.casefold():
                title_match = re.search(re.escape(campaign_name), container_text, flags=re.IGNORECASE)
                tail = container_text[title_match.end():title_match.end() + 220] if title_match else ""
                valid_from, valid_until = offer_dates(tail, today=today)
                detail_url = next(
                    (
                        urljoin(BROCHURES_URL, anchor["href"])
                        for anchor in node.find_all("a", href=True)
                        if "/bukleti/" in anchor["href"]
                    ),
                    None,
                )
                if detail_url or valid_from or valid_until:
                    candidates.append(
                        (len(container_text), depth, valid_from, valid_until, detail_url)
                    )
            node = node.parent if isinstance(node.parent, Tag) else None

    # Some Maxima page versions place "Lasīt" outside the heading wrapper.
    # Walk upward from every brochure link and test its surrounding card text.
    for anchor in soup.find_all("a", href=True):
        if "/bukleti/" not in anchor.get("href", ""):
            continue
        node: Tag | None = anchor
        for depth in range(1, 9):
            if not isinstance(node, Tag):
                break
            container_text = node.get_text(" ", strip=True)
            if len(container_text) <= 900 and campaign_name.casefold() in container_text.casefold():
                title_match = re.search(re.escape(campaign_name), container_text, flags=re.IGNORECASE)
                tail = container_text[title_match.end():title_match.end() + 220] if title_match else ""
                valid_from, valid_until = offer_dates(tail, today=today)
                candidates.append((
                    len(container_text),
                    depth,
                    valid_from,
                    valid_until,
                    urljoin(BROCHURES_URL, anchor["href"]),
                ))
            node = node.parent if isinstance(node.parent, Tag) else None

    if not candidates:
        return None, None, None
    _, _, valid_from, valid_until, detail_url = min(
        candidates,
        key=lambda item: (item[0], item[1], item[4] is None),
    )
    return valid_from, valid_until, detail_url

def store_formats_from_text(text: str) -> list[str]:
    """Return only formats explicitly named in the campaign's store-scope sentence."""
    formats: set[str] = set()
    for match in re.finditer(r"\bMAXIMA\b", text, flags=re.IGNORECASE):
        segment = text[match.end():match.end() + 180]
        store_word = re.search(r"\bveikal\w*", segment, flags=re.IGNORECASE)
        if not store_word:
            continue
        scope_text = segment[:store_word.start()].upper()
        formats.update(re.findall(r"(?<![A-Z])(?:XXX|XX|X)(?![A-Z])", scope_text))
    return [fmt for fmt in ("X", "XX", "XXX") if fmt in formats]


def fetch_campaign_store_formats(
    session: requests.Session,
    detail_url: str | None,
) -> tuple[list[str], str | None]:
    if not detail_url:
        return [], None
    try:
        response = session.get(detail_url, headers=HEADERS, timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        formats = store_formats_from_text(soup.get_text(" ", strip=True))
        return formats, detail_url if formats else None
    except requests.RequestException as exc:
        print(
            f"Warning: could not verify store formats from {detail_url}: {exc}",
            file=sys.stderr,
        )
        return [], None


def has_paldies_marker(card: Tag) -> bool:
    for marker in card.find_all("img"):
        alt = (marker.get("alt") or "").strip().casefold()
        src = (marker.get("src") or marker.get("data-src") or "").casefold()
        if alt == "paldies" or "paldies" in Path(src.split("?", 1)[0]).name:
            return True
    return False


def image_url(img: Tag, base_url: str) -> str | None:
    for attribute in ("src", "data-src", "data-original"):
        value = img.get(attribute)
        if value:
            return urljoin(base_url, value)
    srcset = img.get("srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split()[0]
        return urljoin(base_url, first)
    return None


def find_product_card(img: Tag) -> tuple[Tag, str, list[float]] | None:
    candidates: list[tuple[int, Tag, str, list[float]]] = []
    node: Tag | None = img
    for _ in range(8):
        if node is None:
            break
        card_text = node.get_text(" ", strip=True)
        image_count = len(node.find_all("img")) if isinstance(node, Tag) else 0
        prices = prices_from(card_text)
        if prices and len(card_text) < 1200 and 1 <= image_count <= 3:
            candidates.append((len(card_text), node, card_text, prices))
        node = node.parent if isinstance(node.parent, Tag) else None
    if not candidates:
        return None
    _, card, card_text, prices = min(candidates, key=lambda item: item[0])
    return card, card_text, prices


def price_quality(
    prices: list[float],
    card_text: str,
) -> tuple[float | None, float | None, int | None, list[str]]:
    """Return promo price, comparable price, printed/computed discount and issues."""
    issues: list[str] = []
    if not prices:
        return None, None, None, ["price_not_found"]

    price = prices[0]
    regular: float | None = None
    if len(prices) == 2:
        if prices[1] > price:
            regular = prices[1]
        elif MULTI_BUY_RE.search(card_text):
            # In multi-buy promotions, two extracted values may mean a bundle
            # price and a per-item price rather than promo/regular prices.
            issues.append("ambiguous_multi_buy_price_order")
        else:
            # PDF text order is not always visual order: separate price boxes
            # may be emitted regular-first even when the offer price is lower.
            price, regular = sorted(prices)
    elif len(prices) > 2:
        issues.append("ambiguous_multiple_price_values")

    printed_match = DISCOUNT_RE.search(card_text)
    printed_discount = int(printed_match.group(1)) if printed_match else None
    computed_discount = round((1 - price / regular) * 100) if regular else None

    if printed_discount is not None and computed_discount is not None:
        if abs(printed_discount - computed_discount) > 1:
            issues.append("discount_percent_mismatch")
        discount = printed_discount
    else:
        discount = printed_discount if printed_discount is not None else computed_discount

    if not 0.01 <= price <= 9999:
        issues.append("price_out_of_range")
    return price, regular, discount, issues


def _has_explicit_shelf_price(text: str, price: float) -> bool:
    """Check whether the parsed shelf price is explicitly followed by € (not €/unit)."""
    for match in re.finditer(
        r"(?<!\d)(\d{1,3}[,.]\s*\d{2})\s*€(?!\s*/)",
        text,
        flags=re.IGNORECASE,
    ):
        value = float(re.sub(r"\s+", "", match.group(1)).replace(",", "."))
        if abs(value - price) < 0.005:
            return True
    return False


def stable_offer_id(
    campaign_id: str,
    canonical_product_id: str,
    image: str | None,
    product_source: str,
    store_formats: list[str],
    requires_paldies: bool,
    source_product_id: str | None = None,
) -> str:
    payload = "|".join([
        campaign_id,
        canonical_product_id,
        image or "",
        product_source,
        ",".join(store_formats),
        str(requires_paldies),
        source_product_id or "",
    ])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def parse_campaign(
    session: requests.Session,
    campaign: dict,
    campaign_index: BeautifulSoup,
) -> tuple[list[dict], list[dict], dict]:
    response = session.get(campaign["url"], headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    valid_from, valid_until, detail_url = campaign_index_metadata(
        campaign_index, campaign["name"]
    )
    if not valid_from or not valid_until:
        page_dates = campaign_dates_from_soup(
            soup, page_text, campaign["name"]
        )
        valid_from = valid_from or page_dates[0]
        valid_until = valid_until or page_dates[1]

    store_formats, store_scope_source = fetch_campaign_store_formats(session, detail_url)
    offers_by_id: dict[str, dict] = {}
    reviews_by_id: dict[str, dict] = {}

    for img in soup.find_all("img"):
        name = (img.get("alt") or "").strip()
        if len(name) < 4 or name.casefold() in IGNORED_NAMES | IGNORED_PROMOTIONS:
            continue

        card_result = find_product_card(img)
        if not card_result:
            continue
        card, card_text, prices = card_result
        price, regular, discount, price_issues = price_quality(prices, card_text)
        if price is None:
            continue

        requires_paldies = has_paldies_marker(card)
        card_link = card.find("a", href=True)
        product_source = urljoin(campaign["url"], card_link["href"]) if card_link else campaign["url"]
        image = image_url(img, campaign["url"])
        identity = product_identity(name)
        legacy_product_id = product_id(name)
        offer_id = stable_offer_id(
            campaign["id"],
            identity["canonicalProductId"],
            image,
            product_source,
            store_formats,
            requires_paldies,
        )
        quality = {
            "priceStatus": "review" if price_issues else "verified",
            "dateStatus": "verified" if valid_from and valid_until else "unknown",
            "storeScopeStatus": "verified" if store_formats else "unknown",
            "issues": price_issues,
        }
        offer = {
            "offerId": offer_id,
            "sourceId": campaign["id"],
            "productId": legacy_product_id,
            "canonicalProductId": identity["canonicalProductId"],
            "identityKey": identity["identityKey"],
            "packageSize": identity["packageSize"],
            "name": name,
            "brand": None,
            "image": image,
            "price": price,
            "regularPrice": regular,
            "paldiesPrice": price if requires_paldies else None,
            "priceWithoutPaldies": regular if requires_paldies else None,
            "unit": None,
            "discountPercent": discount,
            "requiresPaldies": requires_paldies,
            "paldis": requires_paldies,
            "validFrom": valid_from,
            "validUntil": valid_until,
            "city": None,
            "store": "Maxima",
            "storeFormats": store_formats,
            "storeScopeSource": store_scope_source,
            "campaign": campaign["name"],
            "campaignSource": campaign["url"],
            "source": product_source,
            "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "quality": quality,
        }

        # Duplicate image/title nodes within one campaign collapse to one offer variant.
        existing = offers_by_id.get(offer_id) or reviews_by_id.get(offer_id)
        if existing and existing["quality"]["priceStatus"] == "verified" and quality["priceStatus"] == "review":
            continue
        if quality["priceStatus"] == "review":
            reviews_by_id[offer_id] = offer
        else:
            offers_by_id[offer_id] = offer
            reviews_by_id.pop(offer_id, None)

    campaign_offers = sorted(
        offers_by_id.values(),
        key=lambda item: (item["name"].casefold(), item["price"]),
    )
    campaign_reviews = sorted(
        reviews_by_id.values(),
        key=lambda item: (item["name"].casefold(), item["price"]),
    )
    metadata = {
        "id": campaign["id"],
        "name": campaign["name"],
        "source": campaign["url"],
        "detailSource": detail_url,
        "validFrom": valid_from,
        "validUntil": valid_until,
        "storeFormats": store_formats,
        "storeScopeSource": store_scope_source,
        "verifiedOffers": len(campaign_offers),
        "reviewOffers": len(campaign_reviews),
    }
    return campaign_offers, campaign_reviews, metadata


def find_paldies_filter_id(soup: BeautifulSoup) -> str:
    """Find the official 'Ar PALDIES karti' filter instead of hard-coding its ID."""
    for field in soup.select('input[name="filters[]"]'):
        container = field.find_parent(class_="lv-filter-category") or field.parent
        label = container.get_text(" ", strip=True).casefold() if isinstance(container, Tag) else ""
        if "ar paldies karti" in label:
            value = str(field.get("value", "")).strip()
            if value:
                return value
    raise RuntimeError("Could not find the official 'Ar PALDIES karti' filter on Maxima offers page")


def parse_paldies_card(
    card: Tag,
    source_url: str,
    checked_at: str | None = None,
) -> dict:
    """Turn one product card from the PALDIES-filtered catalogue into an offer."""
    source_product_id = str(card.get("data-product-id") or "").strip() or None
    product_images = [
        img for img in card.find_all("img")
        if (img.get("alt") or "").strip()
        and (img.get("alt") or "").strip().casefold() not in {"paldies", "image", "maxima"}
    ]
    product_image = product_images[0] if product_images else None
    name = (product_image.get("alt") or "").strip() if product_image else ""
    if not name:
        name_node = card.select_one(".name, .title, h2, h3, h4")
        name = name_node.get_text(" ", strip=True) if name_node else ""
    issues: list[str] = []
    if not name:
        name = f"Maxima PALDIES product {source_product_id or 'unknown'}"
        issues.append("product_name_not_found")
    if not source_product_id:
        issues.append("source_product_id_missing")

    card_text = card.get_text(" ", strip=True)
    prices = prices_from(card_text)
    price, regular, discount, price_issues = price_quality(prices, card_text)
    issues.extend(price_issues)

    valid_from, valid_until = offer_dates(str(card.get("data-dates-interval") or ""))
    has_marker = has_paldies_marker(card)
    classes = card.get("class", [])
    source_marks_paldies = "paldies-card" in classes
    if not has_marker and not source_marks_paldies:
        issues.append("paldies_marker_missing")

    image = image_url(product_image, PALDIES_PAGE_URL) if product_image else None
    identity = product_identity(name)
    legacy_product_id = product_id(name)
    offer_id = stable_offer_id(
        PALDIES_CATALOG_ID,
        identity["canonicalProductId"],
        image,
        source_url,
        [],
        True,
        source_product_id,
    )
    quality = {
        "priceStatus": "review" if issues else "verified",
        "dateStatus": "verified" if valid_from and valid_until else "unknown",
        "storeScopeStatus": "unknown",
        "issues": issues,
    }
    return {
        "offerId": offer_id,
        "sourceId": PALDIES_CATALOG_ID,
        "sourceProductId": source_product_id,
        "productId": legacy_product_id,
        "canonicalProductId": identity["canonicalProductId"],
        "identityKey": identity["identityKey"],
        "packageSize": identity["packageSize"],
        "name": name,
        "brand": None,
        "image": image,
        "price": price,
        "regularPrice": regular,
        "paldiesPrice": price,
        "priceWithoutPaldies": regular,
        "unit": None,
        "discountPercent": discount,
        "requiresPaldies": True,
        "paldis": True,
        "paldiesMarkerDetected": has_marker,
        "validFrom": valid_from,
        "validUntil": valid_until,
        "validityText": str(card.get("data-dates-interval") or "") or None,
        "city": None,
        "store": "Maxima",
        "storeFormats": [],
        "storeScopeSource": None,
        "campaign": PALDIES_CATALOG_NAME,
        "campaignSource": source_url,
        "source": source_url,
        "checkedAt": checked_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "quality": quality,
    }


def collect_paldies_catalog(
    session: requests.Session,
) -> tuple[list[dict], list[dict], dict]:
    """Collect every product exposed by Maxima's PALDIES card filter, across all pages."""
    page_response = session.get(PALDIES_PAGE_URL, headers=HEADERS, timeout=30)
    page_response.raise_for_status()
    page_soup = BeautifulSoup(page_response.text, "html.parser")
    filter_id = find_paldies_filter_id(page_soup)

    limit_options = []
    limit_select = page_soup.find(id="filter_limit")
    if isinstance(limit_select, Tag):
        for option in limit_select.find_all("option"):
            try:
                value = int(option.get("value", ""))
                if 1 <= value <= PALDIES_PAGE_SIZE:
                    limit_options.append(value)
            except (TypeError, ValueError):
                continue
    page_size = max(limit_options) if limit_options else PALDIES_PAGE_SIZE

    sort_by = "newest"
    sort_select = page_soup.find(id="filter_sort_by")
    if isinstance(sort_select, Tag):
        selected = sort_select.find("option", selected=True)
        if isinstance(selected, Tag) and selected.get("value"):
            sort_by = str(selected["value"])
    hidden_sort = page_soup.select_one('#sales-filters-form input[name="sort_by"]')
    if isinstance(hidden_sort, Tag) and hidden_sort.get("value"):
        sort_by = str(hidden_sort["value"])

    ajax_input = page_soup.find(id="ajax-load-more-url")
    if not isinstance(ajax_input, Tag) or not ajax_input.get("value"):
        raise RuntimeError("Maxima PALDIES catalogue page does not expose its AJAX endpoint")
    ajax_url = urljoin(PALDIES_PAGE_URL, str(ajax_input["value"]))
    source_url = PALDIES_PAGE_URL + "?" + requests.compat.urlencode({"filters[]": filter_id})

    ajax_headers = {
        **HEADERS,
        "Referer": PALDIES_PAGE_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    def fetch_page(offset: int) -> tuple[int, list[Tag]]:
        params = [
            ("filters[]", filter_id),
            ("sort_by", sort_by),
            ("limit", str(page_size)),
            ("ajax", "1"),
            ("offset", str(offset)),
        ]
        response = session.get(
            ajax_url,
            params=params,
            headers=ajax_headers,
            timeout=45,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Maxima PALDIES AJAX response is not JSON at offset {offset}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("html"), str):
            raise RuntimeError(f"Maxima PALDIES AJAX response has an unexpected shape at offset {offset}")
        try:
            total_items = int(payload.get("totalItems", 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Maxima PALDIES AJAX response has no valid totalItems count") from exc
        if total_items <= 0:
            raise RuntimeError("Maxima PALDIES filter returned zero products; refusing to publish an empty catalogue")
        result_soup = BeautifulSoup(payload["html"], "html.parser")
        cards = [
            card for card in result_soup.select(".item.paldies-card")
            if isinstance(card, Tag)
        ]
        return total_items, cards

    first_total, first_cards = fetch_page(0)
    if not first_cards:
        raise RuntimeError("Maxima PALDIES catalogue returned no product cards on its first page")

    all_cards: list[Tag] = []
    seen_source_ids: set[str] = set()
    pages = 0
    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")

    # Offsets are the parameter used by Maxima's own load-more endpoint.
    for offset in range(0, first_total, page_size):
        total_items, cards = (first_total, first_cards) if offset == 0 else fetch_page(offset)
        if total_items != first_total:
            raise RuntimeError(
                f"Maxima PALDIES total changed during collection ({first_total} -> {total_items}); retry next run"
            )
        expected_count = min(page_size, first_total - offset)
        if len(cards) != expected_count:
            raise RuntimeError(
                f"Incomplete PALDIES page at offset {offset}: expected {expected_count}, got {len(cards)}"
            )

        page_ids = [
            str(card.get("data-product-id") or "").strip()
            for card in cards
        ]
        if any(not item_id for item_id in page_ids):
            raise RuntimeError(f"PALDIES page at offset {offset} contains a card without data-product-id")
        if len(set(page_ids)) != len(page_ids):
            raise RuntimeError(f"PALDIES page at offset {offset} contains duplicate product IDs")
        overlap = seen_source_ids.intersection(page_ids)
        if overlap:
            raise RuntimeError(
                f"PALDIES pagination repeated {len(overlap)} products at offset {offset}; refusing partial catalogue"
            )

        seen_source_ids.update(page_ids)
        all_cards.extend(cards)
        pages += 1

    if len(seen_source_ids) != first_total:
        raise RuntimeError(
            f"PALDIES catalogue incomplete: server reports {first_total}, collected {len(seen_source_ids)} unique products"
        )

    offers: list[dict] = []
    reviews: list[dict] = []
    for card in all_cards:
        offer = parse_paldies_card(card, source_url, observed_at)
        if offer["quality"]["priceStatus"] == "verified":
            offers.append(offer)
        else:
            reviews.append(offer)

    metadata = {
        "id": PALDIES_CATALOG_ID,
        "name": PALDIES_CATALOG_NAME,
        "source": source_url,
        "apiSource": ajax_url,
        "filterName": "Ar PALDIES karti",
        "filterId": filter_id,
        "detailSource": None,
        "validFrom": None,
        "validUntil": None,
        "storeFormats": [],
        "storeScopeSource": None,
        "expectedOffers": first_total,
        "collectedProducts": len(all_cards),
        "pages": pages,
        "verifiedOffers": len(offers),
        "reviewOffers": len(reviews),
        "complete": len(seen_source_ids) == first_total,
    }
    return offers, reviews, metadata


def _catalogue_id(title: str) -> str:
    folded = title.casefold()
    if "iknedēļas piedāvājums" in folded:
        return "maxima-weekly"
    if "well done premium" in folded:
        return "maxima-well-done-premium"
    if "grāmatu festivāls" in folded:
        return "maxima-book-festival"
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    return f"maxima-catalogue-{slug or hashlib.sha1(title.encode()).hexdigest()[:8]}"


def discover_active_catalogues(
    session: requests.Session,
    index_soup: BeautifulSoup,
    today: date,
) -> list[dict]:
    """Discover every currently active brochure from Maxima's own catalogue index."""
    discovered: dict[str, dict] = {}
    for anchor in index_soup.find_all("a", href=True):
        detail_url = urljoin(BROCHURES_URL, anchor["href"])
        if "/bukleti/" not in detail_url or detail_url.rstrip("/") == BROCHURES_URL.rstrip("/"):
            continue

        candidates: list[tuple[int, int, Tag, str, str, str]] = []
        node: Tag | None = anchor
        for depth in range(1, 10):
            node = node.parent if isinstance(node.parent, Tag) else None
            if node is None:
                break
            heading = node.find(["h1", "h2", "h3", "h4"])
            if not isinstance(heading, Tag):
                continue
            title = heading.get_text(" ", strip=True)
            card_text = node.get_text(" ", strip=True)
            if not title or len(card_text) > 1600:
                continue
            valid_from, valid_until = offer_dates(card_text, today=today)
            if valid_from and valid_until:
                candidates.append((len(card_text), depth, node, title, valid_from, valid_until))

        if not candidates or detail_url in discovered:
            continue
        _, _, card, title, valid_from, valid_until = min(
            candidates, key=lambda item: (item[0], item[1])
        )
        if not (valid_from <= today.isoformat() <= valid_until):
            continue

        try:
            response = session.get(detail_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Warning: could not read active Maxima catalogue {detail_url}: {exc}", file=sys.stderr)
            continue

        detail_soup = BeautifulSoup(response.text, "html.parser")
        pdf_urls = list(dict.fromkeys(
            urljoin(detail_url, link["href"])
            for link in detail_soup.find_all("a", href=True)
            if ".pdf" in link["href"].casefold()
        ))
        detail_text = detail_soup.get_text(" ", strip=True)
        formats, scope_source = fetch_campaign_store_formats(session, detail_url)
        if not formats:
            explicit_format = re.search(r"\bMAXIMA\s+(XXX|XX|X)\b", detail_text, flags=re.IGNORECASE)
            if explicit_format:
                formats = [explicit_format.group(1).upper()]
                scope_source = detail_url

        store_match = re.search(
            r"MAXIMA\s+veikalā\s+(.{2,100}?)(?:\.|Preču daudzums|Atpakaļ|$)",
            detail_text,
            flags=re.IGNORECASE,
        )
        store_description = store_match.group(1).strip(" ,") if store_match else None
        city = "Roja" if store_description and "roj" in store_description.casefold() else None
        source_id = _catalogue_id(title)
        discovered[detail_url] = {
            "id": source_id,
            "name": title,
            "source": detail_url,
            "detailSource": detail_url,
            "pdfSources": pdf_urls,
            "validFrom": valid_from,
            "validUntil": valid_until,
            "storeFormats": formats,
            "storeScopeSource": scope_source or (detail_url if store_description else None),
            "storeDescription": store_description,
            "city": city,
            "verifiedOffers": 0,
            "reviewOffers": 0,
            "pdfPages": 0,
            "complete": bool(pdf_urls),
        }

    return sorted(discovered.values(), key=lambda item: (item["validFrom"], item["name"].casefold()))


def _cluster_values(values: list[float], tolerance: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or value - sum(clusters[-1]) / len(clusters[-1]) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return clusters


def _looks_like_pdf_product_description(text: str) -> bool:
    folded = " ".join(text.casefold().split())
    if re.match(r"^\d+\s+(?:izmērs\b|gab\.?\s*iepak\.?\b|iepak\.?\s*gab\.?\b)", folded):
        return False
    if len(re.findall(r"[a-zāčēģīķļņōŗšūž]{2,}", folded)) < 2:
        return False
    if any(term in folded for term in (
        "piedāvājums spēkā", "preču daudzums ierobežots",
        "cena pie preces", "ar aktuālo preču piedāvājumu",
        "black friday", "cenu festivāls", "veikalos:",
        "cenas ir pārbaudītas", "kontrolpirkumiem",
        "cenu salīdzinājums", "neņemot vērā mazumtirgotāju",
        "reklāmas izplatīšanas laikā", "salīdzinājumā iekļautie produkti",
    )):
        return False
    has_package = bool(re.search(
        r"\b\d+(?:[,.]\d+)?\s*(?:kg|g|ml|l|gab\.?|ruļļi|rullis|pāri|cm|mm)\b",
        folded,
    ))
    return has_package or "grāmata" in folded or "grāmatu" in folded


def _is_pdf_product_title_seed(text: str) -> bool:
    """Recognize product-name blocks even when package details and prices are separate."""
    value = " ".join(text.replace("\xa0", " ").split())
    folded = value.casefold()
    if re.match(r"^\d+\s+(?:izmērs\b|gab\.?\s*iepak\.?\b|iepak\.?\s*gab\.?\b)", folded):
        return False
    words = re.findall(r"[a-zāčēģīķļņōŗšūž]{2,}", folded)
    if not words or len(words) > 14 or len(value) > 180:
        return False
    if any(term in folded for term in (
        "piedāvājums spēkā", "preču daudzums ierobežots",
        "cena pie preces", "ar aktuālo preču piedāvājumu",
        "black friday", "cenu festivāls", "veikalos:",
        "well done premium", "produktu piedāvājumi",
        "pārtikas un ikdienas preču piedāvājumi",
        "augļi un dārzeņi", "grāmatu festivāls",
        "saldēto produktu piedāvājumi", "cenu un marķējumu skaidrojums",
        "cenas ir pārbaudītas", "kontrolpirkumiem",
        "cenu salīdzinājums", "neņemot vērā mazumtirgotāju",
        "reklāmas izplatīšanas laikā", "salīdzinājumā iekļautie produkti",
    )):
        return False
    if re.search(r"\b(?:akcijas cena|iepriekšējā cena|piedāvājuma cena|atlaide)\b", folded):
        return False
    return True

def _clean_pdf_product_name(text: str) -> str:
    value = " ".join(text.replace("\xa0", " ").split())
    starts_with_price = bool(re.match(
        r"^\d{1,3}(?:[,.]\s*\d{2}|\s+\d{2})(?:\s*€)?(?:\s+|$)",
        value,
    ))
    # PDF extraction may retain standalone card bullets inside the clipped cell.
    value = re.sub(r"(?<!\w)[•·▪●◦]+(?!\w)", " ", value)
    value = " ".join(value.split())
    # Brochure card numbers are often extracted as if they were part of the SKU.
    value = re.sub(
        r"^(?:\d{1,2}\.?\s+(?!(?:cm|mm|kg|g|ml|l|gab\.?|iepak\.?)\b))+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    # A trailing bare number after the package size is often a clipped price token.
    value = re.sub(
        r"(\b\d+(?:[,.]\d+)?\s*(?:kg|g|ml|l|gab\.?))\s+\d{1,2}$",
        r"\1",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bWELL\s+PREMIUM\b", "WELL DONE PREMIUM", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\b(?:akcijas cena|piedāvājuma cena|cena ar paldies karti|cena bez paldies kartes|"
        r"cena ar paldies|cena bez paldies|parastā cena|iepriekšējā cena|paldies karte|"
        r"cena par kilogramu|cena par kg)\b:?\s*",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\([^)]*€\s*/\s*[^)]*\)", " ", value, flags=re.IGNORECASE)
    # Broken PDF text blocks can leave only the tail of a unit-price label
    # (for example "€/kg; Tortiljas ..." or "... ( /kg").
    value = re.sub(
        r"^(?:€\s*/\s*(?:kg|l|100\s*g|100\s*ml|gab\.?|rull\w*)\s*[;,:-]\s*)+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\s*[;(]?\s*/\s*(?:kg|l|100\s*g|100\s*ml|gab\.?|rull\w*)\s*[)]?$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\d{1,3}[,.]\d{2}\s*€?", " ", value)
    value = re.sub(r"(?<!\d)\d{1,3}\s+\d{2}(?!\d)", " ", value)
    # When PDF text places the shelf price before the product, a detached
    # one-digit card number can remain before the title ("11,99 6 Forele 1 kg").
    # Only remove it when the original cell definitely began with a price.
    if starts_with_price:
        value = re.sub(r"^\s*\d\s+(?=[A-Za-zĀČĒĢĪĶĻŅŌŖŠŪŽ])", "", value)
    value = re.sub(r"-\s*\d{1,2}\s*%", " ", value)
    value = re.sub(r"\b\d{1,2}\.\d{1,2}\.\s*[-–—]\s*\d{1,2}\.\d{1,2}\.?\d{0,4}\b", " ", value)
    value = value.replace("*", " ")
    value = re.sub(r"\s+", " ", value).strip(" -;,:.")
    # Price tokens may be removed above, exposing a detached unit label at the end.
    value = re.sub(
        r"\s*[;(]?\s*/\s*(?:kg|l|100\s*g|100\s*ml|gab\.?|rull\w*)\s*[)]?$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    # Brochure layout can leak a neighbouring product's size label into the
    # start of the actual title (for example "izmērs 15+ kg Putekļsūcējs...").
    value = re.sub(
        r"^izm.{1,2}rs\s+\d{1,2}\+?\s*(?:kg|g|cm|mm)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    # A trailing "no" is sometimes positioned after the price pair in PDF text
    # order, although visually it belongs to the "no €X" price caption.
    if len(prices_from(text)) >= 2:
        value = re.sub(r"\s+no$", "", value, flags=re.IGNORECASE)
    # Removing a price fragment can expose a second, clipped two-digit price
    # suffix at the beginning of the remaining title (for example "49 Bļoda").
    # Keep one-digit quantity labels and explicit multi-pack labels intact.
    value = re.sub(
        r"^[•·€\s]*\d{2}\s+(?!(?:cm|mm|kg|g|ml|l|gab\.?|iepak\.?)\b)",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^[•·€\s]+", "", value)
    return value

def _pdf_cell_starts_with_price(text: str) -> bool:
    """Detect PDF cells where a price was extracted before the product title."""
    return bool(re.match(
        r"^\s*\d{1,3}(?:[,.]\s*\d{2}|\s+\d{2})(?:\s*€)?\s+\d\s+(?=[A-Za-zĀČĒĢĪĶĻŅŌŖŠŪŽ])",
        text,
    ))


def _pdf_price_quality(text: str) -> tuple[float | None, float | None, int | None, list[str]]:
    prices = prices_from(text)
    if len(prices) == 2 and prices[0] > prices[1]:
        unit_values = [
            float(match.group(1).replace(",", "."))
            for match in re.finditer(
                r"(\d{1,3}[,.]\d{2})\s*€\s*/\s*(?:kg|l|100\s*g|100\s*ml|gab\.?|rull\w*)",
                text,
                flags=re.IGNORECASE,
            )
        ]
        if DISCOUNT_RE.search(text) or (len(unit_values) >= 2 and unit_values[0] < unit_values[1]):
            prices.reverse()
    return price_quality(prices, text)


def _pdf_description_anchors(candidates, words, width: float) -> list[tuple[float, float]]:
    """Find product starts inside text blocks, splitting horizontally merged cards."""
    anchors: list[tuple[float, float]] = []
    split_gap = max(44, width * 0.08)
    for block in candidates:
        block_words = [
            word for word in words
            if float(block[0]) - 1 <= (float(word[0]) + float(word[2])) / 2 <= float(block[2]) + 1
            and float(block[1]) - 1 <= (float(word[1]) + float(word[3])) / 2 <= float(block[3]) + 1
        ]
        if not block_words:
            anchors.append((float(block[0]), float(block[1])))
            continue

        # PDF extraction sometimes merges horizontally adjacent products into
        # one text block. Cluster all its words by x-gap; wrapped text stays
        # within a card's column and does not create extra vertical grid rows.
        block_words.sort(key=lambda word: (float(word[0]), float(word[1])))
        segment: list[tuple] = []
        segments: list[list[tuple]] = []
        for word in block_words:
            if segment and float(word[0]) - max(float(item[2]) for item in segment) > split_gap:
                segments.append(segment)
                segment = []
            segment.append(word)
        if segment:
            segments.append(segment)

        for segment in segments:
            segment_text = " ".join(str(item[4]) for item in segment)
            if _is_pdf_product_title_seed(segment_text) or _looks_like_pdf_product_description(segment_text):
                anchors.append((
                    min(float(item[0]) for item in segment),
                    float(block[1]),
                ))

    unique: list[tuple[float, float]] = []
    for x, y in sorted(anchors, key=lambda point: (point[1], point[0])):
        if not any(
            abs(x - seen_x) < max(12, width * 0.025) and abs(y - seen_y) < 8
            for seen_x, seen_y in unique
        ):
            unique.append((x, y))
    return unique


def _pdf_description_anchors_by_line(
    candidates, words, width: float
) -> list[tuple[float, float]]:
    """Find distinct product starts on separate lines inside a merged PDF text block."""
    anchors: list[tuple[float, float]] = []
    split_gap = max(22, width * 0.045)

    for block in candidates:
        block_words = [
            word for word in words
            if float(block[0]) - 1 <= (float(word[0]) + float(word[2])) / 2 <= float(block[2]) + 1
            and float(block[1]) - 1 <= (float(word[1]) + float(word[3])) / 2 <= float(block[3]) + 1
        ]
        lines: dict[int, list[tuple]] = defaultdict(list)
        for word in block_words:
            lines[int(word[6])].append(word)

        for line_words in lines.values():
            line_words.sort(key=lambda word: (float(word[0]), int(word[7])))
            segment: list[tuple] = []
            segments: list[list[tuple]] = []
            bullet_separated = False
            for word in line_words:
                token = str(word[4]).strip().strip(";,:")
                # Bullet glyphs are reliable separators between product cards
                # when PDF extraction places several offers on one text line.
                if re.fullmatch(r"[•·▪●◦]+", token):
                    bullet_separated = True
                    if segment:
                        segments.append(segment)
                        segment = []
                    continue
                if segment and float(word[0]) - max(float(item[2]) for item in segment) > split_gap:
                    segments.append(segment)
                    segment = []
                segment.append(word)
            if segment:
                segments.append(segment)

            for segment in segments:
                segment_text = " ".join(str(item[4]).lstrip("•·▪●◦") for item in segment)
                if _is_pdf_product_title_seed(segment_text) or _looks_like_pdf_product_description(segment_text):
                    segment_left = min(float(item[0]) for item in segment)
                    segment_right = max(float(item[2]) for item in segment)
                    # In a bullet-separated inline row, use each card's centre
                    # so the midpoint between anchors falls on the separator.
                    anchor_x = (
                        (segment_left + segment_right) / 2
                        if bullet_separated else segment_left
                    )
                    anchors.append((
                        anchor_x,
                        min(float(item[1]) for item in segment),
                    ))

    unique: list[tuple[float, float]] = []
    for x, y in sorted(anchors, key=lambda point: (point[1], point[0])):
        if not any(
            abs(x - seen_x) < max(12, width * 0.025) and abs(y - seen_y) < 18
            for seen_x, seen_y in unique
        ):
            unique.append((x, y))
    return unique


def _pdf_words_in_rect(words, left: float, top: float, right: float, bottom: float) -> str:
    """Extract words within one card, preserving each PDF text block's reading order."""
    selected = [
        word for word in words
        if left <= (float(word[0]) + float(word[2])) / 2 < right
        and top <= (float(word[1]) + float(word[3])) / 2 < bottom
    ]
    blocks: dict[int, list[tuple]] = defaultdict(list)
    for word in selected:
        blocks[int(word[5])].append(word)

    parts: list[str] = []
    for block_number in sorted(blocks):
        block_words = sorted(
            blocks[block_number],
            key=lambda word: (int(word[6]), int(word[7])),
        )
        parts.extend(str(word[4]) for word in block_words)
    return " ".join(parts)


def _pdf_row_bounds(
    candidates, centre_y: float, page_height: float, row_tolerance: float
) -> tuple[float, float]:
    """Bound a row around product descriptions, not an arbitrary fraction of page height."""
    row_blocks = [
        block for block in candidates
        if abs(float(block[1]) - centre_y) <= row_tolerance
    ]
    if not row_blocks:
        return max(0, centre_y - 40), min(page_height, centre_y + 70)
    top_padding = max(32, page_height * 0.04)
    bottom_padding = max(68, page_height * 0.085)
    top = max(0, min(float(block[1]) for block in row_blocks) - top_padding)
    bottom = min(page_height, max(float(block[3]) for block in row_blocks) + bottom_padding)
    return top, bottom

def _pdf_page_cells_legacy(page) -> list[tuple[int, int, str]]:
    """Conservative grid extraction with word-level clipping for multi-column price blocks."""
    blocks = page.get_text("blocks")
    descriptions = [
        block for block in blocks
        if _looks_like_pdf_product_description(block[4])
    ]
    if not descriptions:
        return []

    width, height = float(page.rect.width), float(page.rect.height)
    words = page.get_text("words", sort=True)
    anchors = _pdf_description_anchors(descriptions, words, width)
    if not anchors:
        return []

    x_clusters = _cluster_values([point[0] for point in anchors], max(24, width * 0.07))
    y_clusters = _cluster_values([point[1] for point in anchors], max(55, height * 0.105))
    if not x_clusters or not y_clusters:
        return []

    x_centres = [sum(group) / len(group) for group in x_clusters]
    y_centres = [sum(group) / len(group) for group in y_clusters]
    columns = len(x_centres)
    results: list[tuple[int, int, str]] = []

    for row_index, centre_y in enumerate(y_centres):
        top, bottom = _pdf_row_bounds(
            descriptions, centre_y, height, max(55, height * 0.105)
        )

        for column_index in range(columns):
            left = column_index * width / columns
            right = (column_index + 1) * width / columns
            cell_anchors = [
                point for point in anchors
                if left <= point[0] < right
                and abs(point[1] - centre_y) <= max(55, height * 0.105)
            ]
            if not cell_anchors:
                continue
            cell_text = _pdf_words_in_rect(words, left, top, right, bottom)
            if cell_text.strip():
                results.append((row_index + 1, column_index + 1, cell_text))
    return results


def _pdf_page_cells_split(page) -> list[tuple[int, int, str]]:
    """Fallback grid extraction for brochures with separate title/package text blocks."""
    blocks = page.get_text("blocks")
    candidates = [
        block for block in blocks
        if _looks_like_pdf_product_description(block[4])
        or _is_pdf_product_title_seed(block[4])
    ]
    if not candidates:
        return []

    width, height = float(page.rect.width), float(page.rect.height)
    words = page.get_text("words", sort=True)
    anchors = _pdf_description_anchors(candidates, words, width)
    if not anchors:
        return []

    x_clusters = _cluster_values([point[0] for point in anchors], max(24, width * 0.07))
    y_clusters = _cluster_values([point[1] for point in anchors], max(55, height * 0.105))
    if not x_clusters or not y_clusters:
        return []

    x_centres = [sum(group) / len(group) for group in x_clusters]
    y_centres = [sum(group) / len(group) for group in y_clusters]
    columns = len(x_centres)
    results: list[tuple[int, int, str]] = []

    for row_index, centre_y in enumerate(y_centres):
        top, bottom = _pdf_row_bounds(
            candidates, centre_y, height, max(55, height * 0.105)
        )

        for column_index in range(columns):
            left = column_index * width / columns
            right = (column_index + 1) * width / columns
            cell_anchors = [
                point for point in anchors
                if left <= point[0] < right
                and abs(point[1] - centre_y) <= max(55, height * 0.105)
            ]
            if not cell_anchors:
                continue
            cell_text = _pdf_words_in_rect(words, left, top, right, bottom)
            if not cell_text.strip() or not prices_from(cell_text):
                continue
            results.append((row_index + 1, column_index + 1, cell_text))
    return results


def _pdf_page_cells_geometry(page) -> list[tuple[int, int, str]]:
    """Split brochure grids by clustered columns and the next title in each column."""
    blocks = page.get_text("blocks")
    candidates = [
        block for block in blocks
        if _looks_like_pdf_product_description(block[4])
        or _is_pdf_product_title_seed(block[4])
    ]
    if not candidates:
        return []

    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    words = page.get_text("words", sort=True)
    anchors = _pdf_description_anchors_by_line(candidates, words, width)
    if not anchors:
        return []

    # A product's title can be indented differently from its neighbours. Cluster
    # starts into brochure columns first, instead of using a narrow x-distance
    # from one title as the definition of its entire column.
    x_clusters = _cluster_values(
        [x for x, _ in anchors],
        max(28, width * 0.07),
    )
    if not x_clusters:
        return []
    x_centres = [sum(group) / len(group) for group in x_clusters]
    anchor_columns = [
        min(range(len(x_centres)), key=lambda index: abs(x - x_centres[index]))
        for x, _ in anchors
    ]

    # Cluster y positions so slightly staggered titles in a row still share
    # a row number for diagnostics, while vertical boundaries remain column-local.
    y_clusters = _cluster_values(
        [y for _, y in anchors],
        max(28, height * 0.045),
    )
    y_centres = [sum(group) / len(group) for group in y_clusters]
    has_bullet_separators = any(
        re.fullmatch(r"[•·▪●◦]+", str(word[4]).strip())
        for word in words
    )
    anchor_rows = [
        min(range(len(y_centres)), key=lambda index: abs(y - y_centres[index]))
        for _, y in anchors
    ]

    results: list[tuple[int, int, str]] = []
    for anchor_index, (x, y) in enumerate(anchors):
        column_index = anchor_columns[anchor_index]
        same_column_y = sorted(
            other_y
            for other_index, (_, other_y) in enumerate(anchors)
            if anchor_columns[other_index] == column_index
            and abs(other_y - y) > 3
        )
        next_y = min((value for value in same_column_y if value > y), default=None)
        top = max(float(rect.y0), y - 3)
        bottom = min(float(rect.y1), next_y - 3 if next_y is not None else float(rect.y1))
        if bottom <= top:
            continue

        if has_bullet_separators:
            # When the PDF explicitly separates adjacent product cards with
            # bullets, line anchors are card centres. Bisect only neighbours
            # on the same visual row so irregular card widths remain intact.
            row_xs = sorted(
                anchors[index][0]
                for index, row_number in enumerate(anchor_rows)
                if row_number == anchor_rows[anchor_index]
            )
            previous_x = max((value for value in row_xs if value < x), default=None)
            next_x = min((value for value in row_xs if value > x), default=None)
            left = max(float(rect.x0), (previous_x + x) / 2 if previous_x is not None else float(rect.x0))
            right = min(float(rect.x1), (x + next_x) / 2 if next_x is not None else float(rect.x1))
        else:
            # Without explicit separators, MAXIMA's catalogue grids are
            # regular multi-column layouts, so equal-width columns avoid
            # clipping a left-aligned title at a midpoint between title starts.
            left = float(rect.x0) + column_index * width / len(x_centres)
            right = float(rect.x0) + (column_index + 1) * width / len(x_centres)

        cell_text = _pdf_words_in_rect(words, left, top, right, bottom)
        if cell_text.strip() and prices_from(cell_text):
            row_number = anchor_rows[anchor_index] + 1
            results.append((row_number, column_index + 1, cell_text))

    unique: dict[tuple[int, int, str], tuple[int, int, str]] = {}
    for item in results:
        unique[(item[0], item[1], " ".join(item[2].split()))] = item
    return list(unique.values())

def _pdf_page_cells(page) -> list[tuple[int, int, str]]:
    """Use geometric splitting only to resolve a real multi-price merge."""
    legacy = _pdf_page_cells_legacy(page)
    recovered = _pdf_page_cells_split(page)
    geometry = _pdf_page_cells_geometry(page)
    baseline = max((legacy, recovered), key=len)

    def ambiguous_count(cells: list[tuple[int, int, str]]) -> int:
        return sum(len(prices_from(text)) > 2 for _, _, text in cells)

    baseline_ambiguous = ambiguous_count(baseline)
    geometry_ambiguous = ambiguous_count(geometry)

    # Do not let a geometric guess replace a working product/title extraction
    # just because it found more anchors. Adopt it only when it splits at least
    # one merged multi-price cell into more cards and reduces that ambiguity.
    if (
        baseline_ambiguous > 0
        and len(geometry) > len(baseline)
        and geometry_ambiguous < baseline_ambiguous
    ):
        return geometry
    return baseline


def _catalogue_for_pdf_page(page_text: str, catalogues: list[dict], fallback: dict) -> dict:
    folded = page_text.casefold()
    book_mentions = len(re.findall(r"\bgrāmata\b", folded))
    if "grāmatu festivāls" in folded or book_mentions >= 2:
        match = next((item for item in catalogues if "grāmatu festivāls" in item["name"].casefold()), None)
        if match:
            return match
    if folded.count("well done premium") >= 1:
        match = next((item for item in catalogues if "well done premium" in item["name"].casefold()), None)
        if match:
            return match
    return fallback


def _split_pdf_cell_by_bullets(cell_text: str) -> list[str]:
    """Split a merged PDF cell only when every bullet-separated offer is complete."""
    if not re.search(r"[•·▪●◦]", cell_text):
        return [cell_text]

    chunks = [
        " ".join(chunk.split())
        for chunk in re.split(r"[•·▪●◦]+", cell_text)
        if chunk.strip()
    ]
    priced_chunks = [chunk for chunk in chunks if prices_from(chunk)]
    if len(priced_chunks) < 2:
        return [cell_text]

    # Never discard a price or guess which title owns it. Every part containing
    # shelf prices must contain exactly one plausible product and one price pair.
    for chunk in priced_chunks:
        if len(prices_from(chunk)) != 2:
            return [cell_text]
        name = _clean_pdf_product_name(chunk)
        if not name or not _looks_like_pdf_product_description(name):
            return [cell_text]

    return priced_chunks


def parse_catalogue_pdf(
    pdf_bytes: bytes,
    catalogue: dict,
    all_catalogues: list[dict],
    today: date | None = None,
) -> tuple[list[dict], list[dict], dict[str, int]]:
    """Extract product/price cells from an active Maxima brochure PDF."""
    if fitz is None:
        raise RuntimeError("PyMuPDF is required to collect Maxima brochure PDFs")

    today = today or date.today()
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    offers_by_id: dict[str, dict] = {}
    reviews_by_id: dict[str, dict] = {}
    page_counts: dict[str, int] = defaultdict(int)
    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")

    for page_index, page in enumerate(document):
        page_text = page.get_text("text", sort=True)
        folded_page_text = page_text.casefold()

        # The weekly combined PDF also contains the standalone Black Friday
        # campaign at its end. That campaign has its own PDF URL and is parsed
        # separately; skip the duplicate page here.
        if catalogue["id"] == "maxima-weekly" and re.search(
            r"black\s*friday|cenu\s+festivāls", folded_page_text
        ):
            continue

        page_catalogue = _catalogue_for_pdf_page(page_text, all_catalogues, catalogue)
        cells = _pdf_page_cells(page)
        if not cells:
            continue
        page_counts[page_catalogue["id"]] += 1

        page_dates = offer_dates(page_text, today=today)
        validity_warning = "piedāvājumu periods var būt atšķirīgs" in page_text.casefold()
        if page_dates != (None, None):
            valid_from, valid_until = page_dates
            date_status = "verified"
        elif validity_warning:
            valid_from, valid_until = None, None
            date_status = "unknown"
        else:
            valid_from, valid_until = page_catalogue["validFrom"], page_catalogue["validUntil"]
            date_status = "verified" if valid_from and valid_until else "unknown"

        for row_number, column_number, cell_text in cells:
            product_parts = _split_pdf_cell_by_bullets(cell_text)
            for part_number, product_text in enumerate(product_parts, start=1):
                split_suffix = f":{part_number}" if len(product_parts) > 1 else ""
                name = _clean_pdf_product_name(product_text)
                if not name or not _looks_like_pdf_product_description(name):
                    continue
                price, regular, discount, issues = _pdf_price_quality(product_text)
                if price is None:
                    continue

                # Some brochure pages emit the shelf-price text before the product
                # title. A single price in that order may belong to a neighbouring
                # card; keep it for review instead of publishing a guessed match.
                if _pdf_cell_starts_with_price(product_text):
                    issues.append("price_before_product_name")

                # If a cell contains a unit-price label but only one number survived
                # geometric extraction, that number may be €/kg or €/l rather than
                # the product's shelf price. Keep it out of the verified feed.
                has_unit_price_label = bool(re.search(
                    r"(?:€\s*/\s*(?:kg|l|100\s*g|100\s*ml|gab\.?|rull\w*)|"
                    r"/\s*(?:kg|l|100\s*g|100\s*ml))",
                    product_text,
                    flags=re.IGNORECASE,
                ))
                shelf_prices = prices_from(product_text)
                if (
                    has_unit_price_label
                    and len(shelf_prices) <= 1
                    and not (len(shelf_prices) == 1 and _has_explicit_shelf_price(product_text, price))
                ):
                    issues.append("unit_price_only_or_misaligned")

                # A plausible price pair is not enough when neighbouring cards or
                # store-event copy leaked into the product title.
                name_folded = name.casefold()
                name_issues: list[str] = []
                if (
                    page_catalogue["id"] == "maxima-well-done-premium"
                    and page_index == 1
                    and regular is None
                    and price >= 15
                ):
                    name_issues.append("unit_price_suspected")
                if (
                    ";" in name
                    or len(re.findall(r"\b\d{1,2}\.", name)) >= 2
                    or (
                        re.match(r"^\d{1,2}\s+(?!cm\b|mm\b|kg\b|g\b|ml\b|l\b)", name_folded)
                        and not re.match(r"^\d{1,2}\s+iepak", name_folded)
                    )
                    or name_folded.startswith("izm")
                    or re.search(r"\b(?:g|kg|ml|l)\s+\d{1,2}$", name_folded)
                    or name_folded.endswith(" no")
                    or any(term in name_folded for term in (
                        "roj", "darba dien", "atkl", "dien",
                    ))
                    or re.search(r"\b\d{1,2}:\d{2}\b", name)
                    or re.search(r"\(\s*/\s*kg", name_folded)
                ):
                    name_issues.append("product_name_ambiguous")
                issues = list(dict.fromkeys([*issues, *name_issues]))

                identity = product_identity(name)
                image = None
                source_product_id = f"pdf:{page_index + 1}:{row_number}:{column_number}{split_suffix}"
                offer_id = stable_offer_id(
                    page_catalogue["id"],
                    identity["canonicalProductId"],
                    image,
                    page_catalogue.get("pdfSources", [page_catalogue["source"]])[0],
                    page_catalogue.get("storeFormats", []),
                    False,
                    source_product_id,
                )
                quality = {
                    "priceStatus": "review" if issues else "verified",
                    "dateStatus": date_status,
                    "storeScopeStatus": "verified" if (
                        page_catalogue.get("storeFormats") or page_catalogue.get("storeDescription")
                    ) else "unknown",
                    "issues": issues,
                }
                paldies_marker = bool(re.search(
                    r"(?:cena\s+ar\s+paldies|paldies\s+karte|ar\s+paldies\s+kart)",
                    product_text,
                    flags=re.IGNORECASE,
                ))
                requires_paldies = (
                    paldies_marker
                    or page_catalogue["id"] == "maxima-well-done-premium"
                    or (
                        page_catalogue["id"] == "maxima-catalogue-black-friday-cenu-festivals"
                        and page_index == 0
                    )
                )
                offer = {
                    "offerId": offer_id,
                    "sourceId": page_catalogue["id"],
                    "sourceProductId": source_product_id,
                    "productId": product_id(name),
                    "canonicalProductId": identity["canonicalProductId"],
                    "identityKey": identity["identityKey"],
                    "packageSize": identity["packageSize"],
                    "name": name,
                    "brand": None,
                    "image": image,
                    "price": price,
                    "regularPrice": regular,
                    "paldiesPrice": price if requires_paldies else None,
                    "priceWithoutPaldies": regular if requires_paldies else None,
                    "unit": None,
                    "discountPercent": discount,
                    "requiresPaldies": requires_paldies,
                    "paldis": requires_paldies,
                    "validFrom": valid_from,
                    "validUntil": valid_until,
                    "city": page_catalogue.get("city"),
                    "store": page_catalogue.get("storeDescription") or "Maxima",
                    "storeFormats": page_catalogue.get("storeFormats", []),
                    "storeScopeSource": page_catalogue.get("storeScopeSource"),
                    "campaign": page_catalogue["name"],
                    "campaignSource": page_catalogue["detailSource"],
                    "source": page_catalogue.get("pdfSources", [page_catalogue["source"]])[0],
                    "pdfPage": page_index + 1,
                    "checkedAt": observed_at,
                    "quality": quality,
                }
                if issues:
                    offer["reviewEvidence"] = {
                        "rawCellText": " ".join(product_text.split())[:600],
                        "parsedName": name,
                        "parsedPrices": prices_from(product_text),
                    }
                existing = offers_by_id.get(offer_id) or reviews_by_id.get(offer_id)
                if existing and existing["quality"]["priceStatus"] == "verified" and quality["priceStatus"] == "review":
                    continue
                if quality["priceStatus"] == "review":
                    reviews_by_id[offer_id] = offer
                else:
                    offers_by_id[offer_id] = offer
                    reviews_by_id.pop(offer_id, None)

    document.close()
    return (
        sorted(offers_by_id.values(), key=lambda item: (item["name"].casefold(), item["price"])),
        sorted(reviews_by_id.values(), key=lambda item: (item["name"].casefold(), item["price"])),
        dict(page_counts),
    )


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def rebuild_history(history_data: dict, offers: list[dict], observed_at: datetime) -> dict:
    """Migrate old name-keyed history to canonical products and keep source-level observations."""
    old_products = history_data.get("products", {})
    products: dict[str, dict] = {}

    # The first PDF-import run parsed three differently named URLs containing
    # identical combined weekly brochures. Its fallback attribution incorrectly
    # copied non-WDP pages into the WDP and book-festival histories. Remove only
    # those PDF observations; the corrected run below re-adds the real sections.
    misattributed_pdf_sources = {
        (
            "maxima-book-festival",
            "https://www.maxima.lv/uploads/releases/pdf/MAXIMA_KC48_2026_ViXL.pdf",
        ),
        (
            "maxima-well-done-premium",
            "https://www.maxima.lv/uploads/releases/pdf/MAXIMA_KC48_2026_6zq0.pdf",
        ),
    }

    def get_entry(canonical_id: str, name: str, package_size: str = "") -> dict:
        return products.setdefault(canonical_id, {
            "productId": canonical_id,
            "canonicalProductId": canonical_id,
            "name": name,
            "packageSize": package_size,
            "observations": [],
            "points": [],
        })

    for old_key, old_product in old_products.items():
        name = old_product.get("name", "")
        identity = product_identity(name)
        canonical_id = old_product.get("canonicalProductId") or identity["canonicalProductId"]
        entry = get_entry(canonical_id, name, identity["packageSize"])
        if len(name) > len(entry["name"]):
            entry["name"] = name

        observations = old_product.get("observations")
        if isinstance(observations, list) and observations:
            for observation in observations:
                item = dict(observation)
                item.setdefault("offerId", f"legacy:{old_key}")
                if (item.get("sourceId"), item.get("source")) in misattributed_pdf_sources:
                    continue
                if str(item.get("sourceProductId", "")).startswith("pdf:"):
                    continue
                if item.get("date") and isinstance(item.get("price"), (int, float)):
                    entry["observations"].append(item)
        else:
            # One-time migration of the existing daily history points.
            for point in old_product.get("points", []):
                if not point.get("date") or not isinstance(point.get("price"), (int, float)):
                    continue
                entry["observations"].append({
                    **point,
                    "offerId": f"legacy:{old_key}",
                    "sourceId": "legacy-history",
                    "campaign": "Iepriekšējā cenu vēsture",
                })

    observation_date = observed_at.date().isoformat()
    for offer in offers:
        if offer["quality"]["priceStatus"] != "verified":
            continue
        canonical_id = offer["canonicalProductId"]
        entry = get_entry(canonical_id, offer["name"], offer.get("packageSize", ""))
        if len(offer["name"]) > len(entry["name"]):
            entry["name"] = offer["name"]

        observation = {
            "date": observation_date,
            "offerId": offer["offerId"],
            "sourceProductId": offer.get("sourceProductId"),
            "price": offer["price"],
            "regularPrice": offer.get("regularPrice"),
            "paldiesPrice": offer.get("paldiesPrice"),
            "priceWithoutPaldies": offer.get("priceWithoutPaldies"),
            "requiresPaldies": offer.get("requiresPaldies", False),
            "discountPercent": offer.get("discountPercent"),
            "validFrom": offer.get("validFrom"),
            "validUntil": offer.get("validUntil"),
            "storeFormats": offer.get("storeFormats", []),
            "campaign": offer.get("campaign"),
            "sourceId": offer.get("sourceId"),
            "source": offer.get("source"),
            "checkedAt": observed_at.isoformat(timespec="seconds"),
        }

        # One current observation per offer identity per day; re-runs refresh today's value.
        entry["observations"] = [
            item for item in entry["observations"]
            if not (item.get("date") == observation_date and item.get("offerId") == offer["offerId"])
        ]
        entry["observations"].append(observation)

    for entry in products.values():
        # Merge repeated legacy points and current source observations without losing variants.
        unique: dict[tuple[str, str], dict] = {}
        for observation in entry["observations"]:
            key = (str(observation.get("date", "")), str(observation.get("offerId", "")))
            unique[key] = observation
        observations = sorted(
            unique.values(),
            key=lambda item: (item.get("date", ""), item.get("price", float("inf"))),
        )
        entry["observations"] = observations

        by_date: dict[str, list[dict]] = defaultdict(list)
        for observation in observations:
            by_date[observation["date"]].append(observation)

        points = []
        for point_date, day_observations in sorted(by_date.items()):
            priced = [
                item for item in day_observations
                if isinstance(item.get("price"), (int, float))
            ]
            if not priced:
                continue
            best = min(priced, key=lambda item: item["price"])
            points.append({
                "date": point_date,
                "price": best["price"],
                "regularPrice": best.get("regularPrice"),
                "paldiesPrice": best.get("paldiesPrice"),
                "priceWithoutPaldies": best.get("priceWithoutPaldies"),
                "requiresPaldies": best.get("requiresPaldies", False),
                "discountPercent": best.get("discountPercent"),
                "validFrom": best.get("validFrom"),
                "validUntil": best.get("validUntil"),
                "storeFormats": sorted({
                    fmt for item in day_observations for fmt in item.get("storeFormats", [])
                }),
                "campaigns": sorted({
                    item.get("campaign", "") for item in day_observations if item.get("campaign")
                }),
                "offerCount": len(priced),
                "priceVariants": sorted({item["price"] for item in priced}),
                "checkedAt": best.get("checkedAt"),
            })
        entry["points"] = points

    # Drop empty records left behind by a history-source cleanup.
    products = {
        canonical_id: entry for canonical_id, entry in products.items()
        if entry["observations"]
    }

    return {
        "updatedAt": observed_at.isoformat(timespec="seconds"),
        "sources": sorted({
            offer["campaignSource"] for offer in offers if offer.get("campaignSource")
        }),
        "products": products,
    }


def partition_review_offers(offers: list[dict]) -> tuple[list[dict], list[dict]]:
    """Fail closed: only explicitly verified offers may enter the public feed/history."""
    verified: list[dict] = []
    review: list[dict] = []
    for offer in offers:
        if offer.get("quality", {}).get("priceStatus") == "verified":
            verified.append(offer)
        else:
            review.append(offer)
    return verified, review


def build_review_summary(review_offers: list[dict]) -> dict:
    """Summarize review causes and provide a few compact examples for diagnostics."""
    by_issue: dict[str, int] = defaultdict(int)
    by_catalogue: dict[str, int] = defaultdict(int)
    examples: dict[str, list[dict]] = defaultdict(list)

    for offer in review_offers:
        by_catalogue[offer.get("campaign") or offer.get("sourceId") or "unknown"] += 1
        issues = sorted(set(offer.get("quality", {}).get("issues") or ["unspecified_review"]))
        for issue in issues:
            by_issue[issue] += 1
            if len(examples[issue]) < 3:
                evidence = offer.get("reviewEvidence") or {}
                examples[issue].append({
                    "campaign": offer.get("campaign") or offer.get("sourceId"),
                    "name": offer.get("name"),
                    "price": offer.get("price"),
                    "regularPrice": offer.get("regularPrice"),
                    "pdfPage": offer.get("pdfPage"),
                    "rawCellText": evidence.get("rawCellText"),
                })

    return {
        "total": len(review_offers),
        "byIssue": dict(sorted(by_issue.items())),
        "byCatalogue": dict(sorted(by_catalogue.items())),
        "examplesByIssue": dict(examples),
    }


def reconcile_paldies_prices(
    pdf_offers: list[dict],
    paldies_offers: list[dict],
) -> int:
    """Reconcile only unambiguous, compatible official PALDIES offers."""
    by_product: dict[str, list[dict]] = defaultdict(list)
    for source in paldies_offers:
        if source.get("quality", {}).get("priceStatus") != "verified":
            continue
        if not source.get("requiresPaldies"):
            continue
        canonical_id = source.get("canonicalProductId")
        if canonical_id:
            by_product[canonical_id].append(source)

    reconciled = 0
    for offer in pdf_offers:
        if not offer.get("requiresPaldies"):
            continue
        candidates = []
        for source in by_product.get(offer.get("canonicalProductId"), []):
            # Never transfer prices between different validity periods or
            # store formats, including unknown store scopes.
            if not offer.get("validFrom") or not offer.get("validUntil"):
                continue
            if (source.get("validFrom"), source.get("validUntil")) != (
                offer.get("validFrom"), offer.get("validUntil")
            ):
                continue
            formats = offer.get("storeFormats") or []
            source_formats = source.get("storeFormats") or []
            if not formats or set(formats) != set(source_formats):
                continue
            candidates.append(source)

        # Multiple identical cards are harmless; conflicting prices are not.
        variants = {
            (s.get("price"), s.get("regularPrice"), s.get("discountPercent"))
            for s in candidates
        }
        if len(variants) != 1:
            if len(variants) > 1:
                quality = offer.setdefault("quality", {})
                quality["priceStatus"] = "review"
                issues = quality.setdefault("issues", [])
                if "ambiguous_paldies_prices" not in issues:
                    issues.append("ambiguous_paldies_prices")
                offer.setdefault("reviewEvidence", {})["paldiesCandidates"] = [
                    {
                        "price": candidate.get("price"),
                        "regularPrice": candidate.get("regularPrice"),
                        "discountPercent": candidate.get("discountPercent"),
                        "validFrom": candidate.get("validFrom"),
                        "validUntil": candidate.get("validUntil"),
                        "storeFormats": candidate.get("storeFormats") or [],
                    }
                    for candidate in candidates[:10]
                ]
            continue

        source = candidates[0]
        price = source.get("price")
        regular = source.get("regularPrice")
        discount = source.get("discountPercent")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        if regular is not None:
            if not isinstance(regular, (int, float)) or regular <= price:
                continue
            computed = round((1 - price / regular) * 100)
            if discount is not None and abs(discount - computed) > 1:
                quality = offer.setdefault("quality", {})
                issues = quality.setdefault("issues", [])
                if "paldies_discount_percent_mismatch" not in issues:
                    issues.append("paldies_discount_percent_mismatch")
                quality["priceStatus"] = "review"
                offer.setdefault("reviewEvidence", {})["paldiesCandidate"] = {
                    "price": price,
                    "regularPrice": regular,
                    "discountPercent": discount,
                    "computedDiscountPercent": computed,
                }
                continue

        offer["price"] = price
        offer["regularPrice"] = regular
        offer["paldiesPrice"] = price
        offer["priceWithoutPaldies"] = source.get("priceWithoutPaldies", regular)
        offer["discountPercent"] = discount
        reconciled += 1
    return reconciled


def main():
    session = requests.Session()
    # The weekly offers page exposes the active campaign cards and their own brochure links.
    campaign_index_response = session.get(CAMPAIGNS[0]["url"], headers=HEADERS, timeout=30)
    campaign_index_response.raise_for_status()
    brochure_index = BeautifulSoup(campaign_index_response.text, "html.parser")

    all_offers: list[dict] = []
    all_reviews: list[dict] = []
    campaign_metadata: list[dict] = []

    for campaign in CAMPAIGNS:
        offers, reviews, metadata = parse_campaign(session, campaign, brochure_index)
        if not offers and not reviews:
            print(
                f"Warning: no priced product cards detected for {campaign['name']}",
                file=sys.stderr,
            )
            continue
        all_offers.extend(offers)
        all_reviews.extend(reviews)
        campaign_metadata.append(metadata)

    paldies_offers, paldies_reviews, paldies_metadata = collect_paldies_catalog(session)
    all_offers.extend(paldies_offers)
    all_reviews.extend(paldies_reviews)
    campaign_metadata.append(paldies_metadata)

    if not paldies_metadata["complete"]:
        raise RuntimeError("PALDIES catalogue did not pass completeness verification")
    # Discover every currently active brochure from Maxima's catalogue index.
    today = date.today()
    active_catalogues = discover_active_catalogues(session, brochure_index, today)
    catalogue_offers: list[dict] = []
    catalogue_reviews: list[dict] = []
    catalogue_stats: dict[str, dict] = {
        item["id"]: {"verifiedOffers": 0, "reviewOffers": 0, "pdfPages": 0}
        for item in active_catalogues
    }

    # Some brochure detail pages publish the exact same combined PDF under
    # different URLs. Download each URL, fingerprint the bytes, and parse each
    # distinct PDF only once. The weekly campaign is the fallback for combined
    # brochures; explicit WDP/book-festival pages are assigned by their headings.
    downloaded_pdfs: dict[str, dict] = {}
    failed_pdf_catalogues: set[str] = set()
    for catalogue in active_catalogues:
        for pdf_url in catalogue.get("pdfSources", []):
            try:
                pdf_response = session.get(pdf_url, headers=HEADERS, timeout=120)
                pdf_response.raise_for_status()
                pdf_bytes = pdf_response.content
                digest = hashlib.sha256(pdf_bytes).hexdigest()
                entry = downloaded_pdfs.setdefault(digest, {
                    "content": pdf_bytes,
                    "urls": [],
                    "catalogues": [],
                })
                if pdf_url not in entry["urls"]:
                    entry["urls"].append(pdf_url)
                if catalogue["id"] not in entry["catalogues"]:
                    entry["catalogues"].append(catalogue["id"])
            except requests.RequestException as exc:
                failed_pdf_catalogues.add(catalogue["id"])
                print(f"Warning: could not download brochure PDF {pdf_url}: {exc}", file=sys.stderr)

    for digest, pdf_entry in downloaded_pdfs.items():
        related_catalogues = [
            catalogue for catalogue in active_catalogues
            if catalogue["id"] in pdf_entry["catalogues"]
        ]
        if not related_catalogues:
            continue
        primary_catalogue = next(
            (item for item in related_catalogues if item["id"] == "maxima-weekly"),
            related_catalogues[0],
        )
        try:
            pdf_offers, pdf_reviews, page_counts = parse_catalogue_pdf(
                pdf_entry["content"],
                primary_catalogue,
                related_catalogues,
                today=today,
            )
        except Exception as exc:
            failed_pdf_catalogues.update(item["id"] for item in related_catalogues)
            print(
                f"Warning: could not parse brochure PDF {pdf_entry['urls'][0]}: {exc}",
                file=sys.stderr,
            )
            continue

        catalogue_offers.extend(pdf_offers)
        catalogue_reviews.extend(pdf_reviews)
        for catalogue_id, page_count in page_counts.items():
            catalogue_stats.setdefault(
                catalogue_id, {"verifiedOffers": 0, "reviewOffers": 0, "pdfPages": 0}
            )["pdfPages"] += page_count


    reconciled_paldies = reconcile_paldies_prices(catalogue_offers, paldies_offers)
    if reconciled_paldies:
        print(f"Reconciled {reconciled_paldies} brochure prices against the official PALDIES catalogue")

    # Reconciliation can downgrade an initially parsed PDF offer to review.
    # Move those offers out of the verified feed before history is rebuilt.
    catalogue_offers, reconciliation_reviews = partition_review_offers(catalogue_offers)
    if reconciliation_reviews:
        catalogue_reviews.extend(reconciliation_reviews)
        print(
            f"Moved {len(reconciliation_reviews)} PALDIES-reconciliation offers "
            "from verified PDF data to manual review"
        )

    # PDF variants that duplicate the same product/price already collected from its
    # HTML campaign page are redundant. Keep new catalogue entries and price values.
    existing_offer_keys = {
        (
            item.get("sourceId"),
            item.get("canonicalProductId"),
            item.get("price"),
            item.get("regularPrice"),
            item.get("validFrom"),
            item.get("validUntil"),
        )
        for item in all_offers
    }
    for offer in catalogue_offers:
        key = (
            offer.get("sourceId"),
            offer.get("canonicalProductId"),
            offer.get("price"),
            offer.get("regularPrice"),
            offer.get("validFrom"),
            offer.get("validUntil"),
        )
        if key in existing_offer_keys:
            continue
        all_offers.append(offer)
        existing_offer_keys.add(key)
        catalogue_stats.setdefault(
            offer["sourceId"], {"verifiedOffers": 0, "reviewOffers": 0, "pdfPages": 0}
        )["verifiedOffers"] += 1

    existing_review_keys = {
        (
            item.get("sourceId"),
            item.get("canonicalProductId"),
            item.get("price"),
            item.get("regularPrice"),
            item.get("validFrom"),
            item.get("validUntil"),
        )
        for item in all_reviews
    }
    for offer in catalogue_reviews:
        key = (
            offer.get("sourceId"),
            offer.get("canonicalProductId"),
            offer.get("price"),
            offer.get("regularPrice"),
            offer.get("validFrom"),
            offer.get("validUntil"),
        )
        if key in existing_review_keys:
            continue
        all_reviews.append(offer)
        existing_review_keys.add(key)
        catalogue_stats.setdefault(
            offer["sourceId"], {"verifiedOffers": 0, "reviewOffers": 0, "pdfPages": 0}
        )["reviewOffers"] += 1

    for catalogue in active_catalogues:
        catalogue.update(catalogue_stats.get(
            catalogue["id"], {"verifiedOffers": 0, "reviewOffers": 0, "pdfPages": 0}
        ))
        catalogue["complete"] = (
            bool(catalogue.get("pdfSources"))
            and catalogue["pdfPages"] > 0
            and catalogue["id"] not in failed_pdf_catalogues
        )
        if not catalogue["complete"]:
            print(
                f"Warning: active catalogue was not fully parsed: {catalogue['name']} "
                f"(PDF pages parsed: {catalogue['pdfPages']})",
                file=sys.stderr,
            )

    if not all_offers:
        raise RuntimeError("No verified product offers detected; refusing to overwrite the feed.")

    # Remove accidental duplicate variants from overlapping DOM nodes across page sections.
    unique_offers = {offer["offerId"]: offer for offer in all_offers}
    offers = sorted(
        unique_offers.values(),
        key=lambda item: (item["name"].casefold(), item["price"], item["campaign"]),
    )
    unique_reviews = {offer["offerId"]: offer for offer in all_reviews}
    review_offers = sorted(
        unique_reviews.values(),
        key=lambda item: (item["name"].casefold(), item["campaign"]),
    )

    grouped_products = build_product_groups(offers)
    observed_at = datetime.now().astimezone()
    history_data = load_json(HISTORY_OUT, {"updatedAt": None, "products": {}})
    history_payload = rebuild_history(history_data, offers, observed_at)

    HISTORY_OUT.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_OUT.write_text(
        json.dumps(history_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    payload = {
        "source": BROCHURES_URL,
        "sources": list(dict.fromkeys(
            [metadata["source"] for metadata in campaign_metadata]
            + [item["source"] for item in active_catalogues]
            + [pdf for item in active_catalogues for pdf in item.get("pdfSources", [])]
        )),
        "updatedAt": observed_at.isoformat(timespec="seconds"),
        "campaigns": campaign_metadata,
        "catalogues": active_catalogues,
        "offers": offers,
        "products": grouped_products,
        "reviewOffers": review_offers,
        "reviewSummary": build_review_summary(review_offers),
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(grouped_products),
            "reviewOffers": len(review_offers),
            "paldiesExpected": paldies_metadata["expectedOffers"],
            "paldiesCollected": paldies_metadata["collectedProducts"],
            "paldiesPages": paldies_metadata["pages"],
            "activeCatalogues": len(active_catalogues),
            "catalogueOffers": sum(item["verifiedOffers"] for item in active_catalogues),
            "catalogueReviewOffers": sum(item["reviewOffers"] for item in active_catalogues),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    catalogue_offer_count = sum(item["verifiedOffers"] for item in active_catalogues)
    catalogue_review_count = sum(item["reviewOffers"] for item in active_catalogues)
    print(
        f"Saved {len(offers)} offer variants grouped into {len(grouped_products)} products; "
        f"{len(review_offers)} variants need review; "
        f"{len(active_catalogues)} active brochures, {catalogue_offer_count} verified PDF offers "
        f"and {catalogue_review_count} PDF items need review; "
        f"history contains {len(history_payload['products'])} canonical products"
    )
    review_summary = payload["reviewSummary"]
    print("REVIEW_SUMMARY", json.dumps({
        "total": review_summary["total"],
        "byIssue": review_summary["byIssue"],
        "byCatalogue": review_summary["byCatalogue"],
    }, ensure_ascii=False))
    for issue, examples in review_summary["examplesByIssue"].items():
        print("REVIEW_EXAMPLES", json.dumps({
            "issue": issue,
            "examples": examples,
        }, ensure_ascii=False))

    for catalogue in active_catalogues:
        print("ACTIVE_CATALOGUE", json.dumps({
            key: catalogue.get(key) for key in (
                "id", "name", "validFrom", "validUntil", "storeFormats", "city",
                "verifiedOffers", "reviewOffers", "pdfPages", "complete"
            )
        }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Scraper failed: {exc}", file=sys.stderr)
        raise
