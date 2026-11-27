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
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    print("payload_type=", type(payload).__name__)
    if isinstance(payload, dict):
        print("payload_keys=", list(payload.keys()))
        for key, value in payload.items():
            if isinstance(value, str):
                print("payload_field=", key, "type=str", "length=", len(value),
                      "excerpt=", re.sub(r"\s+", " ", value)[:700])
            elif isinstance(value, list):
                print("payload_field=", key, "type=list", "length=", len(value),
                      "sample=", str(value[:2])[:500])
            else:
                print("payload_field=", key, "type=", type(value).__name__,
                      "value=", str(value)[:500])
        html_candidates = [
            value for value in payload.values()
            if isinstance(value, str) and ("<" in value or "offer-item" in value)
        ]
        markup = max(html_candidates, key=len) if html_candidates else ""
    elif isinstance(payload, str):
        markup = payload
    else:
        markup = ""
    soup = BeautifulSoup(markup, "html.parser")
    product_images = [
        image for image in soup.find_all("img")
        if (image.get("alt") or "").strip()
        and (image.get("alt") or "").strip().casefold() not in {"paldies", "image", "maxima"}
    ]
    names = [(image.get("alt") or "").strip() for image in product_images]
    unique_names = list(dict.fromkeys(names))
    print(f"product_images={len(product_images)} unique_alt_names={len(unique_names)}")
    print("counts=", {
        "offer_item": len(soup.select(".offer-item")),
        "item": len(soup.select(".item")),
        "swiper_slide": len(soup.select(".swiper-slide")),
        "offer_card": len(soup.select(".offer-card")),
        "product": len(soup.select("[data-product-id]")),
    })
    for node in soup.select(".swiper-slide, .offer-item, .item")[:4]:
        print("CARD_SAMPLE", node.name, node.get("class"), node.get_text(" ", strip=True)[:350])
    print("pagination_text=", [node.get_text(" ", strip=True) for node in soup.select("button, a") if any(k in node.get_text(" ", strip=True).casefold() for k in ("next", "atpaka", "nākam"))][:12])
    print("first_names=", unique_names[:12])
    print("last_names=", unique_names[-5:])
    if not product_images:
        print("response_excerpt=", re.sub(r"\s+", " ", markup or response.text)[:1800])



