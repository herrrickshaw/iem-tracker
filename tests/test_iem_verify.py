"""Tests for iem_verify.py: name keys, state parsing, CIN decoding, offline master match and the
two-tier web match (IEM state = plant, CIN state = registered office).

Everything is synthetic. The DuckDB file is redirected to tmp_path and `_fetch` (the only network
call, quickcompany.in) is replaced by a dict lookup, so no request leaves the machine.
"""
import json
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

import iem_verify as iv


def cin(state="GJ", year=2010, cls="PTC", nic="24100", serial="012345", listed="U"):
    c = f"{listed}{nic}{state}{year}{cls}{serial}"
    assert len(c) == 21 and iv.CIN_RE.fullmatch(c)
    return c


# --------------------------------------------------------------------------- pure helpers
def test_key_strips_legal_words_and_punctuation():
    assert iv.key("Shree Cement Pvt. Ltd.") == "SHREE CEMENT"
    assert iv.key("A & B Industries (India) Private Limited") == "A B"
    assert iv.key("The Alpha Co.") == "ALPHA"


def test_split_name_cuts_at_first_suffix():
    assert iv.split_name("Alpha Steels Pvt Ltd, Plot 4, Raigad") == ("ALPHA STEELS PVT LTD", True)
    assert iv.split_name("alpha beta gamma delta epsilon zeta") == ("ALPHA BETA GAMMA DELTA EPSILON", False)


@pytest.mark.parametrize("name,confident,ok", [
    ("HAVELLS INDIA LIMITED", True, True),
    ("STEEL PRIVATE LIMITED", True, False),          # bare generic word
    ("FOODS AND ENERGY LTD", True, False),
    ("ALPHA STEELS LIMITED", False, False),          # not confident
    ("AB LTD", True, False),                          # key too short
    ("X" * 91 + " LTD", True, False),                 # too long
])
def test_usable(name, confident, ok):
    assert iv.usable(name, confident) is ok


@pytest.mark.parametrize("text,state", [
    ("PUNE, MAHARASHTRA", "MAHARASHTRA"),
    ("Kutch Gujarat", "GUJARAT"),
    ("no state here", None),
])
def test_find_state_basic(text, state):
    assert iv.find_state(text) == state


@pytest.mark.parametrize("text", ["CUTTACK, ODISHA", "CUTTACK, ORISSA"])
def test_find_state_odisha_matches_cin_state_code(text):
    """Both spellings must normalise to the name CIN_STATE uses for code 'OR' (and GLEIF uses for IN-OR/IN-OD),
    otherwise tier 1 (CIN state == IEM state) can never succeed for an Odisha plant."""
    assert iv.find_state(text) == iv.CIN_STATE["OR"] == "ODISHA"


@pytest.mark.parametrize("code,state", [("ML", "MEGHALAYA"), ("TR", "TRIPURA"), ("AR", "ARUNACHAL PRADESH"),
                                        ("MN", "MANIPUR"), ("MZ", "MIZORAM"), ("NL", "NAGALAND"), ("SK", "SIKKIM")])
def test_cin_state_covers_every_state_find_state_can_return(code, state):
    """find_state() can return these states, so a CIN registered there must decode to the same name."""
    assert iv.find_state(f"SHILLONG, {state}") == state
    assert iv.CIN_STATE.get(code) == state


def test_slug_variants_suffix_first():
    assert iv.slug_variants("Alpha Steels Pvt Ltd") == [
        "alpha-steels-private-limited", "alpha-steels-limited", "alpha-steels-private-limited"]
    assert iv.slug_variants("Beta Cement Limited")[0] == "beta-cement-limited"


@pytest.mark.parametrize("status,hit,c,verdict", [
    (None, True, None, "UNMAPPED"),
    ("Strike Off", False, "C", "INACTIVE"),
    ("Amalgamated", True, "C", "INACTIVE"),
    ("Under Liquidation", False, "C", "INACTIVE"),
    ("Dormant", False, "C", "DORMANT"),
    ("Active", True, "C", "OPERATIONAL (active + Part B)"),
    ("Active", False, "C", "ACTIVE (registered, no Part B yet)"),
    (None, False, "C", "ACTIVE (registered, no Part B yet)"),
    ("Under process of striking off", False, "C", "INACTIVE"),
    ("Converted to LLP", False, "C", "CHECK"),
])
def test_classify(status, hit, c, verdict):
    assert iv.classify(status, hit, c) == verdict


def test_classify_inactive_is_not_active():
    # 'inactive' contains 'active' — the INACTIVE branch must win
    assert iv.classify("Inactive", True, "C") == "INACTIVE"


