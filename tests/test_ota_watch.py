import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ota_watch import collect, digest, fpds, site
from ota_watch.config import ISSUE_URL

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


ROWS = [doc("A", obligated=85_500_000, desc="EQUITY INVESTMENT AGREEMENT.\nAN OTHER TRANSACTION VEHICLE WAS USED",
            competed="NOT COMPETED"),
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
             quote="awarded to forterra", headline="Consortium orders", why="Two orders through a consortium.")]
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
    assert "&lt;script&gt;" in h and 'href="' + ISSUE_URL + '"' in h
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


# --- first publish to a new or empty dataset (no network; HF is faked) ---

class FakeHf:
    """Stands in for HfApi plus hf_hub_download. `files` is what the dataset holds."""

    def __init__(self, files=None, repo_exists=True):
        self.files, self.repo_exists, self.calls, self.commits = files, repo_exists, [], []

    def create_repo(self, repo_id, repo_type, exist_ok):
        assert exist_ok is True
        self.calls.append("create_repo")
        self.repo_exists = True

    def download(self, repo_id, filename, repo_type):
        from huggingface_hub.errors import EntryNotFoundError
        self.calls.append("download")
        assert self.repo_exists, "downloaded before the repo was created"
        if filename not in (self.files or {}):
            raise EntryNotFoundError(f"{filename} not in {repo_id}")
        return self.files[filename]

    def create_commit(self, repo_id, repo_type, commit_message, operations):
        self.commits.append((commit_message, {o.path_in_repo: o.path_or_fileobj for o in operations}))


def _parquet(tmp_path, keys):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from ota_watch import hf
    rows = [{c: None for c in hf.COLUMNS} | doc(k) for k in keys]
    schema = pa.schema([(c, pa.float64() if c in hf.MONEY else pa.string()) for c in hf.COLUMNS])
    path = tmp_path / "t.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return str(path)


def test_current_rows_creates_repo_then_reads_existing_table(tmp_path):
    from ota_watch import hf
    fake = FakeHf({hf.PARQUET: _parquet(tmp_path, ["A", "B"])}, repo_exists=False)
    rows = hf.current_rows(fake, fake.download)
    assert fake.calls == ["create_repo", "download"]
    assert sorted(r["key"] for r in rows) == ["A", "B"]


@pytest.mark.parametrize("repo_exists", [False, True])
def test_current_rows_empty_when_no_table_yet(repo_exists):
    from ota_watch import hf
    fake = FakeHf({}, repo_exists=repo_exists)  # brand-new repo, or a repo with no table in it
    assert hf.current_rows(fake, fake.download) == []
    assert fake.calls == ["create_repo", "download"]


def test_publish_to_an_empty_dataset(tmp_path, monkeypatch):
    import huggingface_hub
    from ota_watch import hf
    fake = FakeHf({}, repo_exists=False)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: fake)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake.download)
    monkeypatch.setattr(hf.collect, "load", lambda *a, **k: {"A": doc("A"), "B": doc("B")})
    hf.publish(None)
    assert fake.calls == ["create_repo", "download", "create_repo"]  # second is the commit's own exist_ok call
    (message, files), = fake.commits
    assert set(files) == {hf.PARQUET, hf.CSV, "README.md"} and "2 documents" in message


def test_publish_to_an_existing_dataset_keeps_its_rows(tmp_path, monkeypatch):
    import huggingface_hub
    from ota_watch import hf
    fake = FakeHf({hf.PARQUET: _parquet(tmp_path, ["OLD"])})
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: fake)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake.download)
    monkeypatch.setattr(hf.collect, "load", lambda *a, **k: {"A": doc("A")})
    hf.publish(None)
    (message, _), = fake.commits
    assert "2 documents" in message  # OLD from the dataset + A from documents.csv


# Claims checked against the records (2026-11-11 review of 20 published picks).
WEEK = [doc("L3", obligated=280_112_426, mod="P00002", competed="COMPETED", agreement_type="PROTOTYPE"),
        doc("X1", obligated=109_900_000, mod="P00003"),
        doc("X2", obligated=98_227_293, action_type="OTHER TRANSACTION IDV", agreement_type="PROTOTYPE"),
        doc("SS", obligated=64_089_693, mod="P00001", competed="COMPETED"),
        doc("RA", obligated=59_983_313.67, action_type="OTHER TRANSACTION IDV", agreement_type="PROTOTYPE"),
        doc("NR", obligated=22_439_201, competed="NOT COMPETED", agreement_type="PROTOTYPE")]


@pytest.mark.parametrize("text,idx,bad", [
    # 2026-11-02 #1: two records ranked 1st and 4th called "the largest obligations"
    ("These are the largest obligations in this week's records.", [0, 3], "largest"),
    ("At $280.1M it is the largest single obligation this week.", [0], None),
    # 2026-11-02 #4: second-largest new award
    ("It is one of the largest new awards this week.", [4], None),
    ("A new competed OT IDV.", [4], None),
    ("A new, not-competed prototype agreement.", [5], None),
    ("An uncompeted award.", [3], "not competed"),
    ("A new prototype agreement (mod 0).", [3], "new"),
    ("A new production OT.", [4], "production"),
    ("A new OT IDV.", [5], "IDV"),
    ("The descriptions name IDV USA Inc. as the performer.", [5], None),     # a firm's name, not an IDV claim
    ("It definitized a letter contract.", [5], "letter contract"),
])
def test_claim_problem(text, idx, bad):
    got = digest.claim_problem(text, idx, WEEK)
    assert (got is None) if bad is None else (got is not None and bad in got), got
