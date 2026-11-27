import unittest

from scripts.product_identity import build_product_groups, product_identity


class ProductIdentityTests(unittest.TestCase):
    def test_word_order_punctuation_and_diacritics_do_not_split_same_product(self):
        left = product_identity("Siers ar trifelēm WELL DONE PREMIUM, 180 g")
        right = product_identity("Siers WELL DONE PREMIUM ar trifelēm 180g")
        self.assertEqual(left["canonicalProductId"], right["canonicalProductId"])

    def test_equivalent_mass_units_match(self):
        left = product_identity("Piens VALMIERA, 1 kg")
        right = product_identity("Piens VALMIERA 1000g")
        self.assertEqual(left["canonicalProductId"], right["canonicalProductId"])
        self.assertEqual(left["packageSize"], "1 kg")

    def test_product_package_size_is_formatted_for_display(self):
        self.assertEqual(product_identity("Siers WELL DONE PREMIUM, 180 g")["packageSize"], "180 g")

    def test_different_package_sizes_do_not_match(self):
        left = product_identity("Jogurts, 180 g")
        right = product_identity("Jogurts, 450 g")
        self.assertNotEqual(left["canonicalProductId"], right["canonicalProductId"])

    def test_different_flavours_do_not_match(self):
        left = product_identity("Siera uzkoda WELL DONE PREMIUM Pesto 24g")
        right = product_identity("Siera uzkoda WELL DONE PREMIUM Pizza 24g")
        self.assertNotEqual(left["canonicalProductId"], right["canonicalProductId"])

    def test_grouping_keeps_every_campaign_variant(self):
        offers = [
            {
                "name": "Siers ar trifelēm WELL DONE PREMIUM, 180 g",
                "price": 4.49,
                "campaign": "Iknedēļas piedāvājums",
                "storeFormats": ["X", "XX", "XXX"],
            },
            {
                "name": "Siers WELL DONE PREMIUM ar trifelēm 180g",
                "price": 4.39,
                "campaign": "Well Done Premium",
                "storeFormats": ["X", "XX", "XXX"],
            },
        ]
        products = build_product_groups(offers)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["offerCount"], 2)
        self.assertEqual(products[0]["lowestPrice"], 4.39)
        self.assertEqual(
            products[0]["campaigns"],
            ["Iknedēļas piedāvājums", "Well Done Premium"],
        )


if __name__ == "__main__":
    unittest.main()
