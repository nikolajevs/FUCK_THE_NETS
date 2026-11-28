import unittest

from scripts.scrape_spar import (
    STORE,
    discover_booklets,
    fitz,
    is_expired,
    page_campaign,
    page_meta,
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


class SparPageTests(unittest.TestCase):
    def test_expiry_counts_last_day_as_valid(self):
        self.assertTrue(is_expired("2026-11-25", "2026-11-26"))
        self.assertFalse(is_expired("2026-11-25", "2026-11-25"))
        self.assertFalse(is_expired(None, "2026-11-26"))

    def test_page_meta_carries_campaign_and_pdf(self):
        booklet = {**BOOKLET, "pdf": "https://spar.lv/x.pdf"}
        meta = page_meta(booklet)(2, "NEDĒĻAS PIEDĀVĀJUMI no 05.11. līdz 11.11.2026.")
        self.assertEqual(meta["campaignId"], "nedelas-piedavajumi")
        self.assertEqual(meta["source"], "https://spar.lv/x.pdf")

    def test_registry_marks_prices_as_artwork(self):
        self.assertFalse(STORE.prices_in_text)
        self.assertIn("PAR", STORE.vision_hint)

    @unittest.skipIf(fitz is None, "PyMuPDF not installed")
    def test_pages_carry_text_layer_for_name_checks(self):
        from scripts.booklet_vision import open_pdf_pages
        doc = fitz.open()
        doc.new_page().insert_text((50, 100), "Piens Baltais 1L", fontsize=10)
        content = doc.tobytes()
        doc.close()
        booklet = {**BOOKLET, "pdf": "https://spar.lv/x.pdf"}
        with open_pdf_pages(content, page_meta(booklet)) as pages:
            self.assertEqual(len(pages), 1)
            self.assertIn("Baltais", [w[4] for w in pages[0].words])
            self.assertTrue(pages[0].render().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()