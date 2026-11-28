"""Gemini Flash-Lite first-pass vision review for ambiguous Maxima PDF cards.

Load split: Gemini handles the bulk of new review cards (fast API, free tier),
while the local Qwen3-VL step (qwen_pdf_review.py) stays as fallback for cards
Gemini could not resolve — quota exhausted, API errors, or decision=unclear.
The local step only picks cards without ``reviewEvidence.visionAssist``, so a
card resolved here never reaches llama.cpp, and a card left untouched here is
picked up there automatically.

Configuration (env):
  GEMINI_API_KEY        API key; alternatively a key file (see --key-file)
  GEMINI_REVIEW_MODEL   default gemini-3.5-flash-lite
  GEMINI_MAX_REQUESTS   per-run request budget, default 40 (0 = unlimited)
  GEMINI_MIN_INTERVAL   seconds between requests, default 4.5 (~13 RPM)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - compatibility with older PyMuPDF releases
    import fitz

try:
    from scripts.qwen_pdf_review import _crop_rect, _prompt_for_offer, _valid_suggestion
except ModuleNotFoundError:
    from qwen_pdf_review import _crop_rect, _prompt_for_offer, _valid_suggestion

try:
    from scripts.common import write_feed
except ModuleNotFoundError:
    from common import write_feed

try:
    from scripts.vision_price_check import annotate_vision_price_check
except ModuleNotFoundError:
    from vision_price_check import annotate_vision_price_check

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
# Daily free-tier budgets change; keep a persistent ledger so repeated runs
# (daily cron, manual dispatches) never blow through the shared quota.
DEFAULT_DAILY_BUDGET = 200


def load_api_key(explicit: str | None = None, key_file: str | Path | None = None) -> str | None:
    """Resolve the key from env, an explicit value, or a local key file.

    The key file is a local convenience only — it must stay gitignored; CI
    should use the GEMINI_API_KEY secret instead.
    """
    if explicit:
        return explicit.strip() or None
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    candidates = [Path(key_file)] if key_file else [
        Path("gemini.token.txt"), Path("gemini.token"),
    ]
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


class GeminiClient:
    """Minimal generateContent client with pacing, retries and a run budget."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        max_requests: int = 40,
        min_interval: float = 4.5,
        timeout: int = 90,
        sleep=time.sleep,
    ):
        self.api_key = api_key
        self.model = model
        self.max_requests = max_requests
        self.min_interval = min_interval
        self.timeout = timeout
        self._sleep = sleep
        self._last_request = 0.0
        self.requests_used = 0
        self.quota_exhausted = False

    def _post(self, body: dict) -> tuple[int, dict | None, str]:
        request = urllib.request.Request(
            API_URL.format(model=self.model),
            data=json.dumps(body).encode("utf-8"),
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8")), ""
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(detail), detail
            except json.JSONDecodeError:
                return exc.code, None, detail
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return -1, None, str(exc)

    def generate(self, image_bytes: bytes, prompt: str, mime_type: str = "image/png") -> dict | None:
        """Return parsed JSON content, or None on any failure (caller falls back)."""
        if self.quota_exhausted:
            return None
        if self.max_requests and self.requests_used >= self.max_requests:
            print("Gemini run budget reached; remaining cards go to local Qwen")
            self.quota_exhausted = True
            return None

        body = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": mime_type,
                                     "data": base64.b64encode(image_bytes).decode("ascii")}},
                    {"text": prompt},
                ]
            }],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        for attempt in range(3):
            if self.max_requests and self.requests_used >= self.max_requests:
                print("Gemini run budget reached; remaining cards go to local Qwen")
                self.quota_exhausted = True
                return None
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests_used += 1
            status, payload, detail = self._post(body)
            if status == 200 and payload:
                try:
                    parts = payload["candidates"][0]["content"]["parts"]
                    text = "".join(part.get("text", "") for part in parts)
                    return json.loads(text)
                except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                    print("Warning: Gemini returned unparseable content")
                    return None
            if status == 429:
                message = json.dumps(payload or {}) + detail
                if "per day" in message.lower() or "RPD" in message or "daily" in message.lower():
                    print("Warning: Gemini daily quota exhausted; falling back to local Qwen")
                    self.quota_exhausted = True
                    return None
                self._sleep(min(60.0, 15.0 * (attempt + 1)))
                continue
            if status in (500, 503, -1) and attempt < 2:
                self._sleep(5.0 * (attempt + 1))
                continue
            print(f"Warning: Gemini request failed ({status}): {detail[:300]}")
            return None
        return None


def _daily_ledger_path() -> Path:
    return Path(os.environ.get("GEMINI_USAGE_LEDGER", "data/gemini-usage.json"))


def _daily_budget() -> int:
    try:
        return int(os.environ.get("GEMINI_DAILY_BUDGET", DEFAULT_DAILY_BUDGET))
    except ValueError:
        return DEFAULT_DAILY_BUDGET


def _load_ledger(today: str) -> dict:
    path = _daily_ledger_path()
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ledger = {}
    if ledger.get("date") != today:
        ledger = {"date": today, "requests": 0}
    return ledger


def _save_ledger(ledger: dict) -> None:
    path = _daily_ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Warning: cannot write Gemini usage ledger: {exc}")


