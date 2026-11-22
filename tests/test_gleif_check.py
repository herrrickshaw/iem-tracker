"""gleif_check: cache, query, lookup and both CLI modes against a fake GLEIF API (no network)."""
import csv
import sys

import duckdb
import pandas as pd
import pytest
import requests

import gleif_check as gc


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data if data is not None else []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return {"data": self._data}


def lei(cin, name="X LIMITED", ra="RA000394", status="ACTIVE", reg="ISSUED", region="IN-GJ"):
    return {"attributes": {"entity": {"registeredAt": {"id": ra}, "registeredAs": cin, "status": status,
                                      "legalName": {"name": name}, "legalAddress": {"region": region}},
                           "registration": {"status": reg}}}


class FakeSession:
    def __init__(self, responses=None, fn=None):
        self.responses, self.fn, self.calls, self.headers = list(responses or []), fn, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.fn(params["filter[entity.legalName]"]) if self.fn else self.responses.pop(0)


@pytest.fixture
def sleeps(monkeypatch):
    got = []
    monkeypatch.setattr(gc.time, "sleep", got.append)
    return got


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "BASE", tmp_path)
    monkeypatch.setattr(gc, "CACHE", tmp_path / "gleif_cache.csv")
    return tmp_path


CIN = "U24100GJ2010PTC000001"
CIN2 = "U24100MH2010PTC000002"


# --------------------------------------------------------------------------- REGION table
@pytest.mark.parametrize("region,state", [("IN-OD", "ODISHA"), ("IN-OR", "ODISHA"), ("IN-TG", "TELANGANA"), ("IN-TS", "TELANGANA"),
                                          ("IN-UT", "UTTARAKHAND"), ("IN-UK", "UTTARAKHAND"), ("IN-GJ", "GUJARAT"),
                                          ("IN-JK", "JAMMU"), ("IN-PY", "PUDUCHERRY")])
def test_region_aliases(region, state):
    assert gc.REGION[region] == state


def test_region_values_are_names_find_state_can_produce():
    import iem_verify as iv
    assert set(gc.REGION.values()) <= set(iv.STATES)


# --------------------------------------------------------------------------- load_cache
def test_load_cache_missing_file_is_empty(base):
    assert gc.load_cache() == {}


def test_load_cache_keys_by_query(base):
    (base / "gleif_cache.csv").write_text("q,found,cin\nALPHA,1,C1\nBETA,0,\n")
    c = gc.load_cache()
    assert set(c) == {"ALPHA", "BETA"} and c["ALPHA"]["cin"] == "C1" and c["BETA"]["found"] == "0"


# --------------------------------------------------------------------------- query
def test_query_request_shape(sleeps):
    s = FakeSession([Resp(200, [1])])
    assert gc.query(s, "ALPHA") == [1]
    url, params, timeout = s.calls[0]
    assert url == gc.API and timeout == 40
    assert params == {"filter[entity.legalName]": "ALPHA", "filter[entity.legalAddress.country]": "IN", "page[size]": 5}


def test_query_backoff_on_429(sleeps):
    s = FakeSession([Resp(429), Resp(429), Resp(429), Resp(200, [7])])
    assert gc.query(s, "A") == [7]
    assert sleeps == [8, 16, 24]


def test_query_gives_up_after_four(sleeps):
    with pytest.raises(RuntimeError, match="GLEIF rate limit"):
        gc.query(FakeSession([Resp(429)] * 4), "A")
    assert sleeps == [8, 16, 24, 32]


def test_query_http_error_propagates(sleeps):
    with pytest.raises(requests.HTTPError):
        gc.query(FakeSession([Resp(503)]), "A")


# --------------------------------------------------------------------------- lookup
def test_lookup_found_writes_cache_with_header_once(base, sleeps):
    s = FakeSession(fn=lambda name: Resp(200, [lei(CIN, name="ALPHA STEELS PRIVATE LIMITED", region="IN-GJ")]))
    cache = {}
    r = gc.lookup(s, cache, "Alpha Steels Pvt Ltd")
    assert r["found"] == "1" and r["cin"] == CIN and r["status"] == "ACTIVE" and r["lei_status"] == "ISSUED"
    assert r["legal_name"] == "ALPHA STEELS PRIVATE LIMITED" and r["region"] == "IN-GJ"
    assert r["q"] == "ALPHA STEELS PRIVATE LIMITED" and len(s.calls) == 1        # stops at the first variant that hits
    assert sleeps == [1.0]
    lines = (base / "gleif_cache.csv").read_text().splitlines()
    assert lines[0].split(",")[:3] == ["q", "found", "cin"] and len(lines) == 2


