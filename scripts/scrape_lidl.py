#!/usr/bin/env python3
"""Collect Lidl Latvia (lidl.lv) promotional offers from weekly booklet PDFs.

Lidl's Latvian storefront does not expose a structured product feed (the leaflet
API returns ``products: []`` and ``showProductDetails: false``), so offers are
read from the booklet PDF text layer with word coordinates, the same approach
used for Mego.

Discovery:

- the booklet landing page ``https://www.lidl.lv/c/lv-LV/akciju-bukleti/s10024390``
  links to every active leaflet viewer ``/l/lv/akciju-buklets/<slug>/ar/0``;
- for each slug the public leaflet API
  ``GET https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier=<slug>&region_id=0&region_code=0``
  returns the booklet name, validity dates and a direct ``pdfUrl``.

Extraction (per PDF page):

- Lidl's sale-price font distinguishes the actual price from large crossed-out
  regular prices;
- columns are clustered from the anchor x positions; within a column the product
- name is matched to a bold condensed title block with a minimum-distance
  assignment;
- the crossed-out regular price is the small ``LidlFontPro-Book`` decimal over
  the right half of the sale price; when the card also prints a ``-NN%`` badge
  the pair must agree with it (badges alone never create a discount: Lidl also
  prints "2. precei -61%" second-item badges); unit prices such as
  ``1 kg = 16,14 €`` sit inside the name block and are ignored;
- the pack size line under the title ("200 g", "36 x 13 g") is appended to the
  name so product identity keeps sizes apart, leading all-caps words become the
  brand, and a big "3 gab." before the price becomes ``multiBuy``;
- ``Ar Lidl Plus`` near the card flags ``requiresCard`` (card name ``Lidl Plus``).

Only cards with a clean name and a finite sale price are promoted to ``offers``;
ambiguous cards (empty/short/merged names, marketing copy, date banners) go to
``reviewOffers`` and are not shown on the site. Regular prices are kept only when
confidently detected, otherwise ``null`` — the comparator never guesses a
discount from a unit price.

Lidl publishes every booklet issue under a new PDF URL, and the seasonal
catalogues are 100+ MB. A booklet whose PDF URL and parser code (this file,
booklet_images.py, product_identity.py) are unchanged since the previous run is
not downloaded again: its offers are carried over from the previous feed. The
wine catalogue is detected by its statutory alcohol notice and not listed.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

import requests

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

try:
    from scripts.booklet_images import prune_images, save_product_photo
except ModuleNotFoundError:
    from booklet_images import prune_images, save_product_photo

BASE = "https://www.lidl.lv"
BOOKLETS_PAGE = BASE + "/c/lv-LV/akciju-bukleti/s10024390"
LEAFLET_API = "https://endpoints.leaflets.schwarz/v4/flyer"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/lidl-offers.json")
_SCRIPTS = Path(__file__).resolve().parent
# Any change to the parsing code invalidates the per-booklet cache.
PARSER_VERSION = hashlib.sha256(b"".join(
    (_SCRIPTS / name).read_bytes().replace(b"\r\n", b"\n")  # same on Windows and CI
    for name in ("scrape_lidl.py", "booklet_images.py", "product_identity.py")
)).hexdigest()[:12]

DEC_RE = re.compile(r"^(\d{1,3})[,.](\d{2})$")
DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.")
BADGE_RE = re.compile(r"^-(\d{1,2})\s?%$")
_QTY = r"(?:\d+\s*[x×]\s*)?\d+(?:[,.]\d+)?\s*(?:kg|g|ml|cl|l|gab\.?|m|cm|mazg\.)"
# The small line under a title that states the pack ("200 g", "36 x 13 g",
# "400 g vai 500 g", "1 kg iepakojums"). Unit prices contain "=" and are not
# matched; neither are descriptions such as "dažādi veidi".
SIZE_LINE_RE = re.compile(
    rf"^({_QTY}(?:\s*(?:vai|/)\s*{_QTY})?)(?:\s+(?:iepakojums|iepak\.|neto))?$", re.I)
# Leading all-caps words that are labels rather than brands.
NOT_BRANDS = {"BIO", "XXL", "XL", "DUO", "MINI", "MAXI", "LED", "USB", "EKO"}
NOISE_TOK = {
    "€", "/", "=", "ar", "lidl", "plus", "no", "vai", "gab.", "kg", "g", "ml", "l",
    "mazg.", "slāņi", "loksnes", "iepak.", "gab", "cm", "mm", "den", "x", "×", "un",
}
NOISE_SUB = (
    "pirmdien", "nedēļ", "ierobežots", "www.", "lidl.lv", "atlaide", "nesummējas",
    "piemērota", "lētākajam", "izvietotas", "stendā", "produktu", "izlasei", "precei",
    "pērkot", "atlasītajam", "atlasīto", "nenokavē", "esmaspäev", "kuni", "vērtīgi",
    "ietaupīt", "cena", "lētāk", "piedāvājums", "spēkā", "izmantojot", "lietotni",
    "konkursa", "uzvarētājs", "pasaulē", "novērtēta", "marm", "sulīgiem", "challenge",
    "izlase", "akcija", "svaigi", "augļi", "dārzeņi",
)

# These offer titles share one printed heading across several price variants,
# or are placed in a separate text block that the PDF geometry cannot associate
# reliably. Values were checked against the artwork of the active Lidl Latvia
# booklets; keys use the stable booklet slug, printed page, and sale price.
TITLE_OVERRIDES = {
    ("aktualais-akciju-buklets-piedavajumi-no-23-11", 13, 2.29):
        "MAC&CHEESE Četru sieru makaroni vai makaroni ar Čedaras sieru",
    ("aktualais-akciju-buklets-piedavajumi-no-23-11", 14, 4.99):
        "NOWACO Exclusive Baltkāju garneles",
    ("edienu-idejas-ziemassvetkiem-2026", 40, 1.29):
        "FAVORINA Piena šokolādes figūriņa 75 g",
    ("edienu-idejas-ziemassvetkiem-2026", 40, 2.49):
        "FAVORINA Piena šokolādes figūriņa 125 g",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 16, 5.99):
        "LIVARNO Džersija auduma palags ar gumiju",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 16, 6.99):
        "LIVARNO Džersija auduma palags ar gumiju",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 16, 8.99):
        "ESMARA Pidžama sievietēm vai vīriešiem",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 16, 9.99):
        "LIVARNO Smalka flaneļa gultas veļa",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 16, 12.99):
        "LIVARNO Smalka flaneļa gultas veļa",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 16, 15.99):
        "LIVARNO Smalka flaneļa gultas veļa",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 17, 6.99):
        "LIVARNO Spilvens",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 17, 11.99):
        "LIVARNO Sega",
    ("nepartikas-precu-piedavajums-piedavajumi-no-23-11", 17, 14.99):
        "LIVARNO Sega",
    ("rotallietas-2026", 21, 7.99):
        "HOT WHEELS / ENCHANTIMALS Rotaļu automašīna Monster Trucks vai lelle ar mājdzīvnieku",
    ("rotallietas-2026", 26, 1.29):
        "FAVORINA Piena šokolādes figūriņa 75 g",
    ("rotallietas-2026", 26, 2.49):
        "FAVORINA Piena šokolādes figūriņa 125 g",
    ("rotallietas-2026", 29, 1.89):
        "FAVORINA Adventes kalendārs",
    ("rotallietas-2026", 29, 2.49):
        "WINDEL Adventes kalendārs",
    ("rotallietas-2026", 29, 0.49):
        "Karameļu spieķītis",
    ("rotallietas-2026", 29, 0.99):
        "Zefīrs uz kociņa",
    ("rotallietas-2026", 29, 1.49):
        "FAVORINA Zefīri",
    ("rotallietas-2026", 32, 2.99):
        "CRELANDO Dimanta mozaīkas vai gleznošanas komplekts",
}


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def dec_val(token: str) -> float | None:
    m = DEC_RE.match(token)
    if not m:
        return None
    value = float(f"{m.group(1)}.{m.group(2)}")
    return value if 0.10 < value < 200 else None


def _cluster(values: list[float], gap: float) -> list[float]:
    groups: list[list[float]] = []
    for v in sorted(values):
        if not groups or v - groups[-1][-1] > gap:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [sum(g) / len(g) for g in groups]


def discover_booklets(sess: requests.Session) -> list[dict]:
    """Find active leaflet slugs and resolve their metadata + PDF via the API."""
    r = sess.get(BOOKLETS_PAGE, timeout=30)
    r.raise_for_status()
    slugs = sorted(set(re.findall(r"/l/lv/akciju-buklets/([a-z0-9-]+)/ar/0", r.text)))
    booklets: list[dict] = []
    for slug in slugs:
        try:
            ar = sess.get(LEAFLET_API, params={
                "flyer_identifier": slug, "region_id": "0", "region_code": "0",
            }, timeout=30)
            ar.raise_for_status()
            flyer = (ar.json() or {}).get("flyer") or {}
        except Exception as exc:
            print(f"  ! {slug}: {exc}", file=sys.stderr)
            continue
        if not flyer.get("pdfUrl") or not flyer.get("isActive"):
            continue
        booklets.append({
            "id": slug,
            "name": flyer.get("name") or slug,
            "title": flyer.get("title") or "",
            "pdf": flyer["pdfUrl"],
            "preview": flyer.get("thumbnailUrl"),
            "validFrom": flyer.get("offerStartDate") or flyer.get("startDate"),
            "validUntil": flyer.get("offerEndDate") or flyer.get("endDate"),
            "status": flyer.get("status"),
        })
    # Lidl may expose evergreen assortment catalogues as active flyers with a
    # placeholder end date (for example 2222-06-29). They are not current
    # promotional booklets and waste a full PDF download on every refresh.
    current: list[dict] = []
    for booklet in booklets:
        start, end = booklet.get("validFrom"), booklet.get("validUntil")
        if start and end:
            try:
                start_date = date.fromisoformat(str(start)[:10])
                end_date = date.fromisoformat(str(end)[:10])
            except ValueError:
                current.append(booklet)
                continue
            if end_date < start_date or (end_date - start_date).days > 366:
                continue
        current.append(booklet)
    return current


def _clean_name(parts: list[str]) -> str:
    text = re.sub(r"\s+", " ", " ".join(parts)).strip(" -–•*:,+|")
    tokens = text.split()
    # collapse consecutive duplicate words (PILOS PILOS -> PILOS)
    dedup: list[str] = []
    for tok in tokens:
        if not dedup or dedup[-1].casefold() != tok.casefold():
            dedup.append(tok)
    # A/B/C markers printed beside product variants can bleed into the PDF
    # text layer before the actual name ("A B C LEXIBOOK ..."). Drop that
    # leading marker run, and a lone marker when it immediately precedes an
    # all-caps brand. Keep meaningful labels such as "B daļas ...".
    marker_count = 0
    for tok in dedup:
        if re.fullmatch(r"[A-ZĀČĒĢĪĶĻŅŠŪŽ]", tok):
            marker_count += 1
        else:
            break
    if marker_count >= 2 or (
        marker_count == 1 and len(dedup) > 1 and dedup[1].isupper()
    ):
        dedup = dedup[marker_count:]
    return " ".join(dedup)[:90]


def brand_from_name(name: str) -> str | None:
    """Leading all-caps words of a Lidl title ("DELUXE Makarūnu izlase")."""
    tokens = name.split()
    brand: list[str] = []
    for token in tokens:
        letters = re.sub(r"[^A-Za-zĀ-ž]", "", token)
        if len(letters) >= 2 and letters == letters.upper():
            brand.append(token)
        else:
            break
    if not brand or len(brand) == len(tokens) or " ".join(brand) in NOT_BRANDS:
        return None
    return " ".join(brand)


def name_with_size(name: str, size: str | None) -> str:
    """Append the printed pack size unless the title already carries it."""
    if not size:
        return name
    number = re.match(r"\d+(?:[,.]\d+)?", size)
    if number and re.search(rf"(?<![\d,.]){re.escape(number.group(0))}(?![\d,.])", name):
        return name
    return f"{name} {size}"


def pick_regular_price(anchor: dict, candidates: list[dict]) -> float | None:
    """The crossed-out price printed beside/above the right half of the sale price."""
    best, best_cost = None, float("inf")
    width = anchor["x1"] - anchor["x0"]
    for c in candidates:
        dx = c["x0"] - anchor["x0"]
        dy = c["y0"] - anchor["y0"]
        if not (15 <= dx <= width + 130 and -45 <= dy <= 25):
            continue
        if not anchor["v"] * 1.03 < c["v"] < anchor["v"] * 6:
            continue
        cost = abs(dy) + 0.2 * abs(dx - 80)
        if cost < best_cost:
            best, best_cost = c["v"], cost
    return best


def pick_badge(anchor: dict, badges: list[dict]) -> int | None:
    """The "-NN%" badge printed just above the left edge of the sale price."""
    best, best_cost = None, float("inf")
    for b in badges:
        dx = b["x0"] - anchor["x0"]
        dy = b["y0"] - anchor["y0"]
        if not (-25 <= dx <= 45 and -45 <= dy <= 0):
            continue
        cost = abs(dx) + abs(dy)
        if cost < best_cost:
            best, best_cost = b["v"], cost
    return best


def pick_multi_buy(anchor: dict, counts: list[dict]) -> int | None:
    """ "3 gab. 0,78 €": the sale price is per piece when buying N pieces."""
    for c in counts:
        dx = c["x1"] - anchor["x0"]
        dy = c["y0"] - anchor["y0"]
        if -55 <= dx <= 5 and abs(dy) <= 10:
            return c["n"]
    return None


def discount_pair(sale: float, regular: float | None, badge: int | None) -> tuple[float | None, int | None]:
    """Keep a regular price only when it is consistent with the printed badge.

    Lidl also prints "2. precei -61%" (second item) badges and badges of the
    neighbouring card nearby, so a badge alone never creates a discount.
    """
    if regular is None:
        return None, None
    implied = round((regular - sale) / regular * 100)
    if badge is not None and abs(implied - badge) > 2:
        return None, None
    return regular, implied


def _page_text_features(page) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Read sale prices, crossed-out prices, badges, "N gab." counts and titles."""
    titles: list[dict] = []
    sale_prices: list[dict] = []
    regular_prices: list[dict] = []
    badges: list[dict] = []
    piece_counts: list[dict] = []
    gab_marks: list[tuple[float, float]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        size_line = None
        for line in block.get("lines", []):
            line_spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if (size_line is None and line_spans
                    and all("CondPro" in s.get("font", "") and "Bold" not in s.get("font", "")
                            and 7 <= s.get("size", 0) <= 9.5 for s in line_spans)):
                line_text = re.sub(r"\s+", " ", " ".join(s["text"].strip() for s in line_spans))
                match = SIZE_LINE_RE.match(line_text)
                if match:
                    size_line = match.group(1)
            for span in line.get("spans", []):
                font = span.get("font", "")
                text = span.get("text", "").strip()
                value = dec_val(text)
                x0, y0, x1, y1 = span["bbox"]
                if (
                    value is not None
                    and font.startswith("LidlFontPrice")
                    and "PublicBeta" not in font
                    and span.get("size", 0) >= 45
                ):
                    sale_prices.append({
                        "cx": (x0 + x1) / 2,
                        "x0": x0,
                        "x1": x1,
                        "y0": y0,
                        "y1": y1,
                        "v": value,
                    })
                elif (value is not None and "Book" in font and "CondPro" not in font
                        and 8.5 <= span.get("size", 0) <= 18):
                    regular_prices.append({"x0": x0, "y0": y0, "v": value})
                elif BADGE_RE.match(text) and "Bold" in font and span.get("size", 0) >= 14:
                    badges.append({"x0": x0, "y0": y0, "v": int(BADGE_RE.match(text).group(1))})
                elif font.startswith("LidlFontPrice") and re.fullmatch(r"\d{1,2}", text) \
                        and span.get("size", 0) >= 40 and 2 <= int(text) <= 20:
                    piece_counts.append({"x0": x0, "x1": x1, "y0": y0, "n": int(text)})
                elif font.startswith("LidlFontPrice") and text.casefold() in {"gab.", "gab"}:
                    gab_marks.append((x0, y0))
        spans = [
            span
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if "CondPro" in span.get("font", "")
            and "Bold" in span.get("font", "")
            and 9.5 <= span.get("size", 0) <= 13
            and span.get("text", "").strip()
        ]
        if not spans:
            continue
        block_text = " ".join(span["text"].strip() for span in spans).casefold()
        if any(
            noise in block_text
            for noise in ("pazīstami zīmoli", "līdz pat ziemassvētkiem", "no pirmdienas", "no ceturtdienas")
        ):
            continue
        parts = []
        for span in sorted(spans, key=lambda item: (round(item["bbox"][1], 1), item["bbox"][0])):
            for token in span["text"].split():
                if DATE_RE.search(token) or re.fullmatch(r"[\d.,]+", token):
                    continue
                if re.fullmatch(r"-?\d+%?", token):
                    continue
                parts.append(token)
        name = _clean_name(parts)
        if not name:
            continue
        titles.append({
            "name": name_with_size(name, size_line),
            "rect": (
                min(span["bbox"][0] for span in spans),
                min(span["bbox"][1] for span in spans),
                max(span["bbox"][2] for span in spans),
                max(span["bbox"][3] for span in spans),
            ),
        })
    # A big integer only counts as "N gab." when the "gab." label sits right of it.
    counts = [
        c for c in piece_counts
        if any(-15 <= gx - c["x1"] <= 25 and 0 <= gy - c["y0"] <= 30 for gx, gy in gab_marks)
    ]
    return titles, sale_prices, regular_prices, badges, counts


def _match_title_blocks(anchors: list[dict], blocks: list[dict]) -> dict[int, tuple[str, float]]:
    """Match sale prices to title blocks using minimum total distance."""
    if not anchors or not blocks:
        return {}

    costs: list[list[float]] = []
    for anchor_index, anchor in enumerate(anchors):
        row = []
        for block_index, block in enumerate(blocks):
            x0, y0, x1, y1 = block["rect"]
            dx = max(x0 - anchor["x1"], anchor["x0"] - x1, 0)
            dy = max(y0 - anchor["y1"], anchor["y0"] - y1, 0)
            distance = math.hypot(dx, dy)
            # Lidl puts most titles above their price; avoid matching a price
            # to the title of the following row when the blocks are adjacent.
            if y0 > anchor["y1"] + 8:
                distance += 100
            row.append(distance)
        # A dummy block leaves the title empty when every real match is remote.
        row.extend([200.0] * len(anchors))
        costs.append(row)

    # Rectangular Hungarian algorithm. Global assignment avoids stealing a
    # title from another nearby price (common in two-row, multi-column pages).
    n = len(costs)
    m = len(costs[0])
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = costs[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    matches: dict[int, tuple[str, float]] = {}
    for anchor_index, block_index in enumerate(assignment):
        if 0 <= block_index < len(blocks):
            matches[id(anchors[anchor_index])] = (
                blocks[block_index]["name"], costs[anchor_index][block_index]
            )
    return matches


def extract_page(page, page_num: int, booklet: dict, img_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    words = page.get_text("words")
    if not words:
        return [], []
    text = page.get_text("text")
    if "Product name" in text or text.count("XXX") >= 3 or "x,xx" in text:
        return [], []

    width = float(page.rect.width)
    title_blocks, anchors, regular_spans, badges, piece_counts = _page_text_features(page)
    if not anchors:
        # Synthetic PDFs and future Lidl templates may not use Lidl's price
        # font; preserve the coordinate fallback for those pages.
        for w in words:
            x0, y0, x1, y1, tok = w[:5]
            value = dec_val(tok)
            if value is not None and (y1 - y0) >= 28:
                anchors.append({"cx": (x0 + x1) / 2, "x0": x0, "x1": x1,
                                "y0": y0, "y1": y1, "v": value})
    if not anchors:
        return [], []

    cols = _cluster([a["cx"] for a in anchors], 60)
    title_matches = _match_title_blocks(anchors, title_blocks) if title_blocks else {}
    xb = [0] + [(cols[i] + cols[i + 1]) / 2 for i in range(len(cols) - 1)] + [width]
    for a in anchors:
        a["col"] = next(i for i in range(len(cols)) if xb[i] <= a["cx"] < xb[i + 1])

    offers: list[dict] = []
    reviews: list[dict] = []
    seen: set[str] = set()
    for ci in range(len(cols)):
        col_anchors = sorted([a for a in anchors if a["col"] == ci], key=lambda a: a["y0"])
        x_lo, x_hi = xb[ci], xb[ci + 1]
        prev_bottom = 0.0
        for a in col_anchors:
            sale = a["v"]
            name_parts: list[tuple[float, float, str]] = []
            regular = None
            has_plus = False
            for w in words:
                x0, y0, x1, y1, tok = w[:5]
                wx, wy = (x0 + x1) / 2, (y0 + y1) / 2
                h = y1 - y0
                if not (x_lo <= wx < x_hi):
                    continue
                low = tok.casefold()
                if low == "plus" and prev_bottom <= wy < a["y1"] + 30:
                    has_plus = True
                value = dec_val(tok)
                if (value is not None and 10 <= h <= 13.5 and x0 >= a["x1"] - 4
                        and a["y0"] - 12 <= wy <= a["y1"] and sale * 1.08 < value < sale * 4):
                    regular = max(regular or 0, value)
                above = prev_bottom - 2 <= wy < a["y0"] - 2
                left = wx < a["x0"] - 2 and a["y0"] - 2 <= wy <= a["y1"]
                if not title_blocks and (above or left):
                    if value is not None or re.fullmatch(r"-?\d+%?", tok) or re.fullmatch(r"[\d.,]+", tok):
                        continue
                    if DATE_RE.search(tok) or h < 9.5 or h >= 17:
                        continue
                    if low in NOISE_TOK or re.fullmatch(r"\d+", tok):
                        continue
                    if any(s in low for s in NOISE_SUB):
                        continue
                    name_parts.append((wy, wx, tok))
            if title_blocks:
                name, title_distance = title_matches.get(id(a), ("", float("inf")))
            else:
                name_parts.sort()
                name = _clean_name([t for _, _, t in name_parts])
                title_distance = 0.0
            override = TITLE_OVERRIDES.get(
                (booklet["id"], page_num + 1, round(sale, 2))
            )
            if override:
                name = override
                title_distance = 0.0
            # Lidl's own fonts mark the crossed-out price (above the right half
            # of the sale price) and the -NN% badge; the word-based guess above
            # only covers plain PDFs without those fonts.
            if regular_spans:
                regular = pick_regular_price(a, regular_spans)
            regular, discount = discount_pair(sale, regular, pick_badge(a, badges))

            key = f"{name.casefold()}|{sale:.2f}|{page_num}"
            if key in seen:
                continue
            seen.add(key)
            offer = {
                "offerId": f"lidl-{booklet['id']}-p{page_num}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}",
                "name": name,
                "brand": brand_from_name(name),
                "price": sale,
                "regularPrice": regular,
                "discountPercent": discount,
                "requiresCard": has_plus,
                "onlyInApp": False,
                "campaign": booklet["name"],
                "campaignId": booklet["id"],
                "source": booklet["pdf"],
                "pdfPage": page_num + 1,
                "validFrom": booklet.get("validFrom"),
                "validUntil": booklet.get("validUntil"),
                "store": "lidl",
            }
            if has_plus:
                offer["cardName"] = "Lidl Plus"
            pieces = pick_multi_buy(a, piece_counts)
            if pieces:
                offer["multiBuy"] = str(pieces)

            folded = name.casefold()
            name_words = name.split()
            words_count = len(name_words)
            lowered = [w.casefold() for w in name_words]
            brand_caps = len(re.findall(r"\b[A-ZĀČĒĢĪĶĻŅŠŪŽ]{3,}\b", name))
            weak = (
                len(name) < 4
                or len(re.findall(r"[a-zāčēģīķļņšūž]{3,}", folded)) < 1
                or words_count > 12
                or (not title_blocks and brand_caps >= 2)
                or folded.startswith(("www", "no ", "ar ", "cena"))
                or DATE_RE.search(name)
                or name.count(",") >= 3
                or folded in {"līdz", "pāri", "pāris", "komplekts", "vairāk", "zīmoli"}
                or "pazīstami zīmoli" in folded
                or title_distance > 160
            )
            if weak:
                offer["reviewReason"] = "weak_name"
                reviews.append(offer)
            else:
                if img_dir is not None:
                    photo = save_product_photo(page, x_lo, x_hi, a["y0"], a["y1"],
                                               prev_bottom, img_dir, offer["offerId"])
                    if photo:
                        offer["image"] = photo
                offers.append(offer)
            prev_bottom = a["y1"]
    return offers, reviews


def extract_offers_from_pdf(pdf_path: Path, booklet: dict, img_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF parsing")
    doc = fitz.open(pdf_path)
    all_offers: list[dict] = []
    all_review: list[dict] = []
    try:
        for page_num, page in enumerate(doc):
            offers, review = extract_page(page, page_num, booklet, img_dir)
            all_offers.extend(offers)
            all_review.extend(review)
    finally:
        doc.close()
    return dedupe_offers(all_offers), all_review


def dedupe_offers(offers: list[dict]) -> list[dict]:
    """Keep one card per product and price within a booklet.

    Lidl repeats some products on several pages of the same booklet (cover,
    theme spread and category page); the first occurrence is kept, preferring
    a later copy only when it adds a photo.
    """
    kept: dict[tuple, dict] = {}
    for offer in offers:
        key = (offer["name"].casefold(), offer["price"], offer.get("regularPrice"), offer.get("multiBuy"))
        current = kept.get(key)
        if current is None:
            kept[key] = offer
        elif offer.get("image") and not current.get("image"):
            kept[key] = {**offer, "offerId": current["offerId"], "pdfPage": current["pdfPage"]}
    return list(kept.values())


ALCOHOL_NOTICE = "alkohola lietošanai ir negatīva ietekme"


def is_alcohol_catalogue(pdf_path: Path) -> bool:
    """Lidl's wine catalogues open with the statutory alcohol warning."""
    if fitz is None:
        return False
    with fitz.open(pdf_path) as doc:
        return bool(len(doc)) and ALCOHOL_NOTICE in " ".join(doc[0].get_text().split()).casefold()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    if fitz is None:
        print("PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)
    sess = session()
    print("Discovering Lidl booklets...")
    booklets = discover_booklets(sess)
    if not booklets:
        raise RuntimeError("No Lidl booklets discovered")
    today = date.today().isoformat()
    for b in booklets:
        print(f"  - {b['name']} / {b['title']} [{b['validFrom']} – {b['validUntil']}] {b['status']}")

    # Booklets parsed by the same code from the same PDF URL are carried over
    # from the previous feed instead of being downloaded again.
    try:
        previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}
    except (OSError, json.JSONDecodeError):
        previous = {}
    cached = {
        item["pdf"]: item
        for item in previous.get("catalogues", []) + previous.get("skippedBooklets", [])
        if item.get("pdf") and item.get("parserVersion") == PARSER_VERSION
    }

    def catalogue_entry(booklet: dict, offers: int, review: int) -> dict:
        return {
            "id": booklet["id"],
            "name": booklet["name"],
            "title": booklet["title"],
            "pdf": booklet["pdf"],
            "preview": booklet["preview"],
            "validFrom": booklet.get("validFrom"),
            "validUntil": booklet.get("validUntil"),
            "verifiedOffers": offers,
            "reviewOffers": review,
            "complete": offers > 0,
            "parserVersion": PARSER_VERSION,
        }

    def carried(items: list[dict], booklet: dict) -> list[dict]:
        return [
            {**item, "validFrom": booklet.get("validFrom"), "validUntil": booklet.get("validUntil")}
            for item in items if item.get("campaignId") == booklet["id"]
        ]

    all_offers: list[dict] = []
    all_review: list[dict] = []
    catalogues = []
    skipped: list[dict] = []
    for booklet in booklets:
        # Skip booklets whose offer window has not started or already ended.
        if booklet.get("validFrom") and booklet["validFrom"] > today:
            continue
        if booklet.get("validUntil") and booklet["validUntil"] < today:
            continue
        known = cached.get(booklet["pdf"])
        if known and known.get("reason") == "alcohol":
            print(f"Skipping {booklet['name']}: wine catalogue")
            skipped.append(known)
            continue
        if known:
            offers = carried(previous.get("offers", []), booklet)
            review = carried(previous.get("reviewOffers", []), booklet)
            print(f"Unchanged {booklet['name']}: reusing {len(offers)} offers, {len(review)} review")
            all_offers.extend(offers)
            all_review.extend(review)
            catalogues.append(catalogue_entry(booklet, len(offers), len(review)))
            continue
        print(f"Downloading {booklet['pdf']} ...")
        r = sess.get(booklet["pdf"], timeout=240)
        r.raise_for_status()
        pdf_path = Path("tmp/pdfs") / f"lidl_{booklet['id']}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(r.content)
        alcohol = False
        try:
            offers, review = extract_offers_from_pdf(pdf_path, booklet, Path("data/lidl-img"))
            if not offers and not review:
                alcohol = is_alcohol_catalogue(pdf_path)
        finally:
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass
        print(f"  -> {len(offers)} offers, {len(review)} need review")
        if alcohol:
            # The project leaves alcohol out, so the wine catalogue is not listed.
            skipped.append({"id": booklet["id"], "pdf": booklet["pdf"], "reason": "alcohol",
                            "parserVersion": PARSER_VERSION})
            print("  wine catalogue: not listed")
            continue
        all_offers.extend(offers)
        all_review.extend(review)
        catalogues.append(catalogue_entry(booklet, len(offers), len(review)))

    unique = {o["offerId"]: o for o in all_offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in all_review}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())
    products = build_product_groups(offers) if offers else []
    prune_images(Path("data/lidl-img"), {o["image"] for o in offers if o.get("image")})

    payload = {
        "source": BOOKLETS_PAGE,
        "store": "lidl",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogues": catalogues,
        "offers": offers,
        "reviewOffers": reviews,
        "skippedBooklets": skipped,
        "summary": {
            "offerVariants": len(offers),
            "uniqueProducts": len(products),
            "reviewOffers": len(reviews),
            "catalogues": len(catalogues),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} offers ({len(reviews)} review) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Lidl scraper failed: {exc}", file=sys.stderr)
        raise
