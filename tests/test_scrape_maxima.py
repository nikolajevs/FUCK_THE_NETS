import unittest
from datetime import date

from bs4 import BeautifulSoup

from scripts.scrape_maxima import (
    campaign_dates_from_soup,
    campaign_index_metadata,
    collect_paldies_catalog,
    find_paldies_filter_id,
    offer_dates,
    parse_paldies_card,
    prices_from,
    price_quality,
    store_formats_from_text,
)


class MaximaScraperTests(unittest.TestCase):
    def test_prices_ignore_unit_price_and_parse_split_digits(self):
        text = "Olīvas 360 g (9,14 €/kg); (12,47 €/kg) 3 29 4 49"
        self.assertEqual(prices_from(text), [3.29, 4.49])

    def test_unit_prices_are_removed_without_parentheses(self):
        text = "Olīvas 360 g 9,14 €/kg; 12,47 €/kg 3 29 4 49"
        self.assertEqual(prices_from(text), [3.29, 4.49])

    def test_lithuanian_style_single_month_name_range(self):
        self.assertEqual(
            offer_dates("24.-30. novembris", today=date(2026, 11, 27)),
            ("2026-11-24", "2026-11-30"),
        )

    def test_range_spanning_new_year(self):
        self.assertEqual(
            offer_dates("17. novembris - 4. janvāris", today=date(2026, 11, 27)),
            ("2026-11-17", "2027-01-04"),
        )

    def test_numeric_date_range_with_explicit_year(self):
        self.assertEqual(
            offer_dates("24.11. - 30.11.2026", today=date(2026, 11, 27)),
            ("2026-11-24", "2026-11-30"),
        )

    def test_campaign_dates_are_read_from_matching_dom_card(self):
        html = """
        <div class="campaign"><h2>Black Friday cenu festivāls</h2><p>26.-30. novembris</p></div>
        <div class="campaign"><h2>Iknedēļas piedāvājums</h2><p>24.-30. novembris</p><a href="/bukleti/ONUKB-W48">Lasīt</a></div>
        <div class="campaign"><h2>Dāvanu katalogs</h2><p>17. novembris - 4. janvāris</p></div>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(
            campaign_dates_from_soup(
                soup, soup.get_text(" "), "Iknedēļas piedāvājums",
                today=date(2026, 11, 27),
            ),
            ("2026-11-24", "2026-11-30"),
        )

    def test_campaign_index_returns_dates_and_own_detail_url(self):
        html = """
        <div class="campaign">
          <h1>Black Friday cenu festivāls</h1>
          <h2>26.-30. novembris</h2>
          <a href="/bukleti/ONUKB-W48-BF">Lasīt</a>
        </div>
        <div class="campaign">
          <h1>Well Done Premium</h1>
          <h2>24.-30. novembris</h2>
          <a href="/bukleti/ONUKB-W48-WDP">Lasīt</a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(
            campaign_index_metadata(soup, "Well Done Premium", today=date(2026, 11, 27)),
            (
                "2026-11-24",
                "2026-11-30",
                "https://www.maxima.lv/bukleti/ONUKB-W48-WDP",
            ),
        )



    def test_unit_price_per_piece_is_not_mistaken_for_shelf_price(self):
        text = "Zobu birste (0,99 €/gab.); (1,49 €/gab.) 3 99 4 99"
        self.assertEqual(prices_from(text), [3.99, 4.99])

    def test_find_paldies_filter_id_by_official_label(self):
        soup = BeautifulSoup("""
            <form id="sales-filters-form">
              <a class="lv-filter-category">Latvijas produkti
                <input type="checkbox" name="filters[]" value="20">
              </a>
              <a class="lv-filter-category">Ar PALDIES karti
                <input type="checkbox" name="filters[]" value="18">
              </a>
            </form>
        """, "html.parser")
        self.assertEqual(find_paldies_filter_id(soup), "18")

    def test_parse_paldies_card_keeps_product_id_prices_and_validity(self):
        soup = BeautifulSoup("""
          <div class="item paldies-card offer-2-l-sp-sku units-price"
               data-product-id="261106"
               data-dates-interval="24.11. - 30.11.2026.">
            <img alt="Paldies" src="/images/front/icons/paldies-icon.png">
            <img alt="Olīvas pildītas ar apelsīniem WELL DONE PREMIUM, 360 g"
                 src="/uploads/olives.png">
            <div class="price">(9,14 €/kg); (12,47 €/kg) 3 29 4 49</div>
          </div>
        """, "html.parser")
        card = soup.select_one(".item")
        offer = parse_paldies_card(card, "https://www.maxima.lv/piedavajumi/?filters%5B%5D=18")
        self.assertEqual(offer["sourceProductId"], "261106")
        self.assertEqual(offer["price"], 3.29)
        self.assertEqual(offer["regularPrice"], 4.49)
        self.assertEqual(offer["paldiesPrice"], 3.29)
        self.assertEqual(offer["priceWithoutPaldies"], 4.49)
        self.assertTrue(offer["requiresPaldies"])
        self.assertTrue(offer["paldiesMarkerDetected"])
        self.assertEqual(offer["validFrom"], "2026-11-24")
        self.assertEqual(offer["validUntil"], "2026-11-30")
        self.assertEqual(offer["quality"]["priceStatus"], "verified")

    def test_collect_paldies_catalog_walks_every_offset(self):
        def card_html(product_id, name, price):
            return f"""
              <div class="item paldies-card" data-product-id="{product_id}"
                   data-dates-interval="24.11. - 30.11.2026.">
                <img alt="Paldies" src="/images/front/icons/paldies-icon.png">
                <img alt="{name}" src="/uploads/{product_id}.png">
                <div class="price">{price} 4 99</div>
              </div>
            """

        first_page_html = """
          <form id="sales-filters-form">
            <a class="lv-filter-category">Ar PALDIES karti
              <input type="checkbox" name="filters[]" value="18">
            </a>
            <input id="ajax-load-more-url" value="/ajax/salesloadmore">
            <input name="sort_by" value="newest">
            <select id="filter_limit"><option value="2">2</option><option value="1">1</option></select>
          </form>
        """

        class FakeResponse:
            def __init__(self, value):
                self.value = value
                self.text = value if isinstance(value, str) else ""

            def raise_for_status(self):
                return None

            def json(self):
                return self.value

        class FakeSession:
            def __init__(self):
                self.offsets = []

            def get(self, url, headers=None, timeout=None, params=None):
                if url == "https://www.maxima.lv/piedavajumi/":
                    return FakeResponse(first_page_html)
                query = dict(params or [])
                self.offsets.append(int(query.get("offset", "0")))
                offset = int(query.get("offset", "0"))
                pages = {
                    0: card_html("sku-1", "Jogurts VALMIERA, 180 g", "2 29"),
                    2: card_html("sku-3", "Siers WELL DONE, 200 g", "3 29"),
                }
                html = pages.get(offset, "")
                if offset == 0:
                    html += card_html("sku-2", "Piens VALMIERA, 1 l", "1 29")
                return FakeResponse({"totalItems": 3, "html": html})

        session = FakeSession()
        offers, reviews, metadata = collect_paldies_catalog(session)
        self.assertEqual(session.offsets, [0, 2])
        self.assertEqual(len(offers), 3)
        self.assertEqual(reviews, [])
        self.assertEqual(metadata["expectedOffers"], 3)
        self.assertEqual(metadata["collectedProducts"], 3)
        self.assertEqual(metadata["pages"], 2)
        self.assertTrue(metadata["complete"])

    def test_detect_all_explicit_store_formats(self):
        text = "Piedāvājums spēkā visos MAXIMA X, XX un XXX veikalos"
        self.assertEqual(store_formats_from_text(text), ["X", "XX", "XXX"])

    def test_detect_single_explicit_store_format(self):
        self.assertEqual(
            store_formats_from_text("Piedāvājums spēkā MAXIMA XX veikalos"),
            ["XX"],
        )

    def test_discount_mismatch_requires_review(self):
        _, _, _, issues = price_quality([3.29, 4.49], "3 29 4 49 -20%")
        self.assertIn("discount_percent_mismatch", issues)

    def test_more_than_two_shelf_prices_requires_review(self):
        _, _, _, issues = price_quality([1.00, 2.00, 3.00], "1,00 2,00 3,00")
        self.assertIn("ambiguous_multiple_price_values", issues)


if __name__ == "__main__":
    unittest.main()
