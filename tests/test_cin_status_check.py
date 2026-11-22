"""cin_status_check: page parsing, resumable cache and both CLI modes, with curl replaced by canned HTML."""
import csv
import sys
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

import cin_status_check as cc

CIN1 = "U24100GJ2010PTC000001"
CIN2 = "U24100MH2010PTC000002"
CIN3 = "U24100KA2010PTC000003"


def page(cin, status="Active", agm="30 September, 2025", capital="₹ 1.5 Lakh", updated="12 October 2026",
         industry="Manufacturing", name="ALPHA STEELS PRIVATE LIMITED"):
    return (f"<html><head><style>.x{{}}</style><script>var cin='SKIP';</script></head><body>"
            f"<p>{cin} › {industry} › {name} {status}</p>"
            f"<p>The current status of {name} is - {status}. </p>"
            f"<p>The last AGM was held on {agm}.</p><p>Paid up Capital {capital}</p><p>Updated: {updated}</p></body></html>")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "BASE", tmp_path)
    monkeypatch.setattr(cc, "OUT", tmp_path / "cin_status.csv")
    monkeypatch.setattr(cc, "gate", {"lock": cc.threading.Lock(), "next": 0.0, "gap": 0.25})
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    return tmp_path


def fake_curl(monkeypatch, pages):
    calls = []

    def run(cmd, capture_output=None, text=None):
        calls.append(cmd)
        cin = cmd[-1].rsplit("/", 1)[1]
        return SimpleNamespace(stdout=pages.get(cin, ""))
    monkeypatch.setattr(cc.subprocess, "run", run)
    return calls


# --------------------------------------------------------------------------- fetch
def test_fetch_parses_fields_and_requests_the_tofler_url(env, monkeypatch):
    calls = fake_curl(monkeypatch, {CIN1: page(CIN1)})
    r = cc.fetch(CIN1)
    assert calls[0][:4] == ["curl", "-sL", "-m", "30"] and calls[0][-1] == f"https://www.tofler.in/x/company/{CIN1}"
    assert "-A" in calls[0] and cc.UA in calls[0]
    assert r == dict(cin=CIN1, status2="Active", balance_sheet="", agm="30 September, 2025", compliant="₹ 1.5 Lakh",
                     cirp="12 October 2026", nic="", industry="Manufacturing", ok="True")


def test_fetch_not_ok_when_cin_missing_from_page(env, monkeypatch):
    fake_curl(monkeypatch, {CIN1: page(CIN2)})            # generic page for another CIN
    assert cc.fetch(CIN1)["ok"] == "False"


def test_fetch_not_ok_without_status_sentence(env, monkeypatch):
    fake_curl(monkeypatch, {CIN1: f"<p>{CIN1} something else entirely</p>"})
    r = cc.fetch(CIN1)
    assert r["ok"] == "False" and r["status2"] == "" and r["agm"] == ""


def test_fetch_empty_response_is_all_blank(env, monkeypatch):
    fake_curl(monkeypatch, {})
    r = cc.fetch(CIN1)
    assert r["ok"] == "False" and {k: v for k, v in r.items() if k not in ("cin", "ok")} == {k: "" for k in r if k not in ("cin", "ok")}


def test_fetch_ignores_script_and_style_blocks(env, monkeypatch):
    html = f"<script>The current status of X is - Bogus. </script><p>{CIN1}</p><p>The current status of YYY is - Strike Off. </p>"
    fake_curl(monkeypatch, {CIN1: html})
    assert cc.fetch(CIN1)["status2"] == "Strike Off"


def test_fetch_unescapes_html_entities(env, monkeypatch):
    fake_curl(monkeypatch, {CIN1: page(CIN1, name="A &amp; B PRIVATE LIMITED", capital="₹ 2 Crore")})
    assert cc.fetch(CIN1)["compliant"] == "₹ 2 Crore"


def test_fetch_agm_without_comma(env, monkeypatch):
    fake_curl(monkeypatch, {CIN1: page(CIN1, agm="30 September 2025")})
    assert cc.fetch(CIN1)["agm"] == "30 September 2025"


def test_fetch_throttles_to_gate_gap(env, monkeypatch):
    sleeps = []
    monkeypatch.setattr(cc.time, "sleep", sleeps.append)
    monkeypatch.setattr(cc.time, "time", lambda: 100.0)
    fake_curl(monkeypatch, {CIN1: page(CIN1)})
    cc.fetch(CIN1)
    assert sleeps == [] and cc.gate["next"] == 100.25               # first call: nothing to wait for
    cc.fetch(CIN1)
    assert sleeps == [pytest.approx(0.25)]                           # second call waits out the gap


# --------------------------------------------------------------------------- cached
def test_cached_missing_file_is_empty(env):
    assert cc.cached() == {}


def test_cached_returns_only_ok_rows_and_last_one_wins(env):
    (env / "cin_status.csv").write_text(
        "cin,status2,balance_sheet,agm,compliant,cirp,nic,industry,ok\n"
        f"{CIN1},,,,,,,,False\n{CIN2},Active,,,,,,,True\n{CIN1},Active,,,,,,,True\n{CIN2},Strike Off,,,,,,,True\n")
    c = cc.cached()
    assert set(c) == {CIN1, CIN2}
    assert c[CIN1]["status2"] == "Active" and c[CIN2]["status2"] == "Strike Off"


