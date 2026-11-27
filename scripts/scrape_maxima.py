#!/usr/bin/env python3
"""Conservative collector for public Maxima Latvia offer cards."""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://www.maxima.lv/piedavajumi/"
OUT = Path("data/offers.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)"
}
# Maxima sometimes renders prices as separate whole/euro-cent text nodes.
PRICE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,3})(?:[,.]\s*|\s+)(\d{2})(?!\d)")
DISCOUNT_RE = re.compile(r"-\s*(\d{1,2})\s*%")
DATE_RE = re.compile(
    r"(\d{1,2})[.](\d{1,2})[.]?\s*[-–]\s*(\d{1,2})[.](\d{1,2})[.]?(?:\s*(\d{4}))?"
)


def prices_from(text: str) -> list[float]:
    # Remove unit prices like "(3,31 €/kg)" so they are not mistaken for shelf prices.
    cleaned = re.sub(r"\([^)]*€\s*/\s*[^)]*\)", " ", text)
    values = [
        int(euros) + int(cents) / 100
        for euros, cents in PRICE_TOKEN_RE.findall(cleaned)
    ]
    result = []
    for value in values:
        if 0.01 <= value <= 9999 and value not in result:
            result.append(round(value, 2))
    return result


def offer_dates(text: str):
    match = DATE_RE.search(text)
    if not match:
        return None, None
    d1, m1, d2, m2, year = match.groups()
    y = int(year or datetime.now().year)
    try:
        start = date(y, int(m1), int(d1))
        end = date(y, int(m2), int(d2))
        if end < start:
            end = date(y + 1, int(m2), int(d2))
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None, None


def main():
    response = requests.get(URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    valid_from, valid_until = offer_dates(page_text)
    found = {}

    for img in soup.find_all("img"):
        name = (img.get("alt") or "").strip()
        if len(name) < 4 or name.lower() in {"paldies", "image", "maxima"}:
            continue

        node = img
        candidates = []
        for _ in range(7):
            if node is None:
                break
            card_text = node.get_text(" ", strip=True)
            prices = prices_from(card_text)
            if prices and len(card_text) < 1800:
                candidates.append((len(card_text), card_text, prices, node))
            node = node.parent

        if not candidates:
            continue

        _, card_text, prices, card = min(candidates, key=lambda item: item[0])
        price = prices[0]
        regular = prices[1] if len(prices) > 1 and prices[1] > price else None
        discount_match = DISCOUNT_RE.search(card_text)
        discount = int(discount_match.group(1)) if discount_match else (
            round((1 - price / regular) * 100) if regular else None
        )
        image_src = img.get("src") or img.get("data-src")
        image = urljoin(URL, image_src) if image_src else None
        link = card.find("a", href=True)
        source = urljoin(URL, link["href"]) if link else URL

        found[(name.casefold(), price)] = {
            "name": name,
            "brand": None,
            "image": image,
            "price": price,
            "regularPrice": regular,
            "unit": None,
            "discountPercent": discount,
            "validFrom": valid_from,
            "validUntil": valid_until,
            "city": None,
            "store": "Maxima",
            "paldis": "PALDIES" in card_text.upper(),
            "source": source,
            "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    offers = sorted(found.values(), key=lambda item: (item["name"].casefold(), item["price"]))
    if not offers:
        raise RuntimeError(
            "No priced product cards detected; refusing to overwrite the existing feed."
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": URL,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "offers": offers,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} offers to {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Scraper failed: {exc}", file=sys.stderr)
        raise
