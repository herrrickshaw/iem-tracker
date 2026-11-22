"""gleif_bulk (cursor paging, slimming, parquet) and ogd_fetch_master (per-state paging, resume) against fakes."""
import csv
import json
import sys

import pandas as pd
import pytest

import gleif_bulk as gb
import ogd_fetch_master as ogd


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body if body is not None else {}, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


# --------------------------------------------------------------------------- gleif_bulk.slim
def full_record(**over):
    d = {"attributes": {
        "lei": "LEI1",
        "entity": {"legalName": {"name": "ALPHA STEELS PRIVATE LIMITED"}, "registeredAs": "U24100GJ2010PTC000001",
                   "registeredAt": {"id": "RA000394"}, "status": "ACTIVE", "creationDate": "2010-02-02",
                   "legalForm": {"id": "ABCD"}, "category": "GENERAL", "otherNames": [{"name": "OLD ONE"}, {"name": "OLD TWO"}],
                   "legalAddress": {"region": "IN-GJ", "city": "SURAT", "postalCode": "395003"},
                   "headquartersAddress": {"region": "IN-MH"}},
        "registration": {"status": "ISSUED", "initialRegistrationDate": "2015-01-01", "lastUpdateDate": "2025-01-01",
                         "nextRenewalDate": "2026-01-01"}}}
    d["attributes"].update(over)
    return d


def test_slim_extracts_every_field():
    assert gb.slim(full_record()) == {
        "lei": "LEI1", "name": "ALPHA STEELS PRIVATE LIMITED", "cin": "U24100GJ2010PTC000001", "ra": "RA000394",
        "e_status": "ACTIVE", "r_status": "ISSUED", "r_init": "2015-01-01", "r_update": "2025-01-01", "r_next": "2026-01-01",
        "region": "IN-GJ", "city": "SURAT", "pin": "395003", "hq_region": "IN-MH", "created": "2010-02-02", "form": "ABCD",
        "other": "OLD ONE|OLD TWO", "cat": "GENERAL"}


def test_slim_tolerates_missing_optional_blocks():
    d = {"attributes": {"lei": "L", "entity": {"legalAddress": None, "headquartersAddress": None, "legalName": None,
                                               "registeredAt": None, "legalForm": None, "otherNames": None},
                        "registration": {}}}
    s = gb.slim(d)
    assert s["name"] is None and s["ra"] is None and s["region"] is None and s["hq_region"] is None
    assert s["form"] is None and s["other"] == "" and s["cin"] is None


def test_slim_other_names_missing_name_key_is_empty_string():
    r = full_record()
    r["attributes"]["entity"]["otherNames"] = [{"name": "A"}, {}]
    assert gb.slim(r)["other"] == "A|"


# --------------------------------------------------------------------------- gleif_bulk.main
class Pager:
    """Fake session serving pages by URL; first request carries params (no cursor URL yet)."""

    def __init__(self, pages, bad=()):
        self.pages, self.calls, self.headers, self.bad = pages, [], {}, list(bad)

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if self.bad:
            return self.bad.pop(0)
        return Resp(200, self.pages[url])


@pytest.fixture
def gbase(tmp_path, monkeypatch):
    monkeypatch.setattr(gb, "BASE", tmp_path)
    monkeypatch.setattr(gb, "RAW", tmp_path / "gleif_india.jsonl")
    monkeypatch.setattr(gb, "STATE", tmp_path / "gleif_india.cursor")
    monkeypatch.setattr(gb.time, "sleep", lambda s: None)
    return tmp_path


def run_gb(monkeypatch, session, *argv):
    monkeypatch.setattr(sys, "argv", ["gleif_bulk.py", *argv])
    monkeypatch.setattr(gb.requests, "Session", lambda: session)
    gb.main()


