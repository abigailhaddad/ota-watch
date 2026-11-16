"""Offline tests for the data dictionary and the SQLite build (ota_watch/data_docs.py, ota_watch/build_sqlite.py)."""
import datetime
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ota_watch import build_sqlite, data_docs

CFG = {
    "dataset": "someone/example",
    "tables": {
        "parent": {"file": "parent.parquet", "config": "default", "grain": "one parent", "primary_key": "id",
                   "columns": {"id": "row id", "n": "a number", "flag": "a flag"}},
        "child": {"file": "child.parquet", "config": "child", "grain": "one child", "primary_key": "cid",
                  "unconfirmed": ["note"], "columns": {"cid": "row id", "id": "parent id | pipe", "note": "a note"}},
    },
    "joins": [{"left": "parent.id", "right": "child.id", "kind": "||--o{", "label": "id", "note": "exact"}],
}


@pytest.fixture
def data(tmp_path):
    pq.write_table(pa.table({"id": ["a", "b", "c"], "n": pa.array([1, None, 3], pa.int64()),
                             "flag": [True, False, True]}), tmp_path / "parent.parquet")
    pq.write_table(pa.table({"cid": ["x", "y"], "id": ["a", None], "note": ["", "hi"]}), tmp_path / "child.parquet")
    return tmp_path


def test_check_catches_drift(data):
    data_docs.check(str(data), CFG)
    bad = {**CFG, "tables": {**CFG["tables"], "parent": {**CFG["tables"]["parent"], "columns": {"id": "x", "n": "y"}}}}
    with pytest.raises(SystemExit, match="no description"):
        data_docs.check(str(data), bad)


def test_markdown(data):
    md = data_docs.markdown(str(data), CFG)
    assert "| `parent` | `parent.parquet` | `default` | 3 |" in md
    assert "parent ||--o{ child" in md and "string id FK" in md
    assert "| `n` | int64 | a number | 66.7% | 66.7% | `1` |" in md
    assert "| `note` | string | a note **(meaning not confirmed)** | 100.0% | 50.0% | `hi` |" in md
    assert "parent id \\| pipe" in md


def test_one_table_no_joins(data):
    cfg = {"dataset": "someone/example", "tables": {"parent": CFG["tables"]["parent"]}}
    md = data_docs.markdown(str(data), cfg)
    assert "How the tables join" not in md and "| `flag` | bool | a flag |" in md


def test_sqlite_matches_parquet(data, tmp_path):
    out = tmp_path / "x.sqlite"
    assert build_sqlite.build(str(data), str(out), CFG) == {"parent": 3, "child": 2}
    con = sqlite3.connect(out)
    assert con.execute("select id, n, flag from parent order by id").fetchall() == [("a", 1, 1), ("b", None, 0), ("c", 3, 1)]
    assert con.execute("select count(*) from child join parent using (id)").fetchone() == (1,)


def test_sqlite_dates_and_lists(tmp_path):
    pq.write_table(pa.table({"id": ["a", "b"], "d": pa.array([datetime.date(2026, 1, 2), None], pa.date32()),
                             "tags": pa.array([["x", "y"], None], pa.list_(pa.string()))}), tmp_path / "t.parquet")
    cfg = {"dataset": "someone/example", "tables": {"t": {"file": "t.parquet", "config": "default", "grain": "g",
           "primary_key": "id", "columns": {"id": "i", "d": "a date", "tags": "a list"}}}}
    build_sqlite.build(str(tmp_path), str(tmp_path / "t.sqlite"), cfg)
    rows = sqlite3.connect(tmp_path / "t.sqlite").execute("select d, tags from t order by id").fetchall()
    assert rows == [("2026-01-02", '["x", "y"]'), (None, None)]


def test_card_dictionary_matches_published_schema():
    """The card's dictionary section is built from a table with hf.publish's schema."""
    from ota_watch import collect, hf
    schema = pa.schema([(c, pa.float64() if c in hf.MONEY else pa.string()) for c in collect.COLUMNS])
    import io
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist([], schema=schema), buf)
    md = hf.data_dictionary(buf.getvalue())
    assert "## Data dictionary" in md and "| `obligated` | float64 |" in md
