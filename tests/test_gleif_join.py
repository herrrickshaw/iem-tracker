"""gleif_join.main() on a synthetic GLEIF India register and company table (no GLEIF API call).

Verdicts: confirmed / cin_changed / conflict / new / ambiguous / none, plus the
incorporated-after-filing rejection and the state rule (GLEIF region or CIN state).
"""
import sys

import duckdb
import pandas as pd
import pytest

import gleif_join
import iem_verify as iv


def cin(state="GJ", year=2010, cls="PTC", serial="012345", listed="U"):
    return f"{listed}24100{state}{year}{cls}{serial}"


GLEIF = [  # name, cin, region, other (former names)
    ("ALPHA STEELS PRIVATE LIMITED", cin("GJ", serial="000001"), "IN-GJ", ""),
    ("BETA CEMENT LIMITED", cin("MH", cls="PLC", serial="000002", listed="L"), "IN-MH", ""),
    ("GAMMA GLASS PRIVATE LIMITED", cin("KA", serial="000003"), "IN-KA", ""),
    ("DELTA DYES PRIVATE LIMITED", cin("GJ", serial="000004"), "IN-GJ", ""),
    ("DELTA DYES PRIVATE LIMITED", cin("MH", serial="000005"), "IN-MH", ""),
    ("NEWNAME POLYMERS LIMITED", cin("OR", cls="PLC", serial="000006"), "IN-OD", "OLDNAME POLYMERS LIMITED"),
    ("LATE HOMONYM PRIVATE LIMITED", cin("GJ", year=2021, serial="000007"), "IN-GJ", ""),
    ("EPSILON METALS LIMITED", cin("OR", cls="PLC", serial="000008"), "IN-OR", ""),
    ("EPSILON METALS LIMITED", cin("WB", cls="PLC", serial="000009"), "IN-WB", ""),
]
CO = [  # name, state, cin found by web lookup, last_iem
    ("ALPHA STEELS PVT LTD", "GUJARAT", cin("GJ", serial="000001"), "2015-01"),                      # confirmed
    ("BETA CEMENT LTD", "MAHARASHTRA", cin("MH", cls="PTC", serial="000002", listed="U"), "2015-01"),  # cin_changed (U->L, PTC->PLC)
    ("GAMMA GLASS PVT LTD", "KARNATAKA", cin("KA", serial="999999"), "2015-01"),                     # conflict
    ("DELTA DYES PVT LTD", None, None, "2015-01"),                                                   # ambiguous
    ("OLDNAME POLYMERS LIMITED", iv.find_state("CUTTACK, ORISSA"), None, "2015-01"),                 # new via former name, Odisha
    ("LATE HOMONYM PVT LTD", "GUJARAT", None, "2012-01"),                                           # incorporated 2021 > 2012 -> none
    ("NOBODY TEXTILES PVT LTD", "GUJARAT", None, "2015-01"),                                         # none
    ("EPSILON METALS LTD", iv.find_state("JAJPUR, ODISHA"), None, "2015-01"),                        # 2 homonyms, state picks OR
]


@pytest.fixture
def verdicts(tmp_path, monkeypatch):
    pd.DataFrame([dict(lei=f"LEI{i}", name=n, cin=c, region=r, other=o, ra="RA000394",
                       e_status="ACTIVE", r_status="ISSUED", r_next="2027-01-01") for i, (n, c, r, o) in enumerate(GLEIF)]
                 ).to_parquet(tmp_path / "gleif_india.parquet")
    co = pd.DataFrame([dict(id=i + 1, key=iv.key(n), name=n, state=s, cin=c, status="Active" if c else None, src="web" if c else None,
                            first_iem=y, last_iem=y, last_part_b=None, in_part_b=False, usable=True) for i, (n, s, c, y) in enumerate(CO)])
    k = duckdb.connect(str(tmp_path / "iem_verify.duckdb"))
    k.execute("create table co as select * from co"); k.close()
    monkeypatch.setattr(gleif_join, "BASE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["gleif_join.py"])
    gleif_join.main()
    out = pd.read_csv(tmp_path / "gleif_join.csv")
    return dict(zip(out.name, out.verdict)), out


@pytest.mark.parametrize("name,verdict", [
    ("ALPHA STEELS PVT LTD", "confirmed"),
    ("BETA CEMENT LTD", "cin_changed"),
    ("GAMMA GLASS PVT LTD", "conflict"),
    ("DELTA DYES PVT LTD", "ambiguous"),
    ("OLDNAME POLYMERS LIMITED", "new"),
    ("LATE HOMONYM PVT LTD", "none"),
    ("NOBODY TEXTILES PVT LTD", "none"),
    ("EPSILON METALS LTD", "new"),
])
def test_verdicts(verdicts, name, verdict):
    assert verdicts[0][name] == verdict


def test_new_identification_carries_gleif_cin(verdicts):
    out = verdicts[1].set_index("name")
    assert out.loc["OLDNAME POLYMERS LIMITED", "gleif_cin"] == cin("OR", cls="PLC", serial="000006")
    assert out.loc["BETA CEMENT LTD", "gleif_cin"] == cin("MH", cls="PLC", serial="000002", listed="L")
