import unittest

import pymupdf as fitz

from scripts.scrape_lidl import dec_val, extract_page


BOOKLET = {
    "id": "lidl-test",
    "name": "Aktuālais akciju buklets",
    "pdf": "https://assets.leaflets.schwarz/test.pdf",
    "validFrom": "2026-11-23",
    "validUntil": "2026-11-29",
}


def _card(page, x, name, sale, regular=None, plus=False):
    page.insert_text(fitz.Point(x, 100), name, fontsize=12)
    page.insert_text(fitz.Point(x, 150), sale, fontsize=40)
    if regular is not None:
        page.insert_text(fitz.Point(x + 100, 150), regular, fontsize=9)
    if plus:
        page.insert_text(fitz.Point(x, 170), "Ar Lidl Plus", fontsize=7)


class DecimalTests(unittest.TestCase):
    def test_comma_and_dot_decimals(self):
        self.assertEqual(dec_val("0,69"), 0.69)
        self.assertEqual(dec_val("16.99"), 16.99)

    def test_rejects_non_prices(self):
        self.assertIsNone(dec_val("12345"))
        self.assertIsNone(dec_val("x,xx"))
        self.assertIsNone(dec_val("7804549"))
        self.assertIsNone(dec_val("-31%"))


class ExtractionTests(unittest.TestCase):
    def test_sale_regular_and_card(self):
        doc = fitz.open()
        page = doc.new_page(width=467, height=779)
        _card(page, 20, "PILOS Piens", "0,69", regular="1,09", plus=True)
        _card(page, 250, "LENOR mikstinatajs", "5,69")
        offers, reviews = extract_page(page, 0, BOOKLET)
        doc.close()
        by_name = {o["name"]: o for o in offers}
        self.assertIn("PILOS Piens", by_name)
        self.assertIn("LENOR mikstinatajs", by_name)
        pilos = by_name["PILOS Piens"]
        self.assertEqual(pilos["price"], 0.69)
        self.assertEqual(pilos["regularPrice"], 1.09)
        self.assertTrue(pilos["requiresCard"])
        self.assertEqual(pilos["cardName"], "Lidl Plus")
        self.assertEqual(pilos["store"], "lidl")
        lenor = by_name["LENOR mikstinatajs"]
        self.assertEqual(lenor["price"], 5.69)
        self.assertFalse(lenor["requiresCard"])
        self.assertIsNone(lenor["regularPrice"])

    def test_deterministic_offer_ids(self):
        doc = fitz.open()
        page = doc.new_page(width=467, height=779)
        _card(page, 20, "PILOS Piens", "0,69")
        _card(page, 250, "LENOR mikstinatajs", "5,69")
        first, _ = extract_page(page, 0, BOOKLET)
        second, _ = extract_page(page, 0, BOOKLET)
        doc.close()
        self.assertEqual([o["offerId"] for o in first], [o["offerId"] for o in second])

    def test_merged_brands_go_to_review(self):
        doc = fitz.open()
        page = doc.new_page(width=467, height=779)
        page.insert_text(fitz.Point(20, 90), "PHILIPS Baterijas", fontsize=12)
        page.insert_text(fitz.Point(20, 105), "ARIEL Pulveris", fontsize=12)
        page.insert_text(fitz.Point(20, 150), "6,99", fontsize=40)
        page.insert_text(fitz.Point(250, 100), "PILOS Piens", fontsize=12)
        page.insert_text(fitz.Point(250, 150), "0,52", fontsize=40)
        offers, reviews = extract_page(page, 0, BOOKLET)
        doc.close()
        self.assertTrue(any(o.get("reviewReason") for o in reviews))
        self.assertFalse(any("PHILIPS" in o["name"] and "ARIEL" in o["name"] for o in offers))

    def test_template_page_skipped(self):
        doc = fitz.open()
        page = doc.new_page(width=467, height=779)
        page.insert_text(fitz.Point(20, 100), "Product name XXX g", fontsize=12)
        page.insert_text(fitz.Point(20, 150), "0,69", fontsize=40)
        page.insert_text(fitz.Point(250, 150), "1,99", fontsize=40)
        offers, reviews = extract_page(page, 0, BOOKLET)
        doc.close()
        self.assertEqual(offers, [])
        self.assertEqual(reviews, [])


if __name__ == "__main__":
    unittest.main()