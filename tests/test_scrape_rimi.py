import unittest

from scripts.scrape_rimi import (
    card_to_offer,
    is_alcohol,
    last_page,
    parse_cards,
)


def card(code="286441", name="Kartupeļi saldie kg", category="SH-2-2-13",
         label=None, price="2.39", old=None, unit="kg"):
    """Build a listing card fragment shaped like the real SSR markup."""
    label_html = ""
    if label is not None:
        major, cents = label.split(".")
        label_html = f"""
            <div class="price-label"  title="Piedāvājums spēkā tikai MansRimi kartes lietotājiem" >
    <div class="price-label__body">
        <div class="price-label__price">
            <span class="major">{major}</span>
            <div class="minor"><span class="cents">{cents}</span><span class="currency">€</span></div>
        </div>
        <div class="price-per-unit">{label.replace('.', ',')} €/{unit}</div>
    </div>
</div>"""
    old_html = ""
    wrapper = ""
    if old is not None:
        wrapper = " -has-discount"
        old_html = f"""
            <div class="old-price-tag card__old-price">
        <span class="sr-only">Regulārā cena: {old.replace('.', ',')} €</span>
        <span aria-hidden="true">{old.replace('.', ',')}€</span>
    </div>"""
    return f"""<li class="product-grid__item">
        <div data-product-code="{code}"
     class="js-product-container card"
     data-gtm-eec-product='{{"id":"{code}","name":"{name}","category":"{category}","brand":null,"price":2.39,"currency":"EUR"}}'
     >
            <a class="card__url js-gtm-eec-product-click" href="/e-veikals/lv/produkti/augli-un-darzeni/kartupeli-saldie-kg/p/{code}"
           aria-label="{name}"></a>
        <div class="label-badges"></div>
    <div class="card__image-wrapper">
        <div>
            <img src="https://rimibaltic-res.cloudinary.com/image/upload/MAT_{code}_KGM_LV" alt="{name}">
            {label_html}
        </div>
    </div>
    <div class="card__details">
        <p class="card__name">{name}</p>
        <div class="card__details-inner">
            <div class="price-and-favorite">
                <div class="card__price-wrapper{wrapper}">
        <div class="price-tag card__price">
    <span class="sr-only">
        {price} €
                    par {unit}
            </span>
    <span aria-hidden="true">2</span>
</div>
        <div>
            {old_html}
            <p class="card__price-per">
                <span class="sr-only">Cena par vienību: 9,99 €/{unit}</span>
            </p>
        </div>
    </div>
            </div>
        </div>
    </div>
    </div>
    </li>"""


def parse_one(fragment):
    cards = parse_cards(fragment)
    assert len(cards) == 1, cards
    return cards[0]


class ParseCardsTests(unittest.TestCase):
    def test_card_price_label_and_regular_price(self):
        raw = parse_one(card(label="1.19", price="2.39"))
        self.assertEqual(raw["code"], "286441")
        self.assertEqual(raw["name"], "Kartupeļi saldie kg")
        self.assertTrue(raw["hasCardLabel"])
        self.assertEqual(raw["cardPrice"], 1.19)
        self.assertEqual(raw["price"], 2.39)
        self.assertIsNone(raw["oldPrice"])
        self.assertEqual(raw["category"], "SH-2-2-13")
        self.assertIn("MAT_286441", raw["image"])
        self.assertTrue(raw["url"].endswith("/p/286441"))

    def test_regular_discount_with_old_price(self):
        raw = parse_one(card(code="223984", name="Sviests Exporta 82,5% 200g",
                             category="SH-11-7-17", price="1.59", old="2.19", unit="gab."))
        self.assertFalse(raw["hasCardLabel"])
        self.assertIsNone(raw["cardPrice"])
        self.assertEqual(raw["price"], 1.59)
        self.assertEqual(raw["oldPrice"], 2.19)

    def test_unit_price_is_not_mistaken_for_regular_price(self):
        raw = parse_one(card(price="1.59"))
        self.assertIsNone(raw["oldPrice"])
        self.assertEqual(raw["price"], 1.59)

    def test_multiple_cards_and_html_entities(self):
        page = card(code="1", name="Piens Alma 2,5% 1l") + card(code="2", name="Siers &amp; maize")
        cards = parse_cards(page)
        self.assertEqual([c["code"] for c in cards], ["1", "2"])
        self.assertEqual(cards[1]["name"], "Siers & maize")

    def test_cents_keep_leading_zero(self):
        raw = parse_one(card(label="1.05", price="2.39"))
        self.assertEqual(raw["cardPrice"], 1.05)


