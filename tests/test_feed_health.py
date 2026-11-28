import json
import tempfile
import unittest
from pathlib import Path

from scripts.feed_health import (
    HISTORY_LIMIT, Report, assess, check_feeds, issue_plan, issue_title, metrics, snapshot_feeds,
    summary_table, update_history,
)
from scripts.run_scrapers import Job, Result, scraper_status
from scripts.stores import Store

TODAY = "2026-11-28"
STORE = Store("demo", "Demo", "scrape_demo.py", "data/demo-offers.json", "small")


def offer(i, until="2026-11-30", **extra):
    item = {"offerId": f"demo-{i}", "name": f"Produkts {i} 500 g", "price": 1.0 + i / 100,
            "regularPrice": 2.0 + i / 100, "packageSize": "500 g", "validUntil": until}
    item.update(extra)
    return item


def feed(offers, reviews=0, catalogue_until="2026-11-30"):
    return {
        "store": "demo", "updatedAt": "2026-11-28T06:00:00+02:00",
        "catalogues": [{"id": "c1", "validFrom": "2026-11-24", "validUntil": catalogue_until}],
        "offers": offers,
        "reviewOffers": [{"offerId": f"r-{i}", "name": "?"} for i in range(reviews)],
    }


def raw(payload):
    return json.dumps(payload).encode("utf-8")


class MetricsTests(unittest.TestCase):
    def test_counts_and_shares(self):
        offers = [offer(1), offer(2, regularPrice=None), offer(3, until="2026-11-20"), offer(4, image="x.jpg")]
        m = metrics(feed(offers, reviews=4), TODAY)
        self.assertEqual(m["offers"], 4)
        self.assertEqual(m["currentOffers"], 3)
        self.assertEqual(m["activeCatalogues"], 1)
        self.assertEqual(m["regularShare"], 0.75)
        self.assertEqual(m["imageShare"], 0.25)
        self.assertEqual(m["reviewShare"], 0.5)

    def test_expired_flag_and_empty_feed(self):
        payload = feed([], catalogue_until="2026-12-31")
        payload["catalogues"][0]["expired"] = True
        m = metrics(payload, TODAY)
        self.assertEqual(m["activeCatalogues"], 0)
        self.assertIsNone(m["regularShare"])
        self.assertIsNone(m["reviewShare"])


class AssessTests(unittest.TestCase):
    def judge(self, before, after, ok=True):
        report, restore = assess(STORE, raw(before) if isinstance(before, dict) else before,
                                 raw(after) if isinstance(after, dict) else after, ok, TODAY)
        return report.status, restore, report.notes

    def test_same_size_feed_is_ok(self):
        self.assertEqual(self.judge(feed([offer(i) for i in range(30)]),
                                    feed([offer(i) for i in range(28)])), ("ok", False, []))

    def test_collapse_restores_previous(self):
        status, restore, notes = self.judge(feed([offer(i) for i in range(50)]),
                                            feed([offer(i) for i in range(5)]))
        self.assertEqual((status, restore), ("restored", True))
        self.assertIn("offers fell to 5 from 50 still-valid ones", notes)

    def test_booklet_switch_is_not_a_collapse(self):
        old = feed([offer(i, until="2026-11-27") for i in range(50)], catalogue_until="2026-11-27")
        self.assertEqual(self.judge(old, feed([offer(i) for i in range(5)]))[:2], ("ok", False))

    def test_small_feeds_are_not_judged_on_ratios(self):
        self.assertEqual(self.judge(feed([offer(i) for i in range(10)]),
                                    feed([offer(1)]))[:2], ("ok", False))

    def test_empty_feed_with_current_booklet(self):
        status, restore, _ = self.judge(feed([offer(i) for i in range(5)]), feed([]))
        self.assertEqual((status, restore), ("restored", True))
        # Still empty with a current booklet: nothing to restore, but not healthy either.
        status, restore, notes = self.judge(feed([]), feed([]))
        self.assertEqual((status, restore), ("degraded", False))
        status, restore, notes = self.judge(feed([offer(1, until="2026-11-01")]), feed([]))
        self.assertEqual((status, restore), ("degraded", False))
        self.assertIn("no offers although 1 booklet(s) are current", notes)

    def test_broken_files_restore_previous(self):
        good = feed([offer(1)])
        self.assertEqual(self.judge(good, b"{not json")[:2], ("restored", True))
        wrong_store = dict(feed([offer(1)]), store="other")
        status, restore, notes = self.judge(good, wrong_store)
        self.assertEqual((status, restore), ("restored", True))
        self.assertIn("contract", notes[0])
        self.assertEqual(self.judge(None, b"{not json")[:2], ("failed", False))

    def test_failed_scraper(self):
        same = feed([offer(1)])
        status, restore, notes = self.judge(same, same, ok=False)
        self.assertEqual((status, restore), ("failed", False))
        self.assertEqual(notes, ["scraper exited with an error"])

    def test_share_drop_is_degraded(self):
        before = feed([offer(i) for i in range(40)])
        after = feed([offer(i, regularPrice=None) for i in range(40)])
        status, restore, notes = self.judge(before, after)
        self.assertEqual((status, restore), ("degraded", False))
        self.assertEqual(notes, ["regular price share fell from 100% to 0%"])

    def test_review_share_jump_is_degraded(self):
        status, _, notes = self.judge(feed([offer(i) for i in range(40)], reviews=2),
                                      feed([offer(i) for i in range(40)], reviews=60))
        self.assertEqual(status, "degraded")
        self.assertIn("review share rose from 5% to 60%", notes)


