import unittest

from scripts.scrape_spar import (
    carried_pages,
    discover_booklets,
    extract_booklet,
    fitz,
    is_expired,
    name_blocks,
    offers_from_vision,
    page_campaign,
    parse_period,
    parse_subperiod,
)


class DiscoveryTests(unittest.TestCase):
    def test_dearpdf_source_unescaped(self):
        html = """
        <div class="df-element df-lite" data-slug="427" data-title="buklets" id="df_427"></div>
        <script class="df-shortcode-script">window.df_option_427 = {"source":"http:\\/\\/spar.lv\\/wp-content\\/uploads\\/2023\\/04\\/light-SPARavize78.pdf","viewerType":"flipbook","slug":"427","id":427};</script>
        """
        booklets = discover_booklets(html)
        self.assertEqual(len(booklets), 1)
        self.assertEqual(booklets[0]["pdf"],
                         "https://spar.lv/wp-content/uploads/2023/04/light-SPARavize78.pdf")

    def test_no_booklet(self):
        self.assertEqual(discover_booklets("<p>nav</p>"), [])

    def test_deduplicates_same_source(self):
        block = ('<script>window.df_option_1 = {"source":"https:\\/\\/spar.lv\\/a.pdf"};</script>'
                 '<script>window.df_option_2 = {"source":"https:\\/\\/spar.lv\\/a.pdf"};</script>')
        self.assertEqual(len(discover_booklets(block)), 1)


class PeriodTests(unittest.TestCase):
    def test_full_range(self):
        self.assertEqual(parse_period("05.11. - 25.11.2026."), ("2026-11-05", "2026-11-25"))

    def test_range_without_year_uses_current(self):
        from datetime import date
        vf, vu = parse_period("05.11. - 25.11.", date(2026, 11, 10))
        self.assertEqual((vf, vu), ("2026-11-05", "2026-11-25"))

    def test_missing(self):
        self.assertEqual(parse_period("nav"), (None, None))

    def test_subperiod(self):
        self.assertEqual(parse_subperiod("no 05.11. līdz 11.11.2026."),
                         ("2026-11-05", "2026-11-11"))


BOOKLET = {"id": "spar-t", "name": "SPAR akciju avīze", "pdf": "https://spar.lv/x.pdf",
           "validFrom": "2026-11-05", "validUntil": "2026-11-25"}
META = {"campaign": "SPAR akciju avīze", "campaignId": "spar-t",
        "validFrom": "2026-11-05", "validUntil": "2026-11-25"}


def blocks(*names):
    return [{"text": n, "tokens": set(n.lower().split())} for n in names]


def vision(*items):
    return {"results": list(items)}


def item(idx, **kw):
    base = {"id": idx, "price": None, "regularPrice": None, "discountPercent": None,
            "multiBuyCount": None, "multiBuyTotal": None, "multiBuyRegularTotal": None,
            "requiresCard": False, "confidence": 0.9}
    base.update(kw)
    return base


class OffersFromVisionTests(unittest.TestCase):
    def test_normal_card(self):
        offers, reviews = offers_from_vision(
            vision(item(1, price=0.79, regularPrice=1.25)),
            blocks("Piens Baltais 2% 1L"), BOOKLET, 1, META, "test-model")
        self.assertEqual(reviews, [])
        self.assertEqual(len(offers), 1)
        self.assertEqual((offers[0]["price"], offers[0]["regularPrice"]), (0.79, 1.25))
        self.assertEqual(offers[0]["store"], "spar")
        self.assertEqual(offers[0]["priceSource"], "vision")

    def test_bundle_divided_locally(self):
        offers, reviews = offers_from_vision(
            vision(item(1, multiBuyCount=2, multiBuyTotal=2.00, multiBuyRegularTotal=2.98)),
            blocks("Mini pica ar šķiņķi un papriku 178G"), BOOKLET, 0, META, "m")
        self.assertEqual(reviews, [])
        self.assertEqual(offers[0]["price"], 1.00)
        self.assertEqual(offers[0]["regularPrice"], 1.49)
        self.assertEqual(offers[0]["multiBuy"], "2")

    def test_badge_mismatch_goes_to_review(self):
        offers, reviews = offers_from_vision(
            vision(item(1, price=1.00, regularPrice=1.10, discountPercent=50)),
            blocks("Piens Baltais 1L"), BOOKLET, 1, META, "m")
        self.assertEqual(offers, [])
        self.assertEqual(reviews[0]["reviewReason"], "price_mismatch")

    def test_missing_price_goes_to_review(self):
        offers, reviews = offers_from_vision(
            vision(item(1, price=None, confidence=0.0)),
            blocks("Karstais dzēriens Gutta 1L"), BOOKLET, 6, META, "m")
        self.assertEqual(offers, [])
        self.assertEqual(reviews[0]["reviewReason"], "missing_price")

    def test_low_confidence_goes_to_review(self):
        offers, reviews = offers_from_vision(
            vision(item(1, price=1.00, confidence=0.2)),
            blocks("Piens Baltais 1L"), BOOKLET, 1, META, "m")
        self.assertEqual(offers, [])
        self.assertEqual(reviews[0]["reviewReason"], "low_confidence")

    def test_alcohol_dropped(self):
        offers, reviews = offers_from_vision(
            vision(item(1, price=5.00)),
            blocks("Alus Cēsu Oriģinālais 500 ml"), BOOKLET, 1, META, "m")
        self.assertEqual(offers, [])
        self.assertEqual(reviews, [])

    def test_out_of_range_id_ignored(self):
        offers, reviews = offers_from_vision(
            vision(item(9, price=1.00)), blocks("Piens Baltais 1L"), BOOKLET, 1, META, "m")
        self.assertEqual(offers, [])
        self.assertEqual(reviews, [])


