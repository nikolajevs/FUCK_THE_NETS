import unittest

import scripts.scrape_elvi as elvi
from scripts.scrape_elvi import (
    END_OF_LIST_RE,
    card_to_offer,
    fetch_offers,
    parse_cards,
    parse_catalogues,
    parse_detail,
    parse_validity,
)


def card(pid="70697", name="CŪKGAĻAS GULAŠS", detail="Kumelītes 400 g; 5,98 €/kg",
         variant="default", old=None, sale="2.39", single=False,
         tooltip=None, new=False, col_old=None):
    """Build a card fragment shaped like the real elvi2019 markup."""
    new_html = '<p class="new-product"><span>Jaunums</span></p>' if new else ""
    span_html = f"<span>{old}€</span>" if old is not None else ""
    p_class = ' class="single"' if single else ""
    p_html = f"<p{p_class}>{sale}€</p>" if sale is not None else ""
    if variant == "percent":
        p_html = f"<p>−{sale}%</p>"
        span_html = ""
    tooltip_html = ""
    if tooltip == "card":
        tooltip_html = '<div class="tooltip card" title="Cena ar ELVI Klienta karti"></div>'
    elif tooltip == "card_plus3":
        tooltip_html = '<div class="tooltip card_plus3" title="Cena ar 3+ karti"></div>'
    elif tooltip == "moreless":
        tooltip_html = '<div class="tooltip moreless">2+</div>'
    if variant == "two-plus":
        old_col = col_old if col_old is not None else (old or "3.19")
        cols = (f'<div class="col old"><span>{old_col}€</span></div>'
                f'<div class="col discount">{p_html}{tooltip_html}</div>')
    else:
        cols = f'<div class="col discount">{span_html}{p_html}{tooltip_html}</div>'
    return f"""<li data-id="{pid}">
    <div class="product-wrapper">
        <a href="https://elvi.lv/produkti/test-product/" class="product-pic">
            {new_html}
            <div class="table"><div class="cell">
                <img class="lazy" data-src="https://elvi.lv/wp-content/uploads/2026/11/48811.jpg" />
            </div></div>
        </a>
        <div class="price {variant}">
            {cols}
        </div>
        <div class="title">
            <a href="https://elvi.lv/produkti/test-product/">{name}</a>
            <span>{detail}</span>
        </div>
    </div>
</li>"""


def parse_one(fragment):
    cards = parse_cards(fragment)
    assert len(cards) == 1, cards
    return cards[0]


class ParseCardsTests(unittest.TestCase):
    def test_default_discount_card(self):
        raw = parse_one(card(old="3.19", sale="2.39"))
        self.assertEqual(raw["id"], "70697")
        self.assertEqual(raw["name"], "CŪKGAĻAS GULAŠS 400 g")
        self.assertEqual(raw["brand"], "Kumelītes")
        self.assertEqual(raw["price"], 2.39)
        self.assertEqual(raw["regularPrice"], 3.19)
        self.assertEqual(raw["variant"], "default")
        self.assertFalse(raw["isNew"])
        self.assertIsNone(raw["cardName"])
        self.assertIsNone(raw["multiBuy"])
        self.assertIn("48811.jpg", raw["image"])
        self.assertTrue(raw["url"].endswith("/produkti/test-product/"))

    def test_regular_price_range_keeps_lower_bound(self):
        raw = parse_one(card(old="3.19€ - 3.39", sale="2.39"))
        self.assertEqual(raw["regularPrice"], 3.19)

    def test_single_price_has_no_regular(self):
        raw = parse_one(card(sale="0.75", single=True, detail="Tukuma piens; 180 g; 4,17 €/kg"))
        self.assertEqual(raw["price"], 0.75)
        self.assertIsNone(raw["regularPrice"])
        self.assertEqual(raw["brand"], "Tukuma piens")
        self.assertEqual(raw["name"], "CŪKGAĻAS GULAŠS 180 g")

    def test_package_not_duplicated_when_in_name(self):
        raw = parse_one(card(name="ELVI KARTUPEĻI 2,5 KG", detail="0,72 €/kg", sale="1.79", single=True))
        self.assertEqual(raw["name"], "ELVI KARTUPEĻI 2,5 KG")
        self.assertIsNone(raw["brand"])

    def test_new_badge(self):
        raw = parse_one(card(new=True))
        self.assertTrue(raw["isNew"])

    def test_multiple_cards_and_entities(self):
        page = card(pid="1", name="PIENS &amp; SIERS", detail="") + card(pid="2", name="MAIZE", detail="")
        cards = parse_cards(page)
        self.assertEqual([c["id"] for c in cards], ["1", "2"])
        self.assertEqual(cards[0]["name"], "PIENS & SIERS")

    def test_loyalty_card_tooltip(self):
        raw = parse_one(card(tooltip="card"))
        self.assertEqual(raw["cardName"], "ELVI Klienta karte")

    def test_plus3_tooltip(self):
        raw = parse_one(card(tooltip="card_plus3"))
        self.assertEqual(raw["cardName"], "3+ karte")

    def test_two_plus_variant(self):
        raw = parse_one(card(variant="two-plus", col_old="3.19", sale="2.39", tooltip="moreless"))
        self.assertEqual(raw["price"], 2.39)
        self.assertEqual(raw["regularPrice"], 3.19)
        self.assertEqual(raw["multiBuy"], "2+")

    def test_two_plus_defaults_to_2_without_tooltip(self):
        raw = parse_one(card(variant="two-plus", col_old="3.19", sale="2.39"))
        self.assertEqual(raw["multiBuy"], "2+")

    def test_percent_variant_has_no_price(self):
        raw = parse_one(card(variant="percent", sale="25"))
        self.assertIsNone(raw["price"])
        self.assertEqual(raw["percent"], 25)