def test_lookup_second_call_for_different_name_appends_without_second_header(base, sleeps):
    s = FakeSession(fn=lambda name: Resp(200, [lei(CIN)]))
    cache = {}
    gc.lookup(s, cache, "Alpha Steels Pvt Ltd"); gc.lookup(s, cache, "Beta Cement Ltd")
    lines = (base / "gleif_cache.csv").read_text().splitlines()
    assert len(lines) == 3 and lines.count(lines[0]) == 1


def test_lookup_uses_cache_without_http(base, sleeps):
    cache = {"ALPHA STEELS PRIVATE LIMITED": {"q": "ALPHA STEELS PRIVATE LIMITED", "found": "1", "cin": CIN}}
    s = FakeSession()
    assert gc.lookup(s, cache, "Alpha Steels Pvt Ltd")["cin"] == CIN
    assert s.calls == [] and sleeps == []
    assert not (base / "gleif_cache.csv").exists()


def test_lookup_not_found_tries_every_variant_and_returns_last(base, sleeps):
    s = FakeSession(fn=lambda name: Resp(200, []))
    r = gc.lookup(s, {}, "The A & B Mills Ltd")
    assert r["found"] == "0"
    assert [c[1]["filter[entity.legalName]"] for c in s.calls] == ["THE A & B MILLS LIMITED", "THE A AND B MILLS LIMITED", "A & B MILLS LIMITED"]
    assert r["q"] == "A & B MILLS LIMITED" and r["n"] == "0"


def test_lookup_not_found_is_cached_so_no_requery(base, sleeps):
    cache = {}
    s = FakeSession(fn=lambda name: Resp(200, []))
    gc.lookup(s, cache, "Alpha Steels Ltd")
    n = len(s.calls)
    gc.lookup(s, cache, "Alpha Steels Ltd")
    assert len(s.calls) == n


def test_lookup_skips_records_without_mca_registration(base, sleeps):
    data = [lei(CIN, ra="RA000001"), lei("", ra="RA000394"), lei(None, ra="RA000394")]
    r = gc.lookup(FakeSession(fn=lambda n: Resp(200, data)), {}, "Alpha Ltd")
    assert r["found"] == "0" and r["n"] == "3"


def test_lookup_prefers_issued_lei_over_lapsed(base, sleeps):
    data = [lei(CIN, reg="LAPSED"), lei(CIN2, reg="ISSUED")]
    r = gc.lookup(FakeSession(fn=lambda n: Resp(200, data)), {}, "Alpha Ltd")
    assert r["cin"] == CIN2


def test_lookup_keeps_last_matching_when_none_issued(base, sleeps):
    data = [lei(CIN, reg="LAPSED"), lei(CIN2, reg="RETIRED")]
    r = gc.lookup(FakeSession(fn=lambda n: Resp(200, data)), {}, "Alpha Ltd")
    assert r["cin"] == CIN2          # each later match overwrites; only ISSUED stops the scan


def test_lookup_stops_scanning_at_first_issued(base, sleeps):
    data = [lei(CIN, reg="ISSUED"), lei(CIN2, reg="ISSUED")]
    assert gc.lookup(FakeSession(fn=lambda n: Resp(200, data)), {}, "Alpha Ltd")["cin"] == CIN


def test_lookup_second_variant_found(base, sleeps):
    def fn(name):
        return Resp(200, [lei(CIN)] if name == "ALPHA LIMITED" else [])
    s = FakeSession(fn=fn)
    r = gc.lookup(s, {}, "Alpha Pvt Ltd")
    # variants: ALPHA PRIVATE LIMITED only (no &, no THE) -> not found
    assert r["found"] == "0"
    r = gc.lookup(FakeSession(fn=fn), {}, "Alpha Ltd")
    assert r["found"] == "1" and r["q"] == "ALPHA LIMITED"


# --------------------------------------------------------------------------- main
def make_db(base, rows):
    df = pd.DataFrame(rows, columns=["id", "name", "state", "cin", "status", "src", "usable", "in_part_b", "n_part_b"])
    k = duckdb.connect(str(base / "iem_verify.duckdb"))
    k.execute("create table co as select * from df"); k.close()


