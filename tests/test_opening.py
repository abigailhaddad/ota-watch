"""opening.py on a real issue (data/issues/2026-11-16.json): the facts, every check that refuses a sentence, and
how the page uses the result."""
import json
from pathlib import Path
from types import SimpleNamespace

from ota_watch import digest, opening, site

ISSUE = json.loads((Path(__file__).parent.parent / "data" / "issues" / "2026-11-16.json").read_text())


def rid(piid: str) -> str:
    f, index = opening.facts(ISSUE)
    return next(k for k, i in index.items() if ISSUE["records"][i]["piid"] == piid)


WHALESHARK, LODESTAR = "W519TC269A063", "W519TC269A299"       # One Nation Innovation, W6QK ACC-RI
LIB_A, LIB_B = "N66604269610A", "N66604269610B"               # Advanced Technology International, NUWC DIV NEWPORT
PRISM = "HQ0845269E071"                                       # Chariot Defense, signed 2026-08-13


def good():
    return [
        {"text": "One Nation Innovation received prototype agreements from W6QK ACC-RI for the WHALESHARK autonomous low "
                 "profile vessel and for JFRA contested logistics.",
         "sources": [{"record": rid(WHALESHARK), "quote": "AUTONOMOUS LOW PROFILE VESSEL ALPV WHALESHARK"},
                     {"record": rid(LODESTAR), "quote": "JFRA CONTESTED LOGISTICS LODESTAR"}]},
        {"text": "NUWC DIV NEWPORT signed two Liberator prototype agreements with Advanced Technology International, for "
                 "$31.2M and $11.9M.",
         "sources": [{"record": rid(LIB_A), "quote": "LIBERATOR PROTOTYPE PROJECT"},
                     {"record": rid(LIB_B), "quote": "LIBERATOR PROTOTYPE PROJECT"}]},
        {"text": "Chariot Defense received $7.6M on August 13 for a Portable Resilient Integrated Storage Module prototype.",
         "sources": [{"record": rid(PRISM), "quote": "PORTABLE RESILIENT INTEGRATED STORAGE MODULE (PRISM) PROTOTYPE"}]},
    ]


def run(sentences):
    f, index = opening.facts(ISSUE)
    return opening.problems(sentences, f, index, ISSUE["records"])


def fake_client(sentences=None, exc=None):
    def create(**kw):
        if exc:
            raise exc
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"sentences": sentences}))],
                               usage=SimpleNamespace(input_tokens=9000, output_tokens=400))
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_facts_are_new_records_largest_first_then_the_largest_mods_and_mark_picks():
    f, index = opening.facts(ISSUE)
    new = f["new_agreements_orders_and_vehicles_largest_first"]
    assert len(new) == opening.MAX_NEW and len(f["largest_modifications"]) == opening.MAX_MODS
    amounts = [abs(ISSUE["records"][index[r["id"]]]["obligated"]) for r in new]
    assert amounts == sorted(amounts, reverse=True)
    assert new[0]["piid"] == "N000242696125" and new[0].get("picked_out_below")      # the EXOWA pick
    assert len(f["picked_out_below"]) == len(ISSUE["picks"])
    assert opening.facts(ISSUE)[0] == f


def test_good_sentences_pass():
    assert run(good()) == []


def test_each_check_refuses_the_sentence():
    ws = {"record": rid(WHALESHARK), "quote": "AUTONOMOUS LOW PROFILE VESSEL ALPV WHALESHARK"}
    la, lb = ({"record": rid(x), "quote": "LIBERATOR PROTOTYPE PROJECT"} for x in (LIB_A, LIB_B))
    pr = {"record": rid(PRISM), "quote": "PORTABLE RESILIENT INTEGRATED STORAGE MODULE (PRISM) PROTOTYPE"}
    cases = {
        "word not in its cited records: 'Space'": [{"text": "A Space Force office bought the WHALESHARK vessel.", "sources": [ws]}],
        "quote not in": [{"text": "One Nation Innovation is building the WHALESHARK vessel.", "sources": [{**ws, "quote": "WHALESHARK SUBMARINE DRONE"}]}],
        "no checked source": [{"text": "One Nation Innovation is building the WHALESHARK vessel.", "sources": []}],
        "unknown record": [{"text": "One Nation Innovation is building the WHALESHARK vessel.", "sources": [{**ws, "record": "R999"}]}],
        "dollar figure": [{"text": "Chariot Defense received $9.6M for the PRISM prototype.", "sources": [pr]}],
        "numbers not in its cited records": [{"text": "Chariot Defense will deliver 40 PRISM prototype modules.", "sources": [pr]}],
        "date not a signed date": [{"text": "Chariot Defense signed for the PRISM prototype on August 14.", "sources": [pr]}],
        "count": [{"text": "NUWC DIV NEWPORT signed three prototype agreements for Liberator.", "sources": [la, lb]}],
        "says 'not competed'": [{"text": "W6QK ACC-RI gave One Nation Innovation a not competed agreement for WHALESHARK.", "sources": [ws]}],
        "says 'largest'": [{"text": "The largest new award is the WHALESHARK vessel for One Nation Innovation.", "sources": [ws]}],
        "counting word": [{"text": "Many prototype agreements went to Advanced Technology International for Liberator.", "sources": [la, lb]}],
        "hype word": [{"text": "Chariot Defense received a cutting-edge PRISM prototype award.", "sources": [pr]}],
        "advice": [{"text": "Readers should note the PRISM prototype award to Chariot Defense.", "sources": [pr]}],
        "exclamation": [{"text": "Chariot Defense received the PRISM prototype award!", "sources": [pr]}],
        "prose about the input": [{"text": "The record says Chariot Defense received the PRISM prototype award.", "sources": [pr]}],
        "quote for": [{"text": "Chariot Defense received the PRISM prototype award.", "sources": [{**pr, "quote": "PRISM"}]}],
    }
    for want, sentences in cases.items():
        p = run(sentences)
        assert any(want in x for x in p), (want, p)


