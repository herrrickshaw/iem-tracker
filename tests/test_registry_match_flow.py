"""registry_match: pick / query / decide / load_names / main with a fake HTTP session and tmp_path DuckDB.

The data.gov.in API is unreachable from the author's network, so every request is answered by a fake.
"""
import sys

import duckdb
import pandas as pd
import pytest
import requests

import registry_match as rm

CIN_GJ = "U24100GJ2010PTC000001"
CIN_MH = "U24100MH2010PTC000002"
CIN_KA = "U24100KA2010PTC000003"


def rec(name, cin, status="Active", klass="Private", reg="2010-01-01"):
    return {"company_name": name, "corporate_identification_number": cin, "company_status": status,
            "company_class": klass, "date_of_registration": reg}


# --------------------------------------------------------------------------- pick
def test_pick_matches_substring_case_insensitively_in_dict_order():
    r = {"Company_Name": "A", "Registered_Office_Name": "B"}
    assert rm.pick(r, "name") == "A"                       # first key containing the needle wins
    assert rm.pick(r, "office") == "B"


def test_pick_any_needle_and_missing():
    assert rm.pick({"x_cin": "C"}, "identification", "cin") == "C"
    assert rm.pick({"a": 1}, "zzz") is None
    assert rm.pick({}, "a") is None


def test_pick_returns_falsy_value_of_first_matching_key():
    assert rm.pick({"status": "", "company_status": "Active"}, "status") == ""


# --------------------------------------------------------------------------- decide
V = ["ALPHA STEELS PRIVATE LIMITED"]


def test_decide_single_match():
    out = rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ)], V, "GUJARAT")
    assert out == ("MATCHED", CIN_GJ, "Active", "Private", "2010-01-01", "ALPHA STEELS PRIVATE LIMITED")


def test_decide_unmatched_when_no_records_or_names_differ_or_no_cin():
    none = ("UNMATCHED", None, None, None, None, None)
    assert rm.decide([], V, None) == none
    assert rm.decide([rec("ALPHA STEEL PRIVATE LIMITED", CIN_GJ)], V, None) == none
    assert rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", "")], V, None) == none
    assert rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", None)], V, None) == none


def test_decide_ignores_punctuation_case_and_spacing_in_returned_name():
    out = rm.decide([rec("Alpha  Steels, Private Limited.", CIN_GJ)], V, None)
    assert out[0] == "MATCHED" and out[5] == "ALPHA STEELS PRIVATE LIMITED"


def test_decide_accepts_any_variant():
    out = rm.decide([rec("A AND B MILLS LIMITED", CIN_GJ)], ["A & B MILLS LIMITED", "A AND B MILLS LIMITED"], None)
    assert out[0] == "MATCHED"


def test_decide_state_filters_to_same_state_hit():
    out = rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", CIN_MH), rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ)], V, "GUJARAT")
    assert out[0] == "MATCHED" and out[1] == CIN_GJ


def test_decide_state_mismatch_reports_first_hit():
    out = rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", CIN_MH, status="Strike Off"),
                     rec("ALPHA STEELS PRIVATE LIMITED", CIN_KA)], V, "GUJARAT")
    assert out == ("STATE_MISMATCH", CIN_MH, "Strike Off", "Private", "2010-01-01", "ALPHA STEELS PRIVATE LIMITED")


def test_decide_state_mismatch_not_raised_without_hits():
    assert rm.decide([rec("OTHER NAME PRIVATE LIMITED", CIN_MH)], V, "GUJARAT")[0] == "UNMATCHED"


def test_decide_ambiguous_without_state():
    out = rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", CIN_MH), rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ)], V, None)
    assert out[0] == "AMBIGUOUS" and out[1] == CIN_MH


def test_decide_ambiguous_when_two_cins_share_the_state():
    c2 = "U24100GJ2012PTC000009"
    out = rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ), rec("ALPHA STEELS PRIVATE LIMITED", c2)], V, "GUJARAT")
    assert out[0] == "AMBIGUOUS"


def test_decide_same_cin_twice_is_not_ambiguous():
    out = rm.decide([rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ), rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ)], V, None)
    assert out[0] == "MATCHED"