def test_main_pages_until_no_next_and_cleans_cursor(gbase, monkeypatch, capsys):
    pages = {gb.U: {"data": [full_record(lei="L1")], "links": {"next": "http://n/2"}},
             "http://n/2": {"data": [full_record(lei="L2"), full_record(lei="L3")], "links": {}}}
    s = Pager(pages)
    run_gb(monkeypatch, s)
    rows = [json.loads(l) for l in (gbase / "gleif_india.jsonl").read_text().splitlines()]
    assert [r["lei"] for r in rows] == ["L1", "L2", "L3"]
    assert s.calls[0][1] == {"filter[entity.legalAddress.country]": "IN", "page[size]": 200, "page[cursor]": "*"}
    assert s.calls[1][0] == "http://n/2" and s.calls[1][1] is None
    assert not (gbase / "gleif_india.cursor").exists()
    assert "done: 3 records" in capsys.readouterr().out
    assert s.headers["Accept"] == "application/vnd.api+json"


def test_main_stops_on_empty_page_even_with_next_link(gbase, monkeypatch, capsys):
    pages = {gb.U: {"data": [], "links": {"next": "http://n/loop"}}}
    run_gb(monkeypatch, Pager(pages))
    assert "done: 0 records" in capsys.readouterr().out
    # next link is stored first, then the run finishes and removes the cursor
    assert not (gbase / "gleif_india.cursor").exists()


def test_main_resumes_from_cursor_and_counts_existing_rows(gbase, monkeypatch, capsys):
    (gbase / "gleif_india.jsonl").write_text(json.dumps({"lei": "OLD"}) + "\n")
    (gbase / "gleif_india.cursor").write_text("http://n/3\n")
    s = Pager({"http://n/3": {"data": [full_record(lei="L9")], "links": {}}})
    run_gb(monkeypatch, s)
    assert s.calls[0][0] == "http://n/3" and s.calls[0][1] is None
    rows = (gbase / "gleif_india.jsonl").read_text().splitlines()
    assert len(rows) == 2 and json.loads(rows[0])["lei"] == "OLD"           # appended, not truncated
    assert "done: 2 records" in capsys.readouterr().out