# --------------------------------------------------------------------------- DB-backed stages
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "DB", tmp_path / "iem_verify.duckdb")
    return tmp_path


def make_co(rows):
    """rows: dicts with name, state, last_iem (YYYY-MM), optional last_part_b."""
    df = pd.DataFrame([dict(id=i + 1, name=r["name"], key=iv.key(r["name"]), state=r.get("state"), confident=True,
                            usable=True, n_part_a=1, first_iem=r.get("last_iem", "2015-01"), last_iem=r.get("last_iem", "2015-01"),
                            item="", part_b_hit=False, in_part_b=False, n_part_b=0, last_part_b=r.get("last_part_b"),
                            cin=None, status=None, src=None, score=None, cand=None) for i, r in enumerate(rows)])
    k = iv.con()
    k.execute("""create or replace table co as select id, name, key, state, confident, usable, n_part_a, first_iem, last_iem,
                 item, part_b_hit, in_part_b, n_part_b, last_part_b::varchar last_part_b, cin::varchar cin, status::varchar status,
                 src::varchar src, score::double score, cand::varchar cand from df""")
    k.close()


def read_co():
    k = iv.con()
    out = {r[0]: dict(cin=r[1], src=r[2], cand=r[3]) for r in k.execute("select name, cin, src, cand from co").fetchall()}
    k.close()
    return out


def run_web(monkeypatch, pages):
    """pages: slug -> CIN served by the fake site."""
    calls = []

    def fake_fetch(slug, gate):
        calls.append(slug)
        return pages.get(slug), ("Active" if slug in pages else None), None, None

    monkeypatch.setattr(iv, "_fetch", fake_fetch)
    iv.stage_web(SimpleNamespace(candidates=False, partb=False, limit=None, rps=1000.0, workers=1))
    return calls


def test_web_tier1_same_state(db, monkeypatch):
    make_co([{"name": "ALPHA STEELS PRIVATE LIMITED", "state": "GUJARAT"}])
    run_web(monkeypatch, {"alpha-steels-private-limited": cin("GJ")})
    assert read_co()["ALPHA STEELS PRIVATE LIMITED"] == dict(cin=cin("GJ"), src="web-slug+state", cand=None)


def test_web_tier2_plant_state_differs_class_agrees(db, monkeypatch):
    """Plant in Gujarat, registered office in Maharashtra: accepted as tier 2 because PVT name ~ PTC CIN."""
    make_co([{"name": "ALPHA STEELS PRIVATE LIMITED", "state": "GUJARAT"}])
    run_web(monkeypatch, {"alpha-steels-private-limited": cin("MH", cls="PTC")})
    assert read_co()["ALPHA STEELS PRIVATE LIMITED"]["src"] == "web-slug-name-only"


def test_web_tier2_rejected_when_class_disagrees(db, monkeypatch):
    """Public 'LIMITED' name but a private (PTC) CIN in another state: different company, rejected."""
    make_co([{"name": "BETA CEMENT LIMITED", "state": "GUJARAT"}])
    run_web(monkeypatch, {"beta-cement-limited": cin("MH", cls="PTC")})
    assert read_co()["BETA CEMENT LIMITED"] == dict(cin=None, src=None, cand=None)


@pytest.mark.parametrize("name,slug,cls", [
    ("ALPHA STEELS PRIVATE LIMITED", "alpha-steels-private-limited", "FLC"),   # foreign PUBLIC subsidiary vs private name
    ("ALPHA STEELS PRIVATE LIMITED", "alpha-steels-private-limited", "GAP"),   # guarantee/association PUBLIC vs private name
    ("BETA CEMENT LIMITED", "beta-cement-limited", "FTC"),                     # foreign PRIVATE subsidiary vs public name
])
def test_web_tier2_foreign_and_guarantee_classes_must_agree_with_name(db, monkeypatch, name, slug, cls):
    """README: tier 2 is accepted 'only if the company type in the CIN (private or public) agrees with the name'.
    FTC is private, FLC and GAP are public, so they must not pass for the opposite name form."""
    make_co([{"name": name, "state": "GUJARAT"}])
    run_web(monkeypatch, {slug: cin("MH", cls=cls)})
    assert read_co()[name]["cin"] is None


def test_web_tier2_foreign_private_subsidiary_accepted_for_private_name(db, monkeypatch):
    make_co([{"name": "ALPHA STEELS PRIVATE LIMITED", "state": "GUJARAT"}])
    run_web(monkeypatch, {"alpha-steels-private-limited": cin("MH", cls="FTC")})
    assert read_co()["ALPHA STEELS PRIVATE LIMITED"]["src"] == "web-slug-name-only"


