#!/usr/bin/env python3
"""Notice when a store's feed breaks, and survive it.

``run_scrapers.py`` keeps every feed's bytes from before the run and, once
the scrapers are done, calls ``check_feeds``. Each new feed is compared with
the one it replaces:

- **restored**: the new feed is unreadable, breaks the feed contract, or
  its offers collapsed — fewer than ``COLLAPSE_RATIO`` of the previous
  feed's still-valid offers, or none at all while a booklet is current. The
  previous feed is written back, marked ``"health": {"status": "stale"}``,
  and photos the scraper already pruned are restored from git.
- **failed**: the scraper exited with an error (its feed stays as it was).
- **degraded**: the feed is published but looks worse than before: the share
  of offers with a regular price, pack size or photo fell by more than half,
  the review queue swallowed most cards, or no offers are left while a
  booklet is current and there is nothing to restore.
- **ok**: none of the above.

Every run is appended to ``data/health.json`` (last ``HISTORY_LIMIT`` runs per
store) together with the store's current status and how many runs in a row
it has had it. The runner prints a table to the job summary, and the admin
panel shows the status next to each store.

``python scripts/feed_health.py --sync-issues`` (run by the workflow) keeps
one GitHub issue per unhealthy store, labelled ``feed-health``: it is opened
at once when a feed had to be restored, after ``ISSUE_STREAK`` runs in a row
for a failed or degraded store, updated while the problem lasts and closed
when the store is ok again.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from scripts.common import load_previous_feed, riga_today, write_feed
    from scripts.feed_schema import FeedContractError, enforce_contract
    from scripts.stores import ROOT, STORES, Store
except ModuleNotFoundError:
    from common import load_previous_feed, riga_today, write_feed
    from feed_schema import FeedContractError, enforce_contract
    from stores import ROOT, STORES, Store

HEALTH_FILE = ROOT / "data" / "health.json"
HISTORY_LIMIT = 60
COLLAPSE_RATIO = 0.4        # new offers below 40 % of the still-valid previous ones
MIN_OFFERS = 20             # ratios are only judged on feeds at least this big
SHARE_DROP = 0.5            # a share that fell below half of its previous value
MIN_SHARE = 0.2             # ... and was meaningful before
REVIEW_SHARE_LIMIT = 0.5
REVIEW_SHARE_JUMP = 0.25
ISSUE_STREAK = 2
ISSUE_LABEL = "feed-health"
SHARES = {"regularPrice": "regularShare", "packageSize": "sizeShare", "image": "imageShare"}
SHARE_NAMES = {"regularShare": "regular price", "sizeShare": "pack size", "imageShare": "photo"}
STATUS_ORDER = ("ok", "degraded", "failed", "restored")


# --- metrics -------------------------------------------------------------------------

def is_current(item: dict, today: str) -> bool:
    if item.get("expired") is True:
        return False
    valid_until = item.get("validUntil")
    return not valid_until or valid_until >= today


def metrics(payload: dict, today: str) -> dict:
    offers = [o for o in payload.get("offers") or [] if isinstance(o, dict)]
    reviews = [o for o in payload.get("reviewOffers") or [] if isinstance(o, dict)]
    result = {
        "offers": len(offers),
        "review": len(reviews),
        "currentOffers": sum(1 for o in offers if is_current(o, today)),
        "activeCatalogues": sum(
            1 for c in payload.get("catalogues") or [] if isinstance(c, dict) and is_current(c, today)
        ),
        "reviewShare": round(len(reviews) / (len(offers) + len(reviews)), 3)
        if offers or reviews else None,
    }
    for source, key in SHARES.items():
        result[key] = round(sum(1 for o in offers if o.get(source)) / len(offers), 3) if offers else None
    return result


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


# --- assessment ------------------------------------------------------------------------

@dataclass
class Report:
    store: Store
    status: str
    notes: list[str] = field(default_factory=list)
    metrics: dict | None = None
    previous: dict | None = None


def _parse(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def assess(store: Store, before: bytes | None, after: bytes | None, scraper_ok: bool,
           today: str) -> tuple[Report, bool]:
    """Judge one store's new feed; return the report and whether to restore."""
    previous = _parse(before)
    current = _parse(after)
    prev_metrics = metrics(previous, today) if previous else None
    notes: list[str] = []
    restore = False
    degraded = False

    problem = None
    if after is None:
        problem = "feed file is missing"
    elif current is None:
        problem = "feed is not a valid JSON object"
    else:
        try:
            enforce_contract(copy.deepcopy(current), store.id)
        except FeedContractError as exc:
            problem = f"feed breaks the contract: {exc}"

    new_metrics = metrics(current, today) if current is not None else None
    if problem:
        notes.append(problem)
        restore = previous is not None and before != after
    elif new_metrics is not None:
        still_valid = prev_metrics["currentOffers"] if prev_metrics else 0
        if (before != after and still_valid >= MIN_OFFERS
                and new_metrics["offers"] < COLLAPSE_RATIO * still_valid):
            notes.append(f"offers fell to {new_metrics['offers']} from {still_valid} still-valid ones")
            restore = True
        elif new_metrics["offers"] == 0 and new_metrics["activeCatalogues"]:
            notes.append(f"no offers although {new_metrics['activeCatalogues']} booklet(s) are current")
            if before != after and still_valid > 0:
                restore = True
            else:
                degraded = True
        if not restore and prev_metrics and new_metrics["offers"] >= MIN_OFFERS:
            for key, name in SHARE_NAMES.items():
                old, new = prev_metrics.get(key), new_metrics.get(key)
                if old is not None and new is not None and old >= MIN_SHARE and new < old * SHARE_DROP:
                    notes.append(f"{name} share fell from {_percent(old)} to {_percent(new)}")
                    degraded = True
            old_review, new_review = prev_metrics.get("reviewShare"), new_metrics.get("reviewShare")
            if (new_review is not None and new_review > REVIEW_SHARE_LIMIT
                    and new_review > (old_review or 0) + REVIEW_SHARE_JUMP):
                notes.append(f"review share rose from {_percent(old_review)} to {_percent(new_review)}")
                degraded = True

    if not scraper_ok:
        notes.append("scraper exited with an error")

    if restore:
        status = "restored"
    elif not scraper_ok or (problem and not restore):
        status = "failed"
    elif degraded:
        status = "degraded"
    else:
        status = "ok"
    shown = prev_metrics if restore else new_metrics
    return Report(store, status, notes, shown, prev_metrics), restore


