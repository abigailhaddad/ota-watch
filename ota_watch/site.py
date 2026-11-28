"""Build the static site (site/dist) from data/issues/*.json.

    index.html          every weekly issue, newest first
    issues/<date>.html  one page per issue
    about.html          what this is, the caveats
    feed.xml            RSS 2.0, full content of the last FEED_ITEMS issues
    style.css, 404.html

No JavaScript, no cookies, no external assets. Every value from FPDS is
HTML-escaped; links are kept only if they are plain http(s) URLs.

Usage: uv run python -m ota_watch.site --out site/dist
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import shutil
import sys
import urllib.parse
from email.utils import format_datetime
from pathlib import Path

from .config import DATASET_URL, HF_REPO_ID, ISSUE_URL, PROJECT_URL, SITE_URL
from .digest import ISSUES, money

FPDS_URL = "https://www.fpds.gov/"
from .opening import kept_text

TITLE = "OTA Watch"
TAGLINE = ("Federal Other Transaction (OT) awards newly public in FPDS, all listed each week, "
           "with the most remarkable few picked out and summarized by Claude")
FEED_ITEMS = 20


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def safe_url(u) -> str | None:
    u = str(u or "").strip()
    return u if re.match(r"^https?://[^\s<>\"']+$", u) else None


def link(u, text) -> str:
    u = safe_url(u)
    return f'<a href="{e(u)}">{e(text)}</a>' if u else e(text)


def date_label(d: str) -> str:
    x = dt.date.fromisoformat(d)
    return f"{x:%B} {x.day}, {x.year}"


def page(title: str, body: str, *, root: str = "", home: bool = False) -> str:
    past = "" if home else f'<a href="{root}index.html">Past weeks</a> '
    past_footer = "" if home else f'<a href="{root}index.html">Past weeks</a> · '
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(TAGLINE)}">
<meta name="referrer" content="no-referrer">
<link rel="stylesheet" href="{root}style.css">
<link rel="alternate" type="application/rss+xml" title="{e(TITLE)}" href="{root}feed.xml">
</head>
<body>
<header class="site"><a class="brand" href="{root}index.html">{e(TITLE)}</a>
<nav>{past}<a href="{root}about.html">About</a> <a href="{root}feed.xml">RSS</a></nav>
</header>
<main>
{body}
</main>
<footer class="site"><p>Picks chosen and written up by Claude; every figure and fact line comes from the FPDS
records, and quotes are checked against them. Check the source before relying on anything.
<a href="{root}about.html">About</a> · {past_footer}<a href="{root}feed.xml">RSS</a> ·
<a href="{DATASET_URL}">All OT records (Hugging Face)</a> ·
<a href="{ISSUE_URL}">Report a problem (GitHub issue)</a></p></footer>
</body>
</html>
"""


FACT_FIELDS = ["vendor", "contracting_agency", "contracting_office", "competed", "agreement_type", "action_type"]


def facts_line(r: dict) -> str:
    bits = [r.get("vendor") or "(no vendor)"] + [r.get(f) for f in FACT_FIELDS[1:]]
    return " · ".join(e(b) for b in bits if b)


def shared_facts(recs: list[dict]) -> str:
    """A grouped pick's fact line: only the values all its records share, and
    the number of vendors when they differ."""
    if len(recs) == 1:
        return facts_line(recs[0])
    vendors = {r.get("vendor") for r in recs}
    bits = [next(iter(vendors)) or "(no vendor)" if len(vendors) == 1 else f"{len(vendors)} vendors"]
    bits += [recs[0].get(f) for f in FACT_FIELDS[1:] if len({r.get(f) for r in recs}) == 1]
    return " · ".join(e(b) for b in bits if b)


def source_url(r: dict) -> str:
    """FPDS's public feed for this award number (XML: the base award and its
    modifications). The search links FPDS's own feed carries now redirect to
    SAM.gov without showing the record (checked 2026-10-22), so they are not used."""
    q = urllib.parse.quote(f'PIID:"{r.get("piid") or ""}"')
    return f"https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q={q}"


