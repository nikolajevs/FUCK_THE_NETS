import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf as fitz

from scripts.gemini_pdf_review import (
    GeminiClient,
    add_gemini_review_suggestions,
    load_api_key,
    review_pending_directory,
)


def make_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=400, height=400)
    page.insert_text((50, 50), "Test card 5,99 EUR")
    data = document.tobytes()
    document.close()
    return data


def make_offer(offer_id="card-1", with_assist=False) -> dict:
    offer = {
        "offerId": offer_id,
        "name": "Test product",
        "price": 5.99,
        "regularPrice": None,
        "pdfPage": 1,
        "quality": {"issues": ["product_name_ambiguous"]},
        "reviewEvidence": {
            "pdfSha256": "abc123",
            "rawCellText": "Test product 5,99",
            "pdfCrop": [20, 20, 300, 120],
        },
    }
    if with_assist:
        offer["reviewEvidence"]["visionAssist"] = {"decision": "resolved"}
    return offer


def gemini_payload(offer_id="card-1", decision="resolved", confidence=0.95) -> dict:
    return {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({"results": [{
                "offerId": offer_id,
                "decision": decision,
                "confidence": confidence,
                "productName": "Test product",
                "price": 5.99,
                "regularPrice": None,
                "requiresPaldies": False,
                "evidence": "price tag",
                "reason": "clear",
            }]})}]}
        }]
    }


class FakeClient(GeminiClient):
    """Client with canned responses; no network, no pacing."""

    def __init__(self, responses):
        super().__init__("test-key", max_requests=0, min_interval=0, sleep=lambda s: None)
        self.responses = list(responses)
        self.prompts = []

    def _post(self, body):
        self.prompts.append(body)
        if not self.responses:
            return 500, None, "no more canned responses"
        item = self.responses.pop(0)
        if isinstance(item, tuple):
            return item
        return 200, item, ""


