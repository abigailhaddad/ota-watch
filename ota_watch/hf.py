"""Every OT document FPDS has ever made public, on Hugging Face.

data/documents.csv (the weekly site's table) only covers documents signed in
the last ~400 days. The Hugging Face dataset abigailhaddad/ota-watch holds
all of them.

Two commands:

  uv run python -m ota_watch.hf backfill DIR
      Read every OT document from FPDS, one calendar year at a time, into
      DIR/<year>.jsonl. A finished year is skipped on a rerun, so an
      interrupted backfill resumes. Each year also records FPDS's own count
      for that year (DIR/<year>.count.json) to reconcile against.

  uv run python -m ota_watch.hf publish [--history DIR]
      Build the full table and upload it with its dataset card. The table is
      the one already on Hugging Face (or DIR's backfill, if given) with
      data/documents.csv laid over it: a document in documents.csv replaces
      the older copy, and keeps its first_seen. Documents only in the history
      get first_seen = "baseline" (public before this project started).
      Run weekly after collect; needs HF_TOKEN.

A document signed more than ~400 days ago is not re-read weekly, so an FPDS
correction to one reaches the dataset only when the backfill is rerun.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import io
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from . import collect, fpds

REPO_ID = "abigailhaddad/ota-watch"
PARQUET = "data/ot_documents.parquet"
CSV = "data/ot_documents.csv"
MONEY = {"obligated", "total_obligated_raw", "total_all_options_raw"}
COLUMNS = collect.COLUMNS

log = logging.getLogger(__name__)


def feed_count(start: dt.date, end: dt.date, session=None) -> int:
    """FPDS's own count of OT documents signed start..end: the offset of the
    feed's last page plus the entries on it."""
    q = f'AWARD_TYPE:"OTHER TRANSACTION" SIGNED_DATE:[{start:%Y/%m/%d},{end:%Y/%m/%d}]'
    root = ET.fromstring(fpds._get({"FEEDNAME": "PUBLIC", "q": q, "start": 0}, session))
    last = [l.get("href") for l in root.findall("a:link", fpds.NS) if l.get("rel") == "last"]
    if not last:
        return len(root.findall("a:entry", fpds.NS))
    offset = int(re.search(r"start=(\d+)", last[0]).group(1))
    root = ET.fromstring(fpds._get({"FEEDNAME": "PUBLIC", "q": q, "start": offset}, session))
    return offset + len(root.findall("a:entry", fpds.NS))