def _restore_photos(payload: dict, root: Path) -> int:
    """Bring back local photos the scraper pruned; drop the ones git cannot."""
    missing = sorted({
        o["image"] for o in payload.get("offers") or []
        if isinstance(o, dict) and isinstance(o.get("image"), str)
        and not o["image"].startswith(("http://", "https://"))
        and not (root / o["image"]).exists()
    })
    if missing:
        subprocess.run(["git", "checkout", "HEAD", "--", *missing], cwd=root,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    dropped = 0
    for offer in payload.get("offers") or []:
        image = offer.get("image") if isinstance(offer, dict) else None
        if image in missing and not (root / image).exists():
            offer.pop("image", None)
            dropped += 1
    return dropped


def restore_feed(store: Store, before: bytes, reason: str, now: str, root: Path = ROOT) -> None:
    payload = _parse(before)
    if payload is None:
        return
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    payload["health"] = {
        "status": "stale",
        "since": health.get("since") if health.get("status") == "stale" else now,
        "reason": reason,
    }
    dropped = _restore_photos(payload, root)
    if dropped:
        print(f"  {store.label}: {dropped} restored offers lost their pruned photo")
    write_feed(root / store.feed, payload, store.id)


def snapshot_feeds(stores=STORES, root: Path = ROOT) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for store in stores:
        try:
            snapshot[store.id] = (root / store.feed).read_bytes()
        except OSError:
            snapshot[store.id] = None
    return snapshot


def check_feeds(snapshot: dict[str, bytes | None], scraper_ok: dict[str, bool],
                stores=STORES, today: str | None = None, now: str | None = None,
                root: Path = ROOT) -> list[Report]:
    today = today or riga_today()
    now = now or datetime.now().astimezone().isoformat(timespec="seconds")
    reports = []
    for store in stores:
        path = root / store.feed
        try:
            after = path.read_bytes()
        except OSError:
            after = None
        before = snapshot.get(store.id)
        report, restore = assess(store, before, after, scraper_ok.get(store.id, True), today)
        if restore and before is not None:
            restore_feed(store, before, report.notes[0] if report.notes else "restored", now, root)
            print(f"::warning::{store.label}: previous feed restored ({'; '.join(report.notes)})")
        elif report.status != "ok":
            print(f"::warning::{store.label}: {report.status} ({'; '.join(report.notes)})")
        reports.append(report)
    return reports


# --- history -------------------------------------------------------------------------------

def update_history(reports: list[Report], now: str, path: Path = HEALTH_FILE,
                   stores_known=STORES) -> dict:
    health = load_previous_feed(path)
    stores = health.get("stores") if isinstance(health.get("stores"), dict) else {}
    for report in reports:
        entry = stores.get(report.store.id) if isinstance(stores.get(report.store.id), dict) else {}
        history = [h for h in entry.get("history") or [] if isinstance(h, dict)]
        history.append({"at": now, "status": report.status, **(report.metrics or {})})
        same = entry.get("status") == report.status
        stores[report.store.id] = {
            "label": report.store.label,
            "status": report.status,
            "since": entry.get("since", now) if same else now,
            "streak": int(entry.get("streak", 0)) + 1 if same else 1,
            "notes": report.notes,
            "history": history[-HISTORY_LIMIT:],
        }
    known = {store.id for store in stores_known}
    health = {"updatedAt": now, "stores": {k: v for k, v in stores.items() if k in known}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(health, ensure_ascii=False, indent=2) + "\n")
    return health


def summary_table(reports: list[Report]) -> str:
    rows = ["| Store | Health | Offers | Review | Regular price | Pack size | Photo | Notes |",
            "|---|---|---|---|---|---|---|---|"]
    for report in reports:
        m = report.metrics or {}
        offers = m.get("offers", "—")
        if report.previous and isinstance(offers, int):
            delta = offers - report.previous["offers"]
            offers = f"{offers} ({delta:+d})" if delta else str(offers)
        rows.append(
            f"| {report.store.label} | {report.status} | {offers} | {m.get('review', '—')} | "
            f"{_percent(m.get('regularShare'))} | {_percent(m.get('sizeShare'))} | "
            f"{_percent(m.get('imageShare'))} | {'; '.join(report.notes) or ''} |"
        )
    return "\n".join(rows) + "\n"


# --- GitHub issues ---------------------------------------------------------------------------

def issue_title(label: str) -> str:
    return f"Feed health: {label}"


def needs_issue(entry: dict) -> bool:
    status = entry.get("status")
    return status == "restored" or (status in ("failed", "degraded")
                                    and int(entry.get("streak", 0)) >= ISSUE_STREAK)


def issue_body(store_id: str, entry: dict) -> str:
    lines = [
        f"`{store_id}` is **{entry.get('status')}** since {entry.get('since')} "
        f"({entry.get('streak')} run(s) in a row).",
        "",
        *[f"- {note}" for note in entry.get("notes") or []],
        "",
        "| Run | Health | Offers | Review | Regular price | Pack size | Photo |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in (entry.get("history") or [])[-5:]:
        lines.append(
            f"| {item.get('at')} | {item.get('status')} | {item.get('offers', '—')} | "
            f"{item.get('review', '—')} | {_percent(item.get('regularShare'))} | "
            f"{_percent(item.get('sizeShare'))} | {_percent(item.get('imageShare'))} |"
        )
    lines += ["", "Opened and closed automatically by `scripts/feed_health.py` "
              "from `data/health.json`."]
    return "\n".join(lines) + "\n"


def issue_plan(health: dict, open_issues: dict[str, int]) -> list[dict]:
    """What to do on GitHub: open, update or close one issue per store."""
    actions = []
    for store_id, entry in (health.get("stores") or {}).items():
        title = issue_title(entry.get("label") or store_id)
        number = open_issues.get(title)
        if needs_issue(entry):
            body = issue_body(store_id, entry)
            if number is None:
                actions.append({"action": "open", "title": title, "body": body})
            else:
                actions.append({"action": "update", "number": number, "body": body})
        elif entry.get("status") == "ok" and number is not None:
            actions.append({"action": "close", "number": number,
                            "comment": f"Back to ok at {health.get('updatedAt')}."})
    return actions


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def sync_issues(path: Path = HEALTH_FILE) -> int:
    health = load_previous_feed(path)
    if not health.get("stores"):
        print("No feed health data yet")
        return 0
    _gh("label", "create", ISSUE_LABEL, "--force", "--color", "d93f0b",
        "--description", "A store feed broke or degraded (opened by feed_health.py)")
    listed = json.loads(_gh("issue", "list", "--label", ISSUE_LABEL, "--state", "open",
                            "--limit", "100", "--json", "number,title"))
    open_issues = {item["title"]: item["number"] for item in listed}
    for action in issue_plan(health, open_issues):
        if action["action"] == "open":
            _gh("issue", "create", "--title", action["title"], "--body", action["body"],
                "--label", ISSUE_LABEL)
            print(f"Opened issue: {action['title']}")
        elif action["action"] == "update":
            _gh("issue", "edit", str(action["number"]), "--body", action["body"])
            print(f"Updated issue #{action['number']}")
        else:
            _gh("issue", "close", str(action["number"]), "--comment", action["comment"])
            print(f"Closed issue #{action['number']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sync-issues", action="store_true",
                        help="open, update and close feed-health issues with the gh CLI")
    args = parser.parse_args()
    if args.sync_issues:
        return sync_issues()
    health = load_previous_feed(HEALTH_FILE)
    for store_id, entry in (health.get("stores") or {}).items():
        print(f"{entry.get('label', store_id):16} {entry.get('status'):9} x{entry.get('streak')} "
              f"{'; '.join(entry.get('notes') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