class ParseDetailTests(unittest.TestCase):
    def test_brand_package_unit(self):
        self.assertEqual(parse_detail("Kumelītes 400 g; 5,98 €/kg"), ("Kumelītes", "400 g"))

    def test_brand_separate_segment(self):
        self.assertEqual(parse_detail("Latvijas Maiznieks; 500 g, 2,10 €/kg"),
                         ("Latvijas Maiznieks", "500 g"))

    def test_package_only(self):
        self.assertEqual(parse_detail("250 g; 10,36 €/kg"), (None, "250 g"))

    def test_unit_price_only(self):
        self.assertEqual(parse_detail("0,72 €/kg"), (None, None))

    def test_deposit_note_stripped(self):
        self.assertEqual(parse_detail("1,5 l; 0,26 €/l (+0,10 € depozīts)"), (None, "1,5 l"))

    def test_empty(self):
        self.assertEqual(parse_detail(""), (None, None))


class ValidityTests(unittest.TestCase):
    def test_header_dates(self):
        html = '<div class="products-title"><h1>Akcijas avīze</h1><span>Spēkā no 2026-11-11 līdz 2026-12-01</span></div>'
        self.assertEqual(parse_validity(html), ("2026-11-11", "2026-12-01"))

    def test_missing(self):
        self.assertEqual(parse_validity("<div>nav</div>"), (None, None))


class CatalogueTests(unittest.TestCase):
    def test_issuu_link_numbered(self):
        html = ('<a target="_blank" rel="noopener" href="https://issuu.com/elvi_veikali/docs/'
                'elvi_akciju_av_ze_nr.22_11.11.-01.12.2026_?fr=sZjA5MDkzMjQzNzk"><span>Apskatīt</span></a>')
        cats = parse_catalogues(html, "2026-11-11", "2026-12-01")
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["id"], "elvi-akciju-avize-nr-22")
        self.assertEqual(cats[0]["name"], "ELVI akciju avīze Nr. 22")
        self.assertEqual(cats[0]["validFrom"], "2026-11-11")
        self.assertTrue(cats[0]["source"].startswith("https://issuu.com/"))

    def test_no_issuu_link(self):
        self.assertEqual(parse_catalogues("<div>nav</div>", None, None), [])


