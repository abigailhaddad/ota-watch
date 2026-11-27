"""A short written opening for the weekly issue: what this week's new OT agreements are for, checked in code.

Pattern copied from tracker-weekly's opening.py: code builds the facts, Claude writes the prose from them and
nothing else, and code checks every sentence before it is used. If any check fails the opening is left out and
the issue goes without it.

The facts come from the issue file alone (its `records`, the same ones the page lists): the new agreements, orders
and vehicles (mod 0), largest first, up to MAX_NEW, and the MAX_MODS largest modifications, each with its PIID,
vendor, offices, competition, agreement type, signed date, obligation and description; and the headlines of the
picks below, with the records they cover.

Claude returns the paragraph as a list of sentences, each citing the records it rests on, each with a quote. A
sentence is refused, and with it the whole opening, unless:
* it cites at least one record, and each cited quote is found word for word in that record's description
  (ignoring case and whitespace);
* every number, and every capitalised word (vendors, offices, programs, places), appears in the records it cites
  or the issue's dates, apart from a short list of ordinary words;
* every date ("July 16") is a signed date of a record it cites;
* every dollar figure is one of its records' obligations or their sum (digest.dollar_figures, same tolerance);
* a count of things ("seven orders", "two agreements") equals the number of records, or of vendors, it cites;
* digest.claim_problem finds nothing its records contradict ("not competed", "new", "production", "IDV",
  "largest", ...);
* no counting words (most, many, several, majority), no hype words, no advice to the reader, no prose about the
  input, no exclamation marks.

Usage (prints; writes nothing unless --out):
  uv run python -m ota_watch.opening data/issues/2026-11-23.json [--out /tmp/issue_with_opening.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import digest

MODEL = os.environ.get("OTA_MODEL", "claude-opus-5-5")
MAX_NEW = 50
MAX_MODS = 10
DESC_CHARS = 300


# ── facts ─────────────────────────────────────────────────────────────────

def clean(s) -> str:
    return " ".join(str(s or "").split())


def record_fact(rid: str, r: dict, picked: bool) -> dict:
    desc = clean(r.get("description"))
    f = {"id": rid, "piid": r.get("piid"), "mod": r.get("mod") or "0", "action_type": r.get("action_type"),
         "signed": r.get("signed"), "contracting_agency": r.get("contracting_agency"),
         "contracting_office": r.get("contracting_office"), "funding_agency": r.get("funding_agency"),
         "vendor": r.get("vendor"), "competed": r.get("competed"), "agreement_type": r.get("agreement_type"),
         "mod_reason": r.get("mod_reason"), "obligated": digest.money(r.get("obligated")),
         "description": desc if len(desc) <= DESC_CHARS else desc[:DESC_CHARS].rsplit(" ", 1)[0] + " ..."}
    if picked:
        f["picked_out_below"] = True
    return {k: v for k, v in f.items() if v not in (None, "")}


def facts(issue: dict) -> tuple[dict, dict]:
    """(facts for the model, id -> index into issue["records"]). Deterministic for a given issue."""
    recs = issue["records"]
    key = lambda r: (r.get("piid"), r.get("mod"), r.get("parent_piid"))  # noqa: E731
    picked = {key(r) for p in issue.get("picks") or [] for r in p["records"]}
    order = sorted(range(len(recs)), key=lambda i: -abs(digest._f(recs[i].get("obligated"))))
    new = [i for i in order if digest.is_new(recs[i])][:MAX_NEW]
    mods = [i for i in order if not digest.is_new(recs[i])][:MAX_MODS]
    index, new_f, mod_f = {}, [], []
    for n, i in enumerate(new + mods, 1):
        rid = f"R{n}"
        index[rid] = i
        (new_f if digest.is_new(recs[i]) else mod_f).append(record_fact(rid, recs[i], key(recs[i]) in picked))
    by_key = {key(recs[i]): rid for rid, i in index.items()}
    picks = [{"headline_on_the_page": p["headline"], "records": [by_key.get(key(r), r.get("piid")) for r in p["records"]]}
             for p in issue.get("picks") or []]
    s = issue.get("summary") or {}
    f = {"issue_date": issue["date"], "records_signed_from": s.get("signed_from"), "records_signed_to": s.get("signed_to"),
         "picked_out_below": picks, "new_agreements_orders_and_vehicles_largest_first": new_f,
         "largest_modifications": mod_f}
    return f, index


def facts_text(f: dict) -> str:
    return json.dumps(f, indent=1, ensure_ascii=False)


# ── the call ──────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string"},
                       "sources": {"type": "array", "items": {
                           "type": "object",
                           "properties": {"record": {"type": "string"}, "quote": {"type": "string"}},
                           "required": ["record", "quote"], "additionalProperties": False}}},
        "required": ["text", "sources"], "additionalProperties": False}}},
    "required": ["sentences"], "additionalProperties": False,
}

# The newsletter's voice (wording from usajobs-weekly's llm.VOICE), set per topic.
VOICE = ("Voice: dry and a little wry, like a colleague who reads every record and points out the good bits. "
         "Short declarative sentences. Let the record be the joke by describing it exactly; never announce that "
         "something is funny or odd. No puns, no exclamation marks, and no mocking the people involved. Keep it light: mostly plain, with at most one wry turn.")


PROMPT = """You write the opening paragraph of a weekly page about federal Other Transaction (OT) agreements
newly public in FPDS. Defense OT records become public about 90 days after signing. Readers track federal
spending: reporters, congressional staff, lawyers. Below the paragraph the page already has a few picked-out
records with headlines, and every record with its numbers.