class LastPageTests(unittest.TestCase):
    def test_reads_highest_page_reference(self):
        html = '<a data-page="2" href="?currentPage=2">2</a><a data-page="76" href="?currentPage=76">76</a>'
        self.assertEqual(last_page(html), 76)

    def test_single_page(self):
        self.assertEqual(last_page("<span>1</span>"), 1)


class AlcoholTests(unittest.TestCase):
    def test_alcohol_categories_excluded(self):
        for code in ("SH-1", "SH-1-12-3", "SH-1-2-1", "SH-18", "SH-18-1"):
            self.assertTrue(is_alcohol(code), code)

    def test_non_alcoholic_and_food_categories_kept(self):
        for code in ("SH-5-1-1", "SH-5-1-2", "SH-5-12-19", "SH-11-7-17", "SH-2-2-13", "SH-12-1"):
            self.assertFalse(is_alcohol(code), code)

    def test_missing_category_kept(self):
        self.assertFalse(is_alcohol(None))
        self.assertFalse(is_alcohol(""))

    def test_alcoholic_card_drops_out(self):
        raw = parse_one(card(code="9", name="Alus Vietējais gaišais 5,6% 0,5l",
                             category="SH-1-12-1", price="1.29", old="1.79"))
        self.assertIsNone(card_to_offer(raw))


class OfferConversionTests(unittest.TestCase):
    def test_mans_rimi_card_price_offer(self):
        offer = card_to_offer(parse_one(card(label="1.19", price="2.39")))
        self.assertEqual(offer["offerId"], "rimi-286441")
        self.assertEqual(offer["price"], 1.19)
        self.assertEqual(offer["regularPrice"], 2.39)
        self.assertTrue(offer["requiresCard"])
        self.assertEqual(offer["cardName"], "Mans Rimi")
        self.assertEqual(offer["campaignId"], "mans-rimi-cena")
        self.assertEqual(offer["campaign"], "Mans Rimi cena")
        self.assertEqual(offer["store"], "rimi-eveikals")
        self.assertTrue(offer["source"].startswith("https://www.rimi.lv/"))
        self.assertIsNone(offer["validFrom"])
        self.assertIsNone(offer["validUntil"])
        self.assertNotIn("reviewReason", offer)

    def test_regular_discount_offer(self):
        offer = card_to_offer(parse_one(card(code="223984", name="Sviests Exporta 82,5% 200g",
                                             price="1.59", old="2.19")))
        self.assertEqual(offer["price"], 1.59)
        self.assertEqual(offer["regularPrice"], 2.19)
        self.assertFalse(offer["requiresCard"])
        self.assertNotIn("cardName", offer)
        self.assertEqual(offer["campaignId"], "akcija")
        self.assertNotIn("reviewReason", offer)

    def test_regular_price_not_above_sale_is_dropped(self):
        offer = card_to_offer(parse_one(card(label="2.39", price="1.19")))
        self.assertEqual(offer["price"], 2.39)
        self.assertIsNone(offer["regularPrice"])

    def test_missing_price_goes_to_review(self):
        fragment = card(price="2.39").replace('<span class="sr-only">\n        2.39 €', '<span class="sr-only">\n        Cena nav norādīta')
        offer = card_to_offer(parse_one(fragment))
        self.assertIsNone(offer["price"])
        self.assertEqual(offer["reviewReason"], "missing_price")

    def test_weak_name_goes_to_review(self):
        offer = card_to_offer(parse_one(card(name="AB", price="1.59", old="2.19")))
        self.assertEqual(offer["reviewReason"], "weak_name")

    def test_card_without_discount_goes_to_review(self):
        offer = card_to_offer(parse_one(card(price="1.59")))
        self.assertEqual(offer["price"], 1.59)
        self.assertIsNone(offer["regularPrice"])
        self.assertEqual(offer["reviewReason"], "no_discount")


if __name__ == "__main__":
    unittest.main()
