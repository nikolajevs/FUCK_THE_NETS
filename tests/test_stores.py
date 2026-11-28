import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts.run_scrapers import LANES, SECRETS, lanes_from
from scripts.stores import GEMINI_LANE, MANIFEST, ROOT, STORES, Store, by_id, manifest, \
    manifest_text, write_manifest

SCRIPTS = ROOT / "scripts"


class RegistryTests(unittest.TestCase):
    def test_ids_labels_scripts_and_feeds_are_unique(self):
        for field in ("id", "label", "script", "feed"):
            values = [getattr(store, field) for store in STORES]
            self.assertEqual(len(values), len(set(values)), field)

    def test_every_script_exists_and_writes_its_registered_feed(self):
        for store in STORES:
            source = (SCRIPTS / store.script).read_text(encoding="utf-8")
            match = re.search(r'^OUT = Path\("([^"]+)"\)', source, re.M)
            self.assertIsNotNone(match, f"{store.script} has no OUT = Path(...)")
            self.assertEqual(match.group(1), store.feed, store.script)

    def test_feed_store_field_matches_registry_id(self):
        # The site matches a feed's "store" to the registry id; Maxima's feed has none.
        for store in STORES:
            path = ROOT / store.feed
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(payload.get("store"), (None, store.id), store.feed)

    def test_campaign_label_values(self):
        for store in STORES:
            self.assertIn(store.campaign_label, ("name", "slug"), store.id)

    def test_gemini_secret_only_for_gemini_lane(self):
        for store in STORES:
            if "GEMINI_API_KEY" in store.secrets:
                self.assertEqual(store.lane, GEMINI_LANE, store.id)

    def test_by_id(self):
        self.assertEqual(by_id("lidl").feed, "data/lidl-offers.json")
        with self.assertRaises(KeyError):
            by_id("nope")


class ManifestTests(unittest.TestCase):
    def test_committed_manifest_is_up_to_date(self):
        self.assertEqual(MANIFEST.read_text(encoding="utf-8"), manifest_text(),
                         "run: python scripts/stores.py")

    def test_manifest_lists_every_store(self):
        entries = manifest()["stores"]
        self.assertEqual([entry["id"] for entry in entries], [store.id for store in STORES])
        self.assertEqual(set(entries[0]), {"id", "label", "feed", "campaignLabel"})

    def test_write_manifest_only_when_changed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data" / "stores.json"
            self.assertTrue(write_manifest(path))
            self.assertFalse(write_manifest(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), manifest())

    def test_frontend_has_no_hardcoded_feed_list(self):
        for name in ("app.js", "admin.js"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, r'"data/[a-z-]*offers\.json"', name)
            self.assertIn("data/stores.json", source, name)


class LaneTests(unittest.TestCase):
    def test_lanes_follow_registry(self):
        names = [job.name for lane in LANES for job in lane]
        self.assertEqual(sorted(names), sorted(store.label for store in STORES))

    def test_lanes_group_by_first_appearance(self):
        stores = [
            Store("a", "A", "a.py", "data/a.json", "x"),
            Store("b", "B", "b.py", "data/b.json", "y", required=True),
            Store("c", "C", "c.py", "data/c.json", "x", secrets=("K",)),
        ]
        lanes = lanes_from(stores)
        self.assertEqual([[job.name for job in lane] for lane in lanes], [["A", "C"], ["B"]])
        self.assertTrue(lanes[1][0].required)
        self.assertEqual(lanes[0][1].secrets, ("K",))

    def test_secrets_are_collected_from_registry(self):
        self.assertEqual(SECRETS, ("GEMINI_API_KEY",))


if __name__ == "__main__":
    unittest.main()
