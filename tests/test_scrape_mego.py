import unittest

import pymupdf as fitz

from scripts.scrape_mego import _extract_from_page


class MegoScraperTests(unittest.TestCase):
    def test_pdf_price_pairs_stay_within_their_columns(self):
        document = fitz.open()
        page = document.new_page(width=552, height=779)

        page.insert_textbox(
            fitz.Rect(24, 500, 115, 540),
            "GLEZNA ar audumu un LED\n20 cm dažādi veidi",
            fontsize=8,
        )
        page.insert_textbox(fitz.Rect(145, 500, 215, 550), "449", fontsize=28)
        page.insert_textbox(fitz.Rect(165, 537, 195, 558), "699", fontsize=12)

        page.insert_textbox(
            fitz.Rect(290, 500, 390, 540),
            "DEKORS LED gredzenveida\n28 x 41,5 cm",
            fontsize=8,
        )
        page.insert_textbox(fitz.Rect(400, 500, 475, 550), "1299", fontsize=28)
        page.insert_textbox(fitz.Rect(420, 537, 465, 558), "1999", fontsize=12)
        # Unit-price copy is small and must not be mistaken for a shelf price.
        page.insert_text(fitz.Point(290, 570), "1 kg = 15,48 €/kg", fontsize=8)

        booklet = {
            "id": "mego-test",
            "name": "Test booklet",
            "pdf": "https://mego.lv/test.pdf",
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
        }
        offers, reviews = _extract_from_page(page, 0, booklet)
        repeated_offers, _ = _extract_from_page(page, 0, booklet)
        document.close()

        self.assertEqual(reviews, [])
        self.assertEqual(
            [offer["offerId"] for offer in offers],
            [offer["offerId"] for offer in repeated_offers],
        )
        self.assertEqual(len(offers), 2)
        by_name = {offer["name"]: offer for offer in offers}
        picture = next(offer for name, offer in by_name.items() if name.startswith("GLEZNA"))
        led = next(offer for name, offer in by_name.items() if name.startswith("DEKORS LED"))
        self.assertEqual((picture["price"], picture["regularPrice"]), (4.49, 6.99))
        self.assertEqual((led["price"], led["regularPrice"]), (12.99, 19.99))


if __name__ == "__main__":
    unittest.main()
