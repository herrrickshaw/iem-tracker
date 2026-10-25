"""Proof tests for the 2026-10-25 static-analysis sweep (iem-tracker)."""
import sys

import pandas as pd

import gleif_check


class _Res:
    def __init__(self, df): self._df = df
    def df(self): return self._df


class _Con:
    def __init__(self, df): self._df = df
    def execute(self, sql): return _Res(self._df)


def test_identify_state_agrees_false_when_plant_state_unknown(tmp_path, monkeypatch):
    """RUF021 at gleif_check.py:104: `a and b or c` made state_agrees True for a company
    with NO plant state whenever the GLEIF CIN carried an unmapped state code
    (CIN_STATE.get(...) == None). A missing state can never 'agree'."""
    companies = pd.DataFrame({"id": [1], "name": ["ACME PVT LTD"], "state": [None], "in_part_b": [True]})
    monkeypatch.setattr(gleif_check.duckdb, "connect", lambda *a, **k: _Con(companies))
    monkeypatch.setattr(gleif_check, "load_cache", lambda: {})
    monkeypatch.setattr(gleif_check, "lookup", lambda sess, cache, name: dict(
        found="1", cin="U24100ZZ2010PTC012345", status="ACTIVE", lei_status="ISSUED",
        legal_name="ACME PRIVATE LIMITED", region="IN-GJ"))
    monkeypatch.setattr(gleif_check, "BASE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["gleif_check.py", "identify", "--limit", "5"])
    gleif_check.main()
    out = pd.read_csv(tmp_path / "gleif_identified.csv")
    assert len(out) == 1
    assert bool(out.state_agrees.iloc[0]) is False