# --------------------------------------------------------------------------- run
def test_run_fetches_only_uncached_and_writes_header_once(env, monkeypatch, capsys):
    calls = fake_curl(monkeypatch, {CIN1: page(CIN1), CIN2: page(CIN2, status="Strike Off")})
    d = cc.run([CIN1, CIN2])
    assert set(d) == {CIN1, CIN2} and d[CIN2]["status2"] == "Strike Off"
    assert "2 CINs; 0 cached; fetching 2" in capsys.readouterr().out
    n = len(calls)
    d2 = cc.run([CIN1, CIN2])
    assert len(calls) == n and set(d2) == {CIN1, CIN2}              # everything cached now
    text = (env / "cin_status.csv").read_text()
    assert text.count("cin,status2") == 1
    assert len(list(csv.DictReader(open(env / "cin_status.csv")))) == 2


def test_run_retries_failed_pages_and_keeps_latest(env, monkeypatch):
    pages = {CIN1: ""}
    calls = fake_curl(monkeypatch, pages)
    d = cc.run([CIN1])
    assert d[CIN1]["ok"] == "False"
    pages[CIN1] = page(CIN1)
    d = cc.run([CIN1])
    assert d[CIN1]["ok"] == "True" and len(calls) == 2


def test_run_progress_line_every_100(env, monkeypatch, capsys):
    cins = [f"U24100GJ2010PTC{i:06d}" for i in range(100)]
    fake_curl(monkeypatch, {c: page(c) for c in cins})
    cc.run(cins)
    assert "  100/100" in capsys.readouterr().out


# --------------------------------------------------------------------------- main
def make_db(base, rows):
    df = pd.DataFrame(rows, columns=["cin", "status", "src", "in_part_b"])
    k = duckdb.connect(str(base / "iem_verify.duckdb"))
    k.execute("create table co as select * from df"); k.close()


def main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["cin_status_check.py", *argv])
    cc.main()


def test_main_sample_compares_with_first_source(env, monkeypatch, capsys):
    make_db(env, [(CIN1, "Active", "web", False), (CIN2, "Active", "web", False), (CIN3, "Strike Off", "web", False)])
    fake_curl(monkeypatch, {CIN1: page(CIN1, agm="01 March, 2025"),
                            CIN2: page(CIN2, status="Strike Off", agm="01 March, 2024"),
                            CIN3: page(CIN3, status="Strike Off", agm="01 March, 2023")})
    main(monkeypatch, "sample", "--n", "3")
    out = capsys.readouterr().out
    assert "pages parsed: 3/3" in out
    assert "status word agrees with first source: 67%" in out
    assert "Active | Strike Off" in out
    assert "Among Active (1): last AGM in 2025 or later: 100% | 2024 or later: 100% | no AGM date: 0%" in out


def test_main_sample_counts_active_without_recent_agm(env, monkeypatch, capsys):
    make_db(env, [(CIN1, "Active", "web", False), (CIN2, "Active", "web", False)])
    fake_curl(monkeypatch, {CIN1: page(CIN1, agm="01 March, 2025"), CIN2: page(CIN2, agm="01 March, 2024")})
    main(monkeypatch, "sample", "--n", "2")
    out = capsys.readouterr().out
    assert "Among Active (2): last AGM in 2025 or later: 50% | 2024 or later: 100% | no AGM date: 0%" in out


def test_main_run_limit_orders_part_b_first(env, monkeypatch, capsys):
    make_db(env, [(CIN1, "Active", "web", False), (CIN2, "Active", "web", True), (CIN3, "Active", "web", False),
                  (CIN3, "Active", "web", False), (None, None, None, True)])
    calls = fake_curl(monkeypatch, {c: page(c) for c in (CIN1, CIN2, CIN3)})
    main(monkeypatch, "run", "--limit", "2")
    fetched = [c[-1].rsplit("/", 1)[1] for c in calls]
    # CIN2 (in Part B) first, then CIN3 (2 rows) before CIN1 (1 row); the NULL cin is excluded; limit 2
    assert sorted(fetched) == sorted([CIN2, CIN3])
    assert "2 CINs; 0 cached; fetching 2" in capsys.readouterr().out


def test_main_run_candidates_reads_linked_parquet(env, monkeypatch, capsys):
    make_db(env, [(CIN1, "Active", "web", False)])
    pd.DataFrame([(CIN1, True), (CIN2, True), (CIN2, True), (CIN3, False), (None, True)], columns=["cin", "support_candidate"]
                 ).assign(month="2022-01").to_parquet(env / "iem_partA_filings_linked.parquet")
    calls = fake_curl(monkeypatch, {c: page(c) for c in (CIN1, CIN2, CIN3)})
    main(monkeypatch, "run", "--candidates")
    assert sorted(c[-1].rsplit("/", 1)[1] for c in calls) == sorted([CIN1, CIN2])
    assert "2 CINs;" in capsys.readouterr().out


def test_main_creates_output_file_before_run(env, monkeypatch):
    make_db(env, [(CIN1, "Active", "web", False)])
    fake_curl(monkeypatch, {})
    assert not (env / "cin_status.csv").exists()
    main(monkeypatch, "run", "--limit", "1")
    assert (env / "cin_status.csv").exists()


def test_main_sample_agm_on_1_january_2025_counts_as_2025(env, monkeypatch, capsys):
    make_db(env, [(CIN1, "Active", "web", False)])
    fake_curl(monkeypatch, {CIN1: page(CIN1, agm="01 January 2025")})
    main(monkeypatch, "sample", "--n", "1")
    assert "last AGM in 2025 or later: 100% | 2024 or later: 100%" in capsys.readouterr().out