def record_ref(r: dict) -> str:
    mod = r.get("mod") or "0"
    what = "new" if mod == "0" else f"mod {e(mod)}" + (f" ({e(r['mod_reason'].lower())})" if r.get("mod_reason") else "")
    return (f"{e(r.get('piid'))} {what}, signed {e(r.get('signed'))}, "
            f"{e(money(r.get('obligated')))} obligated on this action ({link(source_url(r), 'FPDS record, XML')})")


GROUP_LIST_MAX = 3  # a pick with more records than this lists them on the every-record page, not the issue page


def pick_html(n: int, p: dict, date: str | None = None) -> str:
    recs = p["records"]
    out = [f'<article class="pick"><h2><span class="rank">{n}.</span> {e(p["headline"])}</h2>',
           f'<p class="facts">{shared_facts(recs)}</p>',
           f'<p>{e(p["why"])}</p>',
           f'<blockquote><p>“{e(p["quote"])}”</p><footer>— FPDS description, {e(p["quote_piid"])}</footer></blockquote>']
    if len(recs) == 1:
        out.append(f'<p class="meta">{record_ref(recs[0])}</p>')
    elif len(recs) <= GROUP_LIST_MAX:
        items = "".join(f"<li>{e(r.get('vendor'))}: {record_ref(r)}</li>" for r in recs)
        out.append(f'<p class="meta">{len(recs)} records, {e(money(p["obligated_total"]))} obligated in all:</p>'
                   f'<ul class="meta">{items}</ul>')
    else:   # a large group: its records, each with its FPDS link, are on the week's every-record page
        piids = ", ".join(e(r.get("piid")) for r in recs)
        out.append(f'<p class="meta">{len(recs)} records, {e(money(p["obligated_total"]))} obligated in all: {piids}. '
                   f'Each is on <a href="{e(date)}-all.html">the every-record page</a>.</p>' if date else
                   f'<p class="meta">{len(recs)} records, {e(money(p["obligated_total"]))} obligated in all: {piids}.</p>')
    out.append("</article>")
    return "\n".join(out)


def row_html(r: dict, desc_chars: int = 220) -> str:
    desc = re.sub(r"\s+", " ", r.get("description") or "").strip()
    if len(desc) > desc_chars:
        desc = desc[:desc_chars - 3] + "..."
    return (f"<li>{facts_line(r)}<br>{record_ref(r)}"
            + (f'<br><span class="gist">{e(desc)}</span>' if desc else "") + "</li>")


def window_label(issue: dict) -> str:
    w, s = issue["window"], issue["summary"]
    signed = (f"signed {date_label(s['signed_from'])} to {date_label(s['signed_to'])}"
              if s.get("signed_from") else "")
    if w["kind"] == "signed":
        return f"Launch issue: every OT record {signed}. Defense records become public months after signing."
    after = date_label(w["first_seen_after"][:10]) if w.get("first_seen_after") else "the start"
    return (f"Every OT record that appeared in FPDS after {after}, up to {date_label(w['first_seen_to'][:10])} "
            f"({signed}; Defense records become public months after signing).")