class PageCampaignTests(unittest.TestCase):
    def test_weekly(self):
        meta = page_campaign("NEDĒĻAS PIEDĀVĀJUMI no 05.11. līdz 11.11.2026.", BOOKLET)
        self.assertEqual(meta["campaignId"], "nedelas-piedavajumi")
        self.assertEqual(meta["validUntil"], "2026-11-11")

    def test_everyday(self):
        meta = page_campaign("Īpaši piedāvājumi katru dienu!", BOOKLET)
        self.assertEqual(meta["campaignId"], "katru-dienu")

    def test_main(self):
        meta = page_campaign("Deserta sieriņš Baltais", BOOKLET)
        self.assertEqual(meta["campaignId"], "spar-t")
        self.assertEqual(meta["validUntil"], "2026-11-25")


class NameBlocksTests(unittest.TestCase):
    def test_two_columns_stay_separate(self):
        # two products side by side, same baseline; must not merge
        words = [
            (20, 100, 60, 113, "Piens", 0, 0, 0), (62, 100, 90, 113, "Baltais", 0, 0, 1),
            (20, 110, 40, 123, "1L", 0, 1, 0),
            (200, 100, 240, 113, "Kefīrs", 0, 0, 0), (242, 100, 270, 113, "ALMA", 0, 0, 1),
            (200, 110, 220, 123, "1KG", 0, 1, 0),
        ]
        texts = [b["text"] for b in name_blocks(words)]
        self.assertEqual(len(texts), 2)
        self.assertTrue(any(t.startswith("Piens") for t in texts))
        self.assertTrue(any(t.startswith("Kefīrs") for t in texts))


class IdleWorkTests(unittest.TestCase):
    PDF = "https://spar.lv/x.pdf"

    def test_expiry_counts_last_day_as_valid(self):
        self.assertTrue(is_expired("2026-11-25", "2026-11-26"))
        self.assertFalse(is_expired("2026-11-25", "2026-11-25"))
        self.assertFalse(is_expired(None, "2026-11-26"))

    def test_unchanged_pdf_carries_resolved_pages_only(self):
        previous = {
            "catalogues": [{"source": self.PDF, "pdfSha256": "abc", "resolvedPages": [1, 2]}],
            "offers": [
                {"offerId": "a", "source": self.PDF, "pdfPage": 1},
                {"offerId": "b", "source": self.PDF, "pdfPage": 3},
                {"offerId": "c", "source": "https://spar.lv/other.pdf", "pdfPage": 1},
            ],
            "reviewOffers": [{"offerId": "r", "source": self.PDF, "pdfPage": 2}],
        }
        pages, offers, reviews = carried_pages(previous, self.PDF, "abc")
        self.assertEqual(pages, {1, 2})
        self.assertEqual([o["offerId"] for o in offers], ["a"])
        self.assertEqual([o["offerId"] for o in reviews], ["r"])

    def test_changed_pdf_carries_nothing(self):
        previous = {
            "catalogues": [{"source": self.PDF, "pdfSha256": "old", "resolvedPages": [1]}],
            "offers": [{"offerId": "a", "source": self.PDF, "pdfPage": 1}],
        }
        self.assertEqual(carried_pages(previous, self.PDF, "new"), (set(), [], []))
        self.assertEqual(carried_pages({}, self.PDF, "new"), (set(), [], []))

    @unittest.skipIf(fitz is None, "PyMuPDF not installed")
    def test_resolved_pages_are_not_sent_to_vision_again(self):
        doc = fitz.open()
        for _ in range(2):
            doc.new_page().insert_text((50, 100), "Piens Baltais 1L", fontsize=10)
        content = doc.tobytes()
        doc.close()

        class FakeClient:
            calls = 0

            def generate(self, png, prompt):
                FakeClient.calls += 1
                return {"results": []}

        offers, reviews, resolved = extract_booklet(content, BOOKLET, FakeClient(), None, {1})
        self.assertEqual(FakeClient.calls, 1)
        self.assertEqual(resolved, {2})
        self.assertEqual((offers, reviews), ([], []))


if __name__ == "__main__":
    unittest.main()