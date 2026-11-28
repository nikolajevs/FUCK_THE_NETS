#!/usr/bin/env python3
"""The one list of store sources: everything else reads it.

Adding a chain means adding one ``Store`` here (plus its scraper script).
The daily runner builds its lanes from this list, and ``data/stores.json`` —
the manifest the site (``app.js``) and the admin panel (``admin.js``) load
their feeds from — is generated from it. ``python scripts/stores.py``
rewrites the manifest; the daily runner does the same before scraping, and
a test keeps the committed copy in sync.

Fields:

- ``id``: the store key, the same value the feed writes as ``store``;
- ``label``: the name shown on the site, in the admin panel and in the logs;
- ``script``: scraper file in ``scripts/``;
- ``feed``: the feed it writes, relative to the repository root;
- ``lane``: scrapers sharing a lane run one after another, lanes run in
  parallel (see ``run_scrapers.py``). Scrapers that update the shared Gemini
  budget ledger must share the ``gemini`` lane;
- ``required``: a failure fails the whole daily job;
- ``secrets``: environment secrets passed to this scraper only;
- ``campaign_label``: ``"slug"`` when campaign ids are URL slugs the site
  prettifies instead of using the printed campaign name.

For chains read by the booklet vision engine (``booklet_vision.py``):

- ``vision_hint``: one extra prompt line about this chain's booklets (how its
  bundle or loyalty prices look) — data, not code;
- ``prices_in_text``: whether a PDF's text layer carries the printed prices.
  When it does, every price the model reads must also be found in the text
  under the card; when prices are artwork (Spar), only names are checked.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "stores.json"
GEMINI_LANE = "gemini"


@dataclass(frozen=True)
class Store:
    id: str
    label: str
    script: str
    feed: str
    lane: str
    required: bool = False
    secrets: tuple[str, ...] = ()
    campaign_label: str = "name"
    vision_hint: str = ""
    prices_in_text: bool = True


GEMINI = ("GEMINI_API_KEY",)

# Order is the order the site loads feeds in and the runner groups lanes in.
STORES: tuple[Store, ...] = (
    Store("maxima", "Maxima", "scrape_maxima.py", "data/offers.json", "maxima", required=True),
    Store("mego", "Mego", "scrape_mego.py", "data/mego-offers.json", "small",
          campaign_label="slug"),
    Store("top", "Top!", "scrape_top.py", "data/top-offers.json", "small"),
    Store("lidl", "Lidl", "scrape_lidl.py", "data/lidl-offers.json", "lidl"),
    Store("rimi", "Rimi", "scrape_rimi_booklets.py", "data/rimi-offers.json", GEMINI_LANE),
    Store("rimi-eveikals", "Rimi e-veikals", "scrape_rimi.py",
          "data/rimi-eveikals-offers.json", "rimi-eveikals"),
    Store("elvi", "Elvi", "scrape_elvi.py", "data/elvi-offers.json", "small"),
    Store("latts", "LaTS", "scrape_latts.py", "data/latts-offers.json", "small"),
    Store("vesko", "Vesko", "scrape_vesko.py", "data/vesko-offers.json", "small"),
    Store("aibe", "Aibe", "scrape_aibe.py", "data/aibe-offers.json", "small"),
    Store("citro", "Citro", "scrape_citro.py", "data/citro-offers.json", "small"),
    Store("spar", "Spar", "scrape_spar.py", "data/spar-offers.json", GEMINI_LANE, secrets=GEMINI,
          prices_in_text=False,
          vision_hint="SPAR prints prices as graphics; a badge like '2.00 PAR 2' means "
                      "2 items for 2.00 EUR in total."),
    Store("beta", "Beta", "scrape_beta.py", "data/beta-offers.json", GEMINI_LANE, secrets=GEMINI),
)


def by_id(store_id: str) -> Store:
    for store in STORES:
        if store.id == store_id:
            return store
    raise KeyError(store_id)


def manifest() -> dict:
    """What the site and the admin panel need to know about each store."""
    return {
        "stores": [
            {
                "id": store.id,
                "label": store.label,
                "feed": store.feed,
                "campaignLabel": store.campaign_label,
            }
            for store in STORES
        ]
    }


def manifest_text() -> str:
    return json.dumps(manifest(), ensure_ascii=False, indent=2) + "\n"


def write_manifest(path: Path = MANIFEST) -> bool:
    """Rewrite the manifest when it differs; return whether it changed."""
    text = manifest_text()
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    changed = write_manifest()
    print(f"{MANIFEST.relative_to(ROOT)}: {'updated' if changed else 'up to date'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
