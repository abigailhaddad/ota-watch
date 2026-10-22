"""Build one weekly issue: the OT documents newly public in FPDS, with a few
picked out by Claude.

Which documents: those whose first_seen (data/documents.csv, a UTC
timestamp) is after the previous issue's cutoff (its build time) and on or
before this one's, so a collection run between two issues is never lost. Documents marked
first_seen = 'baseline' (already public when the project started) are never
in a weekly issue. A launch issue can instead take a signed-date window
(--signed-from/--signed-to).

What Claude does: reads one line per document and picks the few most
remarkable (an equity investment, an uncompeted award, a consortium where the
real performer is named only in the description, an odd purpose, a large
production follow-on). It writes a headline and a short explanation per pick
and quotes the record's own description.

What code does, and checks:
* every number in the issue's summary and every pick's fact line is computed
  from the records, not written by the model;
* a pick is dropped (and listed under `dropped` in the issue file) if its
  quote is not in the quoted record's description (compared ignoring case
  and whitespace), or if a dollar figure in its headline or explanation is
  not one of its records' obligations or their sum (within rounding);
* dollars are obligations on each action, never FPDS's "total" fields.

One issue per calendar week (Monday-Sunday): refuses to build one if the
newest issue is in the same week, so a rerun or a late cron does nothing.

Usage:
  uv run python -m ota_watch.digest                      # weekly (date = today, UTC)
  uv run python -m ota_watch.digest --signed-from 2026-07-15 --signed-to 2026-07-21
  uv run python -m ota_watch.digest --dry-run            # build, print, write nothing
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

from . import collect

ROOT = Path(__file__).resolve().parent.parent
ISSUES = ROOT / "data" / "issues"
MODEL = os.environ.get("OTA_MODEL", "claude-opus-5-5")
MAX_PICKS = 5

PROMPT = """You help write a weekly page about federal Other Transaction (OT) awards newly public in FPDS
(the federal procurement database). OTs are agreements outside the normal contracting rules. Defense OT records
become public about 90 days after signing, so most records here were signed months ago.

Below are all {n} FPDS records that became public this week, one per line, each starting with its [index].
Each record is one action: a new agreement, order or vehicle (mod 0) or a modification. "obligated" is the money
obligated by that one action, not a total value.

Pick the {lo} to {hi} most remarkable records for a reader who tracks unusual federal spending: for example equity
investments, uncompeted awards, large production follow-ons, consortium awards where the real performer is named
only in the description, odd purposes, unusual funding arrangements. A pick may group several records that are
clearly one story (same office, same program, same week); list all their indices. Prefer new awards and money
moving over zero-dollar administrative modifications. If nothing stands out, return fewer picks.

Rules:
- Use ONLY what the records say. No outside knowledge about companies, laws, programs or people; do not explain
  what a statute or program is unless the record says it.
- Name agencies, offices and programs exactly as the record writes them; do not expand abbreviations or rename
  them (if the record says "DEPT OF THE AIR FORCE / FA8819 SDA AND COMBAT POWER", do not call it a Space Force
  office; if it says "DIRECTOR SCO", do not spell SCO out).
- Don't characterize a record beyond its fields (no "everyday", "routine", "first-ever") unless the record says so.
- "quote" must be copied exactly from the description of record quote_index (which must be one of the pick's
  indices), 10 to 200 characters.
- Do not state dollar amounts except a record's obligated amount or the sum of the pick's records' amounts.
- headline: plain, specific, no hype, at most 120 characters. why: 1 to 3 sentences.

