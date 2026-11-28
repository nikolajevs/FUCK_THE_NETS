import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from scripts.booklet_vision import (
    PROMPT_VERSION, QWEN_LABEL, Page, PageReader, VisionCache, extract_json, image_page, is_weak,
    name_in_words, offers_from_result, page_prompt, price_in_words, process_pending, read_booklet,
)
from scripts.run_scrapers import selected_stores
from scripts.stores import Store, by_id

IMAGE_STORE = Store("demo", "Demo", "scrape_demo.py", "data/demo-offers.json", "x",
                    vision_hint="Bundles look like '3 par 2'.")
TEXT_STORE = Store("demo", "Demo", "scrape_demo.py", "data/demo-offers.json", "x")
ART_STORE = Store("demo", "Demo", "scrape_demo.py", "data/demo-offers.json", "x", prices_in_text=False)
META = {"campaign": "Demo buklets", "campaignId": "demo-1", "source": "https://x/demo.pdf",
        "validFrom": "2026-11-24", "validUntil": "2026-11-30"}


def card(**overrides):
    item = {"name": "Piens Alma 2,5%", "brand": "Alma", "packageSize": "1 l", "price": 0.99,
            "regularPrice": 1.39, "discountPercent": 29, "multiBuyCount": None,
            "multiBuyTotal": None, "requiresCard": False, "cardName": None,
            "box_2d": [100, 100, 300, 400], "confidence": 0.9, "evidence": "0,99"}
    item.update(overrides)
    return item


def page(words=(), number=1, image=b"png"):
    return Page(f"test:{number}", number, META, lambda: image, "image/png", list(words))


# Words under the card box [100,100,300,400] -> x 0.10..0.40, y 0.10..0.30.
CARD_WORDS = [(0.12, 0.12, 0.2, 0.14, "PIENS"), (0.21, 0.12, 0.3, 0.14, "ALMA"),
              (0.12, 0.15, 0.2, 0.17, "2,5%"), (0.12, 0.25, 0.18, 0.28, "0,99"),
              (0.20, 0.25, 0.26, 0.28, "1,39")]


def judge(store, item, words=()):
    return offers_from_result({"results": [item]}, page(words), store, "model-x")


class PromptTests(unittest.TestCase):
    def test_prompt_is_generic_with_chain_hint(self):
        prompt = page_prompt(IMAGE_STORE)
        self.assertIn("(Demo)", prompt)
        self.assertIn("Chain note: Bundles look like '3 par 2'.", prompt)
        self.assertIn("box_2d", prompt)
        self.assertIn("packageSize", prompt)
        self.assertNotIn("Chain note", page_prompt(TEXT_STORE))

    def test_extract_json_from_noisy_output(self):
        text = 'loading model...\n{"x": 1}\nanswer: {"results": [{"name": "A"}]} done'
        self.assertEqual(extract_json(text), {"results": [{"name": "A"}]})
        self.assertIsNone(extract_json("no json here"))


