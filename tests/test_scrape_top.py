import unittest
from datetime import datetime, timezone

from scripts.scrape_top import (
    DESIGNATION_CAMPAIGNS,
    display_name,
    offer_period,
    product_to_offer,
    resolve_campaign,
    riga_date,
    riga_offset,
)


def booklet(booklet_id="top-buklets-20112026", name="top! buklets",
            start="2026-11-19T22:00:00+00:00", end="2026-12-01T21:59:59+00:00"):
    return {
        "id": booklet_id,
        "name": name,
        "pdf": "https://cdn.etop.lv/cms/discounts/test.pdf",
        "preview": None,
        "validFrom": riga_date(start),
        "validUntil": riga_date(end),
    }


def product(**overrides):
    base = {
        "id": "p-1",
        "keyCode": "tig-000000001",
        "name": "KAFIJA MALTA JACOBS KRONUNG 250G",
        "brand": "JACOBS",
        "price": 4.69,
        "discountedPrice": 4.69,
        "loyaltyPrice": None,
        "discountType": 3,
        "regPriceMin": 5.79,
        "regPriceMax": 6.59,
        "discountStartDate": "2026-11-19T22:00:00+00:00",
        "discountEndDate": "2026-11-30T22:00:00+00:00",
        "promoDesignation": "A1",
        "normalImageUrl": "https://cdn.etop.lv/products/8711000576533-328x328.png",
        "ean": "8711000576533",
    }
    base.update(overrides)
    return base


class DisplayNameTests(unittest.TestCase):
    def test_title_cases_latvian_names_and_units(self):
        self.assertEqual(
            display_name("DESA ĀDAŽI GURMĀNU AR ČEDARAS SIERU 350G"),
            "Desa Ādaži Gurmānu Ar Čedaras Sieru 350g",
        )
        self.assertEqual(display_name("PIENS ALMA 2.5% 2L"), "Piens Alma 2.5% 2l")

    def test_keeps_brand_acronyms_and_multiplies(self):
        self.assertEqual(
            display_name("GARDUMS KAĶIEM DREAMIES CAT C&T BOX JUNIOR 150G"),
            "Gardums Kaķiem Dreamies Cat C&T Box Junior 150g",
        )
        self.assertEqual(
            display_name("BARĪBA KAĶIEM KONS.SHEBA GAĻAS IZLASE 85GX4"),
            "Barība Kaķiem Kons.sheba Gaļas Izlase 85gx4",
        )

    def test_collapses_whitespace(self):
        self.assertEqual(
            display_name("KAUSĒTAIS SIERS TERE  AR GRILĒTU VISTU  200G"),
            "Kausētais Siers Tere Ar Grilētu Vistu 200g",
        )


class RigaTimeTests(unittest.TestCase):
    def test_winter_dates_roll_to_local_day(self):
        self.assertEqual(riga_date("2026-11-19T22:00:00+00:00"), "2026-11-20")
        self.assertEqual(riga_date("2026-11-30T21:59:59+00:00"), "2026-11-30")

    def test_summer_offset_is_three_hours(self):
        self.assertEqual(riga_date("2026-07-15T21:00:00+00:00"), "2026-07-16")
        self.assertEqual(riga_date("2026-07-15T20:59:59+00:00"), "2026-07-15")

    def test_dst_switch_follows_eu_rules(self):
        # Last Sunday of March 2026 is the 29th; switch at 01:00 UTC.
        before = datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)
        after = datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(riga_offset(before).total_seconds(), 2 * 3600)
        self.assertEqual(riga_offset(after).total_seconds(), 3 * 3600)
        # Last Sunday of October 2026 is the 25th; switch back at 01:00 UTC.
        back = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(riga_offset(back).total_seconds(), 2 * 3600)

    def test_offer_period_end_is_exclusive(self):
        self.assertEqual(
            offer_period("2026-11-19T22:00:00+00:00", "2026-11-30T22:00:00+00:00"),
            ("2026-11-20", "2026-11-30"),
        )
        self.assertEqual(
            offer_period("2026-11-19T22:00:00+00:00", "2026-12-14T22:00:00+00:00"),
            ("2026-11-20", "2026-12-14"),
        )
        self.assertEqual(offer_period(None, None), (None, None))


