#!/usr/bin/env python3
"""The contract every store feed meets before it is written.

The site and the admin panel read every feed the same way, so one store's
bad card must not reach them, and one bad card must not cost the store its
whole feed either. ``enforce_contract`` therefore works at two levels:

- **Feed level** (``FeedContractError``): the payload is not an object, has
  no ``offers`` list, or names a different store than the one being written.
  Nothing is written and the previous feed stays published.
- **Offer level**: a published offer without a usable name, price, id or
  dates is moved to ``reviewOffers`` with ``reviewReason = "contract:<rule>"``,
  where the admin panel shows it. A crossed-out ``regularPrice`` that is not
  above the sale price is dropped instead (the card itself is fine, the
  "discount" is not), and repeated ``offerId``s keep their first offer.

Every change is reported back as a short issue string so the scraper log
shows what the contract did.
"""
from __future__ import annotations

import math
import re
from collections import Counter

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_PRICE = 10_000.0


class FeedContractError(ValueError):
    """The payload cannot be published at all."""


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def offer_problem(offer: dict) -> str | None:
    """The first rule a published offer breaks, or None."""
    name = offer.get("name")
    if not isinstance(name, str) or not name.strip():
        return "name"
    offer_id = offer.get("offerId")
    if not isinstance(offer_id, str) or not offer_id.strip():
        return "offerId"
    price = _number(offer.get("price"))
    if price is None or not 0 < price < MAX_PRICE:
        return "price"
    for key in ("validFrom", "validUntil"):
        value = offer.get(key)
        if value is not None and not (isinstance(value, str) and DATE_RE.match(value)):
            return key
    if offer.get("validFrom") and offer.get("validUntil") and offer["validFrom"] > offer["validUntil"]:
        return "validity"
    for key in ("requiresCard", "onlyInApp"):
        if key in offer and not isinstance(offer[key], bool):
            return key
    return None


def enforce_contract(payload, store_id: str | None) -> list[str]:
    """Bring ``payload`` in line with the contract in place; return the issues.

    Raises ``FeedContractError`` when the feed cannot be published.
    """
    if not isinstance(payload, dict):
        raise FeedContractError("feed is not a JSON object")
    offers = payload.get("offers")
    if not isinstance(offers, list):
        raise FeedContractError("feed has no offers list")
    reviews = payload.get("reviewOffers")
    if reviews is None:
        reviews = []
    if not isinstance(reviews, list):
        raise FeedContractError("reviewOffers is not a list")
    stored = payload.get("store")
    if store_id:
        if stored not in (None, store_id):
            raise FeedContractError(f"feed store {stored!r} is not {store_id!r}")
        payload["store"] = store_id
    elif not stored:
        raise FeedContractError("feed names no store")

    counts: Counter[str] = Counter()
    kept: list[dict] = []
    demoted: list[dict] = []
    seen_ids: set[str] = set()
    for offer in offers:
        if not isinstance(offer, dict):
            counts["dropped non-object offer"] += 1
            continue
        problem = offer_problem(offer)
        if problem:
            offer["reviewReason"] = f"contract:{problem}"
            demoted.append(offer)
            counts[f"moved to review ({problem})"] += 1
            continue
        if offer["offerId"] in seen_ids:
            counts["dropped duplicate offerId"] += 1
            continue
        seen_ids.add(offer["offerId"])
        regular = offer.get("regularPrice")
        if regular is not None:
            regular_number = _number(regular)
            if regular_number is None or regular_number <= float(offer["price"]):
                offer["regularPrice"] = None
                counts["regularPrice not above price dropped"] += 1
        kept.append(offer)

    review_objects = [item for item in reviews if isinstance(item, dict)]
    if len(review_objects) != len(reviews):
        counts["dropped non-object review item"] += len(reviews) - len(review_objects)

    payload["offers"] = kept
    payload["reviewOffers"] = review_objects + demoted
    summary = payload.get("summary")
    if isinstance(summary, dict) and (demoted or len(kept) != len(offers)):
        if "offerVariants" in summary:
            summary["offerVariants"] = len(kept)
        if "reviewOffers" in summary:
            summary["reviewOffers"] = len(payload["reviewOffers"])
    return [f"{count} {issue}" for issue, count in sorted(counts.items())]
