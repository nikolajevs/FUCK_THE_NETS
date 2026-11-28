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

import argparse
import hashlib
import json
import os
import re
import shutil
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
IPAPER_HOST = "viewer.ipaper.io"
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
    r"cena ar atlaidi norādīta|izdevīgi|akciju buklets|rimi mini akcijas|"
    r"\bpērkot\b|\bvismaz\b)", re.I)
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


def _offer_is_current(offer: dict, today: date) -> bool:
    """Keep a carried-forward offer only while its known validity includes today."""
    for field, comparison in (("validFrom", lambda value: value <= today),
                              ("validUntil", lambda value: value >= today)):
        raw = offer.get(field)
        if not raw:
            continue
        try:
            value = date.fromisoformat(str(raw))
        except ValueError:
            continue
        if not comparison(value):
            return False
    return True


def _carry_forward_booklet(booklet: dict, previous_offers: list[dict],
                           previous_reviews: list[dict], previous_catalogues: list[dict],
                           previous_campaigns: list[dict], today: date
                           ) -> tuple[list[dict], list[dict], dict, dict]:
    """Preserve the last known active offers if this booklet could not be fetched."""
    booklet_id = booklet["id"]
    offers = [
        {**item, "stale": True}
        for item in previous_offers
        if item.get("campaignId") == booklet_id and _offer_is_current(item, today)
    ]
    reviews = [
        {**item, "stale": True}
        for item in previous_reviews
        if item.get("campaignId") == booklet_id and _offer_is_current(item, today)
    ]

    catalogue = next((item for item in previous_catalogues if item.get("id") == booklet_id), None)
    if catalogue:
        catalogue = {**catalogue, "stale": True}
        catalogue["offerVariants"] = len(offers)
        catalogue["reviewOffers"] = len(reviews)
    else:
        catalogue = {
            "id": booklet_id,
            "name": booklet["name"],
            "source": booklet["source"],
            "validFrom": None,
            "validUntil": None,
            "offerVariants": len(offers),
            "reviewOffers": len(reviews),
            "complete": False,
            "stale": True,
        }
    campaign = next((item for item in previous_campaigns if item.get("id") == booklet_id), None)
    if campaign is None:
        campaign = {"id": booklet_id, "name": booklet["name"]}
    return offers, reviews, catalogue, campaign