def test_decide_reads_alternative_field_names():
    r = {"name": "ALPHA STEELS PRIVATE LIMITED", "cin": CIN_GJ, "status": "Active", "class": "P", "registration_date": "x"}
    assert rm.decide([r], V, None) == ("MATCHED", CIN_GJ, "Active", "P", "x", "ALPHA STEELS PRIVATE LIMITED")


# --------------------------------------------------------------------------- query
class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses=None, fn=None):
        self.responses, self.fn, self.calls, self.headers = list(responses or []), fn, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.fn(url, params) if self.fn else self.responses.pop(0)


@pytest.fixture
def sleeps(monkeypatch):
    got = []
    monkeypatch.setattr(rm.time, "sleep", got.append)
    return got


def test_query_request_shape_and_records(sleeps):
    s = FakeSession([Resp(200, {"records": [{"a": 1}]})])
    assert rm.query(s, "http://h", "KEY", "ALPHA") == [{"a": 1}]
    url, params, timeout = s.calls[0]
    assert url == f"http://h/resource/{rm.RES}"
    assert params == {"api-key": "KEY", "format": "json", "limit": 5, "filters[company_name]": "ALPHA"}
    assert timeout == 45
    assert sleeps == []


def test_query_limit_argument_passed_through(sleeps):
    s = FakeSession([Resp(200, {"records": []})])
    rm.query(s, "http://h", "K", "N", limit=9)
    assert s.calls[0][1]["limit"] == 9


def test_query_missing_records_key_is_empty_list(sleeps):
    assert rm.query(FakeSession([Resp(200, {})]), "http://h", "K", "N") == []


def test_query_retries_429_with_growing_backoff(sleeps):
    s = FakeSession([Resp(429), Resp(429), Resp(200, {"records": [1]})])
    assert rm.query(s, "http://h", "K", "N") == [1]
    assert sleeps == [5, 10]
    assert len(s.calls) == 3


def test_query_gives_up_after_four_429s(sleeps):
    s = FakeSession([Resp(429)] * 4)
    with pytest.raises(RuntimeError, match="rate-limited"):
        rm.query(s, "http://h", "K", "N")
    assert sleeps == [5, 10, 15, 20]
    assert len(s.calls) == 4


def test_query_http_error_is_raised_not_retried(sleeps):
    s = FakeSession([Resp(500)])
    with pytest.raises(requests.HTTPError):
        rm.query(s, "http://h", "K", "N")
    assert len(s.calls) == 1 and sleeps == []


# --------------------------------------------------------------------------- load_names
COLS = ["month", "company_name", "name_has_suffix", "district_state", "source_file"]


def write_a(base, coords=None, text=None):
    if coords is not None:
        pd.DataFrame(coords, columns=COLS).to_parquet(base / "partA_coords.parquet")
    if text is not None:
        pd.DataFrame(text, columns=COLS).to_parquet(base / "partA_text_layout.parquet")


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "BASE", tmp_path)
    monkeypatch.setattr(rm, "DB", tmp_path / "registry.duckdb")
    return tmp_path


def test_load_names_groups_by_key_and_state_and_counts(base):
    write_a(base, coords=[
        ("2018-01", "ALPHA STEELS PVT LTD", True, "PUNE MAHARASHTRA", "f1.pdf"),
        ("2020-05", "ALPHA STEELS PRIVATE LIMITED", True, "PUNE MAHARASHTRA", "f2.pdf"),
        ("2019-02", "ALPHA STEELS PVT LTD", True, "SURAT GUJARAT", "f3.pdf"),
    ])
    g = rm.load_names(2015)
    assert len(g) == 2
    mh = g[g.state == "MAHARASHTRA"].iloc[0]
    assert (mh.k, mh.n, mh.last_month, mh["name"]) == ("ALPHA STEELS", 2, "2020-05", "ALPHA STEELS PRIVATE LIMITED")


def test_load_names_since_boundary_is_inclusive(base):
    write_a(base, coords=[
        ("2014-12", "ALPHA STEELS LIMITED", True, "GOA", "f.pdf"),
        ("2015-01", "BETA CEMENT LIMITED", True, "GOA", "f.pdf"),
    ])
    assert list(rm.load_names(2015)["name"]) == ["BETA CEMENT LIMITED"]
    assert len(rm.load_names(2014)) == 2


