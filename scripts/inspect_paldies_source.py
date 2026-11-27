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


def describe_page(url: str) -> None:
    response = requests.get(url, headers=HEADERS, timeout=30)
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
    for page_url in URLS:
        describe_page(page_url)