def parse_validity(text: str, today: date | None = None) -> tuple[str | None, str | None]:
    """Read validity dates from an iPaper title or the leaflet's first page."""
    today = today or date.today()
    text = str(text or "")
    day_first = re.search(
        r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\s*[-–]\s*"
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?", text)
    if day_first:
        d1, m1, y1, d2, m2, y2 = map(int, day_first.groups())
        start, end = _parse_date(y1, d1, m1), _parse_date(y2, d2, m2)
        return (start.isoformat() if start else None, end.isoformat() if end else None)

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
        if parsed.hostname not in {VIEWER_HOST, IPAPER_HOST} or url in seen:
            continue
        path = parsed.path.casefold()
        name = " ".join(anchor.stripped_strings).strip()
        weekly_booklet = (
            parsed.hostname == VIEWER_HOST
            and ("/akciju-bukleti/" in path or "/specakcijas/" in path)
        )
        christmas_booklet = (
            parsed.hostname == IPAPER_HOST
            and "/tematiskie-bukleti/" in path
            and "ziemassvetku buklets" in _fold(name)
        )
        if not weekly_booklet and not christmas_booklet:
            continue
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


def _review_evidence(page, anchor: dict, regular_price: float | None,
                     pdf_digest: str) -> dict:
    """Keep the exact card crop and text-layer prices for later vision review."""
    bounds = page.rect
    crop = fitz.Rect(
        max(bounds.x0, anchor["cx"] - 75),
        max(bounds.y0, anchor["y0"] - 18),
        min(bounds.x1, anchor["cx"] + 75),
        min(bounds.y1, anchor["y1"] + 112),
    )
    nearby = [
        span for span in _page_spans(page)
        if span["x1"] >= crop.x0 and span["x0"] <= crop.x1
        and span["y1"] >= crop.y0 and span["y0"] <= crop.y1
    ]
    nearby.sort(key=lambda item: (item["y0"], item["x0"]))
    parsed_prices = [anchor["value"]]
    if regular_price is not None:
        parsed_prices.append(regular_price)
    return {
        "pdfSha256": pdf_digest,
        "pdfCrop": [round(value, 2) for value in crop],
        "parsedPrices": parsed_prices,
        "rawCellText": " ".join(span["text"] for span in nearby)[:600],
    }


def _vision_resolved(offer: dict) -> bool:
    evidence = offer.get("reviewEvidence") or {}
    suggestion = evidence.get("visionAssist") or {}
    try:
        confidence = float(suggestion.get("confidence", 0))
    except (TypeError, ValueError):
        return False
    expected_price = offer.get("price")
    expected_regular = offer.get("regularPrice")
    suggested_price = suggestion.get("price")
    suggested_regular = suggestion.get("regularPrice")
    name = suggestion.get("productName")
    evidence_text = suggestion.get("evidence")
    if not isinstance(name, str) or not isinstance(evidence_text, str):
        return False
    name_words = set(re.findall(r"[a-z0-9āčēģīķļņšūž]{3,}", _fold(name)))
    evidence_words = set(re.findall(r"[a-z0-9āčēģīķļņšūž]{3,}", _fold(evidence_text)))
    name_words -= {"komplekts", "veidi", "veids", "gab", "iepak", "dažādi"}
    shared_name_words = name_words & evidence_words
    needed_name_words = 1 if len(name_words) <= 1 else 2
    return (
        suggestion.get("decision") == "resolved"
        and len(name.strip()) >= 5
        and confidence >= 0.9
        and len(shared_name_words) >= needed_name_words
        and isinstance(suggested_price, (int, float))
        and round(float(suggested_price), 2) == round(float(expected_price), 2)
        and (
            expected_regular is None
            or (
                isinstance(suggested_regular, (int, float))
                and round(float(suggested_regular), 2) == round(float(expected_regular), 2)
            )
        )
    )


def _promote_vision_resolutions(reviews: list[dict]) -> tuple[list[dict], list[dict]]:
    """Promote only vision-reviewed names whose prices match the PDF text layer."""
    promoted: list[dict] = []
    unresolved: list[dict] = []
    for offer in reviews:
        evidence = offer.get("reviewEvidence") or {}
        suggestion = evidence.get("visionAssist") or {}
        expected_price = offer.get("price")
        expected_regular = offer.get("regularPrice")
        has_discount = (
            isinstance(expected_regular, (int, float)) and expected_regular > expected_price
        ) or bool(offer.get("discountPercent"))
        if (
            offer.get("reviewReason") == "weak_name"
            and _vision_resolved(offer)
            and has_discount
        ):
            offer["name"] = suggestion["productName"].strip()
            offer.pop("reviewReason", None)
            promoted.append(offer)
        else:
            unresolved.append(offer)
    return promoted, unresolved


def extract_page(page, page_num: int, booklet: dict,
                 pdf_digest: str) -> tuple[list[dict], list[dict]]:
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
        if not name:
            # Several cards can share a price and regular price on one page.
            # Include their PDF position so unresolved cards keep distinct IDs.
            identity += f"|{anchor['cx']:.1f}|{anchor['cy']:.1f}"
        if identity in seen:
            continue
        seen.add(identity)
        offer_id = (
            f"{booklet['id']}-p{page_num + 1}-"
            f"{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:8]}"
        )
        card_band = " ".join(span["text"] for span in card_spans).casefold()
        requires_card = bool(re.search(r"mans\s*rimi|mansrimi|paldies", card_band))
        has_multi_buy_condition = (
            re.search(r"\bpērkot\b", card_band)
            and re.search(r"\bvismaz\b", card_band)
        )
        multi_buy = "2" if has_multi_buy_condition else None
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
        if multi_buy:
            offer["multiBuy"] = multi_buy
        if discount_percent is not None:
            offer["discountPercent"] = discount_percent

        if len(name) < 5 or len(re.findall(r"[a-zāčēģīķļņšūž]{3,}", normalized_name)) < 1:
            offer["reviewReason"] = "weak_name"
            offer["reviewEvidence"] = _review_evidence(
                page, anchor, regular_price, pdf_digest)
            reviews.append(offer)
        elif regular_price is None and discount_percent is None:
            offer["reviewReason"] = "no_discount"
            offer["reviewEvidence"] = _review_evidence(
                page, anchor, regular_price, pdf_digest)
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
    pdf_digest = hashlib.sha256(pdf_bytes).hexdigest()
    try:
        for page_num, page in enumerate(doc):
            page_offers, page_reviews = extract_page(
                page, page_num, booklet, pdf_digest)
            offers.extend(page_offers)
            reviews.extend(page_reviews)
    finally:
        doc.close()
    return offers, reviews, page_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vision-review", action="store_true",
        help="review ambiguous PDF cards with Gemini and local Qwen when configured",
    )
    args = parser.parse_args()

    gemini_client = None
    gemini_ledger = None
    gemini_budget = 0
    qwen_available = False
    if args.vision_review:
        try:
            from scripts.gemini_pdf_review import (
                DEFAULT_MODEL, GeminiClient, _daily_budget, _load_ledger,
                _save_ledger, add_gemini_review_suggestions, load_api_key,
            )
            from scripts.qwen_pdf_review import add_qwen_review_suggestions
        except ModuleNotFoundError:
            from gemini_pdf_review import (
                DEFAULT_MODEL, GeminiClient, _daily_budget, _load_ledger,
                _save_ledger, add_gemini_review_suggestions, load_api_key,
            )
            from qwen_pdf_review import add_qwen_review_suggestions

        api_key = load_api_key()
        if api_key:
            ledger_day = time.strftime("%Y-%m-%d", time.gmtime())
            gemini_ledger = _load_ledger(ledger_day)
            gemini_budget = _daily_budget()
            remaining = max(0, gemini_budget - int(gemini_ledger.get("requests", 0)))
            run_cap = int(os.environ.get("GEMINI_MAX_REQUESTS", "40"))
            effective_cap = min(run_cap or remaining, remaining)
            if effective_cap > 0:
                gemini_client = GeminiClient(
                    api_key,
                    model=os.environ.get("GEMINI_REVIEW_MODEL", DEFAULT_MODEL),
                    max_requests=effective_cap,
                    min_interval=float(os.environ.get("GEMINI_MIN_INTERVAL", "4.5")),
                )
            else:
                print("Gemini review skipped: daily request budget exhausted")
        else:
            print("Gemini review skipped: no API key is configured")

        qwen_cli = os.environ.get("QWEN_LLAMA_CLI", "llama-mtmd-cli")
        qwen_executable = shutil.which(qwen_cli) or (qwen_cli if Path(qwen_cli).is_file() else None)
        qwen_model = Path(os.environ.get("QWEN_MODEL_PATH", ""))
        qwen_projector = Path(os.environ.get("QWEN_MMPROJ_PATH", ""))
        qwen_available = bool(qwen_executable and qwen_model.is_file() and qwen_projector.is_file())
        if not qwen_available:
            print("Local Qwen skipped: llama.cpp and Qwen model files are not configured")

    sess = session()
    previous_vision: dict[str, dict] = {}
    try:
        previous = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    previous_offers = [item for item in previous.get("offers", []) if isinstance(item, dict)]
    previous_reviews = [item for item in previous.get("reviewOffers", []) if isinstance(item, dict)]
    previous_catalogues = [item for item in previous.get("catalogues", []) if isinstance(item, dict)]
    previous_campaigns = [item for item in previous.get("campaigns", []) if isinstance(item, dict)]
    previous_items = previous_reviews + previous_offers
    for item in previous_items:
        evidence = item.get("reviewEvidence") or {}
        if isinstance(evidence.get("visionAssist"), dict):
            previous_vision[str(item.get("offerId", ""))] = evidence

    booklets = discover_booklets(sess)
    if not booklets:
        raise RuntimeError("No active Rimi in-store promotional booklets found")

    offers: list[dict] = []
    reviews: list[dict] = []
    catalogues: list[dict] = []
    campaigns: list[dict] = []
    failed: list[dict] = []
    refreshed_booklet_ids: set[str] = set()
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
            refreshed_booklet_ids.add(booklet["id"])
            for offer in booklet_reviews:
                evidence = offer.get("reviewEvidence") or {}
                old_evidence = previous_vision.get(str(offer.get("offerId", "")))
                if (
                    old_evidence
                    and old_evidence.get("pdfSha256") == evidence.get("pdfSha256")
                    and old_evidence.get("pdfCrop") == evidence.get("pdfCrop")
                ):
                    evidence["visionAssist"] = old_evidence["visionAssist"]
                    if "visionPriceCrossCheck" in old_evidence:
                        evidence["visionPriceCrossCheck"] = old_evidence["visionPriceCrossCheck"]
                    offer["reviewEvidence"] = evidence

            if args.vision_review and booklet_reviews:
                reviewable = [
                    offer for offer in booklet_reviews
                    if offer.get("reviewReason") in {"weak_name", "no_discount"}
                    and not _vision_resolved(offer)
                ]
                if gemini_client and reviewable:
                    add_gemini_review_suggestions(pdf_bytes, reviewable, gemini_client)
                if qwen_available:
                    qwen_pending = [offer for offer in reviewable if not _vision_resolved(offer)]
                    if qwen_pending:
                        add_qwen_review_suggestions(pdf_bytes, qwen_pending)

            vision_promoted, booklet_reviews = _promote_vision_resolutions(booklet_reviews)
            booklet_offers.extend(vision_promoted)
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
            failed.append(booklet)
            print(f"  ! {booklet['name']}: {exc}", file=sys.stderr)
        if index + 1 < len(booklets):
            time.sleep(0.7)

    if failed and not refreshed_booklet_ids:
        raise RuntimeError("No Rimi booklet refreshed successfully; existing feed preserved")

    today = date.today()
    for booklet in failed:
        carried_offers, carried_reviews, catalogue, campaign = _carry_forward_booklet(
            booklet, previous_offers, previous_reviews, previous_catalogues,
            previous_campaigns, today,
        )
        offers.extend(carried_offers)
        reviews.extend(carried_reviews)
        catalogues.append(catalogue)
        campaigns.append(campaign)
        print(f"  Keeping {len(carried_offers)} unexpired offers from the last {booklet['name']} feed")

    if not offers and not reviews:
        raise RuntimeError("No Rimi booklet offers were extracted")
    if gemini_client and gemini_ledger is not None:
        gemini_ledger["requests"] = int(gemini_ledger.get("requests", 0)) + gemini_client.requests_used
        _save_ledger(gemini_ledger)
        print(f"Gemini usage today: {gemini_ledger['requests']}/{gemini_budget}")
    if failed:
        print("Warning: some Rimi booklets could not be refreshed: "
              + ", ".join(booklet["name"] for booklet in failed),
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