def test_load_names_drops_unusable_names(base):
    write_a(base, coords=[
        ("2020-01", "STEEL PRIVATE LIMITED", True, "GOA", "f.pdf"),          # bare generic
        ("2020-01", "ALPHA STEELS LIMITED", False, "GOA", "f.pdf"),          # not confident
        ("2020-01", "GAMMA GLASS LIMITED", True, "GOA", "f.pdf"),
    ])
    assert list(rm.load_names(2015)["name"]) == ["GAMMA GLASS LIMITED"]


def test_load_names_text_layout_2023_files_excluded(base):
    write_a(base,
            coords=[("2020-01", "ALPHA STEELS LIMITED", True, "GOA", "2020-01_a.pdf")],
            text=[("2023-02", "BETA CEMENT LIMITED", True, "GOA", "2023-02_cum.pdf"),
                  ("2012-02", "GAMMA GLASS LIMITED", True, "GOA", "2012-02_t.pdf")])
    got = set(rm.load_names(2010)["name"])
    assert got == {"ALPHA STEELS LIMITED", "GAMMA GLASS LIMITED"}


def test_load_names_works_with_only_text_layout_file(base):
    write_a(base, text=[("2012-02", "GAMMA GLASS LIMITED", True, "GOA", "2012-02_t.pdf")])
    assert list(rm.load_names(2010)["name"]) == ["GAMMA GLASS LIMITED"]


def test_load_names_sorted_newest_then_most_filings(base):
    write_a(base, coords=[
        ("2019-01", "ALPHA STEELS LIMITED", True, "GOA", "f.pdf"),
        ("2021-01", "BETA CEMENT LIMITED", True, "GOA", "f.pdf"),
        ("2021-01", "GAMMA GLASS LIMITED", True, "GOA", "f.pdf"),
        ("2020-01", "GAMMA GLASS LIMITED", True, "GOA", "f.pdf"),
    ])
    assert list(rm.load_names(2015)["name"]) == ["GAMMA GLASS LIMITED", "BETA CEMENT LIMITED", "ALPHA STEELS LIMITED"]


def test_load_names_keeps_state_less_companies(base):
    write_a(base, coords=[("2020-01", "ALPHA STEELS LIMITED", True, "NOWHERE", "f.pdf")])
    g = rm.load_names(2015)
    assert len(g) == 1 and pd.isna(g.state.iloc[0])


# --------------------------------------------------------------------------- main
@pytest.fixture
def run_main(base, monkeypatch, sleeps):
    def go(*argv, session=None, key="K"):
        monkeypatch.setattr(sys, "argv", ["registry_match.py", "--sleep", "0", *argv])
        if key is None:
            monkeypatch.delenv("OGD_KEY", raising=False)
        else:
            monkeypatch.setenv("OGD_KEY", key)
        if session is not None:
            monkeypatch.setattr(rm.requests, "Session", lambda: session)
        rm.main()
    return go


def db_rows(base):
    c = duckdb.connect(str(base / "registry.duckdb"))
    out = {r[0]: r for r in c.execute("select name, verdict, cin, state, last_month, k from reg").fetchall()}
    c.close()
    return out


def answer(table):
    """table: variant name -> list of records."""
    return lambda url, params: Resp(200, {"records": table.get(params["filters[company_name]"], [])})


def two_companies(base):
    write_a(base, coords=[
        ("2021-01", "ALPHA STEELS PVT LTD", True, "SURAT GUJARAT", "f.pdf"),
        ("2020-01", "BETA CEMENT LTD", True, "PUNE MAHARASHTRA", "f.pdf"),
    ])


def test_main_requires_ogd_key(base, run_main):
    two_companies(base)
    with pytest.raises(SystemExit) as e:
        run_main(key=None)
    assert "OGD_KEY" in str(e.value.code)


