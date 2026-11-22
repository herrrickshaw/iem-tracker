"""gleif_join: the jsonl fallback loader, record filters, and --apply (stored verdict table)."""
import json
import sys

import duckdb
import pandas as pd
import pytest

import gleif_join
import iem_verify as iv

CIN_A = "U24100GJ2010PTC000001"
CIN_B = "U24100MH2010PTC000002"


def rec(lei, name, cin, ra="RA000394", other=None, region="IN-GJ"):
    return dict(lei=lei, name=name, cin=cin, ra=ra, other=other, region=region, e_status="ACTIVE", r_status="ISSUED", r_next="2027-01-01")


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(gleif_join, "BASE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["gleif_join.py"])
    return tmp_path


def write_co(base, rows):
    co = pd.DataFrame([dict(id=i + 1, key=iv.key(n), name=n, state=s, cin=None, status=None, src=None, first_iem="2015-01",
                            last_iem="2015-01", last_part_b=None, in_part_b=False, usable=True) for i, (n, s) in enumerate(rows)])
    k = duckdb.connect(str(base / "iem_verify.duckdb"))
    k.execute("create table co as select * from co"); k.close()


def test_load_falls_back_to_jsonl_and_dedups_by_lei(base):
    rows = [rec("L1", "ALPHA STEELS PRIVATE LIMITED", CIN_A), rec("L1", "ALPHA STEELS PRIVATE LIMITED", CIN_A),
            rec("L2", "BETA CEMENT PRIVATE LIMITED", CIN_B)]
    (base / "gleif_india.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    g = gleif_join.load()
    assert sorted(g.lei) == ["L1", "L2"]


def test_load_prefers_parquet_over_jsonl(base):
    pd.DataFrame([rec("P1", "ALPHA STEELS PRIVATE LIMITED", CIN_A)]).to_parquet(base / "gleif_india.parquet")
    (base / "gleif_india.jsonl").write_text(json.dumps(rec("J1", "BETA CEMENT PRIVATE LIMITED", CIN_B)) + "\n")
    assert list(gleif_join.load().lei) == ["P1"]


def test_load_keeps_only_mca_records_with_21_char_cin(base):
    pd.DataFrame([
        rec("L1", "A", CIN_A),
        rec("L2", "B", CIN_B, ra="RA000999"),                 # other registration authority
        rec("L3", "C", None),                                  # no cin
        rec("L4", "D", CIN_A[:-1]),                            # 20 chars
        rec("L5", "E", CIN_A + "X"),                           # 22 chars
    ]).to_parquet(base / "gleif_india.parquet")
    assert list(gleif_join.load().lei) == ["L1"]


def test_main_apply_stores_verdict_table(base, capsys):
    pd.DataFrame([rec("L1", "ALPHA STEELS PRIVATE LIMITED", CIN_A)]).to_parquet(base / "gleif_india.parquet")
    write_co(base, [("ALPHA STEELS PVT LTD", "GUJARAT"), ("NOBODY TEXTILES PVT LTD", "GUJARAT")])
    sys.argv = ["gleif_join.py", "--apply"]
    gleif_join.main()
    k = duckdb.connect(str(base / "iem_verify.duckdb"), read_only=True)
    v = dict(k.execute("select name, verdict from gleif_verdict").fetchall())
    k.close()
    assert v == {"ALPHA STEELS PVT LTD": "new", "NOBODY TEXTILES PVT LTD": "none"}
    assert "stored table gleif_verdict" in capsys.readouterr().out


def test_main_without_apply_does_not_create_table(base, capsys):
    pd.DataFrame([rec("L1", "ALPHA STEELS PRIVATE LIMITED", CIN_A)]).to_parquet(base / "gleif_india.parquet")
    write_co(base, [("ALPHA STEELS PVT LTD", "GUJARAT")])
    gleif_join.main()
    k = duckdb.connect(str(base / "iem_verify.duckdb"), read_only=True)
    n = k.execute("select count(*) from information_schema.tables where table_name='gleif_verdict'").fetchone()[0]
    k.close()
    assert n == 0 and "stored table" not in capsys.readouterr().out
    assert (base / "gleif_join.csv").exists()


def test_main_summary_lines(base, capsys):
    pd.DataFrame([rec("L1", "ALPHA STEELS PRIVATE LIMITED", CIN_A), rec("L2", "BETA CEMENT PRIVATE LIMITED", CIN_B, region="IN-MH")]
                 ).to_parquet(base / "gleif_india.parquet")
    co = pd.DataFrame([
        dict(id=1, key=iv.key("ALPHA STEELS PVT LTD"), name="ALPHA STEELS PVT LTD", state="GUJARAT", cin=CIN_A, status="Active", src="web",
             first_iem="2015-01", last_iem="2015-01", last_part_b=None, in_part_b=False, usable=True),
        dict(id=2, key=iv.key("BETA CEMENT PVT LTD"), name="BETA CEMENT PVT LTD", state="MAHARASHTRA", cin="U24100MH2010PTC000777", status="Active", src="web",
             first_iem="2015-01", last_iem="2015-01", last_part_b=None, in_part_b=False, usable=True),
        dict(id=3, key=iv.key("NOBODY PVT LTD"), name="NOBODY PVT LTD", state="GUJARAT", cin="U24100GJ2010PTC000555", status="Active", src="web",
             first_iem="2015-01", last_iem="2015-01", last_part_b=None, in_part_b=False, usable=True)])
    k = duckdb.connect(str(base / "iem_verify.duckdb"))
    k.execute("create table co as select * from co"); k.close()
    gleif_join.main()
    out = capsys.readouterr().out
    assert "GLEIF India records with an MCA CIN: 2" in out
    assert "of 3 companies identified by the web lookups: confirmed 1, CIN changed 0, conflict 1, not in GLEIF 1" in out
    assert "among those GLEIF covers: 50% confirmed / 50% conflict" in out
