import unittest
from datetime import date

from scripts.scrape_aibe import (
    card_to_offer,
    infer_year,
    parse_brand,
    parse_cards,
    parse_catalogue,
    parse_sale_price,
)


def card(pid="92324", name="Rakveres cīsiņi, Rīgas miesnieks, 300 g",
         euro="1", cent="99", was="2.60", disc="23",
         image="https://aibe.lv/wp-content/uploads/x.png"):
    """A promotion card shaped like the real aibe.lv/akcijas/ SSR markup."""
    was_html = f'<p class="discount-price-was-this">{was}€</p>' if was else ""
    return f"""<div class="bestOffers-block call-popup" data-id="{pid}" data-post-type="akcijos">
      <div class="bestOffers-block-inner"><div class="bestOffers-block-discount">
        <div class="discount-percent"><div class="discount-percent-num"><p>-{disc}%</p></div></div>
        <div class="discount-price"><div class="discount-price-now">
          <p class="discount-price-now-euro">{euro}</p>
          <p class="discount-price-now-cent">{cent}<span class="discount-price-now-symbol">€</span></p>
        </div><div class="discount-price-was">{was_html}</div></div>
      </div>
      <div class="price_quantity pro"><p class="price_quantity-text">1 kg - 6.63 €</p></div>
      <div class="bestOffers-block-discountProduct">
        <div class="bestOffers-block-discountProduct-imgWrapper"><img data-src="{image}" src="data:image/svg+xml;base64,PHN2Zz4=" class="lazyload"></div>
        <div class="bestOffers-block-discountProduct-productAndBrand">
          <p class="bestOffers-block-discountProduct-name">{name}</p>
        </div>
      </div></div></div>"""


def section(title="Gaļas Produkti", validity="24.11 - 14.12", cards=""):
    return f"""<section class="bestOffers"><div class="defaultWrapper"><div class="top-block">
      <div class="top-block-topInner"><h4 class="top-block-title">{title}</h4></div>
      <div class="top-block-bottomInner"><p class="op-block-date">Akcija ir spēkā no {validity}</p></div>
    </div><div class="bestOffers-blocks">{cards}</div></div></section>"""


class ParseBrandTests(unittest.TestCase):
    def test_brand_is_last_non_size_segment(self):
        self.assertEqual(parse_brand("Rakveres cīsiņi, Rīgas miesnieks, 300 g"), "Rīgas miesnieks")
        self.assertEqual(parse_brand("Tradicionālā siļķu fileja, Viči, 240 g / 190 g"), "Viči")
        self.assertEqual(parse_brand("Cāļu stilbiņi atdzesēti, Rīgas miesnieks, sverami"), "Rīgas miesnieks")

    def test_fat_percent_is_not_a_brand(self):
        self.assertEqual(parse_brand("Saldkrējuma sviests, Cesvaines piens, 82%, 180 g"), "Cesvaines piens")

    def test_no_brand_when_only_name_and_size(self):
        self.assertIsNone(parse_brand("Kausētais siers Dzintars klasiskais, 200 g"))
        self.assertIsNone(parse_brand("Augstākā labuma kviešu milti Vilia, 1 kg"))


class ParsePriceTests(unittest.TestCase):
    def test_euro_and_cent_combine(self):
        self.assertEqual(parse_sale_price("1", "99"), 1.99)
        self.assertEqual(parse_sale_price("0", "39"), 0.39)
        self.assertIsNone(parse_sale_price(None, "99"))


class ParseCardsTests(unittest.TestCase):
    def test_card_fields_and_category(self):
        html = section(cards=card())
        cards = parse_cards(html)
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual(c["id"], "92324")
        self.assertEqual(c["price"], 1.99)
        self.assertEqual(c["regularPrice"], 2.60)
        self.assertEqual(c["discountPercent"], 23)
        self.assertEqual(c["brand"], "Rīgas miesnieks")
        self.assertEqual(c["category"], "Gaļas Produkti")
        self.assertEqual(c["validFromRaw"], "24.11")
        self.assertEqual(c["image"], "https://aibe.lv/wp-content/uploads/x.png")

    def test_category_carries_to_following_cards(self):
        html = (section(title="Zivju produkti", cards=card(pid="1"))
                + section(title="Bakaleja", cards=card(pid="2")))
        cards = parse_cards(html)
        self.assertEqual([c["category"] for c in cards], ["Zivju produkti", "Bakaleja"])


class CatalogueTests(unittest.TestCase):
    def test_dearpdf_source_and_year(self):
        html = ('<script>window.option_df_46565 = {"source":"https:\\/\\/aibe.lv\\/wp-content'
                '\\/uploads\\/2022\\/11\\/Nr.16-24.11.2026-14.12.2026.pdf"};</script>')
        catalogues, year = parse_catalogue(html)
        self.assertEqual(year, 2026)
        self.assertEqual(len(catalogues), 1)
        self.assertEqual(catalogues[0]["validFrom"], "2026-11-24")
        self.assertEqual(catalogues[0]["validUntil"], "2026-12-14")
        self.assertIn("Nr. 16", catalogues[0]["name"])
        self.assertTrue(catalogues[0]["source"].endswith(".pdf"))
        self.assertNotIn("\\/", catalogues[0]["source"])


class YearAndOfferTests(unittest.TestCase):
    def test_infer_year_spans_new_year(self):
        self.assertEqual(infer_year("24.11", "14.12", date(2026, 11, 27)), 2026)

    def test_card_to_offer_dates_and_card_flag(self):
        c = parse_cards(section(cards=card()))[0]
        offer = card_to_offer(c, 2026)
        self.assertEqual(offer["offerId"], "aibe-92324")
        self.assertEqual(offer["validFrom"], "2026-11-24")
        self.assertEqual(offer["validUntil"], "2026-12-14")
        self.assertFalse(offer["requiresCard"])
        self.assertEqual(offer["store"], "aibe")
        self.assertNotIn("reviewReason", offer)

    def test_missing_price_goes_to_review(self):
        c = parse_cards(section(cards=card(euro="", cent="")))[0]
        offer = card_to_offer(c, 2026)
        self.assertEqual(offer.get("reviewReason"), "missing_price")


if __name__ == "__main__":
    unittest.main()