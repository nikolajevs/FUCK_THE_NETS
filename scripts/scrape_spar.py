#!/usr/bin/env python3
"""Collect SPAR Latvia (spar.lv) promotional offers.

SPAR publishes its newspaper ("SPAR avīze") on https://spar.lv/akcijas/ as a
DearPDF flipbook whose source PDF is embedded in a ``window.df_option_<id>``
script block. The PDF keeps a real text layer for product *names*, sizes and
the validity period, but the prices are rendered as images.

This script finds the newspaper, its period and each page's campaign (weekly
offers, everyday offers or the whole newspaper); the shared booklet vision
engine (``scripts/booklet_vision.py``) reads the pages. Because SPAR's prices
are artwork, its registry entry sets ``prices_in_text=False``: the engine
checks every card's *name* against the text layer under it, not its price.
Bundle badges ("2.00 PAR 2") are described in the registry's ``vision_hint``
and divided locally. Pages are cached by PDF content, so an unchanged
newspaper costs no Gemini requests; pages Gemini could not read are left for
the workflow's local Qwen step.

SPAR often leaves an ended newspaper on the page for days. A newspaper whose
printed period is over is not read at all.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    from scripts.booklet_vision import (
        PageReader, VisionCache, gemini_client, open_pdf_pages, pending_dir_from_env, read_booklet,
    )
    from scripts.common import is_expired, riga_today, write_feed
    from scripts.product_identity import build_product_groups
    from scripts.stores import by_id
except ModuleNotFoundError:
    from booklet_vision import (
        PageReader, VisionCache, gemini_client, open_pdf_pages, pending_dir_from_env, read_booklet,
    )
    from common import is_expired, riga_today, write_feed
    from product_identity import build_product_groups
    from stores import by_id

STORE = by_id("spar")

BASE = "https://spar.lv"
OFFERS_PAGE = BASE + "/akcijas/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/spar-offers.json")
RETRIES = 3

DEARPDF_RE = re.compile(r'window\.df_option_\d+\s*=\s*(\{.*?\})\s*;', re.S)
SOURCE_RE = re.compile(r'"source"\s*:\s*"([^"]+)"')
PERIOD_RE = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?\s*[–—-]\s*(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?")
LIDZ_RE = re.compile(
    r"no\s*(\d{1,2}\.\d{1,2})\.?\s*līdz\s*(\d{1,2}\.\d{1,2})\.?(\d{4})?", re.I)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def parse_period(text: str, today: date | None = None) -> tuple[str | None, str | None]:
    today = today or date.today()
    m = PERIOD_RE.search(text or "")
    if not m:
        return None, None
    d1, m1, y1, d2, m2, y2 = m.groups()
    year2 = int(y2) if y2 else today.year
    year1 = int(y1) if y1 else (year2 - 1 if int(m2) < int(m1) else year2)
    try:
        return date(year1, int(m1), int(d1)).isoformat(), date(year2, int(m2), int(d2)).isoformat()
    except ValueError:
        return None, None


def parse_subperiod(text: str) -> tuple[str | None, str | None]:
    m = LIDZ_RE.search(text or "")
    if not m:
        return None, None
    start_dm, end_dm, year = m.groups()
    year = int(year) if year else date.today().year
    try:
        sd, sm = (int(x) for x in start_dm.split("."))
        ed, em = (int(x) for x in end_dm.split("."))
        sy = year - 1 if (sm, sd) > (em, ed) else year
        return date(sy, sm, sd).isoformat(), date(year, em, ed).isoformat()
    except ValueError:
        return None, None


def discover_booklets(html_text: str) -> list[dict]:
    booklets: list[dict] = []
    seen: set[str] = set()
    for m in DEARPDF_RE.finditer(html_text):
        src = SOURCE_RE.search(m.group(1))
        if not src:
            continue
        url = urljoin(BASE, src.group(1).replace("\\/", "/")).replace("http://", "https://")
        if url in seen or not url.lower().endswith(".pdf"):
            continue
        seen.add(url)
        bid = "spar-" + (Path(url).stem.lower()[:40] or "avize")
        booklets.append({"id": bid, "name": "SPAR akciju avīze", "pdf": url,
                         "validFrom": None, "validUntil": None})
    return booklets


def fetch(sess: requests.Session, url: str) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = sess.get(url, timeout=90)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"SPAR fetch {url} failed after {RETRIES} attempts: {last_error}")


def period_from_pdf(content: bytes) -> tuple[str | None, str | None]:
    if fitz is None:
        return None, None
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return None, None
    try:
        text = doc[0].get_text() if len(doc) else ""
    finally:
        doc.close()
    m = re.search(r"Akcijas periods:\s*([^\n]+)", text)
    if m:
        vf, vu = parse_period(m.group(1))
        if vf:
            return vf, vu
    return parse_period(text)


def page_campaign(text: str, booklet: dict) -> dict:
    folded = _fold(text)
    if "nedelas piedavajumi" in folded or "nedēļas" in text.casefold():
        vf, vu = parse_subperiod(text)
        return {"campaign": "Nedēļas piedāvājumi", "campaignId": "nedelas-piedavajumi",
                "validFrom": vf, "validUntil": vu}
    if "katru dienu" in folded:
        return {"campaign": "Īpaši piedāvājumi katru dienu", "campaignId": "katru-dienu",
                "validFrom": booklet.get("validFrom"), "validUntil": booklet.get("validUntil")}
    return {"campaign": booklet["name"], "campaignId": booklet["id"],
            "validFrom": booklet.get("validFrom"), "validUntil": booklet.get("validUntil")}


def _finalise_payload(payload: dict) -> None:
    unique = {o["offerId"]: o for o in payload.get("offers", [])}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"] if x["price"] is not None else 0))
    unique_rev = {o["offerId"]: o for o in payload.get("reviewOffers", [])}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())
    products = build_product_groups(offers) if offers else []
    payload["offers"] = offers
    payload.pop("products", None)  # the site groups offers itself
    payload["reviewOffers"] = reviews
    payload["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload["summary"] = {
        "offerVariants": len(offers), "uniqueProducts": len(products),
        "reviewOffers": len(reviews), "catalogues": len(payload.get("catalogues", [])),
    }
    write_feed(OUT, payload)


def page_meta(booklet: dict):
    """Campaign metadata for each page of one newspaper, for the vision engine."""
    def meta_for(page_number: int, text: str) -> dict:
        return {**page_campaign(text, booklet), "source": booklet["pdf"]}
    return meta_for


def main() -> None:
    if fitz is None:
        print("PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)
    sess = session()
    print("Discovering SPAR DearPDF booklets...")
    r = fetch(sess, OFFERS_PAGE)
    booklets = discover_booklets(r.text)
    if not booklets:
        raise RuntimeError("No SPAR booklet discovered")

    riga_day = riga_today()
    catalogues, all_offers, all_reviews = [], [], []
    cache = VisionCache(STORE.id)
    try:
        with gemini_client(STORE) as client:
            reader = PageReader(STORE, cache, client, pending_dir_from_env())
            for booklet in booklets:
                print(f"Reading {booklet['pdf']}")
                try:
                    content = fetch(sess, booklet["pdf"]).content
                except RuntimeError as exc:
                    print(f"  PDF unavailable ({exc}); keeping link without period", file=sys.stderr)
                    content = None
                vf, vu = period_from_pdf(content) if content else (None, None)
                booklet["validFrom"], booklet["validUntil"] = vf, vu
                expired = is_expired(vu, riga_day)
                offers, reviews, resolved, pending = [], [], [], 0
                if content is not None and expired:
                    print(f"  newspaper ended on {vu}; not reading it until SPAR publishes a new one")
                elif content is not None:
                    with open_pdf_pages(content, page_meta(booklet)) as pages:
                        outcome = read_booklet(pages, reader)
                    offers, reviews = outcome.offers, outcome.reviews
                    resolved, pending = sorted(outcome.resolved), outcome.pending
                all_offers.extend(offers)
                all_reviews.extend(reviews)
                catalogues.append({
                    "id": booklet["id"], "name": booklet["name"], "source": booklet["pdf"],
                    "validFrom": vf, "validUntil": vu,
                    "verifiedOffers": len(offers), "reviewOffers": len(reviews),
                    "complete": bool(resolved) and pending == 0,
                    "expired": expired,
                    "resolvedPages": resolved,
                })
                print(f"  period {vf} – {vu}: {len(offers)} offers, {len(reviews)} review")
    finally:
        cache.save()

    payload = {
        "source": OFFERS_PAGE, "store": "spar",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogues": catalogues, "offers": all_offers,
        "reviewOffers": all_reviews, "summary": {},
    }
    _finalise_payload(payload)
    print(f"Saved {len(payload['offers'])} offers ({len(payload['reviewOffers'])} review) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SPAR scraper failed: {exc}", file=sys.stderr)
        raise
