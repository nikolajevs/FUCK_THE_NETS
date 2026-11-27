import unittest
from datetime import date

from bs4 import BeautifulSoup

from scripts.scrape_maxima import (
    campaign_dates_from_soup,
    campaign_index_metadata,
    offer_dates,
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
