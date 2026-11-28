#!/usr/bin/env python3
"""Collect Citro Latvia (citro.lv) promotional offers from its PDF newspaper.

Citro publishes "Citro Avīze" as a PDF linked from https://citro.lv/akcijas/
(the lottery booklet is recorded separately as a catalogue). The PDF keeps a
real text layer. Each product card is anchored by a large cent sale price
(e.g. ``599`` → €5.99, sometimes fused with the euro sign as ``€119``):

- standard cards put the ALL-CAPS product name to the RIGHT of the price and
  the struck-through regular price (a decimal or range like ``6,69-7,79``)
  below-left of it;
- fruit & vegetable cards stack the price on TOP, the regular price below it
  and the ALL-CAPS name underneath.

Both layouts are handled by anchoring on the sale price and collecting the
ALL-CAPS name tokens plus package size either to the right or below. Unit
prices (``7,49 €/kg``), the lottery promo text, period banners and the
"Latvijas produkts" badge are filtered out. Regular-price ranges keep their
lower bound (the most conservative discount). Alcoholic products are dropped,
mirroring the other chains.
"""
from __future__ import annotations

import hashlib
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

try:
    from scripts.product_identity import build_product_groups
except ModuleNotFoundError:
    from product_identity import build_product_groups

try:
    from scripts.booklet_images import prune_images, save_product_photo
except ModuleNotFoundError:
    from booklet_images import prune_images, save_product_photo

BASE = "https://citro.lv"
OFFERS_PAGE = BASE + "/akcijas/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "lv-LV,lv;q=0.9,en;q=0.8",
}
OUT = Path("data/citro-offers.json")
IMG_DIR = Path("data/citro-img")
RETRIES = 3

PDF_LINK_RE = re.compile(r'href="([^"]+\.pdf)"', re.I)
PERIOD_RE = re.compile(
    r"spēkā\s+no\s*(\d{1,2}\.\d{1,2})\.?\s*līdz\s*(\d{1,2}\.\d{1,2})\.?(\d{4})?", re.I)
ANCHOR_RE = re.compile(r"^€?(\d{2,4})€?$")
DECIMAL_RE = re.compile(r"^(\d+[,.]\d+)(?:\s*[-–]\s*(\d+[,.]\d+))?$")
DISCOUNT_RE = re.compile(r"^-(\d{1,2})%$")
DATE_TOKEN_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.?[–—-]?$|^\d{1,2}\.\d{1,2}\.\d{4}\.?$")
URL_TOKEN_RE = re.compile(r"www\.|https?:|\.lv\b", re.I)
ALCOHOL_RE = re.compile(
    r"\b(alus|vins|viskijs|degvins|brendijs|konjaks|sampanietis|likieris|"
    r"dzins|roms|tekila|vodka|sidrs|brut|prosecco|kava|lieris)\b")
ALCOHOL_PHRASE_RE = re.compile(r"alkoholisk\w*\s+(dzerien|kokteil)")
PACKAGE_RE = re.compile(r"(?<![a-z0-9])(\d+(?:[,.]\d+)?)\s*(kg|g|ml|l)(?![a-z])", re.I)
NOISE = {"citro", "latvijas", "produkts", "jaunums", "jaunums!", "akcija",
         "piedavajums", "piedāvājums", "veikalos", "loterija", "loterijas"}


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _fold(text: str) -> str:
    import unicodedata
    normalized = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def is_alcohol_name(name: str) -> bool:
    folded = _fold(name)
    if "bezalkohol" in folded:
        return False
    if ALCOHOL_PHRASE_RE.search(folded):
        return True
    return bool(ALCOHOL_RE.search(folded))


def _money(value: str) -> float | None:
    try:
        amount = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return round(amount, 2) if amount > 0 else None


