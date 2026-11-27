import unittest

from scripts.scrape_latts import (
    card_to_offer,
    is_alcohol,
    last_page,
    parse_brand,
    parse_cards,
    parse_catalogues,
)


def card(pid="29100", name="Limonāde citronu - laima 1.5l, Zīļuks",
         price="0.89", old="1.55", group="Dzērieni/Limonādes un kvass/Limonādes",
         brand="", deposit=False, akc="43", long=False):
    """Build a listing card fragment shaped like the real e-latts SSR markup."""
    old_html = f'<div class="-eOld">{old}</div>' if old is not None else ""
    badge = f'<ins class="-akc"><u>-</u><b>{akc}</b><i>%</i></ins>' if akc else ""
    brand_html = (f'<a class="-brand" style="display:none" href="/brand.b">{brand}</a>'
                  if brand else '<span class="-brand"></span>')
    dep_html = ""
    price_cls = "-oPrice"
    if deposit:
        price_cls += " -vDepozit"
        dep_html = ('<div class="-iDepozit">Depozīts: 0.10€'
                    '<span class="-vWide">Cena ar depozītu: 0.99€</span></div>')
    slug = name.lower().replace(" ", "-")
    title_cls = "-oTitle -sLong" if long else "-oTitle"
    return f"""<div class="-oProduct"  -id="{pid}"><i class="PWS-icon"></i><div>
    <div class="PLP-grid">{badge}</div>
    <div class="-oThumb"><a href="/{slug}.{pid}.p"><img id="PLGS-thumb-{pid}" src="/i/{slug}.spm.1-m1Z.jpeg" alt="{slug}" /></a></div>
    <a class="{title_cls}" href="/{slug}.{pid}.p">{name}</a>{brand_html}
    <div class="-oAttr"><table><tr style="display:none"><td>Grupa:</td><th class="-oGroupPath">{group}</th></tr></table></div>
    <div class="{price_cls}" -price="{price}"><div>€ {price}</div>{old_html}<div class="-ePerUnit">€ 0.59  / L</div>{dep_html}</div>
    <div class="-oBasket"><input basket-key="ppr-spr-lpr-var-scp-bat-lcp-vrnt:{pid}:1:0:0:0:0:0:0" product-id="{pid}" /></div>
    </div></div>"""


def parse_one(fragment):
    cards = parse_cards(fragment)
    assert len(cards) == 1, cards
    return cards[0]


class ParseCardsTests(unittest.TestCase):
    def test_full_card(self):
        raw = parse_one(card())
        self.assertEqual(raw["id"], "29100")
        self.assertEqual(raw["name"], "Limonāde citronu - laima 1.5l, Zīļuks")
        self.assertEqual(raw["price"], 0.89)
        self.assertEqual(raw["regularPrice"], 1.55)
        self.assertEqual(raw["category"], "Dzērieni/Limonādes un kvass/Limonādes")
        self.assertEqual(raw["brand"], "Zīļuks")
        self.assertTrue(raw["url"].startswith("https://www.e-latts.lv/"))
        self.assertTrue(raw["url"].endswith(".29100.p"))
        self.assertTrue(raw["image"].startswith("https://www.e-latts.lv/i/"))

    def test_explicit_brand_label_wins(self):
        raw = parse_one(card(name="Siers šķēlītēs Holandes 500g, SN", brand="Siera Nams"))
        self.assertEqual(raw["brand"], "Siera Nams")

    def test_unit_price_and_deposit_not_used_as_price(self):
        raw = parse_one(card(deposit=True))
        self.assertEqual(raw["price"], 0.89)
        self.assertEqual(raw["regularPrice"], 1.55)

    def test_multiple_cards_and_entities(self):
        page = card(pid="1", name="Piens Alma 2,5% 1l") + card(pid="2", name="Siers &amp; maize")
        cards = parse_cards(page)
        self.assertEqual([c["id"] for c in cards], ["1", "2"])
        self.assertEqual(cards[1]["name"], "Siers & maize")

    def test_missing_old_price(self):
        raw = parse_one(card(old=None))
        self.assertEqual(raw["price"], 0.89)
        self.assertIsNone(raw["regularPrice"])

    def test_long_title_extra_class(self):
        raw = parse_one(card(pid="53043", long=True,
                             name="Barība suņiem mīkstā Adult Mixed izlase 4x100g, Pedigree"))
        self.assertEqual(raw["name"], "Barība suņiem mīkstā Adult Mixed izlase 4x100g, Pedigree")
        self.assertEqual(raw["brand"], "Pedigree")


class LastPageTests(unittest.TestCase):
    def test_reads_highest_page_reference(self):
        html = '<a href="/products.sale.ppl?p=2">2</a><a href="/products.sale.ppl?p=17">17</a>'
        self.assertEqual(last_page(html), 17)

    def test_single_page(self):
        self.assertEqual(last_page("<span>1</span>"), 1)


class AlcoholTests(unittest.TestCase):
    def test_alcohol_category_excluded(self):
        for group in ("Alkoholiskie dzērieni/Stiprie alkoholiskie dzērieni/Vīns",
                      "Alkoholiskie dzērieni/Alus"):
            self.assertTrue(is_alcohol(group), group)

    def test_non_alcoholic_and_food_kept(self):
        for group in ("Dzērieni/Limonādes un kvass/Limonādes",
                      "Dzērieni/Bezalkoholiskais alus un sidrs/Alus",
                      "Bakaleja/Saldumi/Šokolāde",
                      "Bezalkoholiskias alus un vins/Vīns"):
            self.assertFalse(is_alcohol(group), group)

    def test_missing_category_kept(self):
        self.assertFalse(is_alcohol(None))
        self.assertFalse(is_alcohol(""))

    def test_alcoholic_card_drops_out(self):
        raw = parse_one(card(pid="9", name="Vīns sarkanais 0,75l",
                             group="Alkoholiskie dzērieni/Vīns", price="5.99", old="7.49"))
        self.assertIsNone(card_to_offer(raw))