def issue_content(issue: dict) -> str:
    s = issue["summary"]
    lead = kept_text(issue)   # the checked opening (opening.py), if it passed
    parts = ([f'<p class="lede opening">{e(lead)}</p>'] if lead else []) + [f'<p class="lede">{e(window_label(issue))}</p>']
    if not issue["picks"]:
        parts.append("<p>Nothing picked out this week.</p>")
    parts += [pick_html(i, p, issue["date"]) for i, p in enumerate(issue["picks"], 1)]
    # the week's counts come after the picks, with the full lists they describe
    parts.append(f'<p>{s["records"]:,} records: {s["new_awards"]:,} new agreements, orders or vehicles '
                 f'({s["new_not_competed"]:,} not competed; {e(money(s["obligated_new"]))} obligated on them) and '
                 f'{s["modifications"]:,} modifications. {e(money(s["obligated_all"]))} obligated across all records.</p>')
    top, rest_new, mods = week_lists(issue)
    if top:
        parts.append(f'<section class="all"><h2>The {len(top)} largest other new agreements, orders and vehicles</h2>'
                     '<p class="note">By money obligated, leaving out records in the picks above.</p>'
                     "<ul>" + "".join(row_html(r, SHORT_DESC) for r in top) + "</ul></section>")
    parts.append(f'<p class="note">Not listed here: {rest_new:,} more new agreements, orders and vehicles and '
                 f'{len(mods):,} modifications. <a href="{e(issue["date"])}-all.html">Every record this week</a>, '
                 "largest first. Dollar amounts are the money obligated by each action; FPDS's \"total value\" fields "
                 "on OT records are unreliable and are not used.</p>")
    parts.append(how_line(bool(lead or issue["picks"])))
    return "\n".join(parts)


def how_line(claude_wrote: bool) -> str:
    """The last line of every issue (page and feed): a link to the about page (2026-11-28). "Written with Claude."
    only when Claude wrote something in the issue (picks or the opening)."""
    link = f'<a href="{e(SITE_URL)}/about">How this is made</a> · <a href="{e(PROJECT_URL)}">Code</a>'
    return f'<p class="note how">{"Written with Claude. " if claude_wrote else ""}{link}</p>'


WEEK_TOP = 5       # the issue page lists this many new records (largest obligation first, picks left out)
SHORT_DESC = 120   # ... with their description cut at this length; the all-records page has 220


def _is_new(r: dict) -> bool:
    return (r.get("mod") or "0") == "0"


def week_lists(issue: dict) -> tuple[list[dict], int, list[dict]]:
    """(the new records the issue page lists, how many other new records it leaves out, the modifications).
    Records in a pick are not listed again. issue["records"] is already sorted new first, largest first."""
    key = lambda r: (r.get("piid"), r.get("mod"), r.get("parent_piid"))  # noqa: E731
    picked = {key(r) for p in issue.get("picks") or [] for r in p["records"]}
    new = [r for r in issue["records"] if _is_new(r)]
    others = sorted((r for r in new if key(r) not in picked), key=lambda r: -abs(r.get("obligated") or 0))
    mods = [r for r in issue["records"] if not _is_new(r)]
    return others[:WEEK_TOP], len(others) - len(others[:WEEK_TOP]), mods


def all_records_content(issue: dict) -> str:
    new = [r for r in issue["records"] if _is_new(r)]
    mods = [r for r in issue["records"] if not _is_new(r)]
    parts = [f'<p class="lede">{e(window_label(issue))}</p>',
             f'<section class="all"><h2>All new agreements, orders and vehicles ({len(new)})</h2>'
             + ("<ul>" + "".join(row_html(r) for r in new) + "</ul>" if new else "<p>None.</p>") + "</section>"]
    if mods:
        parts.append(f'<section class="all"><h2>All modifications ({len(mods)})</h2>'
                     + "<ul>" + "".join(row_html(r) for r in mods) + "</ul></section>")
    parts.append('<p class="note">Dollar amounts are the money obligated by each action. FPDS\'s "total value" '
                 "fields on OT records are unreliable and are not used. Records are sorted by size within each list.</p>")
    return "\n".join(parts)


def all_records_page(issue: dict) -> str:
    d = issue["date"]
    body = (f'<p class="dateline"><a href="{e(d)}.html">← {e(TITLE)}: week of {e(date_label(d))}</a></p>'
            f'<h1>Every record, week of {e(date_label(d))}</h1>\n' + all_records_content(issue))
    return page(f"{TITLE}: every record, {date_label(d)}", body, root="../")


