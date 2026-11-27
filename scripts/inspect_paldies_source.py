#!/usr/bin/env python3
"""Temporary diagnostics for the public Maxima PALDIES catalogue markup."""
from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)"
}
URLS = [
    "https://www.maxima.lv/paldies",
    "https://www.maxima.lv/piedavajumi/",
]


def inspect_all_paldies_offers(session: requests.Session) -> None:
    page_url = "https://www.maxima.lv/paldies"
    session.get(page_url, headers=HEADERS, timeout=30)
    url = "https://www.maxima.lv/ajax-paldies-login-offers"
    ajax_headers = {
        **HEADERS,
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
    }
    response = session.get(url, headers=ajax_headers, timeout=45)
    print(f"\n===== AJAX {url} =====")
    print(f"status={response.status_code} final_url={response.url} content_type={response.headers.get('content-type')} bytes={len(response.content)}")
    if response.status_code >= 400:
        print("error_excerpt=", re.sub(r"\s+", " ", response.text)[:2400])
        return
    soup = BeautifulSoup(response.text, "html.parser")
    product_images = [
        image for image in soup.find_all("img")
        if (image.get("alt") or "").strip()
        and (image.get("alt") or "").strip().casefold() not in {"paldies", "image", "maxima"}
    ]
    names = [(image.get("alt") or "").strip() for image in product_images]
    unique_names = list(dict.fromkeys(names))
    print(f"product_images={len(product_images)} unique_alt_names={len(unique_names)}")
    print("first_names=", unique_names[:12])
    print("last_names=", unique_names[-5:])
    print("links=", [
        {"text": a.get_text(" ", strip=True)[:80], "href": urljoin(url, a["href"])}
        for a in soup.find_all("a", href=True)[:12]
    ])
    if not product_images:
        print("response_excerpt=", re.sub(r"\s+", " ", response.text)[:1800])


def describe_page(url: str, session: requests.Session) -> None:
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    print(f"\n===== PAGE {url} =====")
    print(f"status={response.status_code} final_url={response.url} bytes={len(response.content)}")

    for form in soup.find_all("form"):
        print("FORM", {
            "action": urljoin(response.url, form.get("action", "")),
            "method": form.get("method", "get"),
            "id": form.get("id"),
            "class": form.get("class"),
        })
        for field in form.find_all(["input", "select", "button"]):
            if not isinstance(field, Tag):
                continue
            print(" FIELD", {
                "tag": field.name,
                "type": field.get("type"),
                "name": field.get("name"),
                "value": field.get("value"),
                "id": field.get("id"),
                "class": field.get("class"),
                "text": field.get_text(" ", strip=True)[:100],
                "data": {k: v for k, v in field.attrs.items() if k.startswith("data-")},
            })

    print("-- PALDIES / offer related links --")
    for anchor in soup.find_all("a", href=True):
        label = anchor.get_text(" ", strip=True)
        href = urljoin(response.url, anchor["href"])
        if any(term in (label + " " + href).casefold() for term in
               ("paldies", "piedāvājum", "piedavajum", "cenu līderi", "cenu-lideri")):
            print(" LINK", {"label": label[:100], "href": href,
                           "class": anchor.get("class"), "data": {
                               k: v for k, v in anchor.attrs.items() if k.startswith("data-")
                           }})

    print("-- FILTER CONTROLS --")
    for field in soup.find_all(["input", "select", "button"]):
        if not isinstance(field, Tag):
            continue
        description = " ".join([
            str(field.get("name", "")), str(field.get("id", "")),
            str(field.get("value", "")), field.get_text(" ", strip=True),
            " ".join(field.get("class", [])),
        ]).casefold()
        if any(term in description for term in ("paldies", "filter", "category", "category", "format")):
            print(" CONTROL", {
                "tag": field.name, "type": field.get("type"), "name": field.get("name"),
                "value": field.get("value"), "id": field.get("id"), "class": field.get("class"),
                "data": {k: v for k, v in field.attrs.items() if k.startswith("data-")},
            })

    print("-- SCRIPTS --")
    for script in soup.find_all("script"):
        src = script.get("src")
        if src:
            print(" SCRIPT_SRC", urljoin(response.url, src))
            continue
        inline = script.string or script.get_text()
        if inline and re.search(r"paldies|offer|product|ajax|filter|pagination", inline, re.I):
            print(" INLINE_SCRIPT", re.sub(r"\s+", " ", inline)[:1800])

    print("-- PAGE COUNT / DATA ATTRIBUTES --")
    for node in soup.find_all(True):
        text = node.get_text(" ", strip=True) if node.name in {"h1", "h2", "h3", "a", "button"} else ""
        attrs = {k: v for k, v in node.attrs.items() if k.startswith("data-")}
        if ("piedāvājumi ar paldies" in text.casefold() or
            ("paldies" in " ".join(str(v) for v in attrs.values()).casefold())):
            print(" NODE", node.name, {"text": text[:200], "attrs": attrs,
                                       "class": node.get("class"), "id": node.get("id")})


if __name__ == "__main__":
    session = requests.Session()
    inspect_all_paldies_offers(session)
    for page_url in URLS:
        describe_page(page_url, session)
