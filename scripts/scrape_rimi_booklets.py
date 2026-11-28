#!/usr/bin/env python3
"""Collect Rimi Latvia's in-store discounts from official digital booklets.

The active paper leaflets are linked from Rimi's ``/akcijas-un-bukleti`` page
and hosted in Rimi's iPaper viewer. The viewer's public ``GetPDF.ashx`` action
returns the booklet PDF, whose text layer keeps product names and prices in
separate positioned text spans. Large prices anchor a product card; a nearby
smaller price is kept as the regular price. Cards without a clean name or a
verifiable discount are sent to ``reviewOffers`` rather than shown as offers.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

BASE = "https://www.rimi.lv"
BOOKLETS_PAGE = BASE + "/akcijas-un-bukleti"
VIEWER_HOST = "bukleti.rimi.lv"
OUT = Path("data/rimi-offers.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
MONEY_RE = re.compile(r"^(\d{1,4})[,.](\d{2})$")
PERCENT_RE = re.compile(r"^[−-](\d{1,2})%$")
ALCOHOL_RE = re.compile(
    r"\b(alus|sidr\w*|vin\w*|degvin\w*|viskij\w*|brendij\w*|"
    r"konjak\w*|sampaniet\w*|prosecco|prosek\w*|likier\w*|tekil\w*|"
    r"dzin\w*|rom\w*|vodk\w*)\b", re.I)
TITLE_NOISE_RE = re.compile(
    r"(preču pieejamība|piedāvājums var atšķirties|uz vietas veikalā|"
    r"rimi express|attēliem ir ilustratīva|reklāmā minētajai|"
    r"cena ar atlaidi norādīta|izdevīgi|akciju buklets|rimi mini akcijas)", re.I)
DATE_RANGE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\s*[-–]\s*\d{1,2}\.\d{1,2}\.$")


def session() -> requests.Session:
    result = requests.Session()
    result.headers.update(HEADERS)
    return result


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _parse_date(year: int, day: int, month: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_validity(text: str, today: date | None = None) -> tuple[str | None, str | None]:
    """Read validity dates from an iPaper title or the leaflet's first page."""
    today = today or date.today()
    text = str(text or "")
    explicit = re.search(
        r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?\s*[-–]\s*"
        r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?", text)
    if explicit:
        y1, d1, m1, y2, d2, m2 = map(int, explicit.groups())
        start, end = _parse_date(y1, d1, m1), _parse_date(y2, d2, m2)
        return (start.isoformat() if start else None, end.isoformat() if end else None)

    short = re.search(
        r"(?<!\d)(\d{1,2})\.(\d{1,2})\.?\s*[-–]\s*"
        r"(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?", text)
    if short:
        d1, m1, d2, m2 = map(int, short.groups()[:4])
        year2 = int(short.group(5) or today.year)
        year1 = year2
        if m2 < m1:
            if today.month == 1 and m1 == 12:
                year1 = year2 - 1
            else:
                year2 += 1
        start, end = _parse_date(year1, d1, m1), _parse_date(year2, d2, m2)
        return (start.isoformat() if start else None, end.isoformat() if end else None)

    ending = re.search(r"līdz\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", text, re.I)
    if ending:
        d, m, y = map(int, ending.groups())
        end = _parse_date(y, d, m)
        return (None, end.isoformat() if end else None)
    return None, None


def discover_booklets(sess: requests.Session) -> list[dict]:
    """Discover active in-store promotional booklets linked by Rimi itself."""
    response = sess.get(BOOKLETS_PAGE, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    booklets: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = urljoin(BOOKLETS_PAGE, anchor.get("href", "")).split("?", 1)[0]
        parsed = urlsplit(url)
        if parsed.hostname != VIEWER_HOST or url in seen:
            continue
        path = parsed.path.casefold()
        if "/akciju-bukleti/" not in path and "/specakcijas/" not in path:
            continue
        name = " ".join(anchor.stripped_strings).strip()
        if not name:
            continue
        seen.add(url)
        slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        booklets.append({"id": "rimi-" + slug, "name": name, "source": url})
    return booklets


def _viewer_settings(sess: requests.Session, booklet: dict) -> tuple[dict, str]:
    response = sess.get(booklet["source"], timeout=45)
    response.raise_for_status()
    marker = "window.staticSettings ="
    start = response.text.find(marker)
    if start < 0:
        raise RuntimeError("iPaper viewer did not include staticSettings")
    settings, _ = json.JSONDecoder().raw_decode(response.text[start + len(marker):])
    title = settings.get("pageTitle") or booklet["name"]
    return settings, title


def download_booklet(sess: requests.Session, booklet: dict) -> tuple[bytes, dict]:
    """Fetch the public PDF through iPaper's own download action."""
    settings, title = _viewer_settings(sess, booklet)
    action = urljoin(booklet["source"], settings.get("url", "") + "GetPDF.ashx")
    response = sess.post(action, data={"pageNumbers": ""},
                         headers={"Referer": booklet["source"]}, timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise RuntimeError("iPaper PDF download returned non-PDF content")
    text = " ".join(settings.get("pageTexts", [])[:1])
    valid_from, valid_until = parse_validity(title + " " + text)
    meta = {
        **booklet,
        "name": title,
        "validFrom": valid_from,
        "validUntil": valid_until,
        "pages": len(settings.get("pages", [])),
    }
    return response.content, meta


def _money(text: str) -> float | None:
    match = MONEY_RE.fullmatch(str(text or "").strip())
    if not match:
        return None
    value = float(f"{match.group(1)}.{match.group(2)}")
    return round(value, 2) if 0 < value < 10000 else None


def _page_spans(page) -> list[dict]:
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = re.sub(r"\s+", " ", span.get("text", "")).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                spans.append({
                    "text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                    "size": float(span.get("size", 0)),
                })
    return spans


def _sale_anchors(spans: list[dict]) -> list[dict]:
    """Find sale prices while excluding large crossed-out reference prices."""
    prices = [
        {**span, "value": _money(span["text"])}
        for span in spans
        if span["size"] >= 16 and _money(span["text"]) is not None
    ]
    anchors = []
    for candidate in prices:
        old_price = any(
            sale["value"] < candidate["value"]
            and sale["size"] >= candidate["size"] + 2
            and abs(sale["cx"] - candidate["cx"]) <= 90
            and 0 < candidate["cy"] - sale["cy"] <= 75
            for sale in prices
        )
        if not old_price:
            anchors.append(candidate)
    return anchors


def _title_blocks(spans: list[dict]) -> list[dict]:
    """Group nearby runs into product labels without assuming page columns."""
    candidates = []
    for span in spans:
        text = span["text"]
        if (not 6.5 <= span["size"] <= 15 or _money(text) is not None
                or PERCENT_RE.fullmatch(text) or "€/" in text or "€ /" in text
                or TITLE_NOISE_RE.search(text) or DATE_RANGE_RE.fullmatch(text)
                or re.fullmatch(r"[\d.,;()€/%\-–]+", text)):
            continue
        candidates.append(span)

    groups: list[dict] = []
    for span in sorted(candidates, key=lambda item: (item["cy"], item["x0"])):
        possible = [
            (abs(span["cy"] - group["lastCy"]) + abs(span["x0"] - group["lastX0"]), group)
            for group in groups
            if 0 <= span["cy"] - group["lastCy"] <= 22
            and abs(span["x0"] - group["lastX0"]) <= 75
        ]
        if possible:
            group = min(possible, key=lambda item: item[0])[1]
        else:
            group = {"spans": []}
            groups.append(group)
        group["spans"].append(span)
        group["lastCy"] = span["cy"]
        group["lastX0"] = span["x0"]

    blocks = []
    for group in groups:
        items = group["spans"]
        ordered = sorted(items, key=lambda item: (round(item["cy"], 1), item["x0"]))
        name = re.sub(r"\s+", " ", " ".join(item["text"] for item in ordered)).strip(" -–•*:,+")
        blocks.append({
            "text": name[:180],
            "x0": min(item["x0"] for item in items),
            "x1": max(item["x1"] for item in items),
            "y0": min(item["y0"] for item in items),
            "y1": max(item["y1"] for item in items),
        })
    return blocks


def _box_gap(first: dict, second: dict) -> tuple[float, float]:
    horizontal = max(first["x0"] - second["x1"], second["x0"] - first["x1"], 0)
    vertical = max(first["y0"] - second["y1"], second["y0"] - first["y1"], 0)
    return horizontal, vertical


def _match_titles(anchors: list[dict], blocks: list[dict]) -> dict[int, dict]:
    """Assign each printed product label to one nearest sale price."""
    candidates = []
    for anchor_index, anchor in enumerate(anchors):
        for block_index, block in enumerate(blocks):
            dx, dy = _box_gap(anchor, block)
            if dx > 190 or dy > 300:
                continue
            candidates.append((dx * 0.55 + dy * 1.05, anchor_index, block_index))
    candidates.sort()

    result: dict[int, dict] = {}
    used_blocks: set[int] = set()
    for _, anchor_index, block_index in candidates:
        if anchor_index in result or block_index in used_blocks:
            continue
        result[anchor_index] = blocks[block_index]
        used_blocks.add(block_index)
    return result


def _match_price_details(anchors: list[dict], details: list[dict],
                         predicate, max_x: float, max_y: float) -> dict[int, dict]:
    """Match each printed regular price or percent badge to one sale price."""
    candidates = []
    for anchor_index, anchor in enumerate(anchors):
        for detail_index, detail in enumerate(details):
            if not predicate(anchor, detail):
                continue
            dx, dy = _box_gap(anchor, detail)
            if dx > max_x or dy > max_y:
                continue
            candidates.append((dx * 0.8 + dy * 1.2, anchor_index, detail_index))
    candidates.sort()

    result: dict[int, dict] = {}
    used_details: set[int] = set()
    for _, anchor_index, detail_index in candidates:
        if anchor_index in result or detail_index in used_details:
            continue
        result[anchor_index] = details[detail_index]
        used_details.add(detail_index)
    return result


def extract_page(page, page_num: int, booklet: dict) -> tuple[list[dict], list[dict]]:
    """Extract discount cards from one positioned-text PDF page."""
    raw_spans = _page_spans(page)
    if not raw_spans:
        return [], []

    anchors = _sale_anchors(raw_spans)
    if not anchors:
        return [], []
    anchors.sort(key=lambda item: (item["cy"], item["cx"]))
    blocks = _title_blocks(raw_spans)
    title_matches = _match_titles(anchors, blocks)
    offers: list[dict] = []
    reviews: list[dict] = []
    seen: set[str] = set()
    percent_spans = [
        {**span, "value": int(PERCENT_RE.fullmatch(span["text"]).group(1))}
        for span in raw_spans if PERCENT_RE.fullmatch(span["text"])
    ]
    regular_spans = [
        {**span, "value": _money(span["text"])}
        for span in raw_spans if _money(span["text"]) is not None and span["size"] >= 8
    ]
    regular_matches = _match_price_details(
        anchors, regular_spans,
        lambda anchor, item: item["value"] > anchor["value"] + 0.005
        and item["size"] <= anchor["size"] - 2,
        max_x=115, max_y=75,
    )
    percent_matches = _match_price_details(
        anchors, percent_spans, lambda _anchor, _item: True, max_x=130, max_y=100,
    )

    for anchor_index, anchor in enumerate(anchors):
        title = title_matches.get(anchor_index)
        name = title["text"] if title else ""

        regular_match = regular_matches.get(anchor_index)
        regular_price = regular_match["value"] if regular_match else None
        percent_match = percent_matches.get(anchor_index)
        discount_percent = percent_match["value"] if percent_match else None
        if discount_percent is not None and regular_price is not None:
            calculated = round((regular_price - anchor["value"]) / regular_price * 100)
            if abs(calculated - discount_percent) > 4:
                # A neighbouring badge can be closer than the actual badge when
                # cards share a spread; the validated price pair remains usable.
                discount_percent = None

        # Keep nearby condition text scoped to the price badge and item label.
        card_spans = [
            span for span in raw_spans
            if abs(span["cx"] - anchor["cx"]) <= 100
            and abs(span["cy"] - anchor["cy"]) <= 100
        ]
        normalized_name = _fold(name)
        if name and ALCOHOL_RE.search(normalized_name) and "bezalkohol" not in normalized_name:
            continue

        identity = f"{_fold(name)}|{anchor['value']:.2f}|{regular_price}"
        if identity in seen:
            continue
        seen.add(identity)
        offer_id = (
            f"{booklet['id']}-p{page_num + 1}-"
            f"{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:8]}"
        )
        card_band = " ".join(span["text"] for span in card_spans).casefold()
        requires_card = bool(re.search(r"mans\s*rimi|mansrimi|paldies", card_band))
        offer = {
            "offerId": offer_id,
            "name": name,
            "price": anchor["value"],
            "regularPrice": regular_price,
            "requiresCard": requires_card,
            "onlyInApp": False,
            "campaign": booklet["name"],
            "campaignId": booklet["id"],
            "source": booklet["source"],
            "pdfPage": page_num + 1,
            "validFrom": booklet.get("validFrom"),
            "validUntil": booklet.get("validUntil"),
            "store": "rimi",
        }
        if requires_card:
            offer["cardName"] = "Mans Rimi"
        if discount_percent is not None:
            offer["discountPercent"] = discount_percent

        if len(name) < 5 or len(re.findall(r"[a-zāčēģīķļņšūž]{3,}", normalized_name)) < 1:
            offer["reviewReason"] = "weak_name"
            reviews.append(offer)
        elif regular_price is None and discount_percent is None:
            offer["reviewReason"] = "no_discount"
            reviews.append(offer)
        else:
            offers.append(offer)
    return offers, reviews


def extract_offers(pdf_bytes: bytes, booklet: dict) -> tuple[list[dict], list[dict], int]:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is required for Rimi booklet parsing")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    offers: list[dict] = []
    reviews: list[dict] = []
    page_count = len(doc)
    try:
        for page_num, page in enumerate(doc):
            page_offers, page_reviews = extract_page(page, page_num, booklet)
            offers.extend(page_offers)
            reviews.extend(page_reviews)
    finally:
        doc.close()
    return offers, reviews, page_count


def main() -> None:
    sess = session()
    booklets = discover_booklets(sess)
    if not booklets:
        raise RuntimeError("No active Rimi in-store promotional booklets found")

    offers: list[dict] = []
    reviews: list[dict] = []
    catalogues: list[dict] = []
    campaigns: list[dict] = []
    failed = []
    for index, booklet in enumerate(booklets):
        try:
            pdf_bytes, meta = download_booklet(sess, booklet)
            today = date.today()
            if meta.get("validUntil") and date.fromisoformat(meta["validUntil"]) < today:
                print(f"  Skipping expired booklet: {meta['name']}")
                continue
            if meta.get("validFrom") and date.fromisoformat(meta["validFrom"]) > today:
                print(f"  Skipping future booklet: {meta['name']}")
                continue
            booklet_offers, booklet_reviews, page_count = extract_offers(pdf_bytes, meta)
            offers.extend(booklet_offers)
            reviews.extend(booklet_reviews)
            meta["pdfPages"] = page_count
            meta["offerVariants"] = len(booklet_offers)
            meta["reviewOffers"] = len(booklet_reviews)
            meta["complete"] = bool(page_count)
            catalogues.append({key: meta[key] for key in (
                "id", "name", "source", "validFrom", "validUntil",
                "offerVariants", "reviewOffers", "complete",
            )})
            campaigns.append({"id": meta["id"], "name": meta["name"]})
            print(f"  {meta['name']}: {len(booklet_offers)} offers, "
                  f"{len(booklet_reviews)} review, {page_count} pages")
        except Exception as exc:
            failed.append(booklet["name"])
            print(f"  ! {booklet['name']}: {exc}", file=sys.stderr)
        if index + 1 < len(booklets):
            time.sleep(0.7)

    if not offers and not reviews:
        raise RuntimeError("No Rimi booklet offers were extracted")
    if failed:
        print("Warning: some Rimi booklets could not be refreshed: " + ", ".join(failed),
              file=sys.stderr)

    # The opening spread often previews items that also appear on an inner page.
    unique_offers = {}
    for offer in offers:
        key = (offer["campaignId"], _fold(offer["name"]), offer["price"], offer.get("regularPrice"))
        unique_offers.setdefault(key, offer)
    offers = list(unique_offers.values())
    offers.sort(key=lambda item: (item["name"].casefold(), item["price"], item["campaign"]))
    reviews.sort(key=lambda item: (item["name"].casefold(), item["campaign"]))
    products = build_product_groups(offers) if offers else []
    payload = {
        "source": BOOKLETS_PAGE,
        "store": "rimi",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "campaigns": campaigns,
        "catalogues": catalogues,
        "offers": offers,
        "products": products,
        "reviewOffers": reviews,
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(products),
            "reviewOffers": len(reviews),
            "catalogues": len(catalogues),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} Rimi booklet offers ({len(reviews)} review) → {OUT}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        main()
    except Exception as exc:
        print(f"Rimi booklet scraper failed: {exc}", file=sys.stderr)
        raise