def discover_booklet(sess: requests.Session) -> dict | None:
    r = sess.get(OFFERS_PAGE, timeout=60)
    r.raise_for_status()
    pdfs = [urljoin(BASE, m) for m in PDF_LINK_RE.findall(r.text)]
    # The page also links the sister-chain ELDO avīze and a lottery booklet;
    # prefer the Citro newspaper itself. Match on the filename, not the host.
    def fname(u):
        return _fold(u.rsplit("/", 1)[-1])
    citro_avize = [u for u in pdfs
                   if "citro" in fname(u) and "avize" in fname(u) and "loterij" not in fname(u)]
    fallback = [u for u in pdfs
                if "avize" in fname(u) and "loterij" not in fname(u) and "eldo" not in fname(u)]
    main = citro_avize[0] if citro_avize else (fallback[0] if fallback else None)
    if not main:
        return None
    return {"id": "citro-avize", "name": "Citro akciju avīze", "pdf": main,
            "validFrom": None, "validUntil": None}


def parse_period_text(text: str) -> tuple[str | None, str | None]:
    m = PERIOD_RE.search(text or "")
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


def parse_period_from_pdf(doc) -> tuple[str | None, str | None]:
    text = " ".join(doc[i].get_text() for i in range(min(2, len(doc))))
    return parse_period_text(text)


def _dedup(words: list) -> list:
    seen: set = set()
    out = []
    for w in words:
        key = (round(w[0], 1), round(w[1], 1), w[4])
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _clean_name(tokens: list[str]) -> str:
    kept: list[str] = []
    for i, tok in enumerate(tokens):
        t = tok.strip()
        t = re.sub(r"^(kg|g|ml|l|gab\.?),$", r"\1", t)  # "g," -> "g"
        if not t or "€" in t:
            continue
        if DISCOUNT_RE.match(t) or DATE_TOKEN_RE.match(t):
            continue
        if URL_TOKEN_RE.search(t) or re.fullmatch(r"Nr\.?|\d+\)|[–—-]", t):
            continue
        if len(t) == 1 and t.isupper():
            continue
        if _fold(t) in NOISE:
            continue
        if DECIMAL_RE.match(t):           # unit-price / regular-price decimals
            continue
        if re.fullmatch(r"\(?\d+[,.]\d+", t):
            continue
        kept.append(t)
    text = " ".join(kept)
    text = re.sub(r"\s+", " ", text).strip(" -–•*:,()")
    text = re.sub(r"^(kg|g|ml|l)\s+", "", text)
    text = re.sub(r"(?<![0-9])\s+(kg|g|ml|l)$", "", text)
    text = re.sub(r"\s+(vai|ar|no)$", "", text, flags=re.I)
    words = []
    for w in text.split():
        if w.isupper() and len(w) > 1 and re.search(r"[A-ZĀČĒĢĪĶĻŅŠŪŽ]", w):
            words.append(w.capitalize())
        else:
            words.append(w)
    return " ".join(words)[:110]


def _anchors(words: list) -> list:
    out = []
    for w in words:
        h = w[3] - w[1]
        if h < 18:
            continue
        m = ANCHOR_RE.match(w[4])
        if not m:
            continue
        val = int(m.group(1))
        if 15 <= val <= 9999:
            out.append((w[0], w[1], w[2], w[3], round(val / 100.0, 2), h))
    # collapse overlapping euro/cent partial runs by position
    by_pos: dict = {}
    for item in out:
        key = (round(item[0] / 3), round(item[1] / 3))
        if key not in by_pos or item[5] > by_pos[key][5]:
            by_pos[key] = item
    return list(by_pos.values())


def _norm_decimal(token: str) -> str:
    return token.replace("\u2009", "").replace("\u00a0", "").replace("€", "").strip()


def _regular_price(words: list, ax0, ay0, ax1, ay1) -> float | None:
    best = None
    for w in words:
        h = w[3] - w[1]
        if h >= 12:
            continue
        m = DECIMAL_RE.match(_norm_decimal(w[4]))
        if not m:
            continue
        cx = (w[0] + w[2]) / 2
        if not (ax0 - 24 <= cx <= ax1 + 50):
            continue
        if not (ay0 - 2 <= w[1] <= ay1 + 34):
            continue
        lo = _money(m.group(1))
        if lo is None:
            continue
        if best is None or lo < best:
            best = lo
    return best


