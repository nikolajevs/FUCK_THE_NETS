#!/usr/bin/env python3
"""Run every store scraper for the daily workflow, independent ones in parallel.

The scrapers write separate ``data/*.json`` files and spend most of their time
waiting for remote servers, so running them one after another made the data
job take ~10 minutes. They now run in lanes: lanes run at the same time, the
scrapers inside one lane run in order.

The stores, their lanes, the required flag and the secrets each scraper may
see all come from the registry in ``scripts/stores.py``:

- Scrapers that read and write the shared Gemini budget ledger
  (``data/gemini-usage.json``) share a lane, so their updates never race.
- The small scrapers share a lane to stay gentle on the runner.

A failing store only produces a warning (its previous data file stays as it
was), except a ``required`` one (Maxima), whose failure fails the job. A secret
such as ``GEMINI_API_KEY`` reaches only the scrapers that declare it.

Before scraping, the runner rewrites the ``data/stores.json`` manifest the site
and the admin panel load their feeds from and keeps every feed's bytes. After
scraping, ``feed_health.check_feeds`` compares each new feed with the one it
replaced, restores feeds that broke, appends the run to ``data/health.json`` and
adds a health table to the job summary.

Output is collected per scraper and printed as one collapsible log group as
soon as that scraper finishes, each line prefixed with its elapsed time; a
duration table is printed at the end and added to the job summary.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from scripts import feed_health
    from scripts.stores import STORES, Store, write_manifest
except ModuleNotFoundError:
    import feed_health
    from stores import STORES, Store, write_manifest

SCRIPTS = Path(__file__).resolve().parent
# Every secret some store declares is withheld from the stores that do not.
SECRETS = tuple(sorted({secret for store in STORES for secret in store.secrets}))
JOB_TIMEOUT_SECONDS = 20 * 60


@dataclass(frozen=True)
class Job:
    name: str
    command: tuple[str, ...]
    required: bool = False
    secrets: tuple[str, ...] = ()


@dataclass
class Result:
    job: Job
    returncode: int
    seconds: float
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def script(name: str, file: str, **kwargs) -> Job:
    return Job(name, (sys.executable, "-u", str(SCRIPTS / file)), **kwargs)


def lanes_from(stores: tuple[Store, ...] | list[Store]) -> list[list[Job]]:
    """Group the registry into lanes, in order of each lane's first store."""
    lanes: dict[str, list[Job]] = {}
    for store in stores:
        lanes.setdefault(store.lane, []).append(
            script(store.label, store.script, required=store.required, secrets=store.secrets)
        )
    return list(lanes.values())


LANES: list[list[Job]] = lanes_from(STORES)


def job_env(job: Job, base: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in base.items() if key not in SECRETS}
    for key in job.secrets:
        if key in base:
            env[key] = base[key]
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_job(job: Job, base_env: dict[str, str], timeout: float = JOB_TIMEOUT_SECONDS) -> Result:
    start = time.monotonic()
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            job.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=job_env(job, base_env),
        )
    except OSError as exc:
        return Result(job, 127, 0.0, [f"cannot start: {exc}"])
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    with proc:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(f"[{time.monotonic() - start:6.1f}s] {line.rstrip()}")
            returncode = proc.wait()
        finally:
            timer.cancel()
    seconds = time.monotonic() - start
    if seconds >= timeout:
        lines.append(f"killed after {timeout:.0f}s timeout")
    return Result(job, returncode, seconds, lines)


def print_result(result: Result, out=sys.stdout) -> None:
    status = "ok" if result.ok else f"exit {result.returncode}"
    out.write(f"::group::{result.job.name}: {status} in {result.seconds:.0f}s\n")
    for line in result.lines:
        out.write(line + "\n")
    out.write("::endgroup::\n")
    if not result.ok:
        level = "error" if result.job.required else "warning"
        out.write(f"::{level}::{result.job.name} scraper exited with code {result.returncode} "
                  f"after {result.seconds:.0f}s\n")
    out.flush()


def run_lanes(lanes: list[list[Job]], base_env: dict[str, str] | None = None,
              out=sys.stdout, timeout: float = JOB_TIMEOUT_SECONDS) -> list[Result]:
    base_env = dict(os.environ if base_env is None else base_env)
    results: list[Result] = []
    lock = threading.Lock()

    def run_lane(lane: list[Job]) -> None:
        for job in lane:
            result = run_job(job, base_env, timeout)
            with lock:
                results.append(result)
                print_result(result, out)

    threads = [threading.Thread(target=run_lane, args=(lane,)) for lane in lanes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    order = [job.name for lane in lanes for job in lane]
    return sorted(results, key=lambda result: order.index(result.job.name))


def summary_table(results: list[Result], wall_seconds: float) -> str:
    rows = ["| Scraper | Result | Time |", "|---|---|---|"]
    for result in results:
        status = "ok" if result.ok else f"failed ({result.returncode})"
        rows.append(f"| {result.job.name} | {status} | {result.seconds:.0f}s |")
    rows.append(f"| **All (parallel)** | | **{wall_seconds:.0f}s** |")
    return "\n".join(rows) + "\n"


def scraper_status(results: list[Result], stores=STORES) -> dict[str, bool]:
    """Whether each store's scraper exited cleanly, keyed by store id."""
    ok_by_label = {result.job.name: result.ok for result in results}
    return {store.id: ok_by_label.get(store.label, True) for store in stores}


def selected_stores(only: str | None, stores=STORES) -> list[Store]:
    """All stores, or the comma-separated ids in ``only`` (unknown ids are an error)."""
    if not only:
        return list(stores)
    wanted = [item.strip() for item in only.split(",") if item.strip()]
    known = {store.id for store in stores}
    unknown = [item for item in wanted if item not in known]
    if unknown:
        raise SystemExit(f"unknown store id(s): {', '.join(unknown)}")
    return [store for store in stores if store.id in wanted]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="comma-separated store ids to run (default: all)")
    args = parser.parse_args(argv)
    stores = selected_stores(args.only)

    os.chdir(SCRIPTS.parent)
    if write_manifest():
        print("data/stores.json updated from scripts/stores.py")
    snapshot = feed_health.snapshot_feeds(stores)
    start = time.monotonic()
    results = run_lanes(lanes_from(stores))
    table = summary_table(results, time.monotonic() - start)
    print(table)

    # Compare every new feed with the one it replaced; restore broken ones.
    reports = feed_health.check_feeds(snapshot, scraper_status(results, stores), stores)
    feed_health.update_history(reports, datetime.now().astimezone().isoformat(timespec="seconds"))
    health_table = feed_health.summary_table(reports)
    print(health_table)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write("### Scrapers\n\n" + table + "\n### Feed health\n\n" + health_table)
    return 1 if any(result.job.required and not result.ok for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
