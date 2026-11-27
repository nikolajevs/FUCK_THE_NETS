"""Conservative product identity matching for Maxima offer titles."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict

PACKAGE_RE = re.compile(
    r"(?<![a-z0-9])(?P<value>\d+(?:[,.]\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|gab\.?|gb|pcs?)(?![a-z])",
    flags=re.IGNORECASE,
)


def _ascii(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def product_identity(name: str) -> dict[str, str]:
    """Build an exact, order-insensitive signature while preserving package size.

    No fuzzy matching is used: a title only joins another title when its normalized
    token multiset and all detected package quantities match.
    """
    raw = _ascii(name or "")
    packages: list[str] = []
    display_packages: list[str] = []
    spans: list[tuple[int, int]] = []

    def display_number(value: float) -> str:
        rendered = f"{value:.3f}".rstrip("0").rstrip(".")
        return rendered.replace(".", ",")

    for match in PACKAGE_RE.finditer(raw):
        value = float(match.group("value").replace(",", "."))
        unit = match.group("unit").casefold().rstrip(".")
        if unit == "kg":
            grams = round(value * 1000)
            packages.append(f"mass:{grams}g")
            display_packages.append(
                f"{display_number(grams / 1000)} kg" if grams >= 1000 and grams % 1000 == 0
                else f"{display_number(value)} kg"
            )
        elif unit == "g":
            grams = round(value)
            packages.append(f"mass:{grams}g")
            display_packages.append(
                f"{display_number(grams / 1000)} kg" if grams >= 1000 and grams % 1000 == 0
                else f"{display_number(value)} g"
            )
        elif unit == "l":
            millilitres = round(value * 1000)
            packages.append(f"volume:{millilitres}ml")
            display_packages.append(f"{display_number(value)} l")
        elif unit == "ml":
            millilitres = round(value)
            packages.append(f"volume:{millilitres}ml")
            display_packages.append(
                f"{display_number(millilitres / 1000)} l" if millilitres >= 1000 and millilitres % 1000 == 0
                else f"{display_number(value)} ml"
            )
        else:
            packages.append(f"count:{round(value)}")
            display_packages.append(f"{display_number(value)} gab.")
        spans.append(match.span())

    without_package = list(raw)
    for start, end in spans:
        for index in range(start, end):
            without_package[index] = " "
    tokens = re.findall(r"[a-z0-9]+", "".join(without_package))
    token_signature = " ".join(sorted(tokens))
    package_signature = " ".join(sorted(packages))
    signature = f"{token_signature}|{package_signature}"

    if not token_signature:
        signature = f"unmatched:{raw}"

    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
    slug_tokens = "-".join(sorted(tokens))[:54].strip("-") or "product"
    package_slug = "-".join(sorted(packages)).replace(":", "-")[:24]
    canonical_id = f"{slug_tokens}-{package_slug}-{digest}" if package_slug else f"{slug_tokens}-{digest}"

    return {
        "canonicalProductId": canonical_id,
        "identityKey": signature,
        "packageSize": ", ".join(sorted(set(display_packages))),
    }


def build_product_groups(offers: list[dict]) -> list[dict]:
    """Group source offers into canonical products without discarding offer variants."""
    groups: dict[str, dict] = {}

    for offer in offers:
        identity = product_identity(offer.get("name", ""))
        offer.update(identity)
        canonical_id = identity["canonicalProductId"]

        product = groups.setdefault(canonical_id, {
            "productId": canonical_id,
            "canonicalProductId": canonical_id,
            "identityKey": identity["identityKey"],
            "packageSize": identity["packageSize"],
            "name": offer.get("name", ""),
            "aliases": [],
            "brand": offer.get("brand"),
            "image": offer.get("image"),
            "offers": [],
        })

        if offer.get("name") and offer["name"] not in product["aliases"]:
            product["aliases"].append(offer["name"])
        if len(offer.get("name", "")) > len(product["name"]):
            product["name"] = offer["name"]
        if not product.get("brand") and offer.get("brand"):
            product["brand"] = offer["brand"]
        if not product.get("image") and offer.get("image"):
            product["image"] = offer["image"]
        product["offers"].append(offer)

    result = []
    for product in groups.values():
        variants = sorted(
            product["offers"],
            key=lambda item: (
                item.get("price") if isinstance(item.get("price"), (int, float)) else float("inf"),
                item.get("campaign", ""),
                item.get("store", ""),
            ),
        )
        valid_prices = [
            item["price"] for item in variants
            if isinstance(item.get("price"), (int, float))
        ]
        discounts = [
            item["discountPercent"] for item in variants
            if isinstance(item.get("discountPercent"), (int, float))
        ]
        product.update({
            "offers": variants,
            "offerCount": len(variants),
            "lowestPrice": min(valid_prices) if valid_prices else None,
            "highestDiscountPercent": max(discounts) if discounts else None,
            "campaigns": sorted({item.get("campaign", "") for item in variants if item.get("campaign")}),
            "storeFormats": sorted({
                fmt for item in variants for fmt in item.get("storeFormats", [])
            }),
        })
        result.append(product)

    return sorted(
        result,
        key=lambda item: (item.get("name", "").casefold(), item["canonicalProductId"]),
    )