def backfill(out: Path, until: dt.date | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    until = until or dt.datetime.now(dt.timezone.utc).date()
    s = requests.Session()
    first = until.year
    while feed_count(dt.date(1900, 1, 1), dt.date(first - 1, 12, 31), s):
        first -= 1
    log.info("earliest year with an OT document: %d", first)
    for year in range(first, until.year + 1):
        path = out / f"{year}.jsonl"
        if path.exists():
            continue
        start, end = dt.date(year, 1, 1), min(dt.date(year, 12, 31), until)
        expected = feed_count(start, end, s)
        rows = fpds.fetch_range(start, end, session=s)
        after = feed_count(start, end, s)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        (out / f"{year}.count.json").write_text(json.dumps(
            {"year": year, "feed_count_before": expected, "feed_count_after": after,
             "fetched": len(rows), "distinct_keys": len({r["key"] for r in rows}),
             "read_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}))
        tmp.replace(path)
        log.info("%d: fetched %d, FPDS count %d before / %d after", year, len(rows), expected, after)


def load_history(d: Path) -> list[dict]:
    rows = []
    for p in sorted(d.glob("*.jsonl")):
        with p.open(encoding="utf-8") as f:
            rows += [json.loads(line) for line in f]
    return rows


def _norm(r: dict) -> dict:
    out = {}
    for c in COLUMNS:
        v = r.get(c)
        if v is None or v == "":
            out[c] = None
        elif c in MONEY:
            out[c] = float(v)
        else:
            out[c] = str(v)
    return out


def merge(base: list[dict], docs: dict[str, dict]) -> tuple[list[dict], dict]:
    """base (history or the current dataset) with documents.csv laid over it."""
    table, dupes = {}, 0
    for r in base:
        if r["key"] in table:
            dupes += 1
        table[r["key"]] = {**_norm(r), "first_seen": r.get("first_seen") or "baseline"}
    replaced = sum(k in table for k in docs)
    for k, r in docs.items():
        table[k] = _norm(r)
    rows = sorted(table.values(), key=lambda r: (r["signed"] or "", r["key"]))
    return rows, {"base_rows": len(base), "base_duplicate_keys": dupes,
                  "documents_csv_rows": len(docs), "replaced_from_documents_csv": replaced,
                  "added_from_documents_csv": len(docs) - replaced, "total": len(rows)}


def fiscal_year(signed: str | None) -> str:
    if not signed:
        return "no signed date"
    d = dt.date.fromisoformat(signed)
    return str(d.year + 1 if d.month >= 10 else d.year)


def _money_b(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x) / 1e9:,.1f}B"


def stats(rows: list[dict]) -> dict:
    """Every figure the dataset card states, computed from the table."""
    years = collections.defaultdict(collections.Counter)
    for r in rows:
        y = years[fiscal_year(r["signed"])]
        y["documents"] += 1
        y["dod"] += r["department"] == "DEPT OF DEFENSE"
        y["dhs"] += r["department"] == "HOMELAND SECURITY, DEPARTMENT OF"
        y["consortium"] += bool(r["consortium"])
        y["production"] += r["agreement_type"] == "PRODUCTION"
    filled = lambda f: min((fy for fy, c in years.items() if c[f] and fy[0].isdigit()), default=None)  # noqa: E731
    total = sum(r["obligated"] or 0 for r in rows)
    on_idv = sum(r["obligated"] or 0 for r in rows if r["kind"] == "OtherTransactionIDV")
    neg = [r["obligated"] for r in rows if (r["obligated"] or 0) < 0]
    late = [r for r in rows if r["signed"] and r["created"]
            and (dt.date.fromisoformat(r["created"][:10]) - dt.date.fromisoformat(r["signed"])).days > 365]
    recon = [r for r in rows if re.search(r"RECONSTRUCT", r["description"] or "", re.I)]
    names = collections.defaultdict(set)
    for r in rows:
        if r["uei"]:
            names[r["uei"]].add(r["vendor"])
    mods = collections.defaultdict(set)
    for r in rows:
        if r["kind"] == "OtherTransactionAward":
            mods[(r["agency_id"], r["piid"], r["parent_piid"])].add(r["mod"])
    return {
        "years": dict(sorted(years.items())),
        "top_vendors": collections.Counter(r["vendor"] for r in rows).most_common(5),
        "first_fy_consortium": filled("consortium"), "first_fy_production": filled("production"),
        "obligated": total, "obligated_on_idv": on_idv,
        "deobligations": len(neg), "deobligated": sum(neg),
        "entered_over_a_year_late": len(late), "reconstructions": len(recon),
        "reconstructed_obligated": sum(r["obligated"] or 0 for r in recon),
        "ueis_with_several_names": sum(len(v) > 1 for v in names.values()),
        "awards_without_base": sum("0" not in m for m in mods.values()), "awards": len(mods),
    }


def card(rows: list[dict], built: str) -> str:
    st = stats(rows)
    kinds = collections.Counter(r["kind"] for r in rows)
    newest = max((r["signed"] for r in rows if r["signed"]), default="")
    oldest = min((r["signed"] for r in rows if r["signed"]), default="")
    pct = lambda n, d: f"{100 * n / d:.0f}%" if d else ""  # noqa: E731
    fy_rows = "\n".join(
        f"| {fy} | {c['documents']:,} | {pct(c['dod'], c['documents'])} | {pct(c['dhs'], c['documents'])} "
        f"| {c['consortium']:,} | {c['production']:,} |" for fy, c in st["years"].items())
    vendors = "; ".join(f"{v} ({n:,})" for v, n in st["top_vendors"])
    return f"""---
license: other
pretty_name: OTA Watch - every federal Other Transaction record in FPDS
tags: [government, procurement, other-transactions, fpds, defense]
configs:
- config_name: default
  data_files: {PARQUET}
---

# Federal Other Transaction (OT) records from FPDS

Every Other Transaction document FPDS's public ATOM feed returns for the query
`AWARD_TYPE:"OTHER TRANSACTION"`: {len(rows):,} documents, signed {oldest} to {newest}.
Built {built} by [ota-watch](https://github.com/abigailhaddad/ota-watch), which also runs the weekly
site [ota-watch.abigailhaddad.com](https://ota-watch.abigailhaddad.com). Every figure on this page is
computed from the table when it is built.

OTs are agreements made outside the Federal Acquisition Regulation, mostly by the Defense Department for
prototypes and follow-on production. They are reported to FPDS but not to USAspending.

## Files

* `{PARQUET}`: the table. ID columns (`piid`, `agency_id`, `mod`, `uei`, `psc`, ...) are text.
* `{CSV}`: the same table as CSV. Spreadsheet programs strip leading zeros from ID columns
  and may turn some into numbers; open it as text, or use the Parquet file.

One row per FPDS document. Each document is one action: a base award or a modification. `kind` is
`OtherTransactionAward` (an agreement, or an order under an OT IDV) or `OtherTransactionIDV` (the parent
vehicle): {", ".join(f"{k} {n:,}" for k, n in sorted(kinds.items(), key=lambda kv: str(kv[0])))}. `key` is
unique per document (`kind|agency_id|piid|mod|parent_piid`; order PIIDs repeat across parent IDVs).

## By fiscal year signed

| Fiscal year | Documents | DoD | DHS | `consortium` filled in | PRODUCTION |
|---|---|---|---|---|---|
{fy_rows}

## Caveats

* **Use `obligated`**, the money obligated by that one action. FPDS's total and base-and-all-options values
  on OT records disagree with each other along a record chain; they are kept only as `total_obligated_raw`
  and `total_all_options_raw`.
* **The vendor is often a consortium manager**, not the company doing the work. The most frequent vendors:
  {vendors}. The `consortium` flag is filled in from fiscal {st["first_fy_consortium"]} (see the table), so
  a blank before then means unknown, not "no".
* **OT IDVs carry money**, unlike most FAR vehicles: {_money_b(st["obligated_on_idv"])} of the
  {_money_b(st["obligated"])} obligated is on `OtherTransactionIDV` documents. Orders under an IDV carry
  money too; whether any is reported on both hasn't been checked.
* **The mix of agencies changes** (DoD and DHS columns above): early years are mostly DHS.
* **`agreement_type`** is PRODUCTION on no document before fiscal {st["first_fy_production"]}; treat
  earlier PROTOTYPE values as a default, not a fact.
* **Dates.** {st["entered_over_a_year_late"]:,} documents were entered in FPDS more than a year after they
  were signed. {st["reconstructions"]:,} descriptions say the record is a "reconstruction"
  ({_money_b(st["reconstructed_obligated"])} obligated), which can fold earlier modifications into one
  document under a later date. Defense records become public about 90 days after signing, so the latest
  months are incomplete.
* **Deobligations:** {st["deobligations"]:,} documents have negative `obligated`
  ({_money_b(st["deobligated"])} in total); sums are net.
* **Names vary:** {st["ueis_with_several_names"]:,} UEIs appear under more than one vendor name; group by
  `uei`. {st["awards_without_base"]:,} of {st["awards"]:,} agreements and orders have no base document.
* **`first_seen`** is when ota-watch first saw the document in FPDS; `baseline` means it was already public
  when the project started, so it says nothing about when FPDS published it.
* **Refreshes.** Documents signed in the last ~400 days are re-read from FPDS weekly. Older ones are as FPDS
  showed them when the history was last read in full (`python -m ota_watch.hf backfill`).
* Only what FPDS's public feed shows. An OT never reported to FPDS is not here.

Data: U.S. government data from FPDS. Code: MIT.
"""


def publish(history: Path | None) -> None:
    from huggingface_hub import HfApi, hf_hub_download
    import pyarrow as pa
    import pyarrow.parquet as pq

    api = HfApi()
    if history:
        base = load_history(history)
    else:
        base = pq.read_table(hf_hub_download(REPO_ID, PARQUET, repo_type="dataset")).to_pylist()
    rows, counts = merge(base, collect.load())
    schema = pa.schema([(c, pa.float64() if c in MONEY else pa.string()) for c in COLUMNS])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), buf, compression="zstd")
    sbuf = io.StringIO()
    w = csv.DictWriter(sbuf, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({c: "" if r[c] is None else r[c] for c in COLUMNS})
    built = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    from huggingface_hub import CommitOperationAdd
    api.create_repo(REPO_ID, repo_type="dataset", exist_ok=True)
    api.create_commit(REPO_ID, repo_type="dataset", commit_message=f"Update {built}: {len(rows):,} documents",
                      operations=[CommitOperationAdd(PARQUET, buf.getvalue()),
                                  CommitOperationAdd(CSV, sbuf.getvalue().encode("utf-8")),
                                  CommitOperationAdd("README.md", card(rows, built).encode("utf-8"))])
    print(json.dumps(counts))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ota_watch.hf")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("dir", type=Path)
    pub = sub.add_parser("publish")
    pub.add_argument("--history", type=Path)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "backfill":
        backfill(a.dir)
    else:
        publish(a.history)


if __name__ == "__main__":
    main()
