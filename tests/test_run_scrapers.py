import io
import sys
import time
import unittest

from scripts.run_scrapers import LANES, Job, job_env, run_lanes, summary_table


def py(name, code, **kwargs):
    return Job(name, (sys.executable, "-c", code), **kwargs)


class RunLanesTests(unittest.TestCase):
    def test_lanes_run_in_parallel_and_jobs_in_order(self):
        out = io.StringIO()
        start = time.monotonic()
        results = run_lanes([
            [py("a1", "import time; time.sleep(1); print('a1')"),
             py("a2", "print('a2')")],
            [py("b", "import time; time.sleep(1); print('b')")],
        ], base_env={}, out=out)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.9, "two 1 s lanes should overlap")
        self.assertEqual([r.job.name for r in results], ["a1", "a2", "b"])
        log = out.getvalue()
        self.assertLess(log.index("::group::a1"), log.index("::group::a2"))
        self.assertIn("] a2", log)

    def test_failure_is_reported_but_other_jobs_continue(self):
        out = io.StringIO()
        results = run_lanes([[py("bad", "raise SystemExit(3)"), py("good", "print('fine')")]],
                            base_env={}, out=out)
        self.assertEqual([(r.job.name, r.returncode) for r in results], [("bad", 3), ("good", 0)])
        self.assertIn("::warning::bad scraper exited with code 3", out.getvalue())

    def test_required_failure_is_an_error(self):
        out = io.StringIO()
        run_lanes([[py("core", "raise SystemExit(1)", required=True)]], base_env={}, out=out)
        self.assertIn("::error::core scraper exited with code 1", out.getvalue())

    def test_hung_job_is_killed(self):
        out = io.StringIO()
        results = run_lanes([[py("slow", "import time; time.sleep(30)")]],
                            base_env={}, out=out, timeout=1)
        self.assertNotEqual(results[0].returncode, 0)
        self.assertIn("killed after 1s timeout", out.getvalue())


class EnvTests(unittest.TestCase):
    def test_gemini_key_only_reaches_jobs_that_need_it(self):
        base = {"GEMINI_API_KEY": "secret", "PATH": "/bin"}
        self.assertNotIn("GEMINI_API_KEY", job_env(Job("x", ("true",)), base))
        with_key = job_env(Job("y", ("true",), secrets=("GEMINI_API_KEY",)), base)
        self.assertEqual(with_key["GEMINI_API_KEY"], "secret")
        self.assertEqual(with_key["PATH"], "/bin")


class LaneConfigTests(unittest.TestCase):
    def test_every_scraper_runs_once(self):
        names = [job.command[-1].replace("\\", "/").rsplit("/", 1)[-1] for lane in LANES for job in lane]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 13)

    def test_gemini_ledger_writers_share_one_lane(self):
        writers = {"scrape_spar.py", "scrape_beta.py", "scrape_rimi_booklets.py"}
        lanes_with_writers = [
            lane for lane in LANES
            if writers & {job.command[-1].replace("\\", "/").rsplit("/", 1)[-1] for job in lane}
        ]
        self.assertEqual(len(lanes_with_writers), 1)

    def test_only_maxima_is_required(self):
        self.assertEqual([job.name for lane in LANES for job in lane if job.required], ["Maxima"])

    def test_summary_table_lists_every_result(self):
        from scripts.run_scrapers import Result
        table = summary_table([Result(Job("A", ("x",)), 0, 1.2), Result(Job("B", ("x",)), 2, 3.0)], 3.4)
        self.assertIn("| A | ok | 1s |", table)
        self.assertIn("| B | failed (2) | 3s |", table)


if __name__ == "__main__":
    unittest.main()