class OfferRuleTests(unittest.TestCase):
    def test_image_page_card(self):
        offers, reviews = judge(IMAGE_STORE, card())
        self.assertEqual(reviews, [])
        offer = offers[0]
        self.assertEqual(offer["name"], "Piens Alma 2,5% 1 l")
        self.assertEqual((offer["price"], offer["regularPrice"], offer["discountPercent"]), (0.99, 1.39, 29))
        self.assertEqual((offer["packageSize"], offer["brand"]), ("1 l", "Alma"))
        self.assertEqual((offer["campaignId"], offer["pdfPage"], offer["store"]), ("demo-1", 1, "demo"))
        self.assertEqual(offer["visionModel"], "model-x")
        self.assertNotIn("textCheck", offer)
        self.assertTrue(offer["offerId"].startswith("demo-demo-1-p1-"))

    def test_size_already_in_name_is_not_repeated(self):
        offers, _ = judge(IMAGE_STORE, card(name="Piens Alma 1L"))
        self.assertEqual(offers[0]["name"], "Piens Alma 1L")

    def test_bundle_is_divided_locally(self):
        offers, _ = judge(IMAGE_STORE, card(price=None, multiBuyCount=2, multiBuyTotal=3.0,
                                            regularPrice=1.99, discountPercent=None))
        self.assertEqual((offers[0]["price"], offers[0]["multiBuy"]), (1.5, "2"))

    def test_review_reasons(self):
        cases = {
            "missing_price": card(price=None, discountPercent=None),
            "percent_only": card(price=None, regularPrice=None),
            "price_mismatch": card(discountPercent=60),
            "unit_price_suspected": card(name="Hig. paketes Always 2 veidi cenas no", price=0.27,
                                         regularPrice=None, packageSize=None, evidence="0,27"),
            "low_confidence": card(confidence=0.7, discountPercent=None),
        }
        for reason, item in cases.items():
            offers, reviews = judge(IMAGE_STORE, item)
            self.assertEqual(offers, [], reason)
            self.assertEqual(reviews[0]["reviewReason"], reason)

    def test_unit_and_from_prices_are_suspected(self):
        for item in (card(name="Kafija Jacobs 0,99", price=0.99, regularPrice=None, discountPercent=None),
                     card(evidence="0,99 €/kg"),
                     card(name="Svece cena no", price=0.99)):
            _, reviews = judge(IMAGE_STORE, item)
            self.assertEqual(reviews[0]["reviewReason"], "unit_price_suspected", item)
        offers, _ = judge(IMAGE_STORE, card(name="Salvetes Ruta 20gab./iep.", packageSize=None))
        self.assertEqual(len(offers), 1)

    def test_consistent_price_triple_confirms_the_card(self):
        # 0.99 / 1.39 / -29 % agree, so a lower confidence is enough.
        offers, _ = judge(IMAGE_STORE, card(confidence=0.6))
        self.assertEqual(len(offers), 1)

    def test_missing_confidence_is_unknown_not_zero(self):
        offers, _ = judge(IMAGE_STORE, card(confidence=None))
        self.assertEqual(offers[0]["visionConfidence"], 0.5)
        _, reviews = judge(IMAGE_STORE, card(confidence=None, discountPercent=None))
        self.assertEqual(reviews[0]["reviewReason"], "low_confidence")

    def test_dropped_and_cleaned(self):
        self.assertEqual(judge(IMAGE_STORE, card(name="Alus Cēsu", packageSize="0,5 l")), ([], []))
        self.assertEqual(judge(IMAGE_STORE, card(name="  ")), ([], []))
        offers, _ = judge(IMAGE_STORE, card(regularPrice=0.99, discountPercent=None))
        self.assertIsNone(offers[0]["regularPrice"])
        offers, _ = judge(IMAGE_STORE, card(price=5000))
        self.assertEqual(offers, [])

    def test_duplicates_on_a_page_are_kept_once(self):
        offers, _ = offers_from_result({"results": [card(), card()]}, page(), IMAGE_STORE, "m")
        self.assertEqual(len(offers), 1)

    def test_text_layer_confirms_name_and_price(self):
        offers, reviews = judge(TEXT_STORE, card(confidence=0.6), CARD_WORDS)
        self.assertEqual(reviews, [])
        self.assertEqual(offers[0]["textCheck"], {"name": True, "price": True})

    def test_text_layer_rejects_wrong_price_or_name(self):
        _, reviews = judge(TEXT_STORE, card(price=1.09, discountPercent=None), CARD_WORDS)
        self.assertEqual(reviews[0]["reviewReason"], "price_not_in_text")
        _, reviews = judge(TEXT_STORE, card(name="Kefīrs Valio"), CARD_WORDS)
        self.assertEqual(reviews[0]["reviewReason"], "name_not_in_text")

    def test_words_outside_the_box_do_not_count(self):
        far = [(0.8, 0.8, 0.9, 0.82, w[4]) for w in CARD_WORDS]
        _, reviews = judge(TEXT_STORE, card(), far)
        self.assertEqual(reviews[0]["reviewReason"], "price_not_in_text")
        # Without a box the whole page is searched.
        offers, _ = judge(TEXT_STORE, card(box_2d=None), far)
        self.assertEqual(len(offers), 1)

    def test_artwork_prices_only_check_names(self):
        words = [w for w in CARD_WORDS if not w[4][0].isdigit()]
        offers, _ = judge(ART_STORE, card(confidence=0.6), words)
        self.assertEqual(offers[0]["textCheck"], {"name": True, "price": None})

    def test_price_and_name_matching(self):
        words = lambda *texts: [(0, 0, 1, 1, t) for t in texts]
        self.assertTrue(price_in_words(1.29, words("1,29")))
        self.assertTrue(price_in_words(1.29, words("€1.29")))
        self.assertTrue(price_in_words(1.29, words("129")))
        self.assertTrue(price_in_words(1.29, words("1", "29")))
        self.assertFalse(price_in_words(1.29, words("1,92", "2,29")))
        self.assertTrue(name_in_words("Rēzeknes desiņas", words("REZEKNES", "DESIŅAS")))
        self.assertTrue(name_in_words("Šokolāde Laima", words("ŠOKOLĀ-", "DE", "LAIMA")))
        self.assertFalse(name_in_words("Kefīrs Valio", words("PIENS", "ALMA")))