def test_main_retries_429_and_5xx_then_succeeds(gbase, monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr(gb.time, "sleep", sleeps.append)
    s = Pager({gb.U: {"data": [full_record(lei="L1")], "links": {}}}, bad=[Resp(429), Resp(503)])
    run_gb(monkeypatch, s)
    assert sleeps[:2] == [10, 20]
    assert len(s.calls) == 3
    assert "done: 1 records" in capsys.readouterr().out


def test_main_http_error_below_500_raises(gbase, monkeypatch):
    with pytest.raises(RuntimeError, match="HTTP 404"):
        run_gb(monkeypatch, Pager({}, bad=[Resp(404)]))


def test_main_rps_controls_sleep_between_pages(gbase, monkeypatch):
    sleeps = []
    monkeypatch.setattr(gb.time, "sleep", sleeps.append)
    pages = {gb.U: {"data": [full_record()], "links": {"next": "http://n/2"}}, "http://n/2": {"data": [full_record(lei="L2")], "links": {}}}
    run_gb(monkeypatch, Pager(pages), "--rps", "4")
    assert sleeps == [0.25]


def test_main_progress_line_every_20_pages(gbase, monkeypatch, capsys):
    pages = {gb.U: {"data": [full_record(lei="L0")], "links": {"next": "http://n/1"}}}
    for i in range(1, 20):
        pages[f"http://n/{i}"] = {"data": [full_record(lei=f"L{i}")], "links": {"next": f"http://n/{i + 1}"} if i < 19 else {}}
    run_gb(monkeypatch, Pager(pages))
    out = capsys.readouterr().out
    assert "20 records, 20 pages" in out and "done: 20 records" in out


def test_main_parquet_dedups_and_summarises(gbase, monkeypatch, capsys):
    rows = [gb.slim(full_record(lei="L1")), gb.slim(full_record(lei="L1")),
            {**gb.slim(full_record(lei="L2")), "ra": "RA000999", "e_status": "INACTIVE"}]
    (gbase / "gleif_india.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    run_gb(monkeypatch, Pager({}), "--parquet")
    df = pd.read_parquet(gbase / "gleif_india.parquet")
    assert sorted(df.lei) == ["L1", "L2"]
    out = capsys.readouterr().out
    assert "2 records; 1 with an MCA CIN; {" in out and "'ACTIVE': 1" in out and "'INACTIVE': 1" in out


# --------------------------------------------------------------------------- ogd_fetch_master
@pytest.fixture
def ogd_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ogd.time, "sleep", lambda s: None)
    out = tmp_path / "m.csv"

    def go(handler, *argv, key="K"):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append((url, params, timeout))
            return handler(params)
        monkeypatch.setattr(ogd.requests, "get", fake_get)
        monkeypatch.setattr(sys, "argv", ["ogd_fetch_master.py", "--out", str(out), *argv])
        if key is None:
            monkeypatch.delenv("OGD_KEY", raising=False)
        else:
            monkeypatch.setenv("OGD_KEY", key)
        ogd.main()
        return calls
    go.out = out
    return go


def pages_by_state(table, size):
    """table: state -> list of records; serves slices by offset/limit."""
    def h(params):
        st, off, lim = params["filters[company_state_code]"], params["offset"], params["limit"]
        return Resp(200, {"records": table.get(st, [])[off:off + lim]})
    return h


def test_ogd_requires_key(ogd_run):
    with pytest.raises(SystemExit) as e:
        ogd_run(pages_by_state({}, 1), "--states", "goa", key=None)
    assert "OGD_KEY" in str(e.value.code)


def test_ogd_pages_through_a_state_and_tags_rows(ogd_run, capsys):
    table = {"goa": [{"cin": f"C{i}", "name": f"N{i}"} for i in range(5)]}
    calls = ogd_run(pages_by_state(table, 2), "--states", "goa", "--page", "2")
    rows = list(csv.DictReader(open(ogd_run.out)))
    assert [r["cin"] for r in rows] == ["C0", "C1", "C2", "C3", "C4"]
    assert {r["_state"] for r in rows} == {"goa"}
    assert [c[1]["offset"] for c in calls] == [0, 2, 4, 6]            # stops on the first empty page
    assert calls[0][1] == {"api-key": "K", "format": "json", "limit": 2, "offset": 0, "filters[company_state_code]": "goa"}
    assert calls[0][0] == ogd.URL and calls[0][2] == 60
    assert "goa 5 rows" in capsys.readouterr().out


def test_ogd_states_list_is_trimmed_and_all_states_by_default(ogd_run):
    calls = ogd_run(pages_by_state({}, 1), "--states", " goa , assam ")
    assert [c[1]["filters[company_state_code]"] for c in calls] == ["goa", "assam"]
    calls = ogd_run(pages_by_state({}, 1))
    assert [c[1]["filters[company_state_code]"] for c in calls] == ogd.STATES


def test_ogd_resume_skips_states_already_in_file(ogd_run, capsys):
    ogd_run(pages_by_state({"goa": [{"cin": "C1"}]}, 1), "--states", "goa")
    capsys.readouterr()
    calls = ogd_run(pages_by_state({"goa": [{"cin": "C1"}], "assam": [{"cin": "C2"}]}, 1), "--states", "goa,assam")
    assert {c[1]["filters[company_state_code]"] for c in calls} == {"assam"}
    assert "skip goa" in capsys.readouterr().out
    rows = list(csv.DictReader(open(ogd_run.out)))
    assert [r["cin"] for r in rows] == ["C1", "C2"]                      # single header, appended
    assert open(ogd_run.out).read().count("cin") == 1


def test_ogd_http_error_stops_that_state_and_moves_on(ogd_run, capsys):
    def h(params):
        if params["filters[company_state_code]"] == "goa":
            return Resp(500, text="boom")
        return Resp(200, {"records": [{"cin": "C2"}]} if params["offset"] == 0 else {"records": []})
    ogd_run(h, "--states", "goa,assam")
    out = capsys.readouterr().out
    assert "goa HTTP 500 boom" in out and "goa 0 rows" in out and "assam 1 rows" in out


def test_ogd_filter_field_option(ogd_run):
    calls = ogd_run(lambda p: Resp(200, {"records": []}), "--states", "goa", "--filter-field", "state_name")
    assert "filters[state_name]" in calls[0][1]


def test_ogd_extra_fields_in_later_pages_are_ignored(ogd_run):
    table = {"goa": [{"cin": "C1"}, {"cin": "C2", "surprise": "x"}]}
    ogd_run(pages_by_state(table, 1), "--states", "goa", "--page", "1")
    assert list(csv.DictReader(open(ogd_run.out)))[1]["cin"] == "C2"