def test_names_must_come_from_the_record_the_sentence_cites():
    s = [{"text": "Chariot Defense and One Nation Innovation received the PRISM prototype award.",
          "sources": [{"record": rid(PRISM), "quote": "PORTABLE RESILIENT INTEGRATED STORAGE MODULE (PRISM) PROTOTYPE"}]}]
    assert any("'Nation'" in x for x in run(s))


def test_too_long():
    assert any(x.startswith("too long") for x in run(good() * 2))


def test_write_keeps_a_passing_opening_and_the_page_leads_with_it():
    o = opening.write(ISSUE, client=fake_client(good()))
    assert o["status"] == "kept" and o["problems"] == []
    assert {s["piid"] for s in o["sources"]} == {WHALESHARK, LODESTAR, LIB_A, LIB_B, PRISM}
    html = site.issue_content({**ISSUE, "opening": o})
    assert html.startswith('<p class="lede opening">One Nation Innovation received prototype agreements')
    # the counts paragraph now follows the picks
    assert html.index("records: ") > html.index('class="pick"')


def test_failed_or_errored_opening_is_left_out():
    bad = good()
    bad[0]["text"] = bad[0]["text"].replace("W6QK ACC-RI", "a Space Force office")
    o = opening.write(ISSUE, client=fake_client(bad))
    e = opening.write(ISSUE, client=fake_client(exc=RuntimeError("boom")))
    assert o["status"] == "dropped" and e["status"] == "error"
    for x in (o, e, None):
        html = site.issue_content({**ISSUE, "opening": x})
        assert "opening" not in html.split("\n")[0] and "Space Force" not in html


def test_old_issues_without_an_opening_still_render():
    assert "opening" not in ISSUE and site.issue_content(ISSUE).startswith('<p class="lede">Every OT record')


def test_digest_main_adds_the_opening_and_never_fails_on_it(tmp_path, monkeypatch, capsys):
    # the fake client answers the picks call and then the opening call (which it can't parse: an error, not a crash)
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"picks": []}))],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    monkeypatch.setattr(digest, "ISSUES", tmp_path)
    (tmp_path / "2026-11-16.json").write_text(json.dumps(ISSUE))
    monkeypatch.setattr(digest.collect, "load", lambda: {r["piid"] + (r["mod"] or ""): {**r, "key": r["piid"] + (r["mod"] or ""),
                                                                                        "first_seen": "2099-01-01T00:00:00+00:00"}
                                                         for r in ISSUE["records"][:3]})
    monkeypatch.setattr(digest.dt, "datetime", type("D", (digest.dt.datetime,), {
        "now": classmethod(lambda cls, tz=None: digest.dt.datetime(2099, 1, 2, tzinfo=digest.dt.timezone.utc))}))
    digest.main(["--date", "2026-11-23", "--dry-run"], client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert len(calls) == 2 and '"opening"' in capsys.readouterr().err


def test_issue_page_lists_the_top_new_records_and_links_every_record():
    # 2026-11-27: the issue page lists the WEEK_TOP largest new records not in a pick; the rest, and every
    # modification, are on the week's every-record page, and the page says how many it left out
    top, rest, mods = site.week_lists(ISSUE)
    new = [r for r in ISSUE["records"] if (r.get("mod") or "0") == "0"]
    in_picks = {(r["piid"], r["mod"]) for p in ISSUE["picks"] for r in p["records"]}
    assert len(top) == site.WEEK_TOP and not any((r["piid"], r["mod"]) in in_picks for r in top)
    assert len(top) + rest + sum(1 for r in new if (r["piid"], r["mod"]) in in_picks) == len(new)
    others = [abs(r["obligated"]) for r in new if (r["piid"], r["mod"]) not in in_picks]
    assert [abs(r["obligated"]) for r in top] == sorted(others, reverse=True)[:site.WEEK_TOP]
    html = site.issue_content(ISSUE)
    assert f"Not listed here: {rest:,} more new agreements, orders and vehicles and {len(mods):,} modifications" in html
    assert 'href="2026-11-16-all.html"' in html and "All modifications" not in html
    full = site.all_records_content(ISSUE)
    assert full.count("<li>") == len(ISSUE["records"])


def test_a_large_grouped_pick_names_its_records_and_lists_them_on_the_every_record_page():
    big = next(p for p in ISSUE["picks"] if len(p["records"]) > site.GROUP_LIST_MAX)
    h = site.pick_html(1, big, ISSUE["date"])
    assert "<li>" not in h and 'href="2026-11-16-all.html"' in h
    assert all(r["piid"] in h for r in big["records"])
    small = next(p for p in ISSUE["picks"] if len(p["records"]) == 2)
    assert site.pick_html(1, small, ISSUE["date"]).count("<li>") == 2