def test_web_no_state_is_parked_not_accepted(db, monkeypatch):
    make_co([{"name": "GAMMA POLYFILMS LIMITED", "state": None}])
    run_web(monkeypatch, {"gamma-polyfilms-limited": cin("GJ", cls="PLC")})
    r = read_co()["GAMMA POLYFILMS LIMITED"]
    assert r["cin"] is None and json.loads(r["cand"])[0]["cin"] == cin("GJ", cls="PLC")


def test_web_odisha_plant_with_odisha_cin_is_tier1(db, monkeypatch):
    make_co([{"name": "DELTA ALLOYS LIMITED", "state": iv.find_state("JAJPUR, ODISHA")}])
    run_web(monkeypatch, {"delta-alloys-limited": cin("OR", cls="PLC")})
    assert read_co()["DELTA ALLOYS LIMITED"]["src"] == "web-slug+state"


def test_web_rejects_cin_incorporated_after_last_filing(db, monkeypatch):
    """README rule 3: 'A match is rejected if the CIN shows incorporation after the filing year.'
    A company that filed its IEM in 2012 cannot be a CIN incorporated in 2019 (a later homonym)."""
    make_co([{"name": "ALPHA STEELS PRIVATE LIMITED", "state": "GUJARAT", "last_iem": "2012-05"}])
    run_web(monkeypatch, {"alpha-steels-private-limited": cin("GJ", year=2019)})
    assert read_co()["ALPHA STEELS PRIVATE LIMITED"]["cin"] is None


def test_web_incorporation_checked_against_latest_part_b(db, monkeypatch):
    make_co([{"name": "ALPHA STEELS PRIVATE LIMITED", "state": "GUJARAT", "last_iem": "2012-05", "last_part_b": "2020-01"}])
    run_web(monkeypatch, {"alpha-steels-private-limited": cin("GJ", year=2019)})
    assert read_co()["ALPHA STEELS PRIVATE LIMITED"]["cin"] == cin("GJ", year=2019)


def test_web_uses_cache_and_stops_at_first_hit(db, monkeypatch):
    make_co([{"name": "ALPHA STEELS PRIVATE LIMITED", "state": "GUJARAT"}])
    calls = run_web(monkeypatch, {"alpha-steels-limited": cin("GJ")})
    assert calls == ["alpha-steels-private-limited", "alpha-steels-limited"]
    # second run: company already has a CIN, nothing fetched
    assert run_web(monkeypatch, {}) == []


# --------------------------------------------------------------------------- offline master match
def test_master_and_match(db, monkeypatch, tmp_path):
    master = pd.DataFrame({
        "Corporate Identification Number": [cin("GJ", serial="000001"), cin("MH", serial="000002"), cin("KA", serial="000003"),
                                            cin("TN", serial="000004"), cin("GJ", serial="000005", cls="PLC")],
        "Company Name": ["ALPHA STEELS PRIVATE LIMITED", "OMEGA FABRICS PRIVATE LIMITED", "OMEGA FABRICS PRIVATE LIMITED",
                         "SIGMA GLASS LIMITED", "KAPPA FERTILISERS AND CHEMICALS LIMITED"],
        "Company Status": ["Active", "Active", "Strike Off", "Active", "Active"]})
    p = tmp_path / "master.csv"; master.to_csv(p, index=False)
    iv.stage_master(SimpleNamespace(master=str(p)))
    k = iv.con()
    assert dict(k.execute("select cin, state from mca_master").fetchall())[cin("MH", serial="000002")] == "MAHARASHTRA"
    k.close()
    make_co([{"name": "ALPHA STEELS PVT LTD", "state": "GUJARAT"},          # exact (state, key)
             {"name": "OMEGA FABRICS PVT LTD", "state": None},              # same name in 2 states, no state -> not identity
             {"name": "SIGMA GLASS LIMITED", "state": None},                # unique across states -> accepted
             {"name": "KAPPA FERTILIZERS AND CHEMICALS LTD", "state": "GUJARAT"}])  # fuzzy within bucket
    iv.stage_match(SimpleNamespace(limit=None))
    r = read_co()
    assert r["ALPHA STEELS PVT LTD"] == dict(cin=cin("GJ", serial="000001"), src="master-exact", cand=None)
    assert r["OMEGA FABRICS PVT LTD"]["cin"] is None
    assert r["SIGMA GLASS LIMITED"]["src"] == "master-exact"
    assert r["KAPPA FERTILIZERS AND CHEMICALS LTD"]["src"] == "master-fuzzy"