class BrandTests(unittest.TestCase):
    def test_trailing_name_segment(self):
        self.assertEqual(parse_brand("Čipsi krēmīgais čilli 130g, Ādažu", ""), "Ādažu")

    def test_explicit_label(self):
        self.assertEqual(parse_brand("Siers 500g, SN", "Siera Nams"), "Siera Nams")

    def test_no_brand(self):
        self.assertIsNone(parse_brand("Piens 1l", ""))

    def test_trailing_size_is_not_a_brand(self):
        self.assertIsNone(parse_brand("AEONS Sarkanvīna etiķis, 250ml", ""))
        self.assertIsNone(parse_brand("Siers Holandes, 500g", ""))

    def test_multiword_brand_kept(self):
        self.assertEqual(parse_brand("Rieksti grauzdēti 100g, Santa Maria", ""), "Santa Maria")
        self.assertEqual(parse_brand("Maize rudzu 500g, Latvijas Maiznieks", ""), "Latvijas Maiznieks")

    def test_descriptor_tail_is_not_a_brand(self):
        self.assertIsNone(parse_brand("Konfektes assortment, sveramas", ""))
        self.assertIsNone(parse_brand("Baterijas AA, 4 dazādas krāsas", ""))


class OfferConversionTests(unittest.TestCase):
    def test_regular_discount_offer(self):
        offer = card_to_offer(parse_one(card()))
        self.assertEqual(offer["offerId"], "latts-29100")
        self.assertEqual(offer["price"], 0.89)
        self.assertEqual(offer["regularPrice"], 1.55)
        self.assertFalse(offer["requiresCard"])
        self.assertNotIn("cardName", offer)
        self.assertEqual(offer["campaignId"], "akcijas")
        self.assertEqual(offer["campaign"], "Akcijas")
        self.assertEqual(offer["store"], "latts")
        self.assertTrue(offer["source"].startswith("https://www.e-latts.lv/"))
        self.assertIsNone(offer["validFrom"])
        self.assertIsNone(offer["validUntil"])
        self.assertNotIn("reviewReason", offer)

    def test_regular_price_not_above_sale_is_dropped(self):
        offer = card_to_offer(parse_one(card(price="1.55", old="0.89")))
        self.assertEqual(offer["price"], 1.55)
        self.assertIsNone(offer["regularPrice"])
        self.assertNotIn("reviewReason", offer)

    def test_missing_price_goes_to_review(self):
        fragment = card().replace('-price="0.89"', '-price=""')
        offer = card_to_offer(parse_one(fragment))
        self.assertIsNone(offer["price"])
        self.assertEqual(offer["reviewReason"], "missing_price")

    def test_weak_name_goes_to_review(self):
        offer = card_to_offer(parse_one(card(name="AB")))
        self.assertEqual(offer["reviewReason"], "weak_name")

    def test_lone_sale_price_is_kept(self):
        offer = card_to_offer(parse_one(card(old=None)))
        self.assertEqual(offer["price"], 0.89)
        self.assertIsNone(offer["regularPrice"])
        self.assertNotIn("reviewReason", offer)


class CatalogueTests(unittest.TestCase):
    PAGE = """<div><h2>LaTS akciju avīze</h2>
    <p>Akcijas periods: 24.11.2026. - 14.12.2026.</p>
    <a href="http://latts.lv/storage/promotions/avize_24_11_x_14_12_2026 web.pdf">Lasīt PDF</a>
    <h2>Zīļuka zemās cenas</h2>
    <a href="http://latts.lv/storage/promotions/123 (8).pdf">Lasīt PDF</a>
    <h2>Ar Draugu karti lētāk!</h2>
    <a href="http://latts.lv/storage/promotions/123.pdf">Lasīt PDF</a></div>"""

    def test_sections_periods_and_ids(self):
        cats = parse_catalogues(self.PAGE)
        self.assertEqual(len(cats), 3)
        self.assertEqual(cats[0]["name"], "LaTS akciju avīze")
        self.assertEqual(cats[0]["id"], "latts-lats-akciju-avize")
        self.assertEqual(cats[0]["validFrom"], "2026-11-24")
        self.assertEqual(cats[0]["validUntil"], "2026-12-14")
        self.assertTrue(cats[0]["source"].endswith("web.pdf"))
        self.assertEqual(cats[1]["name"], "Zīļuka zemās cenas")
        self.assertEqual(cats[2]["name"], "Ar Draugu karti lētāk!")
        self.assertTrue(cats[2]["source"].endswith("/123.pdf"))
        for c in cats:
            self.assertEqual(c["validFrom"], "2026-11-24")

    def test_no_validity(self):
        cats = parse_catalogues('<h2>LaTS akciju avīze</h2><a href="http://x/a.pdf">PDF</a>')
        self.assertEqual(len(cats), 1)
        self.assertIsNone(cats[0]["validFrom"])
        self.assertIsNone(cats[0]["validUntil"])

    def test_empty_page(self):
        self.assertEqual(parse_catalogues("<div>nothing</div>"), [])


if __name__ == "__main__":
    unittest.main()