class CacheTests(unittest.TestCase):
    def test_put_get_and_prompt_version(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = VisionCache("demo", Path(temp), today="2026-11-28")
            cache.put("k1", {"results": []}, "gemini")
            cache.save()
            raw = (Path(temp) / "demo.json").read_bytes()
            self.assertNotIn(b"\r\n", raw)
            self.assertIn(f"k1|v{PROMPT_VERSION}".encode(), raw)
            again = VisionCache("demo", Path(temp), today="2026-11-29")
            self.assertEqual(again.get("k1")["model"], "gemini")
            self.assertIsNone(again.get("k2"))

    def test_old_entries_are_pruned(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = VisionCache("demo", Path(temp), today="2026-01-01")
            cache.put("old", {"results": []}, "m")
            cache.save()
            later = VisionCache("demo", Path(temp), today="2026-03-01")
            later.put("new", {"results": []}, "m")
            later.save()
            self.assertEqual([k.split("|")[0] for k in VisionCache("demo", Path(temp)).pages], ["new"])

    def test_weak_answers_are_only_local_low_confidence_ones(self):
        low = {"model": QWEN_LABEL, "result": {"results": [{"confidence": 0.3}, {"confidence": 0.5}]}}
        high = {"model": QWEN_LABEL, "result": {"results": [{"confidence": 0.9}]}}
        gemini = {"model": "gemini (Gemini API)", "result": {"results": []}}
        self.assertTrue(is_weak(low))
        self.assertFalse(is_weak(high))
        self.assertFalse(is_weak(gemini))


class FakeClient:
    model = "fake-gemini"

    def __init__(self, answer=None):
        self.answer = {"results": [card()]} if answer is None else answer
        self.calls = 0

    def generate(self, image, prompt, mime_type="image/png"):
        self.calls += 1
        return self.answer


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = VisionCache("demo", self.root / "cache", today="2026-11-28")

    def tearDown(self):
        self.temp.cleanup()

    def test_gemini_answer_is_cached_and_reused(self):
        client = FakeClient()
        reader = PageReader(IMAGE_STORE, self.cache, client)
        result, model = reader.read(page())
        self.assertEqual((client.calls, model), (1, "fake-gemini (Gemini API)"))
        reader.read(page())
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.cache.get("test:1")["result"], result)

    def test_unread_pages_are_left_for_qwen(self):
        from PIL import Image
        buffer = BytesIO()
        Image.new("RGB", (4, 4), "white").save(buffer, format="JPEG")
        jpeg_page = image_page(buffer.getvalue(), 3, META, "image/jpeg")
        reader = PageReader(IMAGE_STORE, self.cache, None, self.root / "pending")
        self.assertEqual(reader.read(jpeg_page), (None, None))
        sidecars = list((self.root / "pending" / "demo").glob("*.json"))
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(json.loads(sidecars[0].read_text(encoding="utf-8"))["key"], jpeg_page.key)
        self.assertTrue(sidecars[0].with_suffix(".png").read_bytes().startswith(b"\x89PNG"))

    def test_failed_gemini_call_also_leaves_page_pending(self):
        reader = PageReader(IMAGE_STORE, self.cache, FakeClient(answer={"oops": 1}), self.root / "p")
        self.assertEqual(reader.read(page()), (None, None))
        self.assertEqual(len(list((self.root / "p" / "demo").glob("*.png"))), 1)

    def test_weak_qwen_page_is_read_again_by_gemini(self):
        self.cache.put("test:1", {"results": [{"confidence": 0.2}]}, QWEN_LABEL)
        client = FakeClient()
        PageReader(IMAGE_STORE, self.cache, client).read(page())
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.cache.get("test:1")["model"], "fake-gemini (Gemini API)")
        # Without quota the weak answer is still used.
        self.cache.put("test:2", {"results": [card(confidence=0.2)]}, QWEN_LABEL)
        result, model = PageReader(IMAGE_STORE, self.cache, None).read(page(number=2))
        self.assertEqual(model, QWEN_LABEL)

    def test_read_booklet_counts_and_dedupes_across_pages(self):
        self.cache.put("test:1", {"results": [card()]}, "m")
        self.cache.put("test:2", {"results": [card(), card(name="Maize Rudzu", price=1.1,
                                                          regularPrice=None, discountPercent=None)]}, "m")
        outcome = read_booklet([page(number=1), page(number=2), page(number=3)],
                               PageReader(IMAGE_STORE, self.cache))
        self.assertEqual(sorted(o["name"] for o in outcome.offers), ["Maize Rudzu 1 l", "Piens Alma 2,5% 1 l"])
        self.assertEqual((outcome.resolved, outcome.pending), ({1, 2}, 1))


class QwenStepTests(unittest.TestCase):
    def test_pending_pages_fill_the_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            beta = by_id("beta")
            cache = VisionCache(beta.id, root / "cache")
            reader = PageReader(beta, cache, None, root / "pending")
            reader.read(Page("img:abc", 1, META, lambda: b"\x89PNG fake", "image/png"))
            reader.read(Page("img:def", 2, META, lambda: b"\x89PNG fake", "image/png"))
            def run(image, prompt):
                key = json.loads(image.with_suffix(".json").read_text(encoding="utf-8"))["key"]
                return {"results": [card()]} if key == "img:abc" else None

            updated = process_pending(root / "pending", run=run, cache_dir=root / "cache")
            self.assertEqual(updated, {"beta"})
            cached = VisionCache(beta.id, root / "cache")
            self.assertEqual(cached.get("img:abc")["model"], QWEN_LABEL)
            self.assertIsNone(cached.get("img:def"))
            # The unanswered page stays pending for the next run.
            self.assertEqual(len(list((root / "pending" / "beta").glob("*.json"))), 1)

    def test_runner_only_option(self):
        self.assertEqual([s.id for s in selected_stores("beta,spar")], ["spar", "beta"])
        self.assertEqual(len(selected_stores(None)), 13)
        with self.assertRaises(SystemExit):
            selected_stores("nope")


if __name__ == "__main__":
    unittest.main()