def add_gemini_review_suggestions(
    pdf_bytes: bytes,
    review_offers: list[dict],
    client: GeminiClient,
) -> int:
    """Attach advisory notes from Gemini; unresolved cards stay for local Qwen."""
    if not review_offers or fitz is None:
        return 0
    attached = 0
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for index, offer in enumerate(review_offers, start=1):
            if client.quota_exhausted:
                break
            try:
                page_number = int(offer.get("pdfPage", 0))
            except (TypeError, ValueError):
                continue
            if not 1 <= page_number <= len(document):
                continue
            page = document[page_number - 1]
            clip = _crop_rect(page, offer)
            if clip is None:
                continue
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False)
                image_png = pixmap.tobytes("png")
            except Exception as exc:
                print(f"Warning: could not render Gemini crop for {offer.get('offerId')}: {exc}")
                continue

            result = client.generate(image_png, _prompt_for_offer(offer))
            if not isinstance(result, dict) or not isinstance(result.get("results"), list):
                continue
            candidate_ids = {str(offer.get("offerId", ""))}
            suggestions = [
                suggestion
                for raw in result["results"]
                if (suggestion := _valid_suggestion(candidate_ids, raw)) is not None
            ]
            if not suggestions:
                continue
            suggestion = suggestions[0]
            if suggestion["decision"] != "resolved":
                # Leave unclear cards untouched so local Qwen gets a second look.
                continue
            suggestion["model"] = f"{client.model} (Gemini API)"
            offer.setdefault("reviewEvidence", {})["visionAssist"] = suggestion
            annotate_vision_price_check(offer)
            attached += 1
            print(f"Gemini reviewed card {index}/{len(review_offers)}")
    finally:
        document.close()
    return attached


def review_pending_directory(pending_dir: Path, offers_file: Path, client: GeminiClient) -> int:
    """Run Gemini on prepared PDF crops and merge notes into the feed."""
    if not offers_file.is_file():
        print(f"Warning: offers file does not exist: {offers_file}")
        return 0
    payload = json.loads(offers_file.read_text(encoding="utf-8"))
    current_by_id = {
        str(offer.get("offerId", "")): offer
        for offer in payload.get("reviewOffers", [])
    }
    attached = 0
    for review_file in sorted(pending_dir.glob("*.json")):
        if client.quota_exhausted:
            break
        pdf_file = review_file.with_suffix(".pdf")
        if not pdf_file.is_file():
            print(f"Warning: pending PDF is missing for {review_file.name}")
            continue
        try:
            pending_reviews = json.loads(review_file.read_text(encoding="utf-8"))
            pdf_bytes = pdf_file.read_bytes()
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Warning: cannot read pending Gemini input {review_file.name}: {exc}")
            continue
        if not isinstance(pending_reviews, list):
            continue
        # Only cards still without any vision note in the CURRENT feed belong to
        # this pass (pending files may predate carry-forward of earlier notes).
        fresh = []
        for offer in pending_reviews:
            current = current_by_id.get(str(offer.get("offerId", "")))
            evidence = (current or offer).get("reviewEvidence") or {}
            if not evidence.get("visionAssist"):
                fresh.append(offer)
        attached += add_gemini_review_suggestions(pdf_bytes, fresh, client)
        for pending_offer in pending_reviews:
            pending_evidence = pending_offer.get("reviewEvidence") or {}
            suggestion = pending_evidence.get("visionAssist")
            if not isinstance(suggestion, dict):
                continue
            current = current_by_id.get(str(pending_offer.get("offerId", "")))
            if current is None:
                continue
            current_evidence = current.setdefault("reviewEvidence", {})
            if (
                current_evidence.get("pdfSha256") != pending_evidence.get("pdfSha256")
                or current_evidence.get("rawCellText") != pending_evidence.get("rawCellText")
            ):
                continue
            current_evidence["visionAssist"] = suggestion
            annotate_vision_price_check(current)

    if attached:
        write_feed(offers_file, payload)
    print(f"Added {attached} Gemini suggestions ({client.requests_used} API requests)")
    return attached


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pending_dir", type=Path)
    parser.add_argument("--offers-file", type=Path, default=Path("data/offers.json"))
    parser.add_argument("--key-file", type=Path, default=None)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="list pending cards without calling the API")
    args = parser.parse_args()

    if args.dry_run:
        count = 0
        for review_file in sorted(args.pending_dir.glob("*.json")):
            try:
                offers = json.loads(review_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            fresh = [o for o in offers if not (o.get("reviewEvidence") or {}).get("visionAssist")]
            count += len(fresh)
            print(f"{review_file.name}: {len(fresh)} cards for Gemini")
        print(f"Total: {count} cards would go to Gemini")
        return

    api_key = load_api_key(key_file=args.key_file)
    if not api_key:
        print("Gemini review skipped: no API key (set GEMINI_API_KEY or a key file)")
        return

    today = time.strftime("%Y-%m-%d", time.gmtime())
    ledger = _load_ledger(today)
    budget = _daily_budget()
    remaining_today = max(0, budget - int(ledger.get("requests", 0)))
    run_cap = args.max_requests if args.max_requests is not None else int(
        os.environ.get("GEMINI_MAX_REQUESTS", "40"))
    if run_cap == 0:
        run_cap = remaining_today
    effective_cap = min(run_cap, remaining_today) if run_cap > 0 else remaining_today
    if effective_cap <= 0:
        print("Gemini review skipped: daily budget exhausted; local Qwen takes all cards")
        return

    client = GeminiClient(
        api_key,
        model=os.environ.get("GEMINI_REVIEW_MODEL", DEFAULT_MODEL),
        max_requests=effective_cap,
        min_interval=float(os.environ.get("GEMINI_MIN_INTERVAL", "4.5")),
    )
    try:
        review_pending_directory(args.pending_dir, args.offers_file, client)
    finally:
        ledger["requests"] = int(ledger.get("requests", 0)) + client.requests_used
        _save_ledger(ledger)
        print(f"Gemini usage today: {ledger['requests']}/{budget}")


if __name__ == "__main__":
    main()