Write three or four plain sentences that say what this week's new agreements are for: the technologies,
programs and kinds of work their descriptions name, with the office and vendor where that helps, drawn mostly
from records not picked out below. Good sentences sound like "Naval Surface Warfare Center Crane issued
prototype orders for MACH-XL 26 projects naming Aerojet Rocketdyne and Kratos" or "The OSD DIRECTOR office
signed prototype agreements with General Atomics and Westinghouse Government Services for advanced nuclear
reactors on military bases." At most one sentence may be about a record marked picked_out_below.

Rules:
- Use only what the records say. No outside knowledge about companies, laws, programs or people; do not explain
  an acronym the record does not spell out, and do not rename offices or agencies.
- Every number, date, dollar figure and name you write must appear in the records that sentence cites. Write
  dates as "July 16". Give a dollar figure only as a cited record's obligated amount or their sum.
- If you count things ("two prototype agreements"), the count must equal the records you cite for it.
- Do not use counting or comparing words: no "most", "many", "several", "a number of", "largest", no totals.
- Say "not competed", "new", "production", "prototype" or "vehicle" only when the cited record's fields say so.
- Don't mention the input, these instructions, or what a record lacks. No adjectives of judgment
  (significant, major, notable, key, unusual), no advice to the reader, no exclamation marks, no Markdown.

For each sentence give its text and its sources: the id of every record it draws on (e.g. "R4"), each with a
quote copied character for character from that record's description (at least three words) that the sentence
rests on.

{voice}

