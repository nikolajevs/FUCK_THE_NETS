#!/usr/bin/env python3
"""Helpers every store scraper shares.

- ``write_feed``: the only way a feed reaches ``data/``. It applies the feed
  contract (``feed_schema.enforce_contract``), writes atomically with LF line
  endings and refuses to replace the published feed with one that breaks the
  contract at feed level.
- ``load_previous_feed``: the published feed, for carry-forward logic.
- ``fold`` / ``is_alcohol_name``: text normalisation and the shared alcohol
  filter for chains that only have product names.
- ``riga_offset`` / ``riga_date`` / ``riga_today`` / ``is_expired``:
  Europe/Riga local dates without an external tz database.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from scripts.feed_schema import FeedContractError, enforce_contract
    from scripts.stores import ROOT, STORES
except ModuleNotFoundError:
    from feed_schema import FeedContractError, enforce_contract
    from stores import ROOT, STORES

__all__ = [
    "FeedContractError", "fold", "is_alcohol_name", "is_expired", "load_previous_feed",
    "riga_date", "riga_offset", "riga_today", "store_id_for", "write_feed",
]


# --- text ----------------------------------------------------------------------

def fold(text: str) -> str:
    """Casefold and strip diacritics: "Vīns" -> "vins"."""
    normalized = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


# Standalone alcoholic-drink words (non-alcoholic "bezalkoholisk..." is kept).
ALCOHOL_RE = re.compile(
    r"\b(alus|vins|viskijs|degvins|brendijs|konjaks|sampanietis|likieris|"
    r"dzins|roms|tekila|vodka|sidrs|brut|prosecco|kava|lieris)\b")
ALCOHOL_PHRASE_RE = re.compile(r"alkoholisk\w*\s+(dzerien|kokteil)")


def is_alcohol_name(name: str) -> bool:
    folded = fold(name)
    if "bezalkohol" in folded:
        return False
    if ALCOHOL_PHRASE_RE.search(folded):
        return True
    return bool(ALCOHOL_RE.search(folded))


# --- Europe/Riga local dates ------------------------------------------------------
# Latvia follows EU summer-time rules: EEST (UTC+3) from the last Sunday of
# March 01:00 UTC until the last Sunday of October 01:00 UTC, EET (UTC+2) rest.

def _last_sunday(year: int, month: int) -> int:
    sundays = [week[6] for week in calendar.monthcalendar(year, month) if week[6] != 0]
    return sundays[-1]


def riga_offset(dt_utc: datetime) -> timedelta:
    year = dt_utc.year
    start = datetime(year, 3, _last_sunday(year, 3), 1, 0, tzinfo=timezone.utc)
    end = datetime(year, 10, _last_sunday(year, 10), 1, 0, tzinfo=timezone.utc)
    return timedelta(hours=3) if start <= dt_utc < end else timedelta(hours=2)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def riga_date(value: str | None) -> str | None:
    dt = parse_utc(value)
    if dt is None:
        return None
    return (dt + riga_offset(dt)).date().isoformat()


def riga_today() -> str:
    return riga_date(datetime.now(timezone.utc).isoformat())


def is_expired(valid_until: str | None, today: str) -> bool:
    """``validUntil`` is the last valid day, so it still counts today."""
    return bool(valid_until) and valid_until < today


# --- feeds ---------------------------------------------------------------------------

def store_id_for(path: Path) -> str | None:
    """The registry id of the store whose feed lives at ``path``."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    for store in STORES:
        if (ROOT / store.feed).resolve() == resolved:
            return store.id
    return None


def load_previous_feed(path: Path) -> dict:
    """The published feed at ``path``, or ``{}`` when missing or unreadable."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_feed(path: Path, payload: dict, store_id: str | None = None) -> list[str]:
    """Apply the feed contract and write ``payload`` to ``path``.

    ``store_id`` defaults to the registry entry whose feed is ``path``, then
    to the payload's own ``store``. Returns the contract issues (also printed).
    Raises ``FeedContractError`` without touching ``path`` when the feed
    cannot be published.
    """
    path = Path(path)
    store_id = store_id or store_id_for(path)
    offers_before = len(payload.get("offers") or []) if isinstance(payload, dict) else 0
    issues = enforce_contract(payload, store_id)
    summary = payload.get("summary")
    if (isinstance(summary, dict) and "uniqueProducts" in summary
            and len(payload["offers"]) != offers_before):
        try:
            from scripts.product_identity import build_product_groups
        except ModuleNotFoundError:
            from product_identity import build_product_groups
        summary["uniqueProducts"] = len(build_product_groups(payload["offers"]))
    for issue in issues:
        print(f"  feed contract ({path.name}): {issue}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)
    return issues
