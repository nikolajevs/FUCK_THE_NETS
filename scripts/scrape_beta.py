#!/usr/bin/env python3
"""Extract priced product offers from Beta's monthly image booklet.

MazaCena.lv lists Beta's current booklet as page images. This script only
finds the booklet and its pages; the shared booklet vision engine
(``scripts/booklet_vision.py``) reads them: Gemini Flash-Lite while the daily
budget lasts, the workflow's local Qwen3-VL step for the rest, with every
page's answer cached so an unchanged booklet costs no requests. Only cards
with a legible sale price and high model confidence are published.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from scripts.booklet_vision import (
        PageReader, VisionCache, gemini_client, image_page, pending_dir_from_env, read_booklet,
    )
    from scripts.common import write_feed
    from scripts.product_identity import build_product_groups
    from scripts.stores import by_id
except ModuleNotFoundError:
    from booklet_vision import (
        PageReader, VisionCache, gemini_client, image_page, pending_dir_from_env, read_booklet,
    )
    from common import write_feed
    from product_identity import build_product_groups
    from stores import by_id

STORE = by_id("beta")

STORE_PAGE = "https://mazacena.lv/veikali/beta/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/beta-offers.json")
RETRIES = 3
BOOKLET_PATH_RE = re.compile(r"/akcijas-buklets/(\d+)-beta-akcijas-buklets\.html/?$", re.I)
BOOKLET_IMAGE_RE = re.compile(r"beta.*?_(\d+)\.(webp|png|jpe?g)$", re.I)
IMAGE_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg"}
DATE_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(?:(\d{4})\s*)?(?:-|–|līdz)\s*"
    r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)",
    re.I,
)


def session() -> requests.Session:
    current = requests.Session()
    current.headers.update(HEADERS)
    return current


def fetch(current: requests.Session, url: str) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            response = current.get(url, timeout=60)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < RETRIES:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Beta fetch {url} failed after {RETRIES} attempts: {last_error}")


def discover_booklet(store_html: str) -> str:
    soup = BeautifulSoup(store_html, "html.parser")
    for link in soup.find_all("a", href=True):
        href = urljoin(STORE_PAGE, link["href"])
        if BOOKLET_PATH_RE.search(urlparse(href).path):
            return href
    raise RuntimeError("No Beta booklet link found on the store page")


def discover_page_images(page_html: str, booklet_url: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    candidates: list[str] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(booklet_url, link["href"])
        path = urlparse(href).path
        if Path(path).suffix.lower() in IMAGE_EXTENSIONS and BOOKLET_IMAGE_RE.search(Path(path).name):
            candidates.append(href)
    if not candidates:
        for image in soup.find_all("img"):
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                value = image.get(attr)
                if not value:
                    continue
                href = urljoin(booklet_url, value)
                path = urlparse(href).path
                if Path(path).suffix.lower() in IMAGE_EXTENSIONS and BOOKLET_IMAGE_RE.search(Path(path).name):
                    candidates.append(href)
    numbered: dict[int, str] = {}
    for url in candidates:
        match = BOOKLET_IMAGE_RE.search(Path(urlparse(url).path).name)
        if match:
            numbered.setdefault(int(match.group(1)), url)
    return [numbered[number] for number in sorted(numbered)]


def parse_booklet(page_html: str, url: str) -> dict | None:
    soup = BeautifulSoup(page_html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    match = DATE_RANGE_RE.search(page_text)
    if not match:
        raise RuntimeError("Could not read the Beta booklet validity dates")

    start_day, start_month, start_year, end_day, end_month, end_year = match.groups()
    start = date(int(start_year or end_year), int(start_month), int(start_day))
    end = date(int(end_year), int(end_month), int(end_day))
    if end < date.today():
        return None

    heading = soup.find("h1")
    name = heading.get_text(" ", strip=True) if heading else "Beta akciju buklets"
    name = DATE_RANGE_RE.sub("", name).strip(" -–") or "Beta akciju buklets"
    booklet_id = BOOKLET_PATH_RE.search(urlparse(url).path)
    stable_id = booklet_id.group(1) if booklet_id else "current"
    return {
        "id": f"beta-{stable_id}",
        "name": name,
        "source": url,
        "validFrom": start.isoformat(),
        "validUntil": end.isoformat(),
    }


def image_mime(url: str, response: requests.Response) -> str:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type in {"image/webp", "image/png", "image/jpeg"}:
        return content_type
    suffix = Path(urlparse(url).path).suffix.lower()
    return {".webp": "image/webp", ".png": "image/png"}.get(suffix, "image/jpeg")


def _finalise_payload(payload: dict) -> None:
    unique_offers = {item["offerId"]: item for item in payload.get("offers", [])}
    offers = sorted(unique_offers.values(), key=lambda item: (item["name"].casefold(), item["price"]))
    unique_reviews = {item["offerId"]: item for item in payload.get("reviewOffers", [])}
    reviews = sorted(unique_reviews.values(), key=lambda item: item["name"].casefold())
    payload["offers"] = offers
    payload["reviewOffers"] = reviews
    products = build_product_groups(offers) if offers else []
    payload["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    for catalogue in payload.get("catalogues", []):
        catalogue["verifiedOffers"] = sum(1 for item in offers if item.get("campaignId") == catalogue.get("id"))
        catalogue["reviewOffers"] = sum(1 for item in reviews if item.get("campaignId") == catalogue.get("id"))
    payload["summary"] = {
        "offerVariants": len(offers),
        "uniqueProducts": len(products),
        "reviewOffers": len(reviews),
        "catalogues": len(payload.get("catalogues", [])),
    }
    write_feed(OUT, payload)


def booklet_meta(booklet: dict) -> dict:
    return {
        "campaign": booklet["name"], "campaignId": booklet["id"], "source": booklet["source"],
        "validFrom": booklet["validFrom"], "validUntil": booklet["validUntil"],
    }


def main() -> None:
    current = session()
    store_html = fetch(current, STORE_PAGE).text
    booklet_url = discover_booklet(store_html)
    booklet_html = fetch(current, booklet_url).text
    booklet = parse_booklet(booklet_html, booklet_url)
    if booklet is None:
        payload = {
            "source": STORE_PAGE, "store": "beta",
            "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "catalogues": [], "offers": [], "reviewOffers": [],
            "summary": {"offerVariants": 0, "uniqueProducts": 0, "reviewOffers": 0, "catalogues": 0},
        }
        write_feed(OUT, payload)
        print("No active Beta booklet")
        return

    image_urls = discover_page_images(booklet_html, booklet_url)
    if not image_urls:
        raise RuntimeError(f"No page images found in Beta booklet {booklet_url}")
    booklet["pageCount"] = len(image_urls)
    meta = booklet_meta(booklet)
    pages = []
    for number, image_url in enumerate(image_urls, start=1):
        response = fetch(current, image_url)
        pages.append(image_page(response.content, number, meta, image_mime(image_url, response), image_url))

    cache = VisionCache(STORE.id)
    try:
        with gemini_client(STORE, max_requests=len(pages)) as client:
            outcome = read_booklet(pages, PageReader(STORE, cache, client, pending_dir_from_env()))
    finally:
        cache.save()
    catalogue = {
        **booklet,
        "processedPages": sorted(outcome.resolved),
        "complete": outcome.pending == 0,
    }
    payload = {
        "source": STORE_PAGE, "store": "beta",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogues": [catalogue], "offers": outcome.offers,
        "reviewOffers": outcome.reviews, "summary": {},
    }
    _finalise_payload(payload)
    print(f"Saved {len(payload['offers'])} priced offers ({len(payload['reviewOffers'])} review) to {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Beta booklet scraper failed: {exc}", file=sys.stderr)
        raise