def run(monkeypatch, session, *argv):
    monkeypatch.setattr(sys, "argv", ["gleif_check.py", *argv])
    monkeypatch.setattr(gc.requests, "Session", lambda: session)
    gc.main()


def test_main_validate_reports_agreement_per_tier(base, sleeps, monkeypatch, capsys):
    make_db(base, [
        (1, "ALPHA STEELS PRIVATE LIMITED", "GUJARAT", CIN, "Active", "web-slug+state", True, False, 0),
        (2, "BETA CEMENT LIMITED", "MAHARASHTRA", CIN2, "Active", "web-slug-name-only", True, False, 0),
        (3, "IGNORED LIMITED", "GOA", None, None, None, True, False, 0),                 # no cin: not sampled
        (4, "UNUSABLE LIMITED", "GOA", "U24100GA2010PTC000009", "Active", "web-slug+state", False, False, 0),   # unusable
    ])
    table = {"ALPHA STEELS PRIVATE LIMITED": [lei(CIN, status="ACTIVE")],
             "BETA CEMENT LIMITED": [lei("U24100MH2010PTC000777", status="INACTIVE", region="IN-MH")]}
    run(monkeypatch, FakeSession(fn=lambda n: Resp(200, table.get(n, []))), "validate", "--n", "2")
    out = capsys.readouterr().out
    v = pd.read_csv(base / "gleif_validation.csv").set_index("name")
    assert set(v.index) == {"ALPHA STEELS PRIVATE LIMITED", "BETA CEMENT LIMITED"}
    assert bool(v.loc["ALPHA STEELS PRIVATE LIMITED", "g_found"]) is True
    assert v.loc["BETA CEMENT LIMITED", "g_cin"] == "U24100MH2010PTC000777"
    assert "sample 2: GLEIF holds 2 (100%)" in out
    assert any(l.split()[:1] == ["web-slug+state"] and "same CIN 100%" in l and "status agrees (ACTIVE vs Active) 100%" in l
               for l in out.splitlines())
    assert any(l.split()[:1] == ["web-slug-name-only"] and "same CIN 0%" in l and "status agrees (ACTIVE vs Active) 0%" in l
               for l in out.splitlines())           # GLEIF INACTIVE vs MCA Active: disagree
    assert "CIN disagreements" in out and "U24100MH2010PTC000777" in out


def test_main_validate_status_agreement_counts_disagreement(base, sleeps, monkeypatch, capsys):
    make_db(base, [
        (1, "ALPHA STEELS PRIVATE LIMITED", "GUJARAT", CIN, "Strike Off", "web-slug+state", True, False, 0),
        (2, "BETA CEMENT LIMITED", "MAHARASHTRA", CIN2, "Active", "web-slug-name-only", True, False, 0),
    ])
    table = {"ALPHA STEELS PRIVATE LIMITED": [lei(CIN, status="ACTIVE")],        # GLEIF ACTIVE, MCA Strike Off -> disagree
             "BETA CEMENT LIMITED": [lei(CIN2, status="ACTIVE")]}
    run(monkeypatch, FakeSession(fn=lambda n: Resp(200, table.get(n, []))), "validate", "--n", "2")
    out = capsys.readouterr().out
    t1 = next(l for l in out.splitlines() if l.split()[:1] == ["web-slug+state"])
    assert "same CIN 100%" in t1 and "status agrees (ACTIVE vs Active) 0%" in t1


def test_main_validate_counts_companies_gleif_does_not_hold(base, sleeps, monkeypatch, capsys):
    make_db(base, [
        (1, "ALPHA STEELS PRIVATE LIMITED", "GUJARAT", CIN, "Active", "web-slug+state", True, False, 0),
        (2, "BETA CEMENT LIMITED", "MAHARASHTRA", CIN2, "Active", "web-slug-name-only", True, False, 0),
    ])
    run(monkeypatch, FakeSession(fn=lambda n: Resp(200, [lei(CIN)] if n.startswith("ALPHA") else [])), "validate", "--n", "2")
    out = capsys.readouterr().out
    assert "sample 2: GLEIF holds 1 (50%)" in out
    assert "web-slug-name-only" not in out.split("CIN disagreements")[0].split("GLEIF holds")[1]


