"""Keep data/documents.csv: every OT document seen in FPDS, with the date we
first saw it.

"Newly public" is decided by our own record of what FPDS showed before, not
by signing date: Defense OT documents appear about 90 days after signing,
other agencies' within days, so each run re-reads every document signed in
the last LOOKBACK_DAYS and adds the ones not seen before with first_seen =
the run date. Documents already stored keep their first_seen; their other
fields are refreshed (FPDS records get corrected).

A document signed more than LOOKBACK_DAYS before it first appears in FPDS is
missed. Some are entered long after signing, beyond the 90-day DoD delay: on
2026-10-19..21 FPDS gained documents signed as early as 2026-01-20 (an Army
$14.85M funding action entered 9 months late). 400 days covers that; a full
400-day read is about 7,500 documents, ~12 minutes.

Usage:
  uv run python -m ota_watch.collect                 # weekly
  uv run python -m ota_watch.collect --since 2025-10-01 --first-seen baseline
      # one-off backfill; first_seen 'baseline' marks documents that were
      # already public before this project started
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
from pathlib import Path

from . import fpds

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "data" / "documents.csv"
LOOKBACK_DAYS = 400
COLUMNS = ["first_seen"] + fpds.FIELDS

log = logging.getLogger(__name__)


def load(path: Path = DOCS) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["key"]: r for r in csv.DictReader(f)}


def save(docs: dict[str, dict], path: Path = DOCS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(docs.values(), key=lambda r: (r.get("signed") or "", r["key"]))
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: "" if r.get(c) is None else r.get(c) for c in COLUMNS})
    tmp.replace(path)


def merge(docs: dict[str, dict], fetched: list[dict], first_seen: str) -> dict:
    """Add unseen documents (first_seen given), refresh the rest. Returns counts."""
    new = refreshed = 0
    for r in fetched:
        old = docs.get(r["key"])
        if old is None:
            docs[r["key"]] = {"first_seen": first_seen, **r}
            new += 1
        else:
            docs[r["key"]] = {"first_seen": old["first_seen"], **r}
            refreshed += 1
    return {"fetched": len(fetched), "new": new, "refreshed": refreshed}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ota_watch.collect")
    p.add_argument("--since", help="signed on or after (default: today minus LOOKBACK_DAYS)")
    p.add_argument("--until", help="signed on or before (default: today)")
    p.add_argument("--first-seen", help="value for first_seen of new documents (default: today, UTC)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = dt.datetime.now(dt.timezone.utc).date()
    since = dt.date.fromisoformat(a.since) if a.since else today - dt.timedelta(days=LOOKBACK_DAYS)
    until = dt.date.fromisoformat(a.until) if a.until else today
    docs = load()
    fetched = fpds.fetch_range(since, until)
    out = merge(docs, fetched, a.first_seen or today.isoformat())
    save(docs)
    out.update(window=[since.isoformat(), until.isoformat()], stored=len(docs))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
