import unittest

from scripts.scrape_spar import discover_booklets, parse_period


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


if __name__ == "__main__":
    unittest.main()