def _cluster_columns(anchors: list, width: float):
    if not anchors:
        return [], [0.0, width]
    xs = sorted(a[0] for a in anchors)
    centres, group = [], [xs[0]]
    for x in xs[1:]:
        if x - group[-1] > 60:
            centres.append(sum(group) / len(group)); group = [x]
        else:
            group.append(x)
    centres.append(sum(group) / len(group))
    bounds = [0.0] + [(centres[i] + centres[i + 1]) / 2 for i in range(len(centres) - 1)] + [width]
    cols = [[] for _ in centres]
    for a in anchors:
        idx = min(range(len(centres)), key=lambda i: abs(a[0] - centres[i]))
        cols[idx].append(a)
    return cols, bounds


HEADER_NOISE_RE = re.compile(r"\b(AUGĻI|AUGLI)\s+UN\s+DĀRZEŅI\b.*$", re.I)


def extract_page(page, page_num: int, booklet: dict, img_dir: Path | None) -> tuple[list, list]:
    words = _dedup(page.get_text("words", sort=True))
    if not words:
        return [], []
    width = float(page.rect.width)
    height = float(page.rect.height)
    anchors = _anchors(words)
    if not anchors:
        return [], []
    cols, bounds = _cluster_columns(anchors, width)
    offers, reviews = [], []
    seen: set[str] = set()
    for ci, col in enumerate(cols):
        col_lo, col_hi = bounds[ci], bounds[ci + 1]
        col.sort(key=lambda a: a[1])
        for k, (ax0, ay0, ax1, ay1, sale, ah) in enumerate(col):
            regular = _regular_price(words, ax0, ay0, ax1, ay1)
            right = [w for w in words
                     if w[4].isupper() and len(w[4]) > 1 and (w[3] - w[1]) >= 7
                     and ax1 - 5 <= w[0] <= ax1 + 150 and ay0 - 8 <= w[1] <= ay0 + 35]
            if right:
                rx0, rx1 = ax1 - 6, ax1 + 158
                ry0, ry1 = ay0 - 9, ay0 + 44
            else:
                # price-top card: name sits below. Bound x by neighbouring
                # prices in the same row, y by the next price below.
                row = [a for a in anchors if abs(a[1] - ay0) < 45 and a[0] != ax0]
                left = max((a[0] for a in row if a[0] < ax0), default=None)
                rightn = min((a[0] for a in row if a[0] > ax0), default=None)
                rx0 = ((left + ax0) / 2 - 12) if left is not None else 0.0
                rx1 = ((rightn + ax0) / 2 + 12) if rightn is not None else width
                below = [a[1] for a in anchors if a[1] > ay0 + 20 and abs(a[0] - ax0) < 60]
                next_y = min(below) if below else height
                ry0, ry1 = ay1 + 8, min(next_y - 6, ay1 + 185)
            name_tokens = []
            for w in words:
                h = w[3] - w[1]
                if not (4.5 <= h <= 12.5):
                    continue
                if not (rx0 <= w[0] <= rx1 and ry0 <= w[1] <= ry1):
                    continue
                tok = w[4]
                is_caps = tok.isupper() and len(tok) > 1 and re.search(r"[A-ZĀČĒĢĪĶĻŅŠŪŽ]", tok)
                unit = tok.rstrip(".,").casefold()
                is_size = bool(re.fullmatch(r"\d+(?:[,.]\d+)?", tok)) or unit in {"kg", "g", "ml", "l", "gab.", "gab"}
                if not (is_caps or is_size):
                    continue
                name_tokens.append((w[1], w[0], tok))
            name_tokens.sort(key=lambda t: (round(t[0] / 3), t[1]))
            name = _clean_name([t[2] for t in name_tokens])
            name = HEADER_NOISE_RE.sub("", name).strip()
            if len(name) < 4 or is_alcohol_name(name):
                continue
            key = f"{name.casefold()}|{sale:.2f}|{page_num}"
            if key in seen:
                continue
            seen.add(key)
            offer = {
                "offerId": f"citro-{booklet['id']}-p{page_num}-{hashlib.sha1(key.encode()).hexdigest()[:8]}",
                "name": name, "brand": None, "price": sale, "regularPrice": regular,
                "requiresCard": False, "onlyInApp": False,
                "campaign": booklet["name"], "campaignId": booklet["id"],
                "source": booklet["pdf"], "pdfPage": page_num + 1,
                "validFrom": booklet.get("validFrom"), "validUntil": booklet.get("validUntil"),
                "store": "citro",
            }
            if regular is not None and regular > sale:
                offer["discountPercent"] = round((regular - sale) / regular * 100)
            folded = _fold(name)
            sizes = {m[0] for m in PACKAGE_RE.findall(folded)}
            combo = bool(re.search(r"[+/]|\d\s*[x×]\s*\d|\s[x×]\s", name))
            merged = len(sizes) >= 2 and not combo
            first = name.split()[0].casefold() if name.split() else ""
            weak = (
                len(name) < 6
                or len(re.findall(r"[a-zāčēģīķļņšūž]{3,}", folded)) < 1
                or first in {"ar", "vai", "no", "un"}
                or first[:1].isdigit()
                or name.count(",") >= 3
                or "€/" in name
            )
            if merged:
                offer["reviewReason"] = "merged_name"
                reviews.append(offer)
            elif weak:
                offer["reviewReason"] = "weak_name"
                reviews.append(offer)
            else:
                if img_dir is not None:
                    photo = save_product_photo(page, rx0, rx1, ay0, ay1 + 30, ry0,
                                               img_dir, offer["offerId"])
                    if photo:
                        offer["image"] = photo
                offers.append(offer)
    return offers, reviews


