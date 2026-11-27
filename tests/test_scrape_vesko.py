import unittest

import pymupdf as fitz

from scripts.scrape_vesko import (
    _cent_value,
    _clean_name,
    extract_page,
    is_alcohol_name,
    parse_period,
)


class PeriodTests(unittest.TestCase):
    def test_full_range(self):
        self.assertEqual(parse_period("02.11.-29.11.2026."), ("2026-11-02", "2026-11-29"))

    def test_missing(self):
        self.assertEqual(parse_period("nav datuma"), (None, None))


class AlcoholTests(unittest.TestCase):
    def test_alcoholic_dropped(self):
        self.assertTrue(is_alcohol_name("Alus Cēsu Oriģinālais 500 ml"))
        self.assertTrue(is_alcohol_name("Vīns Voruta 750 ml"))
        self.assertTrue(is_alcohol_name("Alkoholisks dzēriens Barracuda 700 ml"))

    def test_non_alcoholic_kept(self):
        self.assertFalse(is_alcohol_name("Bezalkoholiskais alus 330 ml"))
        self.assertFalse(is_alcohol_name("Piens Alma 2,5% Valio 1 l"))
        self.assertFalse(is_alcohol_name("Enerģijas Dzēriens Red Bull 250 ml"))


class CleanNameTests(unittest.TestCase):
    def test_drops_unit_price_discount_and_url(self):
        tokens = ["PIENS", "ALMA", "2,5%", "Valio", "1", "l", "(3,38", "€/kg)", "-28%", "www.veskoveikals.lv"]
        self.assertEqual(_clean_name(tokens), "Piens Alma 2,5% Valio 1 l")

    def test_drops_vertical_single_letters(self):
        self.assertEqual(_clean_name(["ŠĶIŅĶIS", "Ū", "S", "U", "M", "Zemnieku", "1", "kg"]),
                         "Šķiņķis Zemnieku 1 kg")

    def test_keeps_final_size_unit(self):
        self.assertEqual(_clean_name(["Piens", "Alma", "1", "l"]), "Piens Alma 1 l")


class CentValueTests(unittest.TestCase):
    def test_cent_integers(self):
        self.assertEqual(_cent_value("089"), 0.89)
        self.assertEqual(_cent_value("1225"), 12.25)
        self.assertIsNone(_cent_value("5"))
        self.assertIsNone(_cent_value("12,25"))


class ExtractPageTests(unittest.TestCase):
    def _page(self):
        doc = fitz.open()
        page = doc.new_page(width=510, height=794)
        page.insert_textbox(fitz.Rect(35, 33, 160, 70), "PIENS ALMA\n2,5% Valio 1 l", fontsize=9)
        page.insert_text(fitz.Point(57, 120), "-28%", fontsize=11)
        page.insert_text(fitz.Point(49, 150), "089", fontsize=24)
        page.insert_text(fitz.Point(60, 166), "125", fontsize=20)
        return doc, page

    def test_sale_regular_and_name(self):
        doc, page = self._page()
        booklet = {"id": "vesko-test", "name": "Vesko buklets", "pdf": "https://veskoveikals.lv/x.pdf",
                   "validFrom": "2026-11-02", "validUntil": "2026-11-29"}
        offers, reviews = extract_page(page, 1, booklet, None)
        doc.close()
        self.assertEqual(reviews, [])
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual((offer["price"], offer["regularPrice"]), (0.89, 1.25))
        self.assertEqual(offer["discountPercent"], 28)
        self.assertTrue(offer["name"].startswith("Piens Alma"))
        self.assertEqual(offer["store"], "vesko")
        self.assertEqual(offer["validFrom"], "2026-11-02")


if __name__ == "__main__":
    unittest.main()