This week:
{facts}
"""


def call_model(ftext: str, client=None) -> tuple[list[dict], dict]:
    import anthropic

    c = client or anthropic.Anthropic(
        default_headers={"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]}
        if os.environ.get("ANTHROPIC_WORKSPACE_ID") else None)
    r = c.messages.create(model=MODEL, max_tokens=4000,
                          output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                          messages=[{"role": "user", "content": PROMPT.format(facts=ftext, voice=VOICE)}])
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"model stop_reason={r.stop_reason}")
    out = json.loads(next(b.text for b in r.content if b.type == "text"))["sentences"]
    return out, {"model": MODEL, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}


# ── checks ────────────────────────────────────────────────────────────────

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ALLOWED_CAPS = {w.lower() for w in MONTHS + WEEKDAYS} | {
    "the", "a", "an", "this", "that", "these", "it", "its", "in", "on", "at", "for", "with", "and", "also",
    "separately", "elsewhere", "meanwhile", "another", "both", "each", "among", "by", "from", "under", "as", "if",
    "while", "after", "before", "to", "of", "through", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "u", "s", "us", "ot", "ots", "ota", "otas", "other", "transaction", "transactions",
    "agreement", "agreements", "order", "orders", "vehicle", "vehicles", "idv", "fpds", "dod", "department",
    "defense", "office", "prototype", "production", "federal", "government"}
NUM_WORDS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                                         "nine", "ten", "eleven", "twelve"])}
COUNT_RE = re.compile(r"\b(\d+|" + "|".join(NUM_WORDS) + r")\s+(?:(?:new|separate|more|other|prototype|production|"
                      r"ot|follow-on|related|uncompeted|competed)\s+)*"
                      r"(agreements?|orders?|vehicles?|records?|awards?|ots|otas|modifications?|mods|firms?|"
                      r"companies|vendors?|contracts?|projects?)\b", re.I)
QUANTITY = re.compile(r"\b(most|many|several|numerous|majority|dozens?|handful|a number of|half|bulk|"
                      r"totals?|totaling|combined|percent|%)\b", re.I)
HYPE = re.compile(r"\b(funny|odd(?:ly)?|quirky|ironic(?:ally)?|curious(?:ly)?|amusing(?:ly)?|surprising(?:ly)?|believe it or not|significant(?:ly)?|major|notabl[ey]|key|landmark|sweeping|historic|dramatic(?:ally)?|"
                  r"crucial|critical|important(?:ly)?|striking(?:ly)?|unprecedented|controversial|unusual(?:ly)?|"
                  r"remarkabl[ey]|massive|huge|cutting-edge|game-?changer|signals?|poised|interesting(?:ly)?)\b", re.I)
ADVICE = re.compile(r"\b(you|your|readers?|subscribers?|should|worth (?:watching|noting|following)|keep an eye|"
                    r"stay tuned|watch for|look out)\b", re.I)
META = re.compile(r"\b(input|facts?|provided|the data|in the data|listed below|below|above|"
                  r"does not (?:say|state|specify|describe)|not (?:given|specified|stated)|unclear|unspecified|"
                  r"the records? (?:says?|describes?|shows?))\b", re.I)


def norm(s) -> str:
    s = str(s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().casefold()


def numbers(s: str) -> set[str]:
    """Numbers as written, commas dropped and leading zeros stripped ("2026-07-06" gives 2026, 7 and 6)."""
    out = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or ""):
        n = n.replace(",", "")
        out.add(n if "." in n else (n.lstrip("0") or "0"))
    return out


def sentence_problems(s: dict, f: dict, index: dict, recs: list[dict]) -> list[str]:
    text = " ".join(str(s.get("text") or "").split())
    label = f"sentence {text[:50]!r}"
    if not text:
        return [f"{label}: empty"]
    out, cited = [], []
    for src in s.get("sources") or []:
        rid = str(src.get("record") or "").strip()
        if rid not in index:
            out.append(f"{label}: cites an unknown record {rid!r}")
            continue
        q = norm(src.get("quote")).strip(' ."\'…')
        if len(q) < 12:
            out.append(f"{label}: quote for {rid} too short: {src.get('quote')!r}")
        elif q not in norm(recs[index[rid]].get("description")):
            out.append(f"{label}: quote not in {rid}'s description: {src.get('quote')!r}")
        elif index[rid] not in cited:
            cited.append(index[rid])
    if not cited:
        return out + [f"{label}: no checked source"]
    rows = [recs[i] for i in cited]
    ctx = " \n ".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
    ctx += " \n " + " ".join(str(f.get(k) or "") for k in ("issue_date", "records_signed_from", "records_signed_to"))
    if "!" in text:
        out.append(f"{label}: exclamation mark")
    for name, rx in (("counting word", QUANTITY), ("hype word", HYPE), ("advice or reader address", ADVICE),
                     ("prose about the input", META)):
        if m := rx.search(text):
            out.append(f"{label}: {name}: {m.group(0)!r}")
    if extra := numbers(digest._MONEY.sub(" ", text)) - numbers(ctx):
        out.append(f"{label}: numbers not in its cited records: {sorted(extra)}")
    allowed = [abs(digest._f(r.get("obligated"))) for r in rows] + [abs(sum(digest._f(r.get("obligated")) for r in rows))]
    for val, tol in digest.dollar_figures(text):
        if not any(abs(val - a) <= tol for a in allowed):
            out.append(f"{label}: dollar figure {val:,.0f} is not a cited record's obligation or their sum")
    signed = {r.get("signed") for r in rows if r.get("signed")}
    written = {" ".join(x.split()).replace(" ,", ",").lower() for x in re.findall(r"\b(?:" + "|".join(MONTHS) + r")\s+\d{1,2}(?:,\s*\d{4})?", ctx)}
    for m in re.finditer(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", text):
        mon, day, yr = MONTHS.index(m.group(1)) + 1, int(m.group(2)), m.group(3)
        if " ".join(m.group(0).split()).lower() in written:   # the same date written out in the cited text
            continue
        if not any(d[5:7] == f"{mon:02d}" and d[8:10] == f"{day:02d}" and (not yr or d[:4] == yr) for d in signed):
            out.append(f"{label}: date not a signed date of its cited records: {m.group(0)!r}")
    n_vendors = len({r.get("vendor") for r in rows if r.get("vendor")})
    for m in COUNT_RE.finditer(text):
        v = m.group(1).lower()
        v = NUM_WORDS[v] if v in NUM_WORDS else int(v)
        if v not in (len(rows), n_vendors):
            out.append(f"{label}: count {m.group(0)!r} is not the number of records or vendors it cites ({len(rows)}, {n_vendors})")
    words = {w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z'’]*", ctx)}
    for w in re.findall(r"[A-Za-z][A-Za-z'’]*", digest._MONEY.sub(" ", text)):   # "$31.2M" is not a name
        w2 = re.sub(r"['’]s$", "", w)
        if w2[0].isupper() and w2.casefold() not in words and w2.casefold() not in ALLOWED_CAPS:
            out.append(f"{label}: word not in its cited records: {w2!r}")
    if why := digest.claim_problem(text, cited, recs):
        out.append(f"{label}: {why}")
    return out


def problems(sentences: list[dict], f: dict, index: dict, recs: list[dict]) -> list[str]:
    if not sentences:
        return ["no sentences"]
    out = []
    if len(sentences) > 5:
        out.append(f"too long ({len(sentences)} sentences)")
    words = sum(len(str(s.get("text") or "").split()) for s in sentences)
    if words > 130:
        out.append(f"too long ({words} words)")
    for s in sentences:
        out += sentence_problems(s, f, index, recs)
    return out


def write(issue: dict, client=None) -> dict:
    """{"status": kept|dropped|error|skipped, "text", "sources", "problems", "usage"}. Never raises."""
    f, index = facts(issue)
    if not index:
        return {"status": "skipped", "text": None, "sources": [], "problems": ["no records"], "usage": {}}
    try:
        sentences, usage = call_model(facts_text(f), client)
    except Exception as exc:  # noqa: BLE001 - any failure means no opening
        return {"status": "error", "text": None, "sources": [], "problems": [f"{type(exc).__name__}: {exc}"[:300]], "usage": {}}
    recs = issue["records"]
    p = problems(sentences, f, index, recs)
    text = " ".join(" ".join(str(s.get("text") or "").split()) for s in sentences)
    sources = [{"piid": recs[index[src["record"]]].get("piid"), "mod": recs[index[src["record"]]].get("mod"),
                "quote": src.get("quote")}
               for s in sentences for src in s.get("sources") or [] if src.get("record") in index]
    return {"status": "kept" if not p else "dropped", "text": text, "sources": sources, "problems": p, "usage": usage}


def kept_text(issue: dict) -> str | None:
    o = issue.get("opening") or {}
    return o.get("text") if o.get("status") == "kept" and o.get("text") else None


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(prog="ota_watch.opening")
    ap.add_argument("issue", type=Path)
    ap.add_argument("--out", type=Path, help="write the issue with its opening here (default: print only)")
    a = ap.parse_args(argv)
    issue = json.loads(a.issue.read_text())
    o = write(issue, client)
    print(json.dumps(o, indent=1, ensure_ascii=False))
    if a.out:
        a.out.write_text(json.dumps({**issue, "opening": o}, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
