import unittest

import pymupdf as fitz

from scripts.scrape_lidl import (
    SIZE_LINE_RE,
    brand_from_name,
    dec_val,
    dedupe_offers,
    discount_pair,
    extract_page,
    name_with_size,
    pick_badge,
    pick_multi_buy,
    pick_regular_price,
)


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


# Geometry taken from the 23.11. weekly booklet: the sale price span, the
# crossed-out price (LidlFontPro-Book 10) and the -NN% badge above it.
ANCHOR = {"x0": 379.0, "x1": 445.0, "y0": 326.0, "y1": 382.0, "v": 0.99}


class PricePairTests(unittest.TestCase):
    def test_regular_price_above_right_half_of_sale_price(self):
        regular = [{"x0": 430.0, "y0": 313.0, "v": 1.29},      # this card
                   {"x0": 250.0, "y0": 313.0, "v": 1.79}]      # card to the left
        self.assertEqual(pick_regular_price(ANCHOR, regular), 1.29)

    def test_badge_confirms_or_rejects_the_pair(self):
        badge = pick_badge(ANCHOR, [{"x0": 382.0, "y0": 307.0, "v": 23}])
        self.assertEqual(badge, 23)
        self.assertEqual(discount_pair(0.99, 1.29, badge), (1.29, 23))
        # A "2. precei -61%" style badge that does not fit the pair drops it.
        self.assertEqual(discount_pair(0.99, 1.29, 61), (None, None))

    def test_badge_alone_never_creates_a_discount(self):
        self.assertEqual(discount_pair(0.39, None, 61), (None, None))

    def test_regular_price_must_be_higher_than_sale(self):
        self.assertIsNone(pick_regular_price(ANCHOR, [{"x0": 430.0, "y0": 313.0, "v": 0.89}]))

    def test_piece_count_left_of_price(self):
        anchor = {"x0": 337.2, "x1": 403.6, "y0": 502.8, "y1": 558.6, "v": 0.78}
        self.assertEqual(pick_multi_buy(anchor, [{"x0": 276.0, "x1": 296.7, "y0": 502.8, "n": 3}]), 3)
        self.assertIsNone(pick_multi_buy(anchor, [{"x0": 20.0, "x1": 45.0, "y0": 502.8, "n": 3}]))


class NameTests(unittest.TestCase):
    def test_size_lines(self):
        for line, size in (("200 g", "200 g"), ("36 x 13 g", "36 x 13 g"), ("1 gab.", "1 gab."),
                           ("1 kg iepakojums", "1 kg"), ("400 g vai 500 g", "400 g vai 500 g"),
                           ("1,5 l", "1,5 l")):
            self.assertEqual(SIZE_LINE_RE.match(line).group(1), size)
        for line in ("1 kg = 3,98 €", "dažādi veidi", "pieejams arī 10 kg"):
            self.assertIsNone(SIZE_LINE_RE.match(line))

    def test_size_appended_once(self):
        self.assertEqual(name_with_size("SNICKERS Šokolādes batoniņš", "50 g"),
                         "SNICKERS Šokolādes batoniņš 50 g")
        self.assertEqual(name_with_size("FAVORINA figūriņa 75 g", "75 g"), "FAVORINA figūriņa 75 g")
        self.assertEqual(name_with_size("Cukīni", None), "Cukīni")

    def test_brand_is_leading_caps(self):
        self.assertEqual(brand_from_name("DELUXE Makarūnu izlase 36 x 13 g"), "DELUXE")
        self.assertEqual(brand_from_name("J.D. GROSS Šokolādes konfektes"), "J.D. GROSS")
        self.assertEqual(brand_from_name("CONFISERIE FIRENZE Mīkstās vafeles"), "CONFISERIE FIRENZE")
        self.assertIsNone(brand_from_name("BIO apelsīni 1 kg"))
        self.assertIsNone(brand_from_name("Cukīni"))
        self.assertIsNone(brand_from_name("PILOS"))


class DedupeTests(unittest.TestCase):
    def test_repeated_card_keeps_first_and_takes_photo(self):
        first = {"offerId": "a", "name": "PILOS Piens 1 l", "price": 0.69, "pdfPage": 2}
        again = {"offerId": "b", "name": "PILOS Piens 1 l", "price": 0.69, "pdfPage": 9, "image": "x.jpg"}
        other = {"offerId": "c", "name": "PILOS Piens 1 l", "price": 0.79, "pdfPage": 9}
        kept = dedupe_offers([first, again, other])
        self.assertEqual([o["offerId"] for o in kept], ["a", "c"])
        self.assertEqual(kept[0]["image"], "x.jpg")
        self.assertEqual(kept[0]["pdfPage"], 2)


if __name__ == "__main__":
    unittest.main()