def extract_pdf(pdf_path: Path, booklet: dict, img_dir: Path | None) -> tuple[list, list]:
    if fitz is None:
        raise RuntimeError("PyMuPDF is required for PDF parsing")
    doc = fitz.open(pdf_path)
    vf, vu = parse_period_from_pdf(doc)
    booklet["validFrom"], booklet["validUntil"] = vf, vu
    all_offers, all_review = [], []
    try:
        for page_num, page in enumerate(doc):
            o, r = extract_page(page, page_num, booklet, img_dir)
            all_offers.extend(o)
            all_review.extend(r)
    finally:
        doc.close()
    return all_offers, all_review


def main() -> None:
    if fitz is None:
        print("PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)
    sess = session()
    print("Discovering Citro avīze...")
    booklet = discover_booklet(sess)
    if not booklet:
        raise RuntimeError("No Citro PDF booklet discovered")
    print(f"Downloading {booklet['pdf']}")
    r = sess.get(booklet["pdf"], timeout=120)
    r.raise_for_status()
    pdf_path = Path("tmp/pdfs") / "citro-avize.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(r.content)
    try:
        offers, review = extract_pdf(pdf_path, booklet, None)
    finally:
        pdf_path.unlink(missing_ok=True)
    print(f"  -> {len(offers)} offers, {len(review)} review "
          f"[{booklet.get('validFrom')} – {booklet.get('validUntil')}]")

    unique = {o["offerId"]: o for o in offers}
    offers = sorted(unique.values(), key=lambda x: (x["name"].casefold(), x["price"]))
    unique_rev = {o["offerId"]: o for o in review}
    reviews = sorted(unique_rev.values(), key=lambda x: x["name"].casefold())
    products = build_product_groups(offers) if offers else []
    prune_images(IMG_DIR, {o["image"] for o in offers if o.get("image")})

    catalogues = [{
        "id": booklet["id"], "name": booklet["name"], "source": booklet["pdf"],
        "validFrom": booklet.get("validFrom"), "validUntil": booklet.get("validUntil"),
        "verifiedOffers": len(offers), "reviewOffers": len(reviews),
        "complete": len(offers) > 0,
    }]
    payload = {
        "source": OFFERS_PAGE, "store": "citro",
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogues": catalogues, "offers": offers,
        "reviewOffers": reviews,
        "summary": {"offerVariants": len(offers), "uniqueProducts": len(products),
                    "reviewOffers": len(reviews), "catalogues": len(catalogues)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(offers)} offers ({len(reviews)} review) → {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Citro scraper failed: {exc}", file=sys.stderr)
        raise