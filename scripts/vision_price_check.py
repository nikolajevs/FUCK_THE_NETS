"""Compare vision-model prices with shelf-price candidates from PDF text."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _price_in_cents(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0:
            return None
        return int(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def check_vision_prices_against_text(offer: dict) -> dict | None:
    """Return an advisory comparison for model prices and parsed PDF prices."""
    evidence = offer.get("reviewEvidence")
    if not isinstance(evidence, dict):
        return None
    suggestion = evidence.get("visionAssist")
    if not isinstance(suggestion, dict):
        return None

    parsed_prices = evidence.get("parsedPrices")
    has_text_prices = isinstance(parsed_prices, list)
    text_cents = [
        cents for value in parsed_prices or []
        if (cents := _price_in_cents(value)) is not None
    ]
    text_prices = [cents / 100 for cents in text_cents]

    model_prices: dict[str, float] = {}
    unmatched: list[dict[str, float | str]] = []
    for field in ("price", "regularPrice"):
        cents = _price_in_cents(suggestion.get(field))
        if cents is None:
            continue
        model_prices[field] = cents / 100
        if has_text_prices and cents not in text_cents:
            unmatched.append({"field": field, "price": cents / 100})

    if not model_prices:
        status = "no_model_prices"
    elif not has_text_prices:
        status = "unavailable"
    elif unmatched:
        status = "mismatch"
    else:
        status = "matched"

    return {
        "status": status,
        "textLayerPrices": text_prices,
        "unmatchedModelPrices": unmatched,
    }


def annotate_vision_price_check(offer: dict) -> dict | None:
    """Store the advisory price comparison beside the source evidence."""
    check = check_vision_prices_against_text(offer)
    evidence = offer.get("reviewEvidence")
    if check is not None and isinstance(evidence, dict):
        evidence["visionPriceCrossCheck"] = check
    return check
