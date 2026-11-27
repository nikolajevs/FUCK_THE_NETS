import unittest
from datetime import date

from bs4 import BeautifulSoup

from scripts.scrape_maxima import (
    campaign_dates_from_soup,
    campaign_index_metadata,
    _clean_pdf_product_name,
    _looks_like_pdf_product_description,
    _is_pdf_product_title_seed,
    reconcile_paldies_prices,
    partition_review_offers,
    build_review_summary,
    _catalogue_for_pdf_page,
    collect_paldies_catalog,
    discover_active_catalogues,
    find_paldies_filter_id,
    offer_dates,
    parse_catalogue_pdf,
    parse_paldies_card,
    prices_from,
    price_quality,
    _has_explicit_shelf_price,
    _pdf_page_cells_geometry,
    _split_pdf_cell_by_bullets,
    _pdf_cell_starts_with_price,
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


    def test_numeric_new_year_range_year_after_end_date(self):
        self.assertEqual(
            offer_dates("17.11. - 04.01.2027", today=date(2026, 11, 27)),
            ("2026-11-17", "2027-01-04"),
        )

    def test_named_month_new_year_range_year_after_end_date(self):
        self.assertEqual(
            offer_dates("17. novembris - 4. janvāris 2027", today=date(2026, 11, 27)),
            ("2026-11-17", "2027-01-04"),
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



    def test_diaper_size_and_weight_are_not_mistaken_for_shelf_prices(self):
        cases = [
            (
                "Autiņb. PAMPERS PC S7 17+kg 27gab. "
                "(0,48 €/gab.); (0,88 €/gab.) 12 99 23 69",
                [12.99, 23.69],
            ),
            (
                "Autiņb.biks.PAMPERS PC S5 11-17kg 34gab. "
                "(0,38 €/gab.); (0,68 €/gab.) 12 99 22 99",
                [12.99, 22.99],
            ),
            (
                "Autiņb.PAMPERS Pants S6 13-19kg 42gab. "
                "(0,31 €/gab.); (0,57 €/gab.) 12 99 23 99",
                [12.99, 23.99],
            ),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(prices_from(text), expected)

    def test_pack_volume_count_and_size_ranges_are_not_mistaken_for_shelf_prices(self):
        cases = [
            ("Dzēriens OKF Big Ball Grape 0,34L D 2 49 2 99", [2.49, 2.99]),
            ("Enerģ.d.RED BULL Festive Edition 0,25L D 1 79 2 49", [1.79, 2.49]),
            ("Kaps.tr.m.maš. SOMAT Exc.Prem.5in1 42gb. 1 42 2 99", [1.42, 2.99]),
            ("Kazas piena mais. KABRITA 3 12+ 800g 14 99 19 99", [14.99, 19.99]),
            ("Veļ.mazg.līdz.PERSIL Regular 88m.r.3,96L 3 96 8 99", [3.96, 8.99]),
            ("Sporta zeķes PUMA QUARTER izmēri 35-38, 39-42, 43-46 5 49 8 99", [5.49, 8.99]),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(prices_from(text), expected)

    def test_price_per_pair_is_not_mistaken_for_shelf_price(self):
        text = "Zeķes 1 iepak./2 pāri (0,18 €/pāri) 3 99 5 49"
        self.assertEqual(prices_from(text), [3.99, 5.49])

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



    def test_discover_active_catalogues_uses_current_dates_and_pdf_links(self):
        class FakeResponse:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

        class FakeSession:
            def get(self, url, headers=None, timeout=None):
                if url == "https://www.maxima.lv/bukleti/":
                    return FakeResponse("""
                      <div class="campaign">
                        <h2>Black Friday cenu festivāls</h2>
                        <p>26.11. - 30.11.2026.</p>
                        <a href="/bukleti/black-friday">Lasīt</a>
                      </div>
                      <div class="campaign">
                        <h2>Pagājušās nedēļas akcija</h2>
                        <p>10.11. - 16.11.2026.</p>
                        <a href="/bukleti/old">Lasīt</a>
                      </div>
                    """)
                return FakeResponse("""
                  <h1>Black Friday cenu festivāls</h1>
                  <p>Piedāvājums spēkā visos MAXIMA X, XX un XXX veikalos</p>
                  <a href="/uploads/test.pdf">Lasīt PDF formātā</a>
                """)

        catalogues = discover_active_catalogues(
            FakeSession(), BeautifulSoup("""
              <div class="campaign">
                <h2>Black Friday cenu festivāls</h2>
                <p>26.11. - 30.11.2026.</p>
                <a href="/bukleti/black-friday">Lasīt</a>
              </div>
              <div class="campaign">
                <h2>Pagājušās nedēļas akcija</h2>
                <p>10.11. - 16.11.2026.</p>
                <a href="/bukleti/old">Lasīt</a>
              </div>
            """, "html.parser"),
            date(2026, 11, 27),
        )
        self.assertEqual(len(catalogues), 1)
        self.assertEqual(catalogues[0]["name"], "Black Friday cenu festivāls")
        self.assertEqual(catalogues[0]["pdfSources"], ["https://www.maxima.lv/uploads/test.pdf"])
        self.assertEqual(catalogues[0]["storeFormats"], ["X", "XX", "XXX"])

    def test_pdf_product_name_removes_orphaned_unit_price_labels(self):
        value = _clean_pdf_product_name(
            "€/kg; Tortiljas 320 g, vidējās (5,91 €/kg; 0 35 0 39"
        )
        self.assertEqual(value, "Tortiljas 320 g, vidējās")

        value = _clean_pdf_product_name("Dzēriens 545 ml, LIME&LEMON ( /l")
        self.assertEqual(value, "Dzēriens 545 ml, LIME&LEMON")

    def test_pdf_product_name_removes_card_ordinal_and_trailing_price_fragment(self):
        value = _clean_pdf_product_name(
            "2 Riekstu maisījums WELL PREMIUM 210 g, sāļais"
        )
        self.assertEqual(value, "Riekstu maisījums WELL DONE PREMIUM 210 g, sāļais")

        value = _clean_pdf_product_name("Mini Fuet izlase WELL PREMIUM 320 g 11")
        self.assertEqual(value, "Mini Fuet izlase WELL DONE PREMIUM 320 g")

    def test_pdf_product_name_removes_clipped_price_prefix_after_cleanup(self):
        value = _clean_pdf_product_name(
            "• € 4 99 49 Bļoda AURORA ROYAL WHITE 1 gab., Ø15 cm"
        )
        self.assertEqual(value, "Bļoda AURORA ROYAL WHITE 1 gab., Ø15 cm")

        multi_pack = _clean_pdf_product_name("3 IEPAK. Zeķes SEVEN LEMON 1 gab.")
        self.assertEqual(multi_pack, "3 IEPAK. Zeķes SEVEN LEMON 1 gab")

    def test_price_comparison_legal_copy_is_not_a_product_candidate(self):
        text = (
            "Cenas ir pārbaudītas un salīdzinātas, pamatojoties uz kontrolpirkumiem. "
            "Cenu salīdzinājums balstīts uz faktiskajiem pirkumiem. "
            "Salīdzinājumā iekļautie produkti Citronu ledus tēja NESTEA 1,5 l"
        )
        self.assertFalse(_looks_like_pdf_product_description(text))
        self.assertFalse(_is_pdf_product_title_seed(text))

    def test_unit_price_only_cell_is_sent_to_review_with_evidence(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(25, 190, 300, 275),
            "Provansas majonēze FRANCIS 250 g\n5 16 /kg",
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/weekly",
            "detailSource": "https://www.maxima.lv/bukleti/weekly",
            "pdfSources": ["https://www.maxima.lv/uploads/weekly.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/weekly",
        }
        offers, reviews, _ = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(offers, [])
        self.assertEqual(len(reviews), 1)
        self.assertIn("unit_price_only_or_misaligned", reviews[0]["quality"]["issues"])
        self.assertIn("5 16 /kg", reviews[0]["reviewEvidence"]["rawCellText"])
        self.assertEqual(reviews[0]["reviewEvidence"]["parsedPrices"], [5.16])

    def test_partition_review_offers_excludes_downgraded_items_from_verified_feed(self):
        verified = {"offerId": "verified-1", "quality": {"priceStatus": "verified"}}
        downgraded = {
            "offerId": "review-1",
            "quality": {"priceStatus": "review", "issues": ["ambiguous_paldies_prices"]},
        }
        missing_status = {"offerId": "review-2"}
        kept, review = partition_review_offers([verified, downgraded, missing_status])
        self.assertEqual(kept, [verified])
        self.assertEqual(review, [downgraded, missing_status])

    def test_review_summary_counts_issues_campaigns_and_examples(self):
        reviews = [
            {
                "campaign": "Weekly",
                "name": "Product A",
                "price": 1.99,
                "quality": {"issues": ["price_mismatch", "product_name_ambiguous"]},
                "reviewEvidence": {"rawCellText": "Product A 1 99"},
            },
            {
                "campaign": "Weekly",
                "name": "Product B",
                "price": 2.99,
                "quality": {"issues": ["price_mismatch"]},
            },
            {
                "campaign": "Black Friday",
                "name": "Product C",
                "price": 3.99,
                "quality": {"issues": []},
            },
        ]
        summary = build_review_summary(reviews)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["byIssue"]["price_mismatch"], 2)
        self.assertEqual(summary["byIssue"]["product_name_ambiguous"], 1)
        self.assertEqual(summary["byIssue"]["unspecified_review"], 1)
        self.assertEqual(summary["byCatalogue"]["Weekly"], 2)
        self.assertEqual(summary["byCatalogue"]["Black Friday"], 1)
        self.assertEqual(
            summary["examplesByIssue"]["price_mismatch"][0]["rawCellText"],
            "Product A 1 99",
        )

    def test_reconcile_pdf_paldies_prices_from_official_catalogue(self):
        scope = {
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
        }
        pdf_offers = [{
            "canonicalProductId": "product-123",
            "sourceId": "maxima-well-done-premium",
            "requiresPaldies": True,
            **scope,
            "price": 7.99,
            "regularPrice": None,
            "paldiesPrice": 7.99,
            "priceWithoutPaldies": None,
            "discountPercent": None,
            "quality": {"priceStatus": "verified", "issues": []},
        }]
        paldies_offers = [{
            "canonicalProductId": "product-123",
            "requiresPaldies": True,
            **scope,
            "price": 5.99,
            "regularPrice": 7.99,
            "paldiesPrice": 5.99,
            "priceWithoutPaldies": 7.99,
            "discountPercent": 25,
            "quality": {"priceStatus": "verified"},
        }]
        count = reconcile_paldies_prices(pdf_offers, paldies_offers)
        self.assertEqual(count, 1)
        self.assertEqual(pdf_offers[0]["price"], 5.99)
        self.assertEqual(pdf_offers[0]["regularPrice"], 7.99)
        self.assertEqual(pdf_offers[0]["discountPercent"], 25)

    def test_reconcile_refuses_different_paldies_dates_or_store_formats(self):
        scope = {
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX"],
        }
        pdf_offer = {
            "canonicalProductId": "product-123",
            "requiresPaldies": True,
            **scope,
            "price": 7.99,
            "quality": {"priceStatus": "verified", "issues": []},
        }
        source = {
            "canonicalProductId": "product-123",
            "requiresPaldies": True,
            **scope,
            "price": 5.99,
            "regularPrice": 7.99,
            "discountPercent": 25,
            "quality": {"priceStatus": "verified"},
        }

        date_mismatch = {**source, "validFrom": "2026-11-25"}
        self.assertEqual(
            reconcile_paldies_prices([dict(pdf_offer)], [date_mismatch]),
            0,
        )
        self.assertEqual(pdf_offer["price"], 7.99)

        format_mismatch = {**source, "storeFormats": ["XXX"]}
        offer_copy = dict(pdf_offer)
        self.assertEqual(
            reconcile_paldies_prices([offer_copy], [format_mismatch]),
            0,
        )
        self.assertEqual(offer_copy["price"], 7.99)

    def test_reconcile_marks_conflicting_paldies_prices_for_review(self):
        scope = {
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
        }
        offer = {
            "canonicalProductId": "product-123",
            "requiresPaldies": True,
            **scope,
            "price": 7.99,
            "quality": {"priceStatus": "verified", "issues": []},
        }
        source = {
            "canonicalProductId": "product-123",
            "requiresPaldies": True,
            **scope,
            "price": 5.99,
            "regularPrice": 7.99,
            "discountPercent": 25,
            "quality": {"priceStatus": "verified"},
        }
        conflicting = {**source, "price": 6.29}

        self.assertEqual(
            reconcile_paldies_prices([offer], [source, conflicting]),
            0,
        )
        self.assertEqual(offer["price"], 7.99)
        self.assertEqual(offer["quality"]["priceStatus"], "review")
        self.assertIn("ambiguous_paldies_prices", offer["quality"]["issues"])
        self.assertEqual(len(offer["reviewEvidence"]["paldiesCandidates"]), 2)

    def test_reconcile_marks_paldies_discount_mismatch_for_review(self):
        scope = {
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
        }
        offer = {
            "canonicalProductId": "product-123",
            "requiresPaldies": True,
            **scope,
            "price": 7.99,
            "quality": {"priceStatus": "verified", "issues": []},
        }
        source = {
            "canonicalProductId": "product-123",
            "requiresPaldies": True,
            **scope,
            "price": 5.99,
            "regularPrice": 7.99,
            "discountPercent": 20,
            "quality": {"priceStatus": "verified"},
        }

        self.assertEqual(reconcile_paldies_prices([offer], [source]), 0)
        self.assertEqual(offer["price"], 7.99)
        self.assertEqual(offer["quality"]["priceStatus"], "review")
        self.assertIn(
            "paldies_discount_percent_mismatch",
            offer["quality"]["issues"],
        )
        self.assertEqual(
            offer["reviewEvidence"]["paldiesCandidate"]["computedDiscountPercent"],
            25,
        )

    def test_well_done_page_is_detected_with_one_text_mention(self):
        weekly = {"id": "maxima-weekly", "name": "Iknedēļas piedāvājums"}
        premium = {"id": "maxima-well-done-premium", "name": "Well Done Premium"}
        result = _catalogue_for_pdf_page(
            "WELL DONE PREMIUM Aitas siers 150 g 3 99 4 99",
            [weekly, premium],
            weekly,
        )
        self.assertEqual(result["id"], "maxima-well-done-premium")

    def test_mixed_black_friday_page_is_not_repeated_from_weekly_pdf(self):
        import pymupdf as fitz

        document = fitz.open()
        weekly_page = document.new_page(width=600, height=800)
        weekly_page.insert_textbox(
            fitz.Rect(25, 190, 220, 270),
            "Makaroni EXTRA LINE 500 g\n1 99 2 49",
        )
        bf_page = document.new_page(width=600, height=800)
        bf_page.insert_textbox(
            fitz.Rect(25, 40, 570, 130),
            "BLACK FRIDAY CENU FESTIVĀLS",
        )
        bf_page.insert_textbox(
            fitz.Rect(25, 190, 220, 270),
            "Pampers autiņbiksītes 24 gab.\n20 99 44 99",
        )
        payload = document.tobytes()
        document.close()

        weekly = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/weekly",
            "detailSource": "https://www.maxima.lv/bukleti/weekly",
            "pdfSources": ["https://www.maxima.lv/uploads/weekly.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/weekly",
        }
        black_friday = {
            **weekly,
            "id": "maxima-catalogue-black-friday-cenu-festivals",
            "name": "Black Friday cenu festivāls",
        }
        offers, reviews, pages = parse_catalogue_pdf(
            payload, weekly, [weekly, black_friday], today=date(2026, 11, 27)
        )
        self.assertEqual(len(offers), 1)
        self.assertEqual(reviews, [])
        self.assertEqual(pages, {"maxima-weekly": 1})
        self.assertEqual(offers[0]["name"], "Makaroni EXTRA LINE 500 g")

    def test_black_friday_first_page_prices_require_paldies_card(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(25, 190, 260, 275),
            "Autiņbiksītes PAMPERS 24 gab.\n20 99 44 99",
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-catalogue-black-friday-cenu-festivals",
            "name": "Black Friday cenu festivāls",
            "source": "https://www.maxima.lv/bukleti/black-friday",
            "detailSource": "https://www.maxima.lv/bukleti/black-friday",
            "pdfSources": ["https://www.maxima.lv/uploads/black-friday.pdf"],
            "validFrom": "2026-11-26",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/black-friday",
        }
        offers, reviews, _ = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(len(offers), 1)
        self.assertEqual(reviews, [])
        self.assertTrue(offers[0]["requiresPaldies"])
        self.assertEqual(offers[0]["paldiesPrice"], 20.99)
        self.assertEqual(offers[0]["priceWithoutPaldies"], 44.99)

    def test_pack_count_prefix_is_not_product_name_ambiguity(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(25, 190, 300, 275),
            "3 IEPAK. Socks SEVEN LEMON 1 gab.\n4 99 6 99",
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/weekly",
            "detailSource": "https://www.maxima.lv/bukleti/weekly",
            "pdfSources": ["https://www.maxima.lv/uploads/weekly.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/weekly",
        }
        offers, reviews, _ = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(len(offers), 1)
        self.assertEqual(reviews, [])
        self.assertNotIn("product_name_ambiguous", offers[0]["quality"]["issues"])

    def test_black_friday_layout_fragments_are_cleaned_before_verification(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(25, 190, 270, 275),
            "6 izmērs 15+ kg Putekļsūcējs robots ROBOROCK Q7 BF 1 gab., melns\n139 99 229 99",
        )
        page.insert_textbox(
            fitz.Rect(320, 190, 580, 275),
            "Matu veidotājs vai taisnotājs DYSON 1 gab.\n369 99 499 99 no",
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-catalogue-black-friday-cenu-festivals",
            "name": "Black Friday cenu festivāls",
            "source": "https://www.maxima.lv/bukleti/black-friday",
            "detailSource": "https://www.maxima.lv/bukleti/black-friday",
            "pdfSources": ["https://www.maxima.lv/uploads/black-friday.pdf"],
            "validFrom": "2026-11-26",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/black-friday",
        }
        offers, reviews, _ = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(
            len(offers),
            2,
            f"offers={[offer['name'] for offer in offers]!r}; reviews={[(item['name'], item['quality']['issues']) for item in reviews]!r}",
        )
        self.assertEqual(reviews, [])
        by_name = {offer["name"]: offer for offer in offers}
        robovac = next(value for name, value in by_name.items() if "ROBOROCK Q7" in name)
        dyson = next(value for name, value in by_name.items() if "DYSON" in name)
        self.assertTrue(robovac["name"].startswith("Putek"))
        self.assertIn("robots ROBOROCK Q7 BF 1 gab., melns", robovac["name"])
        self.assertEqual((robovac["price"], robovac["regularPrice"]), (139.99, 229.99))
        self.assertTrue(dyson["name"].startswith("Matu veidot"))
        self.assertTrue(dyson["name"].endswith("DYSON 1 gab."))
        self.assertEqual((dyson["price"], dyson["regularPrice"]), (369.99, 499.99))
        self.assertTrue(robovac["requiresPaldies"])
        self.assertTrue(dyson["requiresPaldies"])

    def test_roja_event_text_in_product_title_requires_review(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(25, 190, 260, 275),
            "Kausētais siers DZINTARS 200 g Rojā Darba dienā\n0 69 1 29",
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-catalogue-roja-selgas-iela-2",
            "name": "Roja, Selgas iela 2",
            "source": "https://www.maxima.lv/bukleti/roja",
            "detailSource": "https://www.maxima.lv/bukleti/roja",
            "pdfSources": ["https://www.maxima.lv/uploads/roja.pdf"],
            "validFrom": "2026-11-26",
            "validUntil": "2026-11-29",
            "storeFormats": [],
            "storeDescription": "Selgas iela 12, Rojā",
            "city": "Roja",
        }
        offers, reviews, _ = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(offers, [])
        self.assertEqual(len(reviews), 1)
        self.assertIn("product_name_ambiguous", reviews[0]["quality"]["issues"])

    def test_pdf_parser_splits_stacked_products_inside_one_text_block(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        # One PDF text block contains two product cards in the same column.
        # Each product title and its two prices occupy separate text lines.
        page.insert_textbox(
            fitz.Rect(20, 100, 260, 260),
            "Coffee Set KITCHEN 1 gab.\n"
            "9 99 21 99\n"
            "Tablecloth HOME 1 gab.\n"
            "14 99 24 99",
            fontsize=10,
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/test",
            "detailSource": "https://www.maxima.lv/bukleti/test",
            "pdfSources": ["https://www.maxima.lv/uploads/test.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/test",
        }
        offers, reviews, pages = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )

        self.assertEqual(len(offers), 2)
        self.assertEqual(reviews, [])
        self.assertEqual(pages, {"maxima-weekly": 1})
        by_name = {offer["name"]: offer for offer in offers}
        coffee = next(value for name, value in by_name.items() if name.startswith("Coffee Set KITCHEN"))
        tablecloth = next(value for name, value in by_name.items() if name.startswith("Tablecloth HOME"))
        self.assertEqual(
            (coffee["price"], coffee["regularPrice"]),
            (9.99, 21.99),
        )
        self.assertEqual(
            (tablecloth["price"], tablecloth["regularPrice"]),
            (14.99, 24.99),
        )

    def test_pdf_parser_splits_wide_price_text_block_by_product_column(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(fitz.Rect(20, 180, 180, 215), "Yellow Cheese 180 g")
        page.insert_textbox(fitz.Rect(220, 180, 380, 215), "Soda Drink 1 l")
        page.insert_textbox(fitz.Rect(420, 180, 580, 215), "Olive Oil 1 l")
        merged_prices = (
            "6 99 14 99" + (" " * 40)
            + "20 99 44 99" + (" " * 40)
            + "19 99 49 99"
        )
        page.insert_text(fitz.Point(25, 245), merged_prices, fontname="cour", fontsize=8)
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/test",
            "detailSource": "https://www.maxima.lv/bukleti/test",
            "pdfSources": ["https://www.maxima.lv/uploads/test.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/test",
        }
        offers, reviews, pages = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(len(offers), 3)
        self.assertEqual(reviews, [])
        self.assertEqual(pages, {"maxima-weekly": 1})
        expected = {
            "Yellow Cheese 180 g": (6.99, 14.99),
            "Soda Drink 1 l": (20.99, 44.99),
            "Olive Oil 1 l": (19.99, 49.99),
        }
        for offer in offers:
            self.assertEqual(
                (offer["price"], offer["regularPrice"]),
                expected[offer["name"]],
            )

    def test_parse_catalogue_pdf_extracts_product_price_pair(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(fitz.Rect(25, 190, 180, 250), "Siers WELL DONE PREMIUM 180 g\n3 29 4 49")
        page.insert_textbox(fitz.Rect(320, 190, 475, 250), "Banani 1 kg\n1 19 -34% 0 79")
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/test",
            "detailSource": "https://www.maxima.lv/bukleti/test",
            "pdfSources": ["https://www.maxima.lv/uploads/test.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/test",
        }
        offers, reviews, pages = parse_catalogue_pdf(payload, catalogue, [catalogue], today=date(2026, 11, 27))
        self.assertEqual(
            len(offers), 2,
            f"offers={[(o['name'], o['price'], o['regularPrice'], o['quality']) for o in offers]!r}; "
            f"reviews={[(o['name'], o['quality'], o.get('reviewEvidence')) for o in reviews]!r}",
        )
        self.assertEqual(reviews, [])
        self.assertEqual(pages, {"maxima-weekly": 1})
        by_name = {offer["name"]: offer for offer in offers}
        cheese = next(offer for name, offer in by_name.items() if "Siers WELL DONE" in name)
        bananas = next(offer for name, offer in by_name.items() if "Banani" in name)
        self.assertEqual((cheese["price"], cheese["regularPrice"]), (3.29, 4.49))
        self.assertEqual((bananas["price"], bananas["regularPrice"]), (0.79, 1.19))
        self.assertEqual(bananas["discountPercent"], 34)
        self.assertEqual(cheese["pdfPage"], 1)
        self.assertEqual(cheese["validFrom"], "2026-11-24")

    def test_pdf_parser_joins_separate_title_and_price_blocks_and_detects_paldies(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(35, 205, 230, 230),
            "Siers STRACCIATELLA WELL DONE PREMIUM",
        )
        page.insert_textbox(
            fitz.Rect(35, 238, 230, 315),
            "150 g\nCena ar Paldies karti 1 79\nParastā cena 1 99",
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-well-done-premium",
            "name": "Well Done Premium",
            "source": "https://www.maxima.lv/bukleti/test",
            "detailSource": "https://www.maxima.lv/bukleti/test",
            "pdfSources": ["https://www.maxima.lv/uploads/test.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/test",
        }
        offers, reviews, pages = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(len(offers), 1)
        self.assertEqual(reviews, [])
        self.assertEqual(pages, {"maxima-well-done-premium": 1})
        offer = offers[0]
        self.assertAlmostEqual(offer["price"], 1.79)
        self.assertAlmostEqual(offer["regularPrice"], 1.99)
        self.assertTrue(offer["requiresPaldies"])
        self.assertEqual(offer["paldiesPrice"], 1.79)
        self.assertEqual(offer["priceWithoutPaldies"], 1.99)
        self.assertNotIn("Cena ar", offer["name"])
        self.assertEqual(offer["quality"]["priceStatus"], "verified")

    def test_detect_all_explicit_store_formats(self):
        text = "Piedāvājums spēkā visos MAXIMA X, XX un XXX veikalos"
        self.assertEqual(store_formats_from_text(text), ["X", "XX", "XXX"])

    def test_detect_single_explicit_store_format(self):
        self.assertEqual(
            store_formats_from_text("Piedāvājums spēkā MAXIMA XX veikalos"),
            ["XX"],
        )

    def test_reversed_pdf_price_order_is_normalized_to_promo_then_regular(self):
        price, regular, discount, issues = price_quality(
            [44.95, 6.99],
            "Aromāta 1 gab. 44,95 6 99",
        )
        self.assertEqual((price, regular), (6.99, 44.95))
        self.assertEqual(discount, 84)
        self.assertEqual(issues, [])

    def test_multi_buy_price_order_remains_ambiguous(self):
        price, regular, discount, issues = price_quality(
            [2.98, 1.49],
            "Biezpiena krēms EMMA 2x 200 g 1+1 1 49",
        )
        self.assertEqual(price, 2.98)
        self.assertIsNone(regular)
        self.assertIsNone(discount)
        self.assertIn("ambiguous_multi_buy_price_order", issues)

    def test_explicit_shelf_price_is_distinguished_from_unit_price(self):
        self.assertTrue(
            _has_explicit_shelf_price(
                "Apelsīnu želeja 5,87 € (9,94 €/kg)",
                5.87,
            )
        )
        self.assertFalse(
            _has_explicit_shelf_price(
                "Avota ūdens (0,59 €/l) 0 89",
                0.89,
            )
        )

    def test_discount_mismatch_requires_review(self):
        _, _, _, issues = price_quality([3.29, 4.49], "3 29 4 49 -20%")
        self.assertIn("discount_percent_mismatch", issues)

    def test_more_than_two_shelf_prices_requires_review(self):
        _, _, _, issues = price_quality([1.00, 2.00, 3.00], "1,00 2,00 3,00")
        self.assertIn("ambiguous_multiple_price_values", issues)

    def test_pdf_parser_splits_inline_bullet_separated_products(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        # Two complete offer cards are laid out on one PDF text line.
        # Bullet glyphs are the only reliable boundary between the cards.
        page.insert_text(
            fitz.Point(20, 220),
            "2 1. • Baking Set 1 iepak./3 gab. 9,99 €; 21,99 € "
            "• Tablecloth 1 gab., 148x250 cm 14,99 €; 24,99",
            fontsize=8,
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/test",
            "detailSource": "https://www.maxima.lv/bukleti/test",
            "pdfSources": ["https://www.maxima.lv/uploads/test.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/test",
        }
        offers, reviews, pages = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )

        self.assertEqual(
            len(offers),
            2,
            f"offers={[(o['name'], o['price'], o['regularPrice']) for o in offers]!r}; "
            f"reviews={[(o['name'], o['quality']['issues']) for o in reviews]!r}",
        )
        self.assertEqual(reviews, [])
        self.assertEqual(pages, {"maxima-weekly": 1})
        by_name = {offer["name"]: offer for offer in offers}
        baking = next(value for name, value in by_name.items() if name.startswith("Baking Set"))
        tablecloth = next(value for name, value in by_name.items() if name.startswith("Tablecloth"))
        self.assertEqual((baking["price"], baking["regularPrice"]), (9.99, 21.99))
        self.assertEqual((tablecloth["price"], tablecloth["regularPrice"]), (14.99, 24.99))

    def test_pdf_product_name_removes_orphaned_card_digit_after_leading_price(self):
        self.assertEqual(
            _clean_pdf_product_name("0,45 0 Kartupeļi 1 kg"),
            "Kartupeļi 1 kg",
        )
        self.assertEqual(
            _clean_pdf_product_name("11,99 6 Varavīksnes forele 1 kg, ķidāta"),
            "Varavīksnes forele 1 kg, ķidāta",
        )
        self.assertEqual(
            _clean_pdf_product_name("0% Greek yogurt 400 g 0,45 €"),
            "0% Greek yogurt 400 g",
        )

    def test_pdf_price_before_product_title_requires_review(self):
        import pymupdf as fitz

        raw_text = "11,99 6 Varavīksnes forele 1 kg, ķidāta"
        self.assertTrue(_pdf_cell_starts_with_price(raw_text))
        self.assertFalse(_pdf_cell_starts_with_price("Varavīksnes forele 1 kg 6,99 11,99"))
        self.assertFalse(_pdf_cell_starts_with_price("5,87 € Apelsīnu želeja 180 g"))
        self.assertEqual(
            _clean_pdf_product_name(raw_text),
            "Varavīksnes forele 1 kg, ķidāta",
        )

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(25, 190, 280, 275),
            raw_text,
            fontsize=10,
        )
        payload = document.tobytes()
        document.close()

        catalogue = {
            "id": "maxima-weekly",
            "name": "Iknedēļas piedāvājums",
            "source": "https://www.maxima.lv/bukleti/test",
            "detailSource": "https://www.maxima.lv/bukleti/test",
            "pdfSources": ["https://www.maxima.lv/uploads/test.pdf"],
            "validFrom": "2026-11-24",
            "validUntil": "2026-11-30",
            "storeFormats": ["X", "XX", "XXX"],
            "storeScopeSource": "https://www.maxima.lv/bukleti/test",
        }
        offers, reviews, _ = parse_catalogue_pdf(
            payload, catalogue, [catalogue], today=date(2026, 11, 27)
        )
        self.assertEqual(offers, [])
        self.assertEqual(len(reviews), 1)
        self.assertIn("price_before_product_name", reviews[0]["quality"]["issues"])

    def test_pdf_geometry_keeps_staggered_products_in_their_columns(self):
        import pymupdf as fitz

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        columns = [
            [20, 35, 28],
            [220, 235, 228],
            [420, 435, 428],
        ]
        for column_index, starts in enumerate(columns):
            for row_index, x in enumerate(starts):
                y = 90 + row_index * 220 + column_index * 4
                page.insert_textbox(
                    fitz.Rect(x, y, x + 160, y + 75),
                    f"Product {column_index + 1}-{row_index + 1} 1 gab.\n"
                    f"{1 + column_index * 3 + row_index}.99 {9 + column_index * 3 + row_index}.99",
                    fontsize=9,
                )

        cells = _pdf_page_cells_geometry(page)
        document.close()

        self.assertEqual(len(cells), 9, f"cells={cells!r}")
        self.assertEqual(
            sorted(column for _, column, _ in cells),
            [1, 1, 1, 2, 2, 2, 3, 3, 3],
        )
        self.assertTrue(
            all(len(prices_from(text)) == 2 for _, _, text in cells),
            f"unexpected merged cells: {cells!r}",
        )

    def test_pdf_bullet_split_requires_complete_pairs_for_each_product(self):
        raw = (
            "2 1. • Baking Set 1 iepak./3 gab. 9,99 €; 21,99 € "
            "• Tablecloth 1 gab., 148x250 cm 14,99 €; 24,99"
        )
        parts = _split_pdf_cell_by_bullets(raw)
        self.assertEqual(len(parts), 2)
        names = [_clean_pdf_product_name(part) for part in parts]
        self.assertEqual(names, [
            "Baking Set 1 iepak./3 gab.",
            "Tablecloth 1 gab., 148x250 cm",
        ])
        self.assertEqual([prices_from(part) for part in parts], [
            [9.99, 21.99],
            [14.99, 24.99],
        ])

        # A partial or noisy second card must keep the original merged cell
        # under review instead of silently dropping or reallocating prices.
        incomplete = "• Baking Set 1 gab. 9,99 €; 21,99 € • Tablecloth 1 gab. 14,99 €; 24,99 €; 29,99 €"
        self.assertEqual(_split_pdf_cell_by_bullets(incomplete), [incomplete])


if __name__ == "__main__":
    unittest.main()