class OfferConversionTests(unittest.TestCase):
    def test_regular_offer(self):
        offer = card_to_offer(parse_one(card(old="3.19", sale="2.39")), "2026-11-11", "2026-12-01")
        self.assertEqual(offer["offerId"], "elvi-70697")
        self.assertEqual(offer["price"], 2.39)
        self.assertEqual(offer["regularPrice"], 3.19)
        self.assertFalse(offer["requiresCard"])
        self.assertEqual(offer["campaignId"], "akcijas-avize")
        self.assertEqual(offer["store"], "elvi")
        self.assertEqual(offer["validFrom"], "2026-11-11")
        self.assertEqual(offer["validUntil"], "2026-12-01")
        self.assertNotIn("reviewReason", offer)

    def test_single_price_offer_kept(self):
        offer = card_to_offer(parse_one(card(sale="0.75", single=True)), None, None)
        self.assertEqual(offer["price"], 0.75)
        self.assertIsNone(offer["regularPrice"])
        self.assertNotIn("reviewReason", offer)

    def test_loyalty_offer(self):
        offer = card_to_offer(parse_one(card(tooltip="card")), None, None)
        self.assertTrue(offer["requiresCard"])
        self.assertEqual(offer["cardName"], "ELVI Klienta karte")
        self.assertNotIn("reviewReason", offer)

    def test_two_plus_offer_keeps_condition(self):
        offer = card_to_offer(parse_one(card(variant="two-plus", col_old="3.19", sale="2.39")), None, None)
        self.assertEqual(offer["multiBuy"], "2+")
        self.assertFalse(offer["requiresCard"])
        self.assertNotIn("reviewReason", offer)

    def test_regular_price_not_above_sale_is_dropped(self):
        offer = card_to_offer(parse_one(card(old="2.39", sale="2.39")), None, None)
        self.assertIsNone(offer["regularPrice"])

    def test_percent_goes_to_review(self):
        offer = card_to_offer(parse_one(card(variant="percent", sale="25")), None, None)
        self.assertIsNone(offer["price"])
        self.assertEqual(offer["reviewReason"], "percent_only")

    def test_missing_price_goes_to_review(self):
        offer = card_to_offer(parse_one(card(sale=None)), None, None)
        self.assertIsNone(offer["price"])
        self.assertEqual(offer["reviewReason"], "missing_price")

    def test_weak_name_goes_to_review(self):
        offer = card_to_offer(parse_one(card(name="AB", detail="")), None, None)
        self.assertEqual(offer["reviewReason"], "weak_name")


class FetchOffersTests(unittest.TestCase):
    """AJAX pagination must degrade gracefully when admin-ajax.php 403s."""

    def setUp(self):
        self._delay = elvi.PAGE_DELAY
        elvi.PAGE_DELAY = 0
        self.ssr = ('<span>Spēkā no 2026-11-11 līdz 2026-12-01</span>'
                    + card(pid="1") + card(pid="2", name="MAIZE", detail=""))

    def tearDown(self):
        elvi.PAGE_DELAY = self._delay

    def test_ajax_403_keeps_ssr_cards(self):
        def ajax(sess, offset):
            raise RuntimeError("ELVI AJAX offset 0 failed after 3 attempts: 403")
        cards, vf, vu, _ = fetch_offers(None, page_fetcher=lambda s, u: self.ssr,
                                        ajax_fetcher=ajax)
        self.assertEqual([c["id"] for c in cards], ["1", "2"])
        self.assertEqual((vf, vu), ("2026-11-11", "2026-12-01"))

    def test_ajax_403_without_ssr_cards_raises(self):
        def ajax(sess, offset):
            raise RuntimeError("403")
        with self.assertRaises(RuntimeError):
            fetch_offers(None, page_fetcher=lambda s, u: "<div>blocked</div>",
                         ajax_fetcher=ajax)

    def test_ajax_extends_and_deduplicates(self):
        pages = {0: card(pid="2", name="MAIZE", detail="") + card(pid="3", name="PIENS", detail=""),
                 16: card(pid="4", name="SIERS", detail="")}
        def ajax(sess, offset):
            return pages.get(offset, '<script>$(".load-more").hide();</script>')
        cards, *_ = fetch_offers(None, page_fetcher=lambda s, u: self.ssr,
                                 ajax_fetcher=ajax)
        self.assertEqual([c["id"] for c in cards], ["1", "2", "3", "4"])


class EndOfListTests(unittest.TestCase):
    def test_hide_script_detected(self):
        text = '<script type="text/javascript">$(".products-container .product-list .load-more").hide();</script>'
        self.assertTrue(END_OF_LIST_RE.search(text))

    def test_cards_not_end(self):
        self.assertIsNone(END_OF_LIST_RE.search(card()))


if __name__ == "__main__":
    unittest.main()