def test_main_classifies_and_caches(base, run_main, capsys):
    two_companies(base)
    s = FakeSession(fn=answer({"ALPHA STEELS PRIVATE LIMITED": [rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ)]}))
    run_main(session=s)
    rows = db_rows(base)
    assert rows["ALPHA STEELS PVT LTD"][1:3] == ("MATCHED", CIN_GJ)
    assert rows["BETA CEMENT LTD"][1:3] == ("UNMATCHED", None)
    assert rows["ALPHA STEELS PVT LTD"][3] == "GUJARAT" and rows["ALPHA STEELS PVT LTD"][4] == "2021-01"
    out = capsys.readouterr().out
    assert "2 usable distinct companies since 2015; 0 already done; querying 2" in out
    assert s.headers["User-Agent"] == "Mozilla/5.0"
    assert all(c[1]["api-key"] == "K" for c in s.calls)


def test_main_resume_skips_done_companies(base, run_main, capsys):
    two_companies(base)
    run_main(session=FakeSession(fn=answer({})))
    capsys.readouterr()
    s2 = FakeSession(fn=answer({}))
    run_main(session=s2)
    assert s2.calls == []
    assert "2 already done; querying 0" in capsys.readouterr().out


def test_main_limit_caps_work_newest_first(base, run_main):
    two_companies(base)
    run_main("--limit", "1", session=FakeSession(fn=answer({})))
    assert list(db_rows(base)) == ["ALPHA STEELS PVT LTD"]          # 2021 filing sorts before 2020


def test_main_since_filters_old_filings(base, run_main, capsys):
    two_companies(base)
    run_main("--since", "2021", session=FakeSession(fn=answer({})))
    assert list(db_rows(base)) == ["ALPHA STEELS PVT LTD"]
    assert "since 2021" in capsys.readouterr().out


def test_main_tries_next_variant_only_when_first_is_empty(base, run_main):
    write_a(base, coords=[("2021-01", "THE A & B MILLS LTD", True, "SURAT GUJARAT", "f.pdf")])
    s = FakeSession(fn=answer({"THE A AND B MILLS LIMITED": [rec("THE A AND B MILLS LIMITED", CIN_GJ)]}))
    run_main(session=s)
    asked = [c[1]["filters[company_name]"] for c in s.calls]
    assert asked == ["THE A & B MILLS LIMITED", "THE A AND B MILLS LIMITED"]      # stops after the second variant hits
    assert db_rows(base)["THE A & B MILLS LTD"][1] == "MATCHED"


def test_main_connection_error_exits_with_hint(base, run_main):
    two_companies(base)

    def boom(url, params):
        raise requests.exceptions.ConnectionError("refused")
    with pytest.raises(SystemExit) as e:
        run_main(session=FakeSession(fn=boom), )
    assert "cannot reach https://api.data.gov.in" in str(e.value.code)
    assert "ConnectionError" in str(e.value.code)


def test_main_base_url_option(base, run_main):
    two_companies(base)
    s = FakeSession(fn=answer({}))
    run_main("--base-url", "http://localhost:9", "--limit", "1", session=s)
    assert s.calls[0][0].startswith("http://localhost:9/resource/")


def test_main_state_less_company_is_stored_with_empty_state(base, run_main):
    write_a(base, coords=[("2021-01", "ALPHA STEELS LTD", True, "NOWHERE", "f.pdf")])
    run_main(session=FakeSession(fn=answer({"ALPHA STEELS LIMITED": [rec("ALPHA STEELS LIMITED", CIN_GJ)]})))
    r = db_rows(base)["ALPHA STEELS LTD"]
    assert r[1] == "MATCHED" and r[3] == ""


def test_main_export_writes_csv_and_summary(base, run_main, capsys):
    two_companies(base)
    run_main(session=FakeSession(fn=answer({"ALPHA STEELS PRIVATE LIMITED": [rec("ALPHA STEELS PRIVATE LIMITED", CIN_GJ)]})))
    capsys.readouterr()
    run_main("--export", key=None)                 # export needs no key and no network
    out = capsys.readouterr().out
    assert "wrote" in out and "IEM_registry_matches.csv" in out
    df = pd.read_csv(base / "IEM_registry_matches.csv")
    assert set(df.verdict) == {"MATCHED", "UNMATCHED"}
    assert len(df) == 2
