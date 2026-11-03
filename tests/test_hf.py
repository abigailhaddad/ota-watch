import datetime as dt

from ota_watch import fpds, hf


def row(key, signed="2026-07-16", **kw):
    r = dict.fromkeys(fpds.FIELDS)
    r.update(key=key, piid="0" + key, signed=signed, obligated=1.5)
    r.update(kw)
    return r


def test_documents_csv_wins_and_keeps_first_seen_history_is_baseline():
    history = [row("A", description="old"), row("B")]
    # documents.csv rows are strings, as collect.load() returns them
    docs = {"A": {**{k: ("" if v is None else str(v)) for k, v in row("A", description="new").items()},
                  "first_seen": "2026-10-26T12:00:00+00:00"},
            "C": {**{k: ("" if v is None else str(v)) for k, v in row("C").items()}, "first_seen": "baseline"}}
    rows, counts = hf.merge(history, docs)
    by = {r["key"]: r for r in rows}
    assert by["A"]["description"] == "new" and by["A"]["first_seen"] == "2026-10-26T12:00:00+00:00"
    assert by["B"]["first_seen"] == "baseline"
    assert by["C"]["obligated"] == 1.5 and by["C"]["piid"] == "0C"      # money a float, IDs keep zeros
    assert by["A"]["mod_reason"] is None                                  # "" -> null
    assert counts == {"base_rows": 2, "base_duplicate_keys": 0, "documents_csv_rows": 2,
                      "replaced_from_documents_csv": 1, "added_from_documents_csv": 1, "total": 3}


def test_republishing_keeps_first_seen_from_the_dataset():
    rows, _ = hf.merge([{**row("A"), "first_seen": "2026-10-26T12:00:00+00:00"}], {})
    assert rows[0]["first_seen"] == "2026-10-26T12:00:00+00:00"


def test_duplicate_keys_in_history_are_counted():
    _, counts = hf.merge([row("A"), row("A")], {})
    assert counts["base_duplicate_keys"] == 1 and counts["total"] == 1


def test_fiscal_year():
    assert hf.fiscal_year("2025-09-30") == "2025"
    assert hf.fiscal_year("2025-10-01") == "2026"
    assert hf.fiscal_year(None) == "no signed date"


def test_card_counts_add_up():
    rows, _ = hf.merge([row("A", signed="2025-10-01"), row("B", signed="2025-09-30", kind="OtherTransactionIDV")], {})
    c = hf.card(rows, "2026-11-03T00:00:00+00:00")
    assert "2 documents, signed 2025-09-30 to 2025-10-01" in c
    assert "| 2025 | 1 | 0% | 0% | 0 | 0 |" in c and "| 2026 | 1 |" in c


def test_stats_are_computed_from_rows():
    rows, _ = hf.merge([
        row("A", signed="2018-01-05", created="2020-03-01 00:00:00", department="HOMELAND SECURITY, DEPARTMENT OF",
            agreement_type="PROTOTYPE", obligated=-2.0, vendor="V1", uei="U1", description="A RECONSTRUCTION OF"),
        row("B", signed="2022-01-05", created="2022-01-06 00:00:00", department="DEPT OF DEFENSE",
            agreement_type="PRODUCTION", consortium="Y", obligated=5.0, vendor="V1 INC", uei="U1",
            kind="OtherTransactionIDV"),
    ], {})
    st = hf.stats(rows)
    assert st["first_fy_consortium"] == "2022" and st["first_fy_production"] == "2022"
    assert st["obligated"] == 3.0 and st["obligated_on_idv"] == 5.0
    assert (st["deobligations"], st["deobligated"]) == (1, -2.0)
    assert st["entered_over_a_year_late"] == 1 and st["reconstructions"] == 1
    assert st["ueis_with_several_names"] == 1
    assert st["years"]["2018"]["dhs"] == 1 and st["years"]["2022"]["dod"] == 1


FEED = """<feed xmlns="http://www.w3.org/2005/Atom">{links}{entries}</feed>"""


def test_feed_count_uses_last_page(monkeypatch):
    pages = {
        0: FEED.format(links='<link rel="last" href="https://x/ATOM?q=x&amp;start=30"/>', entries="<entry/>" * 10),
        30: FEED.format(links="", entries="<entry/>" * 4),
    }
    monkeypatch.setattr(fpds, "_get", lambda params, session=None: pages[params["start"]].encode())
    assert hf.feed_count(dt.date(2020, 1, 1), dt.date(2020, 12, 31)) == 34


def test_feed_count_single_page(monkeypatch):
    monkeypatch.setattr(fpds, "_get", lambda params, session=None: FEED.format(links="", entries="<entry/>" * 3).encode())
    assert hf.feed_count(dt.date(2020, 1, 1), dt.date(2020, 12, 31)) == 3


def test_backfill_starts_at_the_first_year_fpds_has(monkeypatch, tmp_path):
    # FPDS has documents only in 2024 and 2025
    have = {2024: 3, 2025: 2}
    count = lambda start, end, session=None: sum(n for y, n in have.items() if start.year <= y <= end.year)  # noqa: E731
    monkeypatch.setattr(hf, "feed_count", count)
    read = []
    monkeypatch.setattr(fpds, "fetch_range", lambda start, end, session=None: read.append(start.year) or
                        [row(f"{start.year}-{i}", signed=f"{start.year}-06-01") for i in range(have.get(start.year, 0))])
    hf.backfill(tmp_path, until=dt.date(2025, 12, 31))
    assert read == [2024, 2025]
    assert len(hf.load_history(tmp_path)) == 5


def test_idv_overlap_bound_and_timing():
    rows, _ = hf.merge([
        row("I0", kind="OtherTransactionIDV", agency_id="97", piid="V1", signed="2020-01-01", obligated=100.0),
        row("I1", kind="OtherTransactionIDV", agency_id="97", piid="V1", signed="2022-01-01", obligated=10.0),
        row("O1", kind="OtherTransactionAward", action_type="OTHER TRANSACTION ORDER", parent_agency_id="97",
            parent_piid="V1", signed="2021-01-01", obligated=30.0),
        row("I2", kind="OtherTransactionIDV", agency_id="97", piid="V2", signed="2020-01-01", obligated=50.0),
    ], {})
    st = hf.stats(rows)
    assert (st["idvs"], st["idvs_with_orders"]) == (2, 1)
    assert st["on_idvs_with_orders"] == 110.0 and st["on_idvs_with_orders_before_first_order"] == 100.0
    assert st["overlap_bound"] == 30.0