def test_main_identify_writes_found_companies_and_state_agreement(base, sleeps, monkeypatch, capsys):
    make_db(base, [
        (1, "ALPHA STEELS PRIVATE LIMITED", "GUJARAT", None, None, None, True, True, 5),       # GLEIF region agrees
        (2, "BETA CEMENT LIMITED", "KERALA", None, None, None, True, False, 1),                # state differs
        (3, "GAMMA GLASS LIMITED", "TAMIL NADU", None, None, None, True, False, 0),            # not in GLEIF
        (4, "DONE LIMITED", "GOA", CIN, "Active", "web", True, True, 9),                       # already identified
        (5, "UNUSABLE LIMITED", "GOA", None, None, None, False, True, 9),                      # unusable
    ])
    table = {"ALPHA STEELS PRIVATE LIMITED": [lei(CIN, region="IN-GJ", reg="ISSUED", name="ALPHA STEELS PRIVATE LIMITED")],
             "BETA CEMENT LIMITED": [lei(CIN2, region="IN-MH", reg="LAPSED", name="BETA CEMENT LIMITED")]}
    run(monkeypatch, FakeSession(fn=lambda n: Resp(200, table.get(n, []))), "identify", "--limit", "10")
    out = capsys.readouterr().out
    o = pd.read_csv(base / "gleif_identified.csv").set_index("name")
    assert set(o.index) == {"ALPHA STEELS PRIVATE LIMITED", "BETA CEMENT LIMITED"}
    assert bool(o.loc["ALPHA STEELS PRIVATE LIMITED", "state_agrees"]) is True
    assert bool(o.loc["BETA CEMENT LIMITED", "state_agrees"]) is False
    assert o.loc["BETA CEMENT LIMITED", "lei_status"] == "LAPSED"
    assert o.loc["ALPHA STEELS PRIVATE LIMITED", "gleif_name"] == "ALPHA STEELS PRIVATE LIMITED"
    assert "2 of 3 previously unidentified companies found in GLEIF; state agrees for 1" in out


def test_main_identify_limit_and_order_prefers_part_b(base, sleeps, monkeypatch, capsys):
    make_db(base, [
        (1, "ALPHA STEELS PRIVATE LIMITED", "GUJARAT", None, None, None, True, False, 99),
        (2, "BETA CEMENT LIMITED", "GUJARAT", None, None, None, True, True, 1),
        (3, "GAMMA GLASS LIMITED", "GUJARAT", None, None, None, True, True, 7),
    ])
    s = FakeSession(fn=lambda n: Resp(200, []))
    run(monkeypatch, s, "identify", "--limit", "2")
    asked = [c[1]["filter[entity.legalName]"] for c in s.calls]
    assert asked[0].startswith("GAMMA") and any(a.startswith("BETA") for a in asked) and not any(a.startswith("ALPHA") for a in asked)


def test_main_identify_nothing_found_writes_empty_and_reports_zero(base, sleeps, monkeypatch, capsys):
    make_db(base, [(1, "ALPHA STEELS PRIVATE LIMITED", "GUJARAT", None, None, None, True, False, 0)])
    run(monkeypatch, FakeSession(fn=lambda n: Resp(200, [])), "identify")
    assert "0 of 1 previously unidentified companies found in GLEIF; state agrees for 0" in capsys.readouterr().out


def test_main_identify_state_agrees_via_cin_state_code(base, sleeps, monkeypatch, capsys):
    """Region missing in GLEIF but the CIN's state code equals the plant state."""
    make_db(base, [(1, "ALPHA STEELS PRIVATE LIMITED", "GUJARAT", None, None, None, True, False, 0)])
    run(monkeypatch, FakeSession(fn=lambda n: Resp(200, [lei(CIN, region="IN-XX")])), "identify")
    assert bool(pd.read_csv(base / "gleif_identified.csv").state_agrees.iloc[0]) is True


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "gleif_check.py: `same_state = st is not None and REGION.get(...) == st or CIN_STATE.get(cin[6:8]) == st` -- `and` binds tighter "
    "than `or`, so when the plant state is unknown (st is None) and the CIN carries a state code absent from CIN_STATE "
    "(e.g. DD/DN/LD/AN), `None == None` is True and state_agrees is reported True for a company whose state is unknown."))
def test_main_identify_unknown_plant_state_must_not_agree(base, sleeps, monkeypatch, capsys):
    make_db(base, [(1, "ALPHA STEELS PRIVATE LIMITED", None, None, None, None, True, False, 0)])
    run(monkeypatch, FakeSession(fn=lambda n: Resp(200, [lei("U24100DN2010PTC000001", region="IN-DN")])), "identify")
    assert bool(pd.read_csv(base / "gleif_identified.csv").state_agrees.iloc[0]) is False
