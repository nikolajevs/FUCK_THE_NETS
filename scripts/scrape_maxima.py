#!/usr/bin/env python3
"""Collect Maxima Latvia offers with explicit price, loyalty and campaign checks."""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

URL = "https://www.maxima.lv/piedavajumi/"
CAMPAIGN_NAME = "Iknedēļas piedāvājums"
OUT = Path("data/offers.json")
HISTORY_OUT = Path("data/price-history.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)"
}
PRICE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,3})(?:[,.]\s*|\s+)(\d{2})(?!\d)")
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
    """Read shelf prices, excluding parenthesized €/kg or €/l unit prices."""
    # Remove unit prices even when the source omits their surrounding parentheses.
    cleaned = re.sub(r"(?<!\d)\d{1,3}[,.]\s*\d{2}\s*€\s*/\s*(?:kg|l|100\s*g|100\s*ml)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\([^)]*€\s*/\s*[^)]*\)", " ", cleaned)
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
    """Parse Latvian campaign date ranges, including ranges spanning New Year."""
    today = today or date.today()
    text = " ".join(text.replace("\xa0", " ").split()).casefold()
    year_match = re.search(r"\b(20\d{2})\b", text)
    explicit_year = int(year_match.group(1)) if year_match else None

    # 17. novembris - 4. janvāris
    cross_month = re.search(
        rf"(?P<d1>\d{{1,2}})\.?\s*(?P<m1>{MONTH_PATTERN})\.?\s*[-–—]\s*"
        rf"(?P<d2>\d{{1,2}})\.?\s*(?P<m2>{MONTH_PATTERN})\.?",
        text,
    )
    if cross_month:
        d1, m1, d2, m2 = cross_month.group("d1", "m1", "d2", "m2")
        month1, month2 = MONTHS[m1.rstrip(".")], MONTHS[m2.rstrip(".")]
    else:
        # 24.-30. novembris
        same_month = re.search(
            rf"(?P<d1>\d{{1,2}})\.?\s*[-–—]\s*(?P<d2>\d{{1,2}})\.?\s*"
            rf"(?P<m>{MONTH_PATTERN})\.?",
            text,
        )
        if same_month:
            d1, d2, month_name = same_month.group("d1", "d2", "m")
            month1 = month2 = MONTHS[month_name.rstrip(".")]
        else:
            # Numeric form: 24.11.-30.11.2026
            numeric = re.search(
                r"(?P<d1>\d{1,2})\.(?P<m1>\d{1,2})\.?\s*[-–—]\s*"
                r"(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})\.?",
                text,
            )
            if not numeric:
                return None, None
            d1, m1, d2, m2 = numeric.group("d1", "m1", "d2", "m2")
            month1, month2 = int(m1), int(m2)

    year1 = explicit_year or today.year
    year2 = year1 + (1 if month2 < month1 else 0)
    try:
        start = date(year1, month1, int(d1))
        end = date(year2, month2, int(d2))
    except ValueError:
        return None, None
    if end < start:
        return None, None
    return start.isoformat(), end.isoformat()


def campaign_dates(page_text: str, campaign_name: str = CAMPAIGN_NAME,
                   today: date | None = None) -> tuple[str | None, str | None]:
    """Use the date range adjacent to the weekly-offer heading, not another banner."""
    matches = list(re.finditer(re.escape(campaign_name), page_text, flags=re.IGNORECASE))
    for match in matches:
        # Keep the window tight: the page contains several adjacent campaign banners.
        # A wider window can accidentally assign the gift-catalog dates to weekly offers.
        nearby_text = page_text[match.end():match.end() + 100]
        dates = offer_dates(nearby_text, today=today)
        if dates != (None, None):
            return dates
    return None, None


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


def find_weekly_brochure_url(soup: BeautifulSoup) -> str | None:
    """Find the brochure link belonging to the weekly-offer campaign card."""
    headings = soup.find_all(
        lambda tag: isinstance(tag, Tag)
        and tag.name in {"h1", "h2", "h3", "h4", "p"}
        and tag.get_text(" ", strip=True).casefold() == CAMPAIGN_NAME.casefold()
    )
    for heading in headings:
        node = heading
        for _ in range(7):
            if not isinstance(node, Tag):
                break
            for anchor in node.find_all("a", href=True):
                href = anchor.get("href", "")
                if "/bukleti/" in href:
                    return urljoin(URL, href)
            node = node.parent
    # Fallback for markup where the campaign title is nested inside a non-heading node.
    for anchor in soup.select('a[href*="/bukleti/"]'):
        node = anchor
        for _ in range(5):
            if not isinstance(node, Tag):
                break
            label = node.get_text(" ", strip=True).casefold()
            if CAMPAIGN_NAME.casefold() in label and len(label) < 700:
                return urljoin(URL, anchor["href"])
            node = node.parent
    return None


