import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ota_watch import collect, digest, fpds, site

FIX = Path(__file__).parent / "fixtures"


def test_parse_real_fpds_entry():
    root = ET.fromstring((FIX / "fpds_one_entry.xml").read_bytes())
    (entry,) = root.findall("a:entry", fpds.NS)
    r = fpds.parse_entry(entry)
    assert r["kind"] == "OtherTransactionAward" and r["action_type"] == "OTHER TRANSACTION ORDER"
    assert (r["agency_id"], r["piid"], r["mod"]) == ("9700", "N65236269E038", "P00001")
    assert r["parent_piid"] == "N652362490003"
    assert r["key"] == "OtherTransactionAward|9700|N65236269E038|P00001|N652362490003"
    assert r["signed"] == "2026-10-07" and r["obligated"] == 0.0
    assert r["total_obligated_raw"] == 13295559.44           # kept raw, never shown
    assert r["vendor"] == "ADVANCED TECHNOLOGY INTERNATIONAL" and r["uei"] == "LDMMF472BB93"
    assert r["competed"] == "COMPETED" and r["agreement_type"] == "PRODUCTION"
    assert r["contracting_agency"] == "DEPT OF THE NAVY" and r["funding_agency"] == "U.S. COAST GUARD"
    assert r["description"].startswith("OTA IWRP 26-BLANT1-3154-008 ATLAS")
    assert list(r) == fpds.FIELDS


def doc(key, signed="2026-07-16", first_seen="2026-10-26", mod="0", obligated=0.0, desc="X", **kw):
    r = dict.fromkeys(fpds.FIELDS)
    r.update(key=key, piid=key, signed=signed, mod=mod, obligated=obligated, description=desc,
             competed="COMPETED", vendor="V" + key, department="DEPT OF DEFENSE")
    r.update(kw)
    return {"first_seen": first_seen, **r}


def test_merge_keeps_first_seen_and_refreshes(tmp_path):
    docs = {}
    a = {k: v for k, v in doc("A").items() if k != "first_seen"}
    assert collect.merge(docs, [a], "2026-10-19") == {"fetched": 1, "new": 1, "refreshed": 0}
    collect.save(docs, tmp_path / "d.csv")
    docs = collect.load(tmp_path / "d.csv")
    b = dict(a, description="corrected", obligated=5.0)
    assert collect.merge(docs, [b, {**a, "key": "B", "piid": "B"}], "2026-10-26")["new"] == 1
    assert docs["A"]["first_seen"] == "2026-10-19" and docs["A"]["description"] == "corrected"
    assert docs["B"]["first_seen"] == "2026-10-26"


def test_select_by_first_seen_never_baseline_and_by_signed():
    docs = {k: doc(k, first_seen=fs, signed=sg) for k, fs, sg in [
        ("old", "baseline", "2026-07-16"), ("prev", "2026-10-19", "2026-07-10"),
        ("this", "2026-10-26", "2026-07-20"), ("future", "2026-11-02", "2026-07-25")]}
    got = digest.select(docs, after="2026-10-19", until="2026-10-26")
    assert [r["key"] for r in got] == ["this"]
    got = digest.select(docs, after=None, until="2026-10-26", signed_from="2026-07-15", signed_to="2026-07-21")
    assert [r["key"] for r in got] == ["old", "this"]


ROWS = [doc("A", obligated=85_500_000, desc="EQUITY INVESTMENT AGREEMENT.\nAN OTHER TRANSACTION VEHICLE WAS USED"),
        doc("B", obligated=12_457_553.78, desc="AGREEMENT/ORDER AWARDED TO OVERLAND AI\nFOR FUNDING MILESTONES"),
        doc("C", obligated=10_106_478.67, desc="AWARDED TO FORTERRA FOR FUNDING MILESTONES"),
        doc("D", obligated=-132_493, desc="PROCUREMENT AND HUSBANDRY OF NONHUMAN PRIMATES")]


def pick(**kw):
    p = {"indices": [0], "headline": "Equity investment of $85.5M", "why": "Not competed.",
         "quote_index": 0, "quote": "an other transaction vehicle was used"}
    p.update(kw)
    return p


