#!/usr/bin/env python3
"""Collect SPAR Latvia (spar.lv) promotional booklets.

SPAR publishes its newspaper ("SPAR avīze") on https://spar.lv/akcijas/ as a
DearPDF flipbook whose source PDF is embedded in a ``window.df_option_<id>``
script block. The PDF keeps a text layer for product *names* and the validity
period, but the prices are rendered as images (only the weekly multibuy badge
and the loyalty-card percentage are text), so individual prices cannot be
verified from the text layer.

Mirroring LaTS's image-only newspaper and Elvi's issuu flipbook, SPAR is
therefore recorded as a catalogue link with its printed period rather than a
priced feed: the site surfaces the booklet so shoppers can open the current
offers, while no unverified price enters the comparison feed. The main period
("Akcijas periods: 05.11. - 25.11.2026.") and any weekly sub-period are read
from the first page text.
"""
from __future__ import annotations

import json
import re
import sys
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
TITLE_RE = re.compile(r'"data-title"\s*=\s*"([^"]+)"|data-title="([^"]+)"')
PERIOD_RE = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?\s*[–—-]\s*(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{4}))?")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


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
        slug = re.search(r"/(\d+)(?:/|$)", url)
        bid = "spar-" + (Path(url).stem.lower()[:40] or "avize")
        booklets.append({"id": bid, "name": "SPAR akciju avīze", "pdf": url,
                         "validFrom": None, "validUntil": None})
    return booklets


def fetch(sess: requests.Session, url: str) -> requests.Response:
    import time
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
    # prefer the explicit "Akcijas periods:" line, else the first date range
    m = re.search(r"Akcijas periods:\s*([^\n]+)", text)
    if m:
        vf, vu = parse_period(m.group(1))
        if vf:
            return vf, vu
    return parse_period(text)


def main() -> None:
    sess = session()
    print("Discovering SPAR DearPDF booklets...")
    r = fetch(sess, OFFERS_PAGE)
    booklets = discover_booklets(r.text)
    if not booklets:
        raise RuntimeError("No SPAR booklet discovered")
    catalogues = []
    for booklet in booklets:
        print(f"Reading {booklet['pdf']}")
        try:
            pr = fetch(sess, booklet["pdf"])
            vf, vu = period_from_pdf(pr.content)
        except RuntimeError as exc:
            print(f"  PDF unavailable ({exc}); keeping link without period", file=sys.stderr)
            vf, vu = None, None
        booklet["validFrom"], booklet["validUntil"] = vf, vu
        catalogues.append({
            "id": booklet["id"], "name": booklet["name"], "source": booklet["pdf"],
            "validFrom": vf, "validUntil": vu,
            "verifiedOffers": 0, "reviewOffers": 0, "complete": False,
            "note": "Cenas PDF ir attēli; buklets pieejams kā kataloga saite.",
        })
        print(f"  period {vf} – {vu}")

    payload = {
        "source": OFFERS_PAGE,
        "store": "spar",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogues": catalogues,
        "offers": [],
        "products": [],
        "reviewOffers": [],
        "summary": {"offerVariants": 0, "uniqueProducts": 0,
                    "reviewOffers": 0, "catalogues": len(catalogues)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(catalogues)} catalogue link(s) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SPAR scraper failed: {exc}", file=sys.stderr)
        raise