def fetch_campaign_store_formats(session: requests.Session, brochure_url: str | None
                                 ) -> tuple[list[str], str | None]:
    if not brochure_url:
        return [], None
    try:
        response = session.get(brochure_url, headers=HEADERS, timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        formats = store_formats_from_text(soup.get_text(" ", strip=True))
        return formats, brochure_url if formats else None
    except requests.RequestException as exc:
        print(f"Warning: could not verify store formats from {brochure_url}: {exc}", file=sys.stderr)
        return [], None


def has_paldies_marker(card: Tag) -> bool:
    for marker in card.find_all("img"):
        alt = (marker.get("alt") or "").strip().casefold()
        src = (marker.get("src") or marker.get("data-src") or "").casefold()
        if alt == "paldies" or "paldies" in Path(src.split("?", 1)[0]).name:
            return True
    return False


def image_url(img: Tag) -> str | None:
    for attribute in ("src", "data-src", "data-original"):
        value = img.get(attribute)
        if value:
            return urljoin(URL, value)
    srcset = img.get("srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split()[0]
        return urljoin(URL, first)
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


def price_quality(prices: list[float], card_text: str) -> tuple[float | None, float | None,
                                                               int | None, list[str]]:
    """Return promo price, comparable price, printed/computed discount and issues."""
    issues: list[str] = []
    if not prices:
        return None, None, None, ["price_not_found"]

    price = prices[0]
    regular: float | None = None
    if len(prices) == 2:
        if prices[1] > price:
            regular = prices[1]
        else:
            issues.append("second_price_not_higher")
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


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def main():
    session = requests.Session()
    response = session.get(URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    valid_from, valid_until = campaign_dates(page_text)
    brochure_url = find_weekly_brochure_url(soup)
    store_formats, store_scope_source = fetch_campaign_store_formats(session, brochure_url)

    found: dict[str, dict] = {}
    review_offers: list[dict] = []
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
        source = urljoin(URL, card_link["href"]) if card_link else URL
        pid = product_id(name)
        image = image_url(img)
        quality = {
            "priceStatus": "review" if price_issues else "verified",
            "dateStatus": "verified" if valid_from and valid_until else "unknown",
            "storeScopeStatus": "verified" if store_formats else "unknown",
            "issues": price_issues,
        }
        offer = {
            "productId": pid,
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
            "paldis": requires_paldies,  # Backward compatibility with the current frontend.
            "validFrom": valid_from,
            "validUntil": valid_until,
            "city": None,
            "store": "Maxima",
            "storeFormats": store_formats,
            "storeScopeSource": store_scope_source,
            "campaign": CAMPAIGN_NAME,
            "source": source,
            "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "quality": quality,
        }

        # Keep one entry per product. Do not silently replace a clean card with a worse duplicate.
        previous = found.get(pid)
        if previous is None or (
            previous["quality"]["priceStatus"] == "review"
            and quality["priceStatus"] == "verified"
        ):
            found[pid] = offer
        if quality["priceStatus"] == "review":
            review_offers.append(offer)

    offers = sorted(
        found.values(),
        key=lambda item: (item["name"].casefold(), item["price"]),
    )
    if not offers:
        raise RuntimeError(
            "No priced product cards detected; refusing to overwrite the existing feed."
        )

    history_data = load_json(HISTORY_OUT, {"updatedAt": None, "products": {}})
    products = history_data.get("products", {})
    observed_at = datetime.now().astimezone()
    observed_date = observed_at.date().isoformat()

    for offer in offers:
        # Ambiguous prices remain visible with a review flag, but do not contaminate history.
        if offer["quality"]["priceStatus"] != "verified":
            continue
        pid = offer["productId"]
        product = products.setdefault(pid, {
            "productId": pid,
            "name": offer["name"],
            "unit": offer.get("unit"),
            "points": [],
        })
        product["name"] = offer["name"]
        product["unit"] = offer.get("unit")
        point = {
            "date": observed_date,
            "price": offer["price"],
            "regularPrice": offer.get("regularPrice"),
            "paldiesPrice": offer.get("paldiesPrice"),
            "priceWithoutPaldies": offer.get("priceWithoutPaldies"),
            "requiresPaldies": offer["requiresPaldies"],
            "discountPercent": offer.get("discountPercent"),
            "validFrom": offer.get("validFrom"),
            "validUntil": offer.get("validUntil"),
            "storeFormats": offer.get("storeFormats", []),
            "checkedAt": observed_at.isoformat(timespec="seconds"),
        }
        product["points"] = [
            p for p in product.get("points", [])
            if p.get("date") != observed_date
        ]
        product["points"].append(point)
        product["points"].sort(key=lambda p: p.get("date", ""))

    history_payload = {
        "updatedAt": observed_at.isoformat(timespec="seconds"),
        "source": URL,
        "products": products,
    }
    HISTORY_OUT.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_OUT.write_text(
        json.dumps(history_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    payload = {
        "source": URL,
        "campaign": CAMPAIGN_NAME,
        "updatedAt": observed_at.isoformat(timespec="seconds"),
        "validFrom": valid_from,
        "validUntil": valid_until,
        "storeFormats": store_formats,
        "storeScopeSource": store_scope_source,
        "offers": offers,
        "reviewOffers": review_offers,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    verified = sum(1 for offer in offers if offer["quality"]["priceStatus"] == "verified")
    review = len(offers) - verified
    print(
        f"Saved {verified} verified offers, {review} offers needing review, "
        f"and history for {len(products)} products"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Scraper failed: {exc}", file=sys.stderr)
        raise
