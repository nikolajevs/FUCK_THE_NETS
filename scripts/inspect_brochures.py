#!/usr/bin/env python3
"""Temporary inspection of active Maxima brochure catalogue PDFs."""
from __future__ import annotations

import re
import requests
import fitz
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin

BASE = "https://www.maxima.lv"
URL = BASE + "/bukleti"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LatviaOffersBot/1.0; +https://github.com/nikolajevs/FUCK_THE_NETS)"}


def main():
    session = requests.Session()
    response = session.get(URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    print("INDEX", response.status_code, len(response.content))

    brochures = []
    for anchor in soup.select('a[href*="/bukleti/"]'):
        href = urljoin(BASE, anchor.get("href", ""))
        if href.rstrip("/") == URL.rstrip("/"):
            continue
        node = anchor
        for _ in range(7):
            parent = node.parent
            if not isinstance(parent, Tag):
                break
            node = parent
            if node.name in {"article", "li"} or any(
                "campaign" in str(c).casefold() or "booklet" in str(c).casefold()
                for c in node.get("class", [])
            ):
                break
        title = ""
        for heading in node.find_all(["h1", "h2", "h3", "h4"]):
            title = heading.get_text(" ", strip=True)
            if title:
                break
        text = node.get_text(" ", strip=True)
        if not title:
            title = anchor.get("title") or anchor.get_text(" ", strip=True)
        if href not in {item["detail"] for item in brochures}:
            brochures.append({"title": title, "detail": href, "cardText": text[:300]})
    print("BROCHURE_COUNT", len(brochures))

    for item in brochures:
        detail = session.get(item["detail"], headers=HEADERS, timeout=30)
        detail.raise_for_status()
        detail_soup = BeautifulSoup(detail.text, "html.parser")
        pdf_links = []
        for a in detail_soup.find_all("a", href=True):
            href = urljoin(detail.url, a["href"])
            if ".pdf" in href.casefold():
                pdf_links.append(href)
        pdf_links = list(dict.fromkeys(pdf_links))
        title = detail_soup.find(["h1", "h2"])
        heading = title.get_text(" ", strip=True) if title else item["title"]
        body_text = detail_soup.get_text(" ", strip=True)
        print("\nBROCHURE", {"title": heading, "url": detail.url, "pdfs": pdf_links[:5],
                              "storeScope": body_text[body_text.casefold().find("piedāvājums spēkā"):
                                                       body_text.casefold().find("piedāvājums spēkā")+180]
                              if "piedāvājums spēkā" in body_text.casefold() else None})
        for pdf_url in pdf_links[:2]:
            try:
                pdf_response = session.get(pdf_url, headers=HEADERS, timeout=90)
                pdf_response.raise_for_status()
                doc = fitz.open(stream=pdf_response.content, filetype="pdf")
                all_text = "\n".join(f"--- PAGE {i+1} ---\n{page.get_text('text', sort=True)}"
                                     for i, page in enumerate(doc))
                print("PDF_META", {"url": pdf_url, "status": pdf_response.status_code,
                                   "bytes": len(pdf_response.content), "pages": len(doc),
                                   "textChars": len(all_text)})
                print("PDF_TEXT_SAMPLE", re.sub(r"\s+", " ", all_text)[:5500])
            except Exception as exc:
                print("PDF_ERROR", pdf_url, type(exc).__name__, str(exc)[:400])


if __name__ == "__main__":
    main()
