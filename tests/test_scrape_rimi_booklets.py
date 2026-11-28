import unittest
from datetime import date

from scripts.scrape_rimi_booklets import _carry_forward_booklet, _offer_is_current


class CarryForwardValidityTests(unittest.TestCase):
    def test_offer_is_current_through_its_final_valid_day(self):
        offer = {"validFrom": "2026-11-24", "validUntil": "2026-11-28"}
        self.assertTrue(_offer_is_current(offer, date(2026, 11, 28)))

    def test_expired_and_not_yet_active_offers_are_not_carried(self):
        expired = {"validUntil": "2026-11-27"}
        future = {"validFrom": "2026-11-29"}
        today = date(2026, 11, 28)
        self.assertFalse(_offer_is_current(expired, today))
        self.assertFalse(_offer_is_current(future, today))

    def test_missing_or_unreadable_dates_do_not_drop_an_offer(self):
        self.assertTrue(_offer_is_current({}, date(2026, 11, 28)))
        self.assertTrue(_offer_is_current({"validUntil": "unknown"}, date(2026, 11, 28)))

    def test_failed_booklet_keeps_only_its_unexpired_offers_and_marks_them_stale(self):
        booklet = {"id": "weekly", "name": "Current weekly", "source": "https://example.test/weekly"}
        previous_offers = [
            {"offerId": "current", "campaignId": "weekly", "validUntil": "2026-11-28"},
            {"offerId": "expired", "campaignId": "weekly", "validUntil": "2026-11-27"},
            {"offerId": "other", "campaignId": "mini"},
        ]
        previous_reviews = [{"offerId": "review", "campaignId": "weekly"}]
        previous_catalogues = [{"id": "weekly", "name": "Previous weekly", "complete": True}]
        previous_campaigns = [{"id": "weekly", "name": "Previous weekly"}]

        offers, reviews, catalogue, campaign = _carry_forward_booklet(
            booklet, previous_offers, previous_reviews, previous_catalogues,
            previous_campaigns, date(2026, 11, 28),
        )

        self.assertEqual([offer["offerId"] for offer in offers], ["current"])
        self.assertTrue(offers[0]["stale"])
        self.assertTrue(reviews[0]["stale"])
        self.assertEqual(catalogue["name"], "Previous weekly")
        self.assertTrue(catalogue["stale"])
        self.assertEqual(catalogue["offerVariants"], 1)
        self.assertEqual(catalogue["reviewOffers"], 1)
        self.assertEqual(campaign["name"], "Previous weekly")


if __name__ == "__main__":
    unittest.main()
