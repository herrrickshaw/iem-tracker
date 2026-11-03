"""Unit tests for the two-tier CIN/IEM state rule and the offline registry decision (no network)."""
import subprocess
import sys
from pathlib import Path

import iem_verify as v
import registry_match as r

ROOT = Path(__file__).resolve().parent.parent


def test_tier1_when_cin_state_equals_plant_state():
    assert v.web_match_tier("U12345MH2001PTC123456", "ABC PRIVATE LIMITED", "MAHARASHTRA") == v.TIER1


def test_tier2_when_state_differs_but_company_class_agrees():
    assert v.web_match_tier("U12345MH2001PTC123456", "ABC PVT LTD", "GUJARAT") == v.TIER2


def test_rejected_when_state_differs_and_class_disagrees():
    assert v.web_match_tier("U12345MH2001OPC123456", "ABC PVT LTD", "GUJARAT") is None


def test_parked_when_iem_state_unknown():
    assert v.web_match_tier("U12345MH2001PTC123456", "ABC PVT LTD", None) == v.PARKED


def test_decide_prefers_same_state_and_flags_state_mismatch():
    rec = {"company_name": "ABC PRIVATE LIMITED", "corporate_identification_number": "U12345MH2001PTC123456",
           "company_status": "Active"}
    assert r.decide([rec], ["ABC PRIVATE LIMITED"], "MAHARASHTRA")[0] == "MATCHED"
    assert r.decide([rec], ["ABC PRIVATE LIMITED"], "GUJARAT")[0] == "STATE_MISMATCH"
    assert r.decide([], ["ABC PRIVATE LIMITED"], None)[0] == "UNMATCHED"


def test_decide_ambiguous_without_state():
    recs = [{"company_name": "ABC LIMITED", "cin": f"U12345{s}2001PLC123456"} for s in ("MH", "GJ")]
    assert r.decide(recs, ["ABC LIMITED"], None)[0] == "AMBIGUOUS"


def test_cli_help_smoke():
    out = subprocess.run([sys.executable, str(ROOT / "iem"), "help"], capture_output=True, text=True, check=True).stdout
    assert "iem status" in out