class CheckFeedsTests(unittest.TestCase):
    def test_restore_writes_previous_feed_marked_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / STORE.feed
            path.parent.mkdir(parents=True)
            previous = feed([offer(i, image=("data/demo-img/a.jpg" if i == 0 else None))
                             for i in range(40)])
            path.write_text(json.dumps(previous), encoding="utf-8")
            snapshot = snapshot_feeds([STORE], root)
            path.write_text(json.dumps(feed([offer(1)])), encoding="utf-8")

            reports = check_feeds(snapshot, {"demo": True}, [STORE], TODAY, "2026-11-28T07:00:00+02:00", root)

            self.assertEqual(reports[0].status, "restored")
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(written["offers"]), 40)
            self.assertEqual(written["health"]["status"], "stale")
            self.assertEqual(written["health"]["since"], "2026-11-28T07:00:00+02:00")
            # The pruned photo cannot come back from git here, so it is dropped.
            self.assertNotIn("image", written["offers"][0])

    def test_healthy_feed_is_left_alone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / STORE.feed
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(feed([offer(1)])), encoding="utf-8")
            snapshot = snapshot_feeds([STORE], root)
            new_text = json.dumps(feed([offer(1), offer(2)]))
            path.write_text(new_text, encoding="utf-8")
            reports = check_feeds(snapshot, {"demo": True}, [STORE], TODAY, "now", root)
            self.assertEqual(reports[0].status, "ok")
            self.assertEqual(path.read_text(encoding="utf-8"), new_text)
            self.assertIn("| Demo | ok | 2 (+1) |", summary_table(reports))


class HistoryTests(unittest.TestCase):
    def test_streak_since_and_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "health.json"
            report = Report(STORE, "degraded", ["x"], {"offers": 1}, None)
            for run in range(HISTORY_LIMIT + 3):
                health = update_history([report], f"t{run}", path, [STORE])
            entry = health["stores"]["demo"]
            self.assertEqual(entry["streak"], HISTORY_LIMIT + 3)
            self.assertEqual(entry["since"], "t0")
            self.assertEqual(len(entry["history"]), HISTORY_LIMIT)
            health = update_history([Report(STORE, "ok", [], {"offers": 2}, None)], "t-ok", path, [STORE])
            self.assertEqual((health["stores"]["demo"]["streak"], health["stores"]["demo"]["since"]), (1, "t-ok"))

    def test_unknown_stores_are_dropped(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "health.json"
            path.write_text(json.dumps({"stores": {"gone": {"status": "ok"}}}), encoding="utf-8")
            health = update_history([Report(STORE, "ok")], "t", path, [STORE])
            self.assertEqual(list(health["stores"]), ["demo"])


class IssuePlanTests(unittest.TestCase):
    def health(self, status, streak=1):
        return {"updatedAt": "t", "stores": {"demo": {
            "label": "Demo", "status": status, "streak": streak, "since": "t0",
            "notes": ["something"], "history": [{"at": "t", "status": status, "offers": 3}]}}}

    def test_restored_opens_at_once(self):
        actions = issue_plan(self.health("restored"), {})
        self.assertEqual([a["action"] for a in actions], ["open"])
        self.assertEqual(actions[0]["title"], issue_title("Demo"))
        self.assertIn("- something", actions[0]["body"])

    def test_degraded_and_failed_wait_for_a_streak(self):
        self.assertEqual(issue_plan(self.health("degraded", 1), {}), [])
        self.assertEqual([a["action"] for a in issue_plan(self.health("failed", 2), {})], ["open"])

    def test_open_issue_is_updated_then_closed(self):
        open_issues = {issue_title("Demo"): 42}
        self.assertEqual(issue_plan(self.health("restored"), open_issues)[0]["action"], "update")
        close = issue_plan(self.health("ok"), open_issues)
        self.assertEqual((close[0]["action"], close[0]["number"]), ("close", 42))
        self.assertEqual(issue_plan(self.health("ok"), {}), [])


class RunnerTests(unittest.TestCase):
    def test_scraper_status_by_store_id(self):
        results = [Result(Job("Demo", ("x",)), 3, 1.0)]
        self.assertEqual(scraper_status(results, [STORE]), {"demo": False})
        self.assertEqual(scraper_status([], [STORE]), {"demo": True})


if __name__ == "__main__":
    unittest.main()