Records:
{records}
"""

SCHEMA = {
    "type": "object",
    "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "indices": {"type": "array", "items": {"type": "integer"}},
            "headline": {"type": "string"},
            "why": {"type": "string"},
            "quote_index": {"type": "integer"},
            "quote": {"type": "string"},
        },
        "required": ["indices", "headline", "why", "quote_index", "quote"],
        "additionalProperties": False}}},
    "required": ["picks"],
    "additionalProperties": False,
}


def money(x) -> str:
    x = float(x or 0)
    sign = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e9:
        return f"{sign}${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"{sign}${x / 1e6:.1f}M"
    return f"{sign}${x:,.0f}"


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def is_new(r: dict) -> bool:
    return (r.get("mod") or "0") == "0"


def record_line(i: int, r: dict) -> str:
    desc = re.sub(r"\s+", " ", r.get("description") or "").strip()
    return (f"[{i}] {r['piid']} mod {r.get('mod') or '0'} | {r.get('action_type')} | signed {r.get('signed')}"
            f" | {r.get('contracting_agency')} / {r.get('contracting_office')} | funded by {r.get('funding_agency')}"
            f" | vendor {r.get('vendor')} | {r.get('competed')} | {r.get('agreement_type')}"
            f" | mod reason: {r.get('mod_reason') or '-'} | obligated {money(r.get('obligated'))}"
            f" | description: {desc}")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


_MONEY = re.compile(r"\$\s?(-?[\d,]+(?:\.\d+)?)\s*(billion|million|thousand|[BMK])?\b", re.I)
_MULT = {"b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}


def dollar_figures(text: str) -> list[tuple[float, float]]:
    """[(value, tolerance)] for every $ figure: tolerance is half a unit of the
    last digit shown, or 1% of the value, whichever is larger."""
    out = []
    for num, unit in _MONEY.findall(text or ""):
        mult = _MULT.get((unit or "").lower(), 1.0)
        val = abs(float(num.replace(",", ""))) * mult
        decimals = len(num.split(".")[1]) if "." in num else 0
        half_unit = 0.5 * mult / (10 ** decimals)
        out.append((val, max(half_unit, 0.01 * val)))
    return out


def check_pick(p: dict, rows: list[dict]) -> str | None:
    """Why the pick must be dropped, or None."""
    idx = p.get("indices") or []
    if not idx or any(not (0 <= i < len(rows)) for i in idx) or len(set(idx)) != len(idx):
        return f"bad indices {idx}"
    if p.get("quote_index") not in idx:
        return "quote_index is not one of the pick's records"
    q = norm(p.get("quote"))
    if len(q) < 10:
        return "quote too short"
    if q not in norm(rows[p["quote_index"]].get("description")):
        return "quote not found in the record's description"
    amounts = [abs(_f(rows[i].get("obligated"))) for i in idx]
    allowed = amounts + [abs(sum(_f(rows[i].get("obligated")) for i in idx))]
    for val, tol in dollar_figures(f"{p.get('headline')} {p.get('why')}"):
        if not any(abs(val - a) <= tol for a in allowed):
            return f"dollar figure {val:,.0f} is not a record's obligation or their sum"
    return None


def summarize(rows: list[dict]) -> dict:
    new = [r for r in rows if is_new(r)]
    signed = sorted(r["signed"] for r in rows if r.get("signed"))
    by_dept: dict[str, int] = {}
    for r in rows:
        k = r.get("department") or "(none)"
        by_dept[k] = by_dept.get(k, 0) + 1
    return {
        "records": len(rows), "new_awards": len(new), "modifications": len(rows) - len(new),
        "new_not_competed": sum(1 for r in new if (r.get("competed") or "").upper() == "NOT COMPETED"),
        "obligated_new": round(sum(_f(r.get("obligated")) for r in new), 2),
        "obligated_all": round(sum(_f(r.get("obligated")) for r in rows), 2),
        "signed_from": signed[0] if signed else None, "signed_to": signed[-1] if signed else None,
        "by_department": dict(sorted(by_dept.items(), key=lambda kv: -kv[1])),
    }


def call_model(rows: list[dict], client=None) -> tuple[list[dict], dict]:
    import anthropic

    c = client or anthropic.Anthropic(
        default_headers={"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]}
        if os.environ.get("ANTHROPIC_WORKSPACE_ID") else None)
    prompt = PROMPT.format(n=len(rows), lo=min(3, len(rows)), hi=MAX_PICKS,
                           records="\n".join(record_line(i, r) for i, r in enumerate(rows)))
    r = c.messages.create(model=MODEL, max_tokens=8000,
                          output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                          messages=[{"role": "user", "content": prompt}])
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"model stop_reason={r.stop_reason}")
    picks = json.loads(next(b.text for b in r.content if b.type == "text"))["picks"]
    return picks, {"model": MODEL, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}


def fact(r: dict) -> dict:
    """The fields shown for a record (computed, never model-written)."""
    keep = ["piid", "mod", "parent_piid", "action_type", "signed", "first_seen", "department", "contracting_agency",
            "contracting_office", "funding_agency", "vendor", "uei", "competed", "agreement_type", "mod_reason",
            "description", "fpds_link"]
    out = {k: r.get(k) or None for k in keep}
    out["obligated"] = round(_f(r.get("obligated")), 2)
    return out


def build_issue(rows: list[dict], picks: list[dict], usage: dict, *, date: str, window: dict) -> dict:
    kept, dropped = [], []
    for p in picks:
        why_not = check_pick(p, rows)
        if why_not:
            dropped.append({**p, "dropped_because": why_not})
            continue
        recs = [fact(rows[i]) for i in p["indices"]]
        kept.append({"headline": p["headline"].strip(), "why": p["why"].strip(), "quote": p["quote"].strip(),
                     "quote_piid": rows[p["quote_index"]]["piid"], "records": recs,
                     "obligated_total": round(sum(r["obligated"] for r in recs), 2)})
        if len(kept) == MAX_PICKS:
            break
    order = sorted(range(len(rows)), key=lambda i: (not is_new(rows[i]), -abs(_f(rows[i].get("obligated")))))
    return {"date": date, "window": window, "summary": summarize(rows), "picks": kept, "dropped": dropped,
            "records": [fact(rows[i]) for i in order], "usage": usage,
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def same_week(a: str, b: str) -> bool:
    return dt.date.fromisoformat(a).isocalendar()[:2] == dt.date.fromisoformat(b).isocalendar()[:2]


def issue_files() -> list[Path]:
    return sorted(ISSUES.glob("*.json"))


def select(docs: dict[str, dict], *, after: str | None, until: str,
           signed_from: str | None = None, signed_to: str | None = None) -> list[dict]:
    rows = list(docs.values())
    if signed_from or signed_to:
        rows = [r for r in rows if (signed_from or "") <= (r.get("signed") or "") <= (signed_to or "9999")]
    else:
        rows = [r for r in rows if r["first_seen"] != "baseline"
                and (after is None or r["first_seen"] > after) and r["first_seen"] <= until]
    return sorted(rows, key=lambda r: (r.get("signed") or "", r["key"]))


def main(argv=None, client=None) -> int:
    p = argparse.ArgumentParser(prog="ota_watch.digest")
    p.add_argument("--date", help="issue date (default: today, UTC)")
    p.add_argument("--signed-from")
    p.add_argument("--signed-to")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    date = a.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    files = issue_files()
    last = json.loads(files[-1].read_text()) if files else None
    if last and not a.dry_run and same_week(last["date"], date):
        print(f"newest issue is {last['date']}, the same week as {date}; one issue a week, nothing built")
        return 0
    if not (a.signed_from or a.signed_to) and last is None:
        raise SystemExit("no previous issue: give --signed-from/--signed-to for the first one")
    after = None if (a.signed_from or a.signed_to) else last["window"]["first_seen_to"]
    cutoff = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rows = select(collect.load(), after=after, until=cutoff, signed_from=a.signed_from, signed_to=a.signed_to)
    window = ({"kind": "signed", "signed_from": a.signed_from, "signed_to": a.signed_to, "first_seen_to": cutoff}
              if (a.signed_from or a.signed_to) else
              {"kind": "first_seen", "first_seen_after": after, "first_seen_to": cutoff})
    picks, usage = call_model(rows, client) if rows else ([], {})
    issue = build_issue(rows, picks, usage, date=date, window=window)
    print(json.dumps({k: issue[k] for k in ("date", "window", "summary")} | {
        "picks": [p["headline"] for p in issue["picks"]],
        "dropped": [(d["headline"], d["dropped_because"]) for d in issue["dropped"]], "usage": usage},
        indent=1), file=sys.stderr)
    if not a.dry_run:
        ISSUES.mkdir(parents=True, exist_ok=True)
        path = ISSUES / f"{date}.json"
        if path.exists():
            raise SystemExit(f"{path} exists")
        path.write_text(json.dumps(issue, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
