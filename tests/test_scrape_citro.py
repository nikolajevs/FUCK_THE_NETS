import unittest

import pymupdf as fitz

from scripts.scrape_citro import (
    _clean_name,
    discover_booklet,
    extract_page,
    is_alcohol_name,
    parse_period_text,
)


class DiscoveryTests(unittest.TestCase):
    def _session(self, html):
        class R:
            text = html
            def raise_for_status(self): pass
        class S:
            def get(self, url, timeout=0): return R()
        return S()

    def test_prefers_citro_over_eldo_and_lottery(self):
        html = """
        <a href="https://citro.lv/wp-content/uploads/2026/11/EldoAvize_12E_05.11.-02.12.2026-web.pdf">Eldo</a>
        <a href="https://citro.lv/wp-content/uploads/2026/11/Citro_Avize_PDF_lidz_10MB.pdf">Citro</a>
        <a href="https://citro.lv/wp-content/uploads/2026/11/CitroLoterija2026-Buklets-A4.pdf">Loterija</a>
        """
        booklet = discover_booklet(self._session(html))
        self.assertIsNotNone(booklet)
        self.assertIn("Citro_Avize", booklet["pdf"])
        self.assertNotIn("Eldo", booklet["pdf"])
        self.assertNotIn("Loterija", booklet["pdf"])

    def test_no_pdf_returns_none(self):
        self.assertIsNone(discover_booklet(self._session("<p>nav</p>")))


class PeriodTests(unittest.TestCase):
    def test_period_from_text(self):
        vf, vu = parse_period_text("Piedāvājums spēkā no 19.11. līdz 02.12.2026.")
        self.assertEqual((vf, vu), ("2026-11-19", "2026-12-02"))

    def test_period_missing(self):
        self.assertEqual(parse_period_text("nav datuma"), (None, None))


class CleanNameTests(unittest.TestCase):
    def test_unit_tokens_and_noise(self):
        tokens = ["MEDUS", "TORTE", "800", "g,", "7,49", "€/kg", "LATVIJAS", "PRODUKTS"]
        self.assertEqual(_clean_name(tokens), "Medus Torte 800 g")

    def test_alcohol(self):
        self.assertTrue(is_alcohol_name("Alus Cēsu 500 ml"))
        self.assertFalse(is_alcohol_name("Sula Cido 1 l"))


class ExtractPageTests(unittest.TestCase):
    def test_standard_card_name_right_of_price(self):
        doc = fitz.open()
        page = doc.new_page(width=468, height=842)
        page.insert_text(fitz.Point(243, 84), "599", fontsize=24)
        page.insert_textbox(fitz.Rect(282, 66, 420, 88), "MEDUS TORTE", fontsize=9)
        page.insert_textbox(fitz.Rect(282, 88, 420, 100), "800 g", fontsize=6)
        page.insert_text(fitz.Point(243, 96), "6,69-7,79 €", fontsize=8)
        booklet = {"id": "citro-avize", "name": "Citro akciju avīze", "pdf": "https://citro.lv/x.pdf",
                   "validFrom": "2026-11-19", "validUntil": "2026-12-02"}
        offers, reviews = extract_page(page, 1, booklet, None)
        doc.close()
        self.assertEqual(len(offers), 1, offers + reviews)
        offer = offers[0]
        self.assertEqual(offer["price"], 5.99)
        self.assertEqual(offer["regularPrice"], 6.69)
        self.assertTrue(offer["name"].startswith("Medus Torte"))
        self.assertEqual(offer["store"], "citro")


if __name__ == "__main__":
    unittest.main()