import unittest

from scripts.qwen_pdf_review import _model_json, _valid_suggestion


class QwenPdfReviewTests(unittest.TestCase):
    def test_model_json_extracts_result_after_logs_and_ansi_codes(self):
        result = {"results": [{"offerId": "card-1", "decision": "unclear"}]}
        content = (
            "\x1b[32mllama.cpp startup log\x1b[0m\n"
            "<think>Inspect the visible card.</think>\n"
            '{"results":[{"offerId":"card-1","decision":"unclear"}]}\n'
        )

        self.assertEqual(_model_json(content), result)

    def test_model_json_rejects_missing_results_array(self):
        self.assertIsNone(_model_json("log {\"offerId\":\"card-1\"}"))
        self.assertIsNone(_model_json("not json at all"))

    def test_valid_suggestion_accepts_and_normalizes_valid_result(self):
        suggestion = _valid_suggestion(
            {"card-1"},
            {
                "offerId": "card-1",
                "decision": "resolved",
                "confidence": 0.9,
                "productName": "Product",
                "price": 1,
                "regularPrice": None,
                "requiresPaldies": False,
            },
        )

        self.assertEqual(suggestion["confidence"], 0.9)
        self.assertEqual(suggestion["price"], 1.0)
        self.assertIsNone(suggestion["regularPrice"])
        self.assertIs(suggestion["requiresPaldies"], False)

    def test_valid_suggestion_rejects_untrusted_or_malformed_values(self):
        base = {
            "offerId": "card-1",
            "decision": "resolved",
            "confidence": 0.9,
            "price": 1.99,
        }
        invalid = [
            {**base, "offerId": "other-card"},
            {**base, "decision": "guess"},
            {**base, "confidence": True},
            {**base, "confidence": float("nan")},
            {**base, "confidence": 1.1},
            {**base, "price": "1.99"},
            {**base, "price": 10000},
            {**base, "productName": []},
            {**base, "requiresPaldies": "yes"},
        ]

        for item in invalid:
            with self.subTest(item=item):
                self.assertIsNone(_valid_suggestion({"card-1"}, item))


if __name__ == "__main__":
    unittest.main()