@pytest.mark.parametrize("p,reason", [
    (pick(), None),
    (pick(quote="AGREEMENT. AN OTHER TRANSACTION"), None),                  # line break in the record
    (pick(quote="a vehicle nobody wrote"), "quote not found"),
    (pick(quote_index=1), "quote_index"),
    (pick(indices=[9]), "bad indices"),
    (pick(headline="Equity investment of $90M"), "dollar figure"),
    (pick(indices=[1, 2], quote_index=1, quote="awarded to overland ai for funding",
          headline="Army sends $22.6M through a consortium", why="Two orders: $12.46M and $10.1M."), None),
    (pick(indices=[1, 2], quote_index=1, quote="awarded to overland ai for funding",
          headline="Army sends $25M through a consortium"), "dollar figure"),
    (pick(indices=[3], quote_index=3, quote="husbandry of nonhuman primates",
          headline="Monkey OT", why="It de-obligated $132,493."), None),
])
def test_check_pick(p, reason):
    got = digest.check_pick(p, ROWS)
    assert (got is None) if reason is None else (reason in got), got


def test_build_issue_drops_bad_picks_and_computes_numbers():
    picks = [pick(), pick(quote="made up words here"), pick(indices=[1, 2], quote_index=2,
             quote="awarded to forterra", headline="Consortium orders")]
    i = digest.build_issue(ROWS, picks, {"model": "m"}, date="2026-10-26", window={"kind": "first_seen"})
    assert [p["headline"] for p in i["picks"]] == ["Equity investment of $85.5M", "Consortium orders"]
    assert i["dropped"][0]["dropped_because"].startswith("quote not found")
    assert i["picks"][1]["obligated_total"] == round(12_457_553.78 + 10_106_478.67, 2)
    s = i["summary"]
    assert s["records"] == 4 and s["new_awards"] == 4 and s["obligated_new"] == round(sum(r["obligated"] for r in ROWS), 2)
    assert [r["piid"] for r in i["records"]] == ["A", "B", "C", "D"]       # new first, then by size


def test_one_issue_a_week(tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "ISSUES", tmp_path)
    (tmp_path / "2026-10-22.json").write_text(json.dumps({"date": "2026-10-22", "window": {"first_seen_to": "2026-10-22"}}))

    def boom(*a, **k):
        raise AssertionError("built a second issue")
    monkeypatch.setattr(digest, "call_model", boom)
    assert digest.main(["--date", "2026-10-25"]) == 0                    # Sunday: same week as Thu 10-22
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-22.json"]
    assert digest.same_week("2026-10-22", "2026-10-25") and not digest.same_week("2026-10-22", "2026-10-26")


def test_site_escapes_and_has_no_scripts(tmp_path):
    evil = '<script>alert(1)</script> "q" &'
    rows = [doc("A", obligated=1e6, desc=evil, vendor=evil, fpds_link="javascript:alert(1)")]
    i = digest.build_issue(rows, [pick(quote="alert(1)", headline=evil, why=evil)], {}, date="2026-10-26",
                           window={"kind": "first_seen", "first_seen_after": "2026-10-19", "first_seen_to": "2026-10-26"})
    out = tmp_path / "dist"
    files = site.build([i], out)
    assert files == ["404.html", "about.html", "feed.xml", "index.html", "issues/2026-10-26.html", "style.css"]
    for f in files:
        t = (out / f).read_text()
        assert "<script" not in t and "javascript:" not in t, f
    h = (out / "issues/2026-10-26.html").read_text()
    assert "&lt;script&gt;" in h and 'href="https://github.com/abigailhaddad/ota-watch/issues/new"' in h
    assert 'href="https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&amp;q=PIID%3A%22A%22"' in h
    assert "Past weeks" in h and "Past weeks" not in (out / "index.html").read_text()
    ET.fromstring((out / "feed.xml").read_text())                         # well-formed RSS


def test_grouped_pick_shows_only_shared_facts():
    recs = [digest.fact(doc("A", vendor="NORTHROP", contracting_office="FA8819", competed="COMPETED")),
            digest.fact(doc("B", vendor="RAYTHEON", contracting_office="FA8819", competed="COMPETED")),
            digest.fact(doc("C", vendor="NORTHROP", contracting_office="FA8819", competed="NOT COMPETED"))]
    line = site.shared_facts(recs)
    assert line.startswith("2 vendors") and "FA8819" in line
    assert "NORTHROP" not in line and "COMPETED" not in line
    assert site.shared_facts(recs[:1]).startswith("NORTHROP")


def test_timestamped_first_seen_between_issues_is_not_lost():
    docs = {k: doc(k, first_seen=fs) for k, fs in [
        ("before", "2026-10-22T12:00:00+00:00"), ("same_day_after", "2026-10-22T18:00:00+00:00"),
        ("monday", "2026-10-26T12:05:00+00:00"), ("baseline", "baseline")]}
    got = digest.select(docs, after="2026-10-22T12:31:31+00:00", until="2026-10-26T12:30:00+00:00")
    assert sorted(r["key"] for r in got) == ["monday", "same_day_after"]