class CampaignTests(unittest.TestCase):
    def test_booklet_campaign_matches_by_period(self):
        booklets = [
            booklet(),
            booklet("lielo-veikalu-top-buklets-20112026", "Lielo veikalu top! buklets",
                    end="2026-12-15T21:59:59+00:00"),
        ]
        weekly = product(promoDesignation="A1")
        large = product(promoDesignation="LA", discountEndDate="2026-12-14T22:00:00+00:00")
        self.assertEqual(resolve_campaign(weekly, booklets)[0], "top-buklets-20112026")
        self.assertEqual(resolve_campaign(large, booklets)[0], "lielo-veikalu-top-buklets-20112026")
        self.assertEqual(resolve_campaign(large, booklets)[2], booklets[1]["pdf"])

    def test_non_booklet_campaigns_use_designation(self):
        for designation, (campaign_id, label) in DESIGNATION_CAMPAIGNS.items():
            campaign_id_, label_, source = resolve_campaign(product(promoDesignation=designation + "+D"), [])
            self.assertEqual(campaign_id_, campaign_id)
            self.assertEqual(label_, label)
            self.assertTrue(source.startswith("https://etop.lv/"))

    def test_fallback_when_booklet_missing(self):
        campaign_id, label, _ = resolve_campaign(product(promoDesignation="A1"), [])
        self.assertEqual(campaign_id, "top-buklets")
        self.assertEqual(label, "top! buklets")


class OfferConversionTests(unittest.TestCase):
    def test_regular_discount_uses_min_regular_price(self):
        offer = product_to_offer(product(), [booklet()])
        self.assertEqual(offer["price"], 4.69)
        self.assertEqual(offer["regularPrice"], 5.79)
        self.assertFalse(offer["requiresCard"])
        self.assertEqual(offer["name"], "Kafija Malta Jacobs Kronung 250g")
        self.assertEqual(offer["brand"], "Jacobs")
        self.assertEqual(offer["store"], "top")
        self.assertEqual(offer["offerId"], "top-tig-000000001")
        self.assertEqual((offer["validFrom"], offer["validUntil"]), ("2026-11-20", "2026-11-30"))
        self.assertNotIn("reviewReason", offer)

    def test_loyalty_price_becomes_sale_price_with_card(self):
        offer = product_to_offer(
            product(price=3.29, discountedPrice=3.29, loyaltyPrice=2.99,
                    discountType=9, promoDesignation="TC", regPriceMin=0, regPriceMax=0),
            [booklet()],
        )
        self.assertEqual(offer["price"], 2.99)
        self.assertEqual(offer["regularPrice"], 3.29)
        self.assertTrue(offer["requiresCard"])
        self.assertEqual(offer["cardName"], "mans top!")
        self.assertEqual(offer["campaignId"], "top-cena")

    def test_everyday_low_price_has_no_regular_price(self):
        offer = product_to_offer(
            product(price=1.49, discountedPrice=1.49, loyaltyPrice=1.49,
                    discountType=21, promoDesignation="PP", regPriceMin=0, regPriceMax=0),
            [booklet()],
        )
        self.assertEqual(offer["price"], 1.49)
        self.assertIsNone(offer["regularPrice"])
        self.assertFalse(offer["requiresCard"])
        self.assertEqual(offer["campaignId"], "zema-cena")

    def test_lowest_price_guarantee(self):
        offer = product_to_offer(
            product(price=1.29, discountedPrice=1.29, loyaltyPrice=None,
                    discountType=18, promoDesignation="ZG", regPriceMin=0, regPriceMax=0),
            [booklet()],
        )
        self.assertEqual(offer["price"], 1.29)
        self.assertIsNone(offer["regularPrice"])
        self.assertEqual(offer["campaignId"], "zemaka-cena")

    def test_missing_price_goes_to_review(self):
        offer = product_to_offer(product(price=0, discountedPrice=0), [booklet()])
        self.assertIsNone(offer["price"])
        self.assertEqual(offer["reviewReason"], "missing_price")

    def test_weak_name_goes_to_review(self):
        offer = product_to_offer(product(name="AB"), [booklet()])
        self.assertEqual(offer["reviewReason"], "weak_name")


if __name__ == "__main__":
    unittest.main()