class LoadApiKeyTests(unittest.TestCase):
    def test_env_key_wins(self):
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "env-key"}):
            self.assertEqual(load_api_key(), "env-key")

    def test_key_file_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "gemini.token.txt"
            key_file.write_text("file-key\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(load_api_key(key_file=key_file), "file-key")

    def test_missing_key_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertIsNone(load_api_key(key_file=Path(tmp) / "absent"))


class GeminiClientTests(unittest.TestCase):
    def test_resolved_response_parsed(self):
        client = FakeClient([gemini_payload()])
        result = client.generate(b"png", "prompt")
        self.assertEqual(result["results"][0]["offerId"], "card-1")
        self.assertEqual(client.requests_used, 1)

    def test_daily_quota_429_stops_further_requests(self):
        quota = (429, {"error": {"message": "Quota exceeded for requests per day"}}, "per day")
        client = FakeClient([quota, gemini_payload()])
        self.assertIsNone(client.generate(b"png", "p"))
        self.assertTrue(client.quota_exhausted)
        self.assertIsNone(client.generate(b"png", "p"))
        self.assertEqual(client.requests_used, 1)

    def test_run_budget_stops_requests(self):
        client = FakeClient([gemini_payload(), gemini_payload()])
        client.max_requests = 1
        self.assertIsNotNone(client.generate(b"png", "p"))
        self.assertIsNone(client.generate(b"png", "p"))
        self.assertTrue(client.quota_exhausted)

    def test_server_error_retries_then_gives_up(self):
        client = FakeClient([(503, None, "unavailable")] * 3)
        self.assertIsNone(client.generate(b"png", "p"))
        self.assertEqual(client.requests_used, 3)
        self.assertFalse(client.quota_exhausted)

    def test_malformed_content_returns_none(self):
        client = FakeClient([{"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}])
        self.assertIsNone(client.generate(b"png", "p"))


class SuggestionAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.pdf_bytes = make_pdf_bytes()

    def test_resolved_card_gets_advisory_note(self):
        offer = make_offer()
        client = FakeClient([gemini_payload()])
        attached = add_gemini_review_suggestions(self.pdf_bytes, [offer], client)
        self.assertEqual(attached, 1)
        assist = offer["reviewEvidence"]["visionAssist"]
        self.assertEqual(assist["decision"], "resolved")
        self.assertIn("Gemini API", assist["model"])
        self.assertEqual(assist["price"], 5.99)

    def test_unclear_card_left_for_local_qwen(self):
        offer = make_offer()
        client = FakeClient([gemini_payload(decision="unclear", confidence=0.3)])
        attached = add_gemini_review_suggestions(self.pdf_bytes, [offer], client)
        self.assertEqual(attached, 0)
        self.assertNotIn("visionAssist", offer["reviewEvidence"])

    def test_wrong_offer_id_rejected(self):
        offer = make_offer("card-1")
        client = FakeClient([gemini_payload(offer_id="someone-else")])
        attached = add_gemini_review_suggestions(self.pdf_bytes, [offer], client)
        self.assertEqual(attached, 0)
        self.assertNotIn("visionAssist", offer["reviewEvidence"])

    def test_invalid_price_rejected(self):
        offer = make_offer()
        payload = gemini_payload()
        payload["candidates"][0]["content"]["parts"][0]["text"] = json.dumps({"results": [{
            "offerId": "card-1", "decision": "resolved", "confidence": 0.9, "price": -3,
        }]})
        client = FakeClient([payload])
        self.assertEqual(add_gemini_review_suggestions(self.pdf_bytes, [offer], client), 0)

    def test_missing_crop_skipped_without_request(self):
        offer = make_offer()
        del offer["reviewEvidence"]["pdfCrop"]
        client = FakeClient([gemini_payload()])
        self.assertEqual(add_gemini_review_suggestions(self.pdf_bytes, [offer], client), 0)
        self.assertEqual(client.requests_used, 0)


class PendingDirectoryTests(unittest.TestCase):
    def test_merge_respects_evidence_and_skips_assisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending_dir = Path(tmp) / "pending"
            pending_dir.mkdir()
            offers_file = Path(tmp) / "offers.json"

            fresh = make_offer("fresh-card")
            assisted = make_offer("assisted-card", with_assist=True)
            stale = make_offer("stale-card")
            (pending_dir / "abc123.json").write_text(
                json.dumps([fresh, assisted, stale]), encoding="utf-8")
            (pending_dir / "abc123.pdf").write_bytes(make_pdf_bytes())

            current_fresh = make_offer("fresh-card")
            current_assisted = make_offer("assisted-card", with_assist=True)
            current_stale = make_offer("stale-card")
            current_stale["reviewEvidence"]["rawCellText"] = "changed text"
            offers_file.write_text(json.dumps({
                "store": "maxima",
                "offers": [],
                "reviewOffers": [current_fresh, current_assisted, current_stale],
            }), encoding="utf-8")

            client = FakeClient([
                gemini_payload(offer_id="fresh-card"),
                gemini_payload(offer_id="stale-card", decision="unclear", confidence=0.3),
            ])
            attached = review_pending_directory(pending_dir, offers_file, client)
            self.assertEqual(attached, 1)
            # Fresh card + stale card (its text changed, note must be re-earned);
            # the already-assisted card is never re-sent.
            self.assertEqual(client.requests_used, 2)

            payload = json.loads(offers_file.read_text(encoding="utf-8"))
            by_id = {o["offerId"]: o for o in payload["reviewOffers"]}
            self.assertIn("visionAssist", by_id["fresh-card"]["reviewEvidence"])
            self.assertEqual(by_id["assisted-card"]["reviewEvidence"]["visionAssist"],
                             {"decision": "resolved"})
            self.assertNotIn("visionAssist", by_id["stale-card"]["reviewEvidence"])


if __name__ == "__main__":
    unittest.main()