def issue_page(issue: dict, prev: str | None, nxt: str | None) -> str:
    nav = ([f'<a href="{e(prev)}.html">← Older</a>'] if prev else []) + \
          ([f'<a href="{e(nxt)}.html">Newer →</a>'] if nxt else [])
    body = (f'<p class="dateline">{e(date_label(issue["date"]))}</p><h1>{e(TITLE)}: week of {e(date_label(issue["date"]))}</h1>\n'
            + issue_content(issue) + (f'\n<p class="pager">{" · ".join(nav)}</p>' if nav else ""))
    return page(f"{TITLE}: {date_label(issue['date'])}", body, root="../")


def index_page(issues: list[dict]) -> str:
    rows = []
    for i in reversed(issues):
        top = i["picks"][0]["headline"] if i["picks"] else "nothing picked out"
        rows.append(f'<li><span class="date">{e(date_label(i["date"]))}</span> '
                    f'<a href="issues/{e(i["date"])}.html">{e(top)}</a> '
                    f'<span class="count">{i["summary"]["records"]:,} records, {len(i["picks"])} picks</span></li>')
    lst = '<ul class="weeks">' + "".join(rows) + "</ul>" if rows else "<p>No issues yet.</p>"
    return page(TITLE, f'<h1>{e(TITLE)}</h1>\n<p class="lede">{e(TAGLINE)}. '
                       f'<a href="about.html">About</a> · <a href="feed.xml">RSS feed</a></p>\n{lst}', home=True)


def about_page() -> str:
    # 2026-11-28: one sentence on what is covered and from where, then one link per pipeline step. Models, counts
    # and thresholds live in the linked files, not here (tests/test_about.py checks).
    src = f"{PROJECT_URL}/blob/main"
    body = f"""<h1>About {e(TITLE)}</h1>
<p>Each week's federal Other Transaction (OT) records newly public in <a href="{FPDS_URL}">FPDS</a>, the federal
procurement database, with a few picked out and written up by Claude.</p>
<ul>
<li>Data: <a href="{FPDS_URL}">FPDS</a>; every record: <a href="{e(DATASET_URL)}">huggingface.co/datasets/{e(HF_REPO_ID)}</a></li>
<li>Selection: <a href="{e(src)}/ota_watch/digest.py">digest.py</a></li>
<li>Claude: <a href="{e(src)}/config/ota-watch.yaml">ota-watch.yaml</a></li>
<li>Checks: <a href="{e(src)}/ota_watch/digest.py">digest.py</a> (picks), <a href="{e(src)}/ota_watch/opening.py">opening.py</a> (opening)</li>
</ul>"""
    return page(f"About · {TITLE}", body)


def feed_xml(issues: list[dict]) -> str:
    x = lambda s: html.escape("" if s is None else str(s), quote=False)  # noqa: E731
    newest = list(reversed(issues))[:FEED_ITEMS]
    built = max((dt.datetime.fromisoformat(i["built_at"]) for i in newest), default=dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc))
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
           "<channel>", f"<title>{x(TITLE)}</title>", f"<link>{SITE_URL}/</link>",
           f"<description>{x(TAGLINE)}. Check the source before relying on anything.</description>",
           "<language>en-us</language>", f'<atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>',
           f"<lastBuildDate>{format_datetime(built.astimezone(dt.timezone.utc), usegmt=True)}</lastBuildDate>"]
    for i in newest:
        url = f"{SITE_URL}/issues/{i['date']}"
        t = dt.datetime.fromisoformat(i["built_at"]).astimezone(dt.timezone.utc)
        out += ["<item>", f"<title>{x(TITLE)}: week of {x(date_label(i['date']))}</title>", f"<link>{url}</link>",
                f'<guid isPermaLink="true">{url}</guid>', f"<pubDate>{format_datetime(t, usegmt=True)}</pubDate>",
                f"<description>{x(issue_content(i))}</description>", "</item>"]
    return "\n".join(out + ["</channel>", "</rss>"]) + "\n"


CSS = """:root{--bg:#fbfbf8;--fg:#1d1d1f;--muted:#5f6368;--line:#e2e1da;--accent:#1d4ed8;--quote:#f1f1ec}
@media (prefers-color-scheme: dark){:root{--bg:#141414;--fg:#e8e6e1;--muted:#a3a19c;--line:#2e2d2a;--accent:#7aa2ff;--quote:#1f1e1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
header.site,main,footer.site{max-width:46rem;margin:0 auto;padding:0 16px}
header.site{display:flex;flex-wrap:wrap;gap:.5rem 1.5rem;align-items:baseline;justify-content:space-between;padding-top:1rem;padding-bottom:.75rem;border-bottom:1px solid var(--line)}
.brand{font-weight:800;font-size:1.2rem;text-decoration:none;color:var(--fg)}
nav a{margin-right:1rem;color:var(--muted)}
a{color:var(--accent);overflow-wrap:anywhere}
h1{font-size:1.6rem;line-height:1.25;margin:1.5rem 0 .75rem}
h2{font-size:1.2rem;line-height:1.3;margin:2rem 0 .5rem}
summary h2{display:inline}
summary{cursor:pointer;margin:2rem 0 .5rem}
.rank,.meta,.dateline,.note,.count,.date,.gist{color:var(--muted)}
.facts{font-weight:600;margin:.25rem 0}
.meta{font-size:.95rem}
ul.meta{padding-left:1.1rem}
.dateline{margin:1.5rem 0 0;font-size:.95rem}
blockquote{margin:.75rem 0;padding:.5rem 1rem;background:var(--quote);border-left:3px solid var(--accent)}
blockquote p{margin:0}
blockquote footer{color:var(--muted);font-size:.9rem;margin-top:.25rem}
.pick{border-bottom:1px solid var(--line);padding-bottom:1rem}
.all ul,.weeks{padding-left:1.1rem}
.all li{margin:.5rem 0;font-size:.95rem}
.weeks li{margin:.6rem 0}
.date{display:block;font-size:.9rem}
.count{font-size:.9rem;white-space:nowrap}
.note,.gist{font-size:.9rem}
.pager{margin:2rem 0}
footer.site{margin-top:3rem;padding-top:1rem;padding-bottom:2rem;border-top:1px solid var(--line);color:var(--muted);font-size:.9rem}
"""


def load_issues(d: Path = ISSUES) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


def build(issues: list[dict], out: Path) -> list[str]:
    out = Path(out)
    if out.name != "dist":
        raise SystemExit(f"refusing to wipe {out}: the output directory must be named dist")
    issues = sorted(issues, key=lambda i: i["date"])
    if out.exists():
        shutil.rmtree(out)
    (out / "issues").mkdir(parents=True)
    files = {"index.html": index_page(issues), "about.html": about_page(), "feed.xml": feed_xml(issues),
             "style.css": CSS, "404.html": page(f"Not found · {TITLE}",
                                                '<h1>Not found</h1><p><a href="/index.html">Past weeks</a></p>', root="/")}
    for k, i in enumerate(issues):
        prev = issues[k - 1]["date"] if k > 0 else None
        nxt = issues[k + 1]["date"] if k + 1 < len(issues) else None
        files[f"issues/{i['date']}.html"] = issue_page(i, prev, nxt)
        files[f"issues/{i['date']}-all.html"] = all_records_page(i)
    for rel, text in files.items():
        (out / rel).write_text(text, encoding="utf-8")
    return sorted(files)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ota_watch.site")
    p.add_argument("--out", type=Path, required=True, help="output directory, wiped first (must be named dist)")
    a = p.parse_args(argv)
    files = build(load_issues(), a.out)
    print(f"site: {len(files)} files -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