def inspect_filtered_paldies(session: requests.Session) -> None:
    page_url = "https://www.maxima.lv/piedavajumi/"
    response = session.get(page_url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    print("\n===== FILTERED PALDIES CATALOGUE =====")
    for element_id in ("ajax-load-more-url", "totalProducts", "filter_limit", "filter_sort_by"):
        node = soup.find(id=element_id)
        print("GLOBAL_FIELD", element_id, node.attrs if isinstance(node, Tag) else None)
    if not soup.find(id="ajax-load-more-url"):
        print("No ajax-load-more-url found in initial page")
        return

    ajax_url = urljoin(response.url, soup.find(id="ajax-load-more-url").get("value", ""))
    options = [
        {"value": option.get("value"), "text": option.get_text(" ", strip=True)}
        for option in soup.select("#filter_limit option")
    ]
    print("AJAX_URL", ajax_url, "LIMIT_OPTIONS", options)
    for element_id in ("totalProducts", "ajax-load-more-url"):
        print("GLOBAL_FIELD", element_id, (soup.find(id=element_id).attrs if soup.find(id=element_id) else None))
    for button in soup.select(".lv-load-more, [data-page], [data-offset]"):
        print("LOAD_MORE_CONTROL", button.name, button.attrs, button.get_text(" ", strip=True)[:120])
    for script in soup.find_all("script"):
        inline = script.string or script.get_text()
        if inline and any(term in inline for term in ("ajax-load-more-url", "totalProducts", "lv-load-more", "salesloadmore")):
            print("INLINE_OFFERS_SCRIPT", re.sub(r"\\s+", " ", inline)[:10000])

    params = [
        ("filters[]", "18"),
        ("sort_by", "newest"),
        ("limit", "75"),
        ("ajax", "1"),
    ]
    headers = {
        **HEADERS,
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    result = session.get(ajax_url, params=params, headers=headers, timeout=45)
    print("FILTERED_RESPONSE", result.status_code, result.url,
          result.headers.get("content-type"), len(result.content))
    if result.status_code >= 400:
        print("FILTERED_ERROR", re.sub(r"\s+", " ", result.text)[:1600])
        return
    try:
        payload = result.json()
    except ValueError:
        print("FILTERED_NOT_JSON", re.sub(r"\s+", " ", result.text)[:1600])
        return
    print("FILTERED_JSON_KEYS", list(payload) if isinstance(payload, dict) else type(payload).__name__)
    if not isinstance(payload, dict):
        return
    print("FILTERED_TOTAL", payload.get("totalItems"), "MORE", payload.get("hasMore"),
          "OTHER_FIELDS", {key: value for key, value in payload.items() if key != "html" and not isinstance(value, (dict, list))})
    markup = payload.get("html", "")
    print("FILTERED_HTML_LENGTH", len(markup))
    result_soup = BeautifulSoup(markup, "html.parser")
    items = result_soup.select(".item")
    names = []
    for item in items:
        image = item.find("img", alt=True)
        if image and image.get("alt", "").strip():
            names.append(image["alt"].strip())
    print("FILTERED_ITEMS", len(items), "NAMES", len(names), "UNIQUE_NAMES", len(set(names)))
    print("FILTERED_FIRST", [n for n in names if n.casefold() != "paldies"][:8])
    print("FILTERED_LAST", [n for n in names if n.casefold() != "paldies"][-5:])
    for item in items[:2]:
        print("ITEM_STRUCT", item.attrs, [(img.get("alt"), img.get("src")) for img in item.find_all("img")[:4]])

    asset_url = "https://www.maxima.lv/js/front/pages/offers/offers.js?1793968385"
    asset_urls = [
        "https://www.maxima.lv/js/front/offers.js?1793968385",
        "https://www.maxima.lv/js/front/pages/offers/offers.js?1793968385",
        "https://www.maxima.lv/js/front/app/offers.js?1793968385",
    ]
    for asset_url in asset_urls:
        asset = session.get(asset_url, headers=HEADERS, timeout=30)
        print("OFFERS_JS", asset_url, asset.status_code, len(asset.content))
        js = asset.text
        for term in ("ajax-load-more-url", "totalProducts", "lv-load-more", "offset", "skip", "loadMore", "salesloadmore"):
            positions = [m.start() for m in re.finditer(re.escape(term), js, flags=re.IGNORECASE)]
            for position in positions[:3]:
                print("OFFERS_JS_CONTEXT", asset_url, term,
                      re.sub(r"\s+", " ", js[max(0, position-550):position+1200]))

    # Probe typical paging parameters without changing the source filter.
    for key, value in (("offset", "0"), ("offset", "75"), ("offset", "150"),
                       ("offset", "225"), ("page", "2"), ("start", "75"),
                       ("skip", "75"), ("pageNumber", "2"), ("from", "75")):
        candidate_params = [
            ("filters[]", "18"), ("sort_by", "newest"), ("limit", "75"), ("ajax", "1"),
            (key, value),
        ]
        candidate = session.get(ajax_url, params=candidate_params, headers=headers, timeout=45)
        try:
            candidate_json = candidate.json()
            candidate_html = candidate_json.get("html", "")
            candidate_soup = BeautifulSoup(candidate_html, "html.parser")
            ids = [
                node.get("data-product-id")
                for node in candidate_soup.select(".item[data-product-id]")
            ]
            print("PAGE_PROBE", key, value, "status", candidate.status_code,
                  "total", candidate_json.get("totalItems"), "count", len(ids),
                  "first_ids", ids[:5], "last_ids", ids[-5:])
        except (ValueError, AttributeError):
            print("PAGE_PROBE", key, value, "status", candidate.status_code,
                  "response", re.sub(r"\s+", " ", candidate.text)[:300])

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

    print("-- FILTER FORM DETAILS --")
    filter_form = soup.find(id="sales-filters-form")
    if filter_form:
        for field in filter_form.find_all(["input", "select", "button"]):
            if not isinstance(field, Tag):
                continue
            parent = field.parent
            print(" FILTER_FIELD", {
                "tag": field.name, "type": field.get("type"), "name": field.get("name"),
                "value": field.get("value"), "id": field.get("id"), "class": field.get("class"),
                "parent_text": parent.get_text(" ", strip=True)[:180] if isinstance(parent, Tag) else "",
                "parent_class": parent.get("class") if isinstance(parent, Tag) else None,
            })
        for node in filter_form.select("#ajax-load-more-url, #totalProducts, #filter_limit"):
            print(" HIDDEN_OR_LIMIT", node.name, node.attrs)
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
            if "loadAllOffers" in inline:
                match = re.search(r"function loadAllOffers\s*\(\).*?(?=\$\( document \)|$)", inline, re.S)
                snippet = match.group(0) if match else inline
                print(" INLINE_LOAD_ALL", re.sub(r"\s+", " ", snippet)[:7000])

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
    inspect_filtered_paldies(session)
    for page_url in URLS:
        describe_page(page_url, session)
