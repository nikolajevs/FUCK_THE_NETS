import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.common import (
    FeedContractError, is_alcohol_name, is_expired, riga_date, store_id_for, write_feed,
)
from scripts.feed_schema import enforce_contract, offer_problem
from scripts.stores import ROOT, STORES


def offer(**overrides):
    base = {
        "offerId": "x-1", "name": "Piens Alma 1 l", "price": 1.29, "regularPrice": 1.79,
        "requiresCard": False, "validFrom": "2026-11-24", "validUntil": "2026-11-30",
    }
    base.update(overrides)
    return base


def feed(*offers, store="lidl", reviews=None):
    payload = {
        "store": store, "updatedAt": "2026-11-28T10:00:00+02:00",
        "offers": list(offers), "reviewOffers": list(reviews or []),
        "summary": {"offerVariants": len(offers), "uniqueProducts": len(offers),
                    "reviewOffers": len(reviews or [])},
    }
    if store is None:
        del payload["store"]
    return payload


class OfferRuleTests(unittest.TestCase):
    def test_valid_offer(self):
        self.assertIsNone(offer_problem(offer()))
        self.assertIsNone(offer_problem(offer(regularPrice=None, validFrom=None, validUntil=None)))

    def test_broken_fields(self):
        cases = {
            "name": [offer(name=""), offer(name=None)],
            "offerId": [offer(offerId=""), offer(offerId=7)],
            "price": [offer(price=None), offer(price=0), offer(price=-1), offer(price=True),
                      offer(price=float("nan")), offer(price="1.29"), offer(price=20000)],
            "validUntil": [offer(validUntil="30.11.2026")],
            "validity": [offer(validFrom="2026-12-01", validUntil="2026-11-30")],
            "requiresCard": [offer(requiresCard="yes")],
        }
        for rule, offers in cases.items():
            for item in offers:
                self.assertEqual(offer_problem(item), rule, item)


class EnforceContractTests(unittest.TestCase):
    def test_clean_feed_is_untouched(self):
        payload = feed(offer())
        before = copy.deepcopy(payload)
        self.assertEqual(enforce_contract(payload, "lidl"), [])
        self.assertEqual(payload, before)

    def test_bad_offer_moves_to_review_and_summary_follows(self):
        payload = feed(offer(), offer(offerId="x-2", price=None))
        issues = enforce_contract(payload, "lidl")
        self.assertEqual([o["offerId"] for o in payload["offers"]], ["x-1"])
        self.assertEqual(payload["reviewOffers"][0]["reviewReason"], "contract:price")
        self.assertEqual(payload["summary"]["offerVariants"], 1)
        self.assertEqual(payload["summary"]["reviewOffers"], 1)
        self.assertEqual(issues, ["1 moved to review (price)"])

    def test_regular_price_not_above_price_is_dropped(self):
        payload = feed(offer(regularPrice=1.29), offer(offerId="x-2", regularPrice="2"))
        issues = enforce_contract(payload, "lidl")
        self.assertEqual([o["regularPrice"] for o in payload["offers"]], [None, None])
        self.assertEqual(issues, ["2 regularPrice not above price dropped"])

    def test_duplicate_ids_keep_the_first(self):
        payload = feed(offer(price=1.0), offer(price=2.0))
        enforce_contract(payload, "lidl")
        self.assertEqual([o["price"] for o in payload["offers"]], [1.0])

    def test_missing_store_is_filled_and_wrong_store_refused(self):
        payload = feed(offer(), store=None)
        enforce_contract(payload, "maxima")
        self.assertEqual(payload["store"], "maxima")
        with self.assertRaises(FeedContractError):
            enforce_contract(feed(offer(), store="rimi"), "lidl")
        with self.assertRaises(FeedContractError):
            enforce_contract(feed(offer(), store=None), None)

    def test_unpublishable_payloads(self):
        for payload in ([], {"store": "lidl"}, {"store": "lidl", "offers": {}},
                        {"store": "lidl", "offers": [], "reviewOffers": "x"}):
            with self.assertRaises(FeedContractError):
                enforce_contract(payload, "lidl")

    def test_non_objects_are_dropped(self):
        payload = feed(offer(), "junk", reviews=[None, {"name": "ok"}])
        issues = enforce_contract(payload, "lidl")
        self.assertEqual(len(payload["offers"]), 1)
        self.assertEqual(payload["reviewOffers"], [{"name": "ok"}])
        self.assertIn("1 dropped non-object offer", issues)

    def test_committed_feeds_meet_the_contract(self):
        # Every feed is written through write_feed, so applying the contract
        # again must not move a single published offer.
        for store in STORES:
            path = ROOT / store.feed
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            published = len(payload["offers"])
            enforce_contract(payload, store.id)
            self.assertEqual(len(payload["offers"]), published, store.feed)


class WriteFeedTests(unittest.TestCase):
    def test_writes_lf_json_and_recounts_products(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "out" / "feed.json"
            payload = feed(offer(), offer(offerId="x-2", name="Maize 500 g", price=None))
            payload["summary"]["uniqueProducts"] = 2
            issues = write_feed(path, payload, "lidl")
            raw = path.read_bytes()
            self.assertNotIn(b"\r\n", raw)
            written = json.loads(raw.decode("utf-8"))
            self.assertEqual(written["summary"]["uniqueProducts"], 1)
            self.assertEqual(len(written["reviewOffers"]), 1)
            self.assertEqual(issues, ["1 moved to review (price)"])
            self.assertFalse(path.with_name("feed.json.tmp").exists())

    def test_refused_feed_leaves_the_published_one(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "feed.json"
            path.write_text('{"published": true}', encoding="utf-8")
            with self.assertRaises(FeedContractError):
                write_feed(path, {"store": "lidl"}, "lidl")
            self.assertEqual(path.read_text(encoding="utf-8"), '{"published": true}')

    def test_store_id_comes_from_the_registry(self):
        self.assertEqual(store_id_for(ROOT / "data" / "lidl-offers.json"), "lidl")
        self.assertEqual(store_id_for(ROOT / "data" / "offers.json"), "maxima")
        self.assertIsNone(store_id_for(ROOT / "data" / "unknown.json"))


class HelperTests(unittest.TestCase):
    def test_alcohol(self):
        self.assertTrue(is_alcohol_name("Vīns Voruta 750 ml"))
        self.assertTrue(is_alcohol_name("Alkoholisks dzēriens Barracuda 700 ml"))
        self.assertFalse(is_alcohol_name("Bezalkoholiskais alus 330 ml"))
        self.assertFalse(is_alcohol_name("Piens Alma 2,5% 1 l"))

    def test_riga_dates(self):
        self.assertEqual(riga_date("2026-11-19T22:00:00+00:00"), "2026-11-20")
        self.assertEqual(riga_date("2026-07-15T20:59:59+00:00"), "2026-07-15")
        self.assertTrue(is_expired("2026-11-27", "2026-11-28"))
        self.assertFalse(is_expired("2026-11-28", "2026-11-28"))
        self.assertFalse(is_expired(None, "2026-11-28"))


if __name__ == "__main__":
    unittest.main()
