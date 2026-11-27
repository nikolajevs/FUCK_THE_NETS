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

from scripts.product_identity import build_product_groups, product_identity

BROCHURES_URL = "https://www.maxima.lv/bukleti/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)"
}
OUT = Path("data/offers.json")
HISTORY_OUT = Path("data/price-history.json")

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
        r"(?<!\d)\d{1,3}[,.]\s*\d{2}\s*€\s*/\s*(?:kg|l|100\s*g|100\s*ml)\b",
        " ", text, flags=re.IGNORECASE,
    )
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

    cross_month = re.search(
        rf"(?P<d1>\d{{1,2}})\.?\s*(?P<m1>{MONTH_PATTERN})\.?\s*[-–—]\s*"
        rf"(?P<d2>\d{{1,2}})\.?\s*(?P<m2>{MONTH_PATTERN})\.?",
        text,
    )
    if cross_month:
        d1, m1, d2, m2 = cross_month.group("d1", "m1", "d2", "m2")
        month1, month2 = MONTHS[m1.rstrip(".")], MONTHS[m2.rstrip(".")]
    else:
        same_month = re.search(
            rf"(?P<d1>\d{{1,2}})\.?\s*[-–—]\s*(?P<d2>\d{{1,2}})\.?\s*"
            rf"(?P<m>{MONTH_PATTERN})\.?",
            text,
        )
        if same_month:
            d1, d2, month_name = same_month.group("d1", "d2", "m")
            month1 = month2 = MONTHS[month_name.rstrip(".")]
        else:
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
    """Resolve a campaign's dates and its own /bukleti/ detail page from the brochure index."""
    candidates: list[tuple[int, int, str | None, str | None, str | None]] = []
    for heading in soup.find_all(
        lambda tag: isinstance(tag, Tag)
        and tag.name in {"h1", "h2", "h3", "h4", "p"}
        and tag.get_text(" ", strip=True).casefold() == campaign_name.casefold()
    ):
        node: Tag | None = heading
        for depth in range(1, 8):
            if not isinstance(node, Tag):
                break
            container_text = node.get_text(" ", strip=True)
            if len(container_text) <= 700 and campaign_name.casefold() in container_text.casefold():
                title_match = re.search(re.escape(campaign_name), container_text, flags=re.IGNORECASE)
                tail = container_text[title_match.end():title_match.end() + 180] if title_match else ""
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


def stable_offer_id(
    campaign_id: str,
    canonical_product_id: str,
    image: str | None,
    product_source: str,
    store_formats: list[str],
    requires_paldies: bool,
) -> str:
    payload = "|".join([
        campaign_id,
        canonical_product_id,
        image or "",
        product_source,
        ",".join(store_formats),
        str(requires_paldies),
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

    return {
        "updatedAt": observed_at.isoformat(timespec="seconds"),
        "sources": sorted({
            offer["campaignSource"] for offer in offers if offer.get("campaignSource")
        }),
        "products": products,
    }


def main():
    session = requests.Session()
    brochure_response = session.get(BROCHURES_URL, headers=HEADERS, timeout=30)
    brochure_response.raise_for_status()
    brochure_index = BeautifulSoup(brochure_response.text, "html.parser")

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
        "sources": [campaign["url"] for campaign in CAMPAIGNS],
        "updatedAt": observed_at.isoformat(timespec="seconds"),
        "campaigns": campaign_metadata,
        "offers": offers,
        "products": grouped_products,
        "reviewOffers": review_offers,
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(grouped_products),
            "reviewOffers": len(review_offers),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Saved {len(offers)} offer variants grouped into {len(grouped_products)} products; "
        f"{len(review_offers)} variants need review; "
        f"history contains {len(history_payload['products'])} canonical products"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Scraper failed: {exc}", file=sys.stderr)
        raise
