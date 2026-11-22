"""compile_iem_ab.load_a / load_b / main on synthetic parquet, DuckDB, CSV inputs redirected to tmp_path.

No network and no real data: BASE is monkeypatched so every read and write happens under tmp_path.
"""
import duckdb
import pandas as pd
import pytest

import compile_iem_ab as c


# --------------------------------------------------------------------------- norm_date edge cases
@pytest.mark.parametrize("raw,out", [
    ("1/1/2020", "01/01/2020"),
    ("31-12-1999", "31/12/1999"),
    ("12/12/2012", "12/12/2012"),
    ("15 March 2019", "15/03/2019"),
    ("", None),
    (12345, None),               # str(12345) is not a date
    (pd.NA, None),               # non-float NA: falls to the parser, which rejects it
])
def test_norm_date_more(raw, out):
    assert c.norm_date(raw) == out


def test_norm_date_does_not_treat_nan_string_as_missing_but_returns_none():
    assert c.norm_date("nan") is None


def test_norm_date_day_first_ambiguity_resolved_as_day_first():
    # 3/7 is 3 July (d/m), not 7 March
    assert c.norm_date("03/07/2021") == "03/07/2021"
    assert c.norm_date("3 Jul 2021") == "03/07/2021"


# --------------------------------------------------------------------------- fixtures
COORD_COLS = ["company_name", "name_has_suffix", "district_state", "item", "month", "iem_no", "filing_date",
              "source_file", "serial"]
TEXT_COLS = ["company_name", "name_has_suffix", "district_state", "item", "month", "ack_no", "filing_date",
             "source_file", "serial"]


def coords(rows):
    return pd.DataFrame(rows, columns=COORD_COLS)


def text(rows):
    return pd.DataFrame(rows, columns=TEXT_COLS)


def partb(rows):
    return pd.DataFrame(rows, columns=["company_name", "state", "month", "mem_no", "mem_date", "commenced",
                                       "source_file", "name_has_suffix"])


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- load_a
def test_load_a_merges_sources_and_drops_2023_text_files(base):
    coords([
        ("ALPHA STEELS PRIVATE LIMITED", True, "PUNE MAHARASHTRA", "STEEL", "2021-03", 10, "5/3/2021", "2021-03_a.pdf", 1),
    ]).to_parquet(base / "partA_coords.parquet")
    text([
        ("BETA CEMENT LIMITED", True, "GUJARAT", "CEMENT", "2015-05", 77, "01/05/2015", "2015-05_b.pdf", 3),
        ("OMEGA FABRICS LIMITED", True, "GUJARAT", "CLOTH", "2023-02", 5, "01/02/2023", "2023-02_cumulative.pdf", 1),
    ]).to_parquet(base / "partA_text_layout.parquet")
    a = c.load_a()
    assert sorted(a.company_name) == ["ALPHA STEELS PRIVATE LIMITED", "BETA CEMENT LIMITED"]
    row = a.set_index("company_name")
    assert row.loc["ALPHA STEELS PRIVATE LIMITED", "src"] == "coords"
    assert row.loc["BETA CEMENT LIMITED", "src"] == "text"
    assert row.loc["ALPHA STEELS PRIVATE LIMITED", "a_no"] == 10
    assert row.loc["BETA CEMENT LIMITED", "a_no"] == 77              # ack_no renamed to a_no
    assert row.loc["ALPHA STEELS PRIVATE LIMITED", "a_date"] == "05/03/2021"
    assert row.loc["ALPHA STEELS PRIVATE LIMITED", "state"] == "MAHARASHTRA"
    assert row.loc["ALPHA STEELS PRIVATE LIMITED", "k"] == "ALPHA STEELS"
    assert list(a.index) == [0, 1]                                    # index reset


def test_load_a_2023_prefix_filter_only_applies_to_text_layout(base):
    # a coordinate-parsed file whose name starts with 2023 is kept
    coords([("ALPHA STEELS PRIVATE LIMITED", True, "GUJARAT", "X", "2023-04", 1, "1/4/2023", "2023-04_c.pdf", 1)]
           ).to_parquet(base / "partA_coords.parquet")
    text([("BETA CEMENT LIMITED", True, "GUJARAT", "X", "2022-04", 1, "1/4/2022", "2022-04_t.pdf", 1)]
         ).to_parquet(base / "partA_text_layout.parquet")
    assert sorted(c.load_a().company_name) == ["ALPHA STEELS PRIVATE LIMITED", "BETA CEMENT LIMITED"]


def test_load_a_drops_short_keys_and_keeps_boundary_length(base):
    coords([
        ("AB LIMITED", True, "GOA", "X", "2021-01", 1, "1/1/2021", "f1.pdf", 1),            # key "AB"  (2) -> dropped
        ("ABC LIMITED", True, "GOA", "X", "2021-01", 2, "1/1/2021", "f1.pdf", 2),           # key "ABC" (3) -> dropped
        ("ABCD LIMITED", True, "GOA", "X", "2021-01", 3, "1/1/2021", "f1.pdf", 3),          # key "ABCD" (4) -> kept
    ]).to_parquet(base / "partA_coords.parquet")
    text([]).to_parquet(base / "partA_text_layout.parquet")
    assert list(c.load_a().company_name) == ["ABCD LIMITED"]


def test_load_a_dedups_on_month_serial_name_keeping_earliest_source_file(base):
    coords([
        ("ALPHA STEELS PRIVATE LIMITED", True, "GOA", "LATER", "2021-03", 1, "1/3/2021", "2021-03_z.pdf", 1),
        ("ALPHA STEELS PRIVATE LIMITED", True, "GOA", "EARLIER", "2021-03", 1, "1/3/2021", "2021-03_a.pdf", 1),
        ("ALPHA STEELS PRIVATE LIMITED", True, "GOA", "OTHER_SERIAL", "2021-03", 2, "1/3/2021", "2021-03_a.pdf", 2),
        ("ALPHA STEELS PRIVATE LIMITED", True, "GOA", "OTHER_MONTH", "2021-04", 1, "1/4/2021", "2021-04_a.pdf", 1),
    ]).to_parquet(base / "partA_coords.parquet")
    text([]).to_parquet(base / "partA_text_layout.parquet")
    a = c.load_a()
    assert len(a) == 3
    assert a[(a.month == "2021-03") & (a.serial == 1)].item.tolist() == ["EARLIER"]


def test_load_a_unknown_state_is_empty_string_not_nan(base):
    coords([("ALPHA STEELS PRIVATE LIMITED", True, "NOWHERE", "X", "2021-03", 1, "1/3/2021", "f.pdf", 1)]
           ).to_parquet(base / "partA_coords.parquet")
    text([]).to_parquet(base / "partA_text_layout.parquet")
    assert c.load_a().state.tolist() == [""]


# --------------------------------------------------------------------------- load_b
def test_load_b_normalises_names_filters_and_dates(base):
    partb([
        ("  alpha   steels  pvt ltd. ,", None, "2022-01", 10, "5-3-2021", "01/01/2022", "f.pdf", True),
        ("ab ltd", "GOA", "2022-01", 11, "1/1/2021", None, "f.pdf", True),                 # key too short -> dropped
        ("Beta Cement Limited", "GUJARAT", "2022-02", None, None, None, "f.pdf", True),
    ]).to_parquet(base / "partB_all.parquet")
    b = c.load_b()
    assert list(b.name) == ["ALPHA STEELS PVT LTD", "BETA CEMENT LIMITED"]
    assert list(b.k) == ["ALPHA STEELS", "BETA CEMENT"]
    assert list(b.state) == ["", "GUJARAT"]                                   # NaN state -> ""
    assert b.m_date.tolist()[0] == "05/03/2021"
    assert pd.isna(b.m_date.tolist()[1])                                          # None under pandas<3, NaN under pandas>=3


# --------------------------------------------------------------------------- main
def write_inputs(base):
    coords([
        ("ALPHA STEELS PRIVATE LIMITED", True, "PUNE MAHARASHTRA", "STEEL", "2021-03", 10, "5/3/2021", "2021-03_a.pdf", 1),   # number+date
        ("BETA CEMENT LIMITED", True, "SURAT GUJARAT", "CEMENT", "2019-06", 11, "1/6/2019", "2019-06_a.pdf", 1),             # name (same month)
        ("GAMMA GLASS PRIVATE LIMITED", True, "KOCHI KERALA", "GLASS", "2022-02", 12, "1/2/2022", "2022-02_a.pdf", 1),       # none, candidate
        ("DELTA DYES PRIVATE LIMITED", True, "PUNE MAHARASHTRA", "DYE", "2020-12", 13, "1/12/2020", "2020-12_a.pdf", 1),     # none, pre-2021
        ("EPSILON METALS LIMITED", True, "PUNE MAHARASHTRA", "METAL", "2021-01", 14, "1/1/2021", "2021-01_a.pdf", 1),        # none, boundary 2021-01
        ("ZETA PAPERS LIMITED", True, "CUTTACK ODISHA", "PAPER", "2021-05", 15, "1/5/2021", "2021-05_a.pdf", 1),             # B in other state
        ("ETA TEXTILES LIMITED", True, "PUNE MAHARASHTRA", "CLOTH", "2021-06", 16, "1/6/2021", "2021-06_a.pdf", 1),          # B earlier month
        ("THETA FOODS LIMITED", True, "PUNE MAHARASHTRA", "FOOD", "2021-07", 17, "1/7/2021", "2021-07_a.pdf", 1),            # generic -> unusable
    ]).to_parquet(base / "partA_coords.parquet")
    text([]).to_parquet(base / "partA_text_layout.parquet")
    partb([
        ("ALPHA STEELS PVT LTD", "MAHARASHTRA", "2022-01", 10, "5/3/2021", "01/06/2022", "b1.pdf", True),
        ("BETA CEMENT LTD", "GUJARAT", "2019-06", 99, "1/1/2000", "01/01/2020", "b2.pdf", True),
        ("ZETA PAPERS LTD", "ASSAM", "2022-01", 98, "1/1/2000", None, "b3.pdf", True),
        ("ETA TEXTILES LTD", "", "2021-05", 97, "1/1/2000", None, "b4.pdf", True),
        ("ONLYB WIRES LIMITED", "GOA", "2022-03", 5, "1/1/2022", None, "b5.pdf", True),
    ]).to_parquet(base / "partB_all.parquet")


def run_main(base, capsys):
    c.main()
    out = capsys.readouterr().out
    xl = pd.read_excel(base / "IEM_A_vs_B_support_candidates.xlsx", sheet_name=None)
    return out, xl


def test_main_without_company_db_links_and_flags_candidates(base, capsys):
    write_inputs(base)
    out, xl = run_main(base, capsys)
    assert "note: iem_verify.duckdb or gleif_verdict unavailable" in out
    assert "Part A filings: 8  | Part B records: 5" in out
    af = xl["Part A filings"].set_index("company_name")
    assert af.loc["ALPHA STEELS PRIVATE LIMITED", "link"] == "number+date"
    assert af.loc["ALPHA STEELS PRIVATE LIMITED", "b_month"] == "2022-01"
    assert af.loc["ALPHA STEELS PRIVATE LIMITED", "b_commenced"] == "01/06/2022"
    assert af.loc["BETA CEMENT LIMITED", "link"] == "name"                    # B month == A month counts (>=)
    assert af.loc["BETA CEMENT LIMITED", "b_month"] == "2019-06"
    assert af.loc["ZETA PAPERS LIMITED", "link"] == "none"                    # state differs in Part B
    assert af.loc["ETA TEXTILES LIMITED", "link"] == "none"                   # Part B month is before the filing month
    cand = xl["Candidates"]
    # without a company table every name counts as usable (usable defaults to True)
    assert sorted(cand.company_name) == ["EPSILON METALS LIMITED", "ETA TEXTILES LIMITED", "GAMMA GLASS PRIVATE LIMITED",
                                         "THETA FOODS LIMITED", "ZETA PAPERS LIMITED"]
    # newest first (verified_active is all False without an identity source)
    assert list(cand.month) == sorted(cand.month, reverse=True)
    assert "DELTA DYES PRIVATE LIMITED" not in set(cand.company_name)         # 2020-12 < 2021-01


def test_main_state_less_part_b_links_by_name(base, capsys):
    write_inputs(base)
    out, xl = run_main(base, capsys)
    # ETA TEXTILES has a Part B with unknown state but in an EARLIER month, so no link
    assert xl["Part A filings"].set_index("company_name").loc["ETA TEXTILES LIMITED", "link"] == "none"


def test_main_summary_by_year(base, capsys):
    write_inputs(base)
    out, xl = run_main(base, capsys)
    s = xl["Summary by year"].set_index("year")
    assert s.loc[2021, "part_a_filings"] == 5
    assert s.loc[2021, "linked_number_date"] == 1
    assert s.loc[2021, "linked_name_only"] == 0
    assert s.loc[2021, "no_part_b_found"] == 4
    assert s.loc[2021, "share_linked"] == pytest.approx(0.2)
    assert s.loc[2019, "linked_name_only"] == 1
    assert s.loc[2019, "share_linked"] == pytest.approx(1.0)


def test_main_companies_matched_includes_part_b_only_companies(base, capsys):
    write_inputs(base)
    out, xl = run_main(base, capsys)
    comp = xl["Companies matched"].set_index("name")
    assert bool(comp.loc["ONLYB WIRES LIMITED", "in_part_b"]) is True
    assert bool(comp.loc["ONLYB WIRES LIMITED", "in_part_a"]) is False
    assert bool(comp.loc["GAMMA GLASS PRIVATE LIMITED", "in_part_a"]) is True
    assert bool(comp.loc["GAMMA GLASS PRIVATE LIMITED", "in_part_b"]) is False
    assert comp.loc["ALPHA STEELS PRIVATE LIMITED", "filings"] == 1
    assert comp.loc["ALPHA STEELS PRIVATE LIMITED", "filings_with_b"] == 1
    assert comp.loc["GAMMA GLASS PRIVATE LIMITED", "open_filings"] == 1


def test_main_writes_parquets(base, capsys):
    write_inputs(base)
    run_main(base, capsys)
    linked = pd.read_parquet(base / "iem_partA_filings_linked.parquet")
    assert len(linked) == 8 and set(linked.link) == {"number+date", "name", "none"}
    comp = pd.read_parquet(base / "iem_companies_compiled.parquet")
    flag = comp.set_index("name").support_candidate
    assert bool(flag["GAMMA GLASS PRIVATE LIMITED"]) is True
    assert bool(flag["ALPHA STEELS PRIVATE LIMITED"]) is False
    assert bool(flag["ONLYB WIRES LIMITED"]) is False


def test_main_filing_age_and_stdout_summary(base, capsys):
    write_inputs(base)
    out, xl = run_main(base, capsys)
    cand = xl["Candidates"].set_index("company_name")
    # age = 2026.75 - (year + (month-1)/12), 1 decimal
    assert cand.loc["GAMMA GLASS PRIVATE LIMITED", "filing_age_yrs"] == pytest.approx(round(2026.75 - (2022 + 1 / 12), 1))
    assert cand.loc["EPSILON METALS LIMITED", "filing_age_yrs"] == pytest.approx(round(2026.75 - 2021.0, 1))
    assert "candidates (filings): 5 | companies: 5 | with CIN: 0 | verified active: 0" in out


def test_main_notes_sheet_present(base, capsys):
    write_inputs(base)
    out, xl = run_main(base, capsys)
    assert set(xl) == {"Candidates", "Part A filings", "Companies matched", "Summary by year", "Notes"}
    assert len(xl["Notes"]) == 6


# --------------------------------------------------------------------------- with the company database
def build_db(base):
    k = duckdb.connect(str(base / "iem_verify.duckdb"))
    co = pd.DataFrame([
        # key, state, name, usable, in_part_b, n_part_a, n_part_b, last_iem, last_part_b, cin, status, src, id
        ("GAMMA GLASS", "KERALA", "GAMMA GLASS PRIVATE LIMITED", True, False, 1, 0, "2022-02", None,
         "U23100KL2010PTC000001", "Active", "web-slug+state", 1),
        ("EPSILON METALS", "MAHARASHTRA", "EPSILON METALS LIMITED", True, False, 1, 0, "2021-01", None,
         "U27100MH2005PLC000002", "Strike Off", "web-slug+state", 2),
        ("ETA TEXTILES", "MAHARASHTRA", "ETA TEXTILES LIMITED", True, False, 1, 0, "2021-06", None,
         "U17100MH2005PLC000003", "Active", "web-slug+state", 3),
        ("ZETA PAPERS", "ODISHA", "ZETA PAPERS LIMITED", False, False, 1, 0, "2021-05", None,
         None, None, None, 4),                                                                       # usable False
        ("NEWCO LEI", "ODISHA", "NEWCO LEI LIMITED", True, False, 1, 0, "2021-05", None,
         None, None, None, 5),
    ], columns=["key", "state", "name", "usable", "in_part_b", "n_part_a", "n_part_b", "last_iem", "last_part_b",
                "cin", "status", "src", "id"])
    gv = pd.DataFrame([
        (1, "confirmed", "U23100KL2010PTC000001", "ACTIVE", "ISSUED", "2027-01-01"),
        (2, "cin_changed", "L27100MH2005PLC000002", "ACTIVE", "ISSUED", "2027-02-01"),
        (3, "conflict", "U99999MH1990PTC000009", "ACTIVE", "LAPSED", None),
        (5, "new", "U00000OR2012PLC000005", "INACTIVE", "LAPSED", None),
    ], columns=["id", "verdict", "gleif_cin", "gleif_entity_status", "lei_status", "lei_next_renewal"])
    k.execute("create table co as select * from co")
    k.execute("create table gleif_verdict as select * from gv")
    k.close()


def db_inputs(base):
    coords([
        ("GAMMA GLASS PRIVATE LIMITED", True, "KOCHI KERALA", "GLASS", "2022-02", 12, "1/2/2022", "a.pdf", 1),
        ("EPSILON METALS LIMITED", True, "PUNE MAHARASHTRA", "METAL", "2021-01", 14, "1/1/2021", "a.pdf", 2),
        ("ETA TEXTILES LIMITED", True, "PUNE MAHARASHTRA", "CLOTH", "2021-06", 16, "1/6/2021", "a.pdf", 3),
        ("ZETA PAPERS LIMITED", True, "CUTTACK ODISHA", "PAPER", "2021-05", 15, "1/5/2021", "a.pdf", 4),
        ("NEWCO LEI LIMITED", True, "CUTTACK ODISHA", "PAPER", "2021-05", 18, "1/5/2021", "a.pdf", 5),
    ]).to_parquet(base / "partA_coords.parquet")
    text([]).to_parquet(base / "partA_text_layout.parquet")
    partb([("ONLYB WIRES LIMITED", "GOA", "2022-03", 5, "1/1/2022", None, "b5.pdf", True)]).to_parquet(base / "partB_all.parquet")


def test_main_with_company_db_identity_and_status(base, capsys):
    db_inputs(base); build_db(base)
    out, xl = run_main(base, capsys)
    assert "note:" not in out
    af = xl["Part A filings"].set_index("company_name")
    assert af.loc["GAMMA GLASS PRIVATE LIMITED", "identity"] == "confirmed by LEI register"
    assert af.loc["EPSILON METALS LIMITED", "identity"] == "confirmed (CIN updated)"
    assert af.loc["EPSILON METALS LIMITED", "cin"] == "L27100MH2005PLC000002"          # CIN replaced by GLEIF's
    assert af.loc["ETA TEXTILES LIMITED", "identity"].startswith("CONFLICT")
    assert af.loc["ETA TEXTILES LIMITED", "cin"] == "U17100MH2005PLC000003"            # conflict keeps the web CIN
    assert af.loc["NEWCO LEI LIMITED", "identity"] == "identified via LEI register"
    assert af.loc["NEWCO LEI LIMITED", "cin"] == "U00000OR2012PLC000005"
    assert af.loc["NEWCO LEI LIMITED", "mca_status"] == "Dissolved or inactive (LEI)"  # INACTIVE LEI entity


def test_main_with_company_db_candidates_exclude_inactive_unusable(base, capsys):
    db_inputs(base); build_db(base)
    out, xl = run_main(base, capsys)
    cand = xl["Candidates"].set_index("company_name")
    # EPSILON's CIN was updated from GLEIF but its MCA status stays 'Strike Off' -> matches the inactive filter
    assert "EPSILON METALS LIMITED" not in cand.index
    assert "NEWCO LEI LIMITED" not in cand.index          # 'Dissolved or inactive (LEI)' contains "dissol"
    assert "ZETA PAPERS LIMITED" not in cand.index        # unusable name
    assert {"GAMMA GLASS PRIVATE LIMITED", "ETA TEXTILES LIMITED"} <= set(cand.index)


def test_main_with_company_db_verified_active_requires_no_conflict(base, capsys):
    db_inputs(base); build_db(base)
    out, xl = run_main(base, capsys)
    cand = xl["Candidates"].set_index("company_name")
    assert bool(cand.loc["GAMMA GLASS PRIVATE LIMITED", "verified_active"]) is True
    assert bool(cand.loc["ETA TEXTILES LIMITED", "verified_active"]) is False      # Active but identity CONFLICT
    assert "ZETA PAPERS LIMITED" not in cand.index                                 # unusable name
    # verified-active rows sort first
    assert cand.index[0] == "GAMMA GLASS PRIVATE LIMITED"


def test_main_registry_csv_fills_missing_cin_only(base, capsys):
    db_inputs(base); build_db(base)
    pd.DataFrame([
        ("NEWCO LEI", "ODISHA", "MATCHED", "U11111OR2001PTC000111", "Active"),       # NEWCO already has a CIN via LEI -> untouched
        ("ZETA PAPERS", "ODISHA", "MATCHED", "U22222OR2001PTC000222", "Active"),     # no CIN -> filled from registry
        ("ZETA PAPERS", "ODISHA", "AMBIGUOUS", "U33333OR2001PTC000333", "Active"),   # non-MATCHED rows ignored
    ], columns=["k", "state", "verdict", "cin", "status"]).to_csv(base / "IEM_registry_matches.csv", index=False)
    out, xl = run_main(base, capsys)
    af = xl["Part A filings"].set_index("company_name")
    assert af.loc["ZETA PAPERS LIMITED", "cin"] == "U22222OR2001PTC000222"
    assert af.loc["ZETA PAPERS LIMITED", "mca_status"] == "Active"
    assert af.loc["NEWCO LEI LIMITED", "cin"] == "U00000OR2012PLC000005"


def test_main_apify_operating_evidence(base, capsys):
    db_inputs(base); build_db(base)
    pd.DataFrame([
        # strong: Active, AGM 2025+, balance sheet >= 2025-03-31, no flags
        ("U23100KL2010PTC000001", "Active", "06/30/2025", "03/31/2025", "False", "False", "False"),
        # weak: balance sheet one day short of the 2025-03-31 cut-off
        ("U17100MH2005PLC000003", "Active", "06/30/2025", "03/30/2025", "False", "False", "False"),
        # not Active
        ("U00000OR2012PLC000005", "Amalgamated", "06/30/2025", "03/31/2025", "False", "False", "False"),
        # duplicate cin row is dropped (first kept)
        ("U23100KL2010PTC000001", "Strike Off", "01/01/2010", "01/01/2010", "True", "True", "True"),
    ], columns=["cin", "status", "dateOfLastAGM", "balanceSheetDate", "annualReturnDefaulter", "balanceSheetDefaulter", "underCIRP"]
    ).to_csv(base / "apify_mca.csv", index=False)
    out, xl = run_main(base, capsys)
    live = xl["Part A filings"]   # operating_evidence is on the linked parquet
    linked = pd.read_parquet(base / "iem_partA_filings_linked.parquet").set_index("company_name")
    assert linked.loc["GAMMA GLASS PRIVATE LIMITED", "operating_evidence"] == "MCA record: Active, AGM 2025+ and balance sheet FY2024-25+"
    assert linked.loc["ETA TEXTILES LIMITED", "operating_evidence"] == "MCA record: Active but filings lag, or flagged"
    assert linked.loc["NEWCO LEI LIMITED", "operating_evidence"] == "MCA record: not Active"
    assert linked.loc["EPSILON METALS LIMITED", "operating_evidence"] == "no live MCA record yet"
    assert bool(linked.loc["GAMMA GLASS PRIVATE LIMITED", "mca_defaulter"]) is False
    assert len(live) == 5


@pytest.mark.parametrize("flag_col", ["annualReturnDefaulter", "balanceSheetDefaulter", "underCIRP"])
def test_main_apify_any_defaulter_flag_demotes_to_lagging(base, capsys, flag_col):
    db_inputs(base); build_db(base)
    row = {"cin": "U23100KL2010PTC000001", "status": "Active", "dateOfLastAGM": "06/30/2025", "balanceSheetDate": "03/31/2025",
           "annualReturnDefaulter": "False", "balanceSheetDefaulter": "False", "underCIRP": "False"}
    row[flag_col] = "True"
    pd.DataFrame([row]).to_csv(base / "apify_mca.csv", index=False)
    run_main(base, capsys)
    linked = pd.read_parquet(base / "iem_partA_filings_linked.parquet").set_index("company_name")
    assert bool(linked.loc["GAMMA GLASS PRIVATE LIMITED", "mca_defaulter"]) is True
    assert linked.loc["GAMMA GLASS PRIVATE LIMITED", "operating_evidence"] == "MCA record: Active but filings lag, or flagged"


def test_main_apify_agm_on_cutoff_is_strong(base, capsys):
    db_inputs(base); build_db(base)
    pd.DataFrame([("U23100KL2010PTC000001", "Active", "01/01/2025", "03/31/2025", "False", "False", "False")],
                 columns=["cin", "status", "dateOfLastAGM", "balanceSheetDate", "annualReturnDefaulter",
                          "balanceSheetDefaulter", "underCIRP"]).to_csv(base / "apify_mca.csv", index=False)
    run_main(base, capsys)
    linked = pd.read_parquet(base / "iem_partA_filings_linked.parquet").set_index("company_name")
    assert linked.loc["GAMMA GLASS PRIVATE LIMITED", "operating_evidence"].startswith("MCA record: Active, AGM 2025+")
    pd.DataFrame([("U23100KL2010PTC000001", "Active", "12/31/2024", "03/31/2025", "False", "False", "False")],
                 columns=["cin", "status", "dateOfLastAGM", "balanceSheetDate", "annualReturnDefaulter",
                          "balanceSheetDefaulter", "underCIRP"]).to_csv(base / "apify_mca.csv", index=False)
    run_main(base, capsys)
    linked = pd.read_parquet(base / "iem_partA_filings_linked.parquet").set_index("company_name")
    assert linked.loc["GAMMA GLASS PRIVATE LIMITED", "operating_evidence"] == "MCA record: Active but filings lag, or flagged"


# --------------------------------------------------------------------------- survivors of the first mutation pass
def test_load_b_keeps_a_four_character_key(base):
    partb([("abcd ltd", "GOA", "2022-01", 1, "1/1/2021", None, "f.pdf", True),
           ("abc ltd", "GOA", "2022-01", 2, "1/1/2021", None, "f.pdf", True)]).to_parquet(base / "partB_all.parquet")
    assert list(c.load_b().name) == ["ABCD LTD"]


@pytest.mark.parametrize("a_state,b_state,linked", [
    ("PUNE MAHARASHTRA", "", True),          # Part B state unknown
    ("NOWHERE", "GOA", True),                # Part A state unknown
    ("NOWHERE", "", True),
    ("PUNE MAHARASHTRA", "GOA", False),
    ("PUNE MAHARASHTRA", "MAHARASHTRA", True),
])
def test_name_link_state_rule(base, capsys, a_state, b_state, linked):
    coords([("ALPHA STEELS PRIVATE LIMITED", True, a_state, "X", "2021-03", 1, "1/3/2021", "f.pdf", 1)]).to_parquet(base / "partA_coords.parquet")
    text([]).to_parquet(base / "partA_text_layout.parquet")
    partb([("ALPHA STEELS LTD", b_state, "2022-01", 99, "1/1/2000", None, "b.pdf", True)]).to_parquet(base / "partB_all.parquet")
    c.main()
    capsys.readouterr()
    assert (pd.read_parquet(base / "iem_partA_filings_linked.parquet").link.iloc[0] == "name") is linked


def test_main_cirp_status_excludes_candidate(base, capsys):
    db_inputs(base)
    build_db(base)
    k = duckdb.connect(str(base / "iem_verify.duckdb"))
    k.execute("update co set status='Under CIRP' where key='ETA TEXTILES'")
    k.close()
    out, xl = run_main(base, capsys)
    assert "ETA TEXTILES LIMITED" not in set(xl["Candidates"].company_name)


def test_main_companies_without_a_gleif_verdict_keep_null_status(base, capsys):
    db_inputs(base)
    build_db(base)
    run_main(base, capsys)
    linked = pd.read_parquet(base / "iem_partA_filings_linked.parquet").set_index("company_name")
    z = linked.loc["ZETA PAPERS LIMITED"]
    assert pd.isna(z.mca_status) and pd.isna(z.cin)                    # no verdict row: not treated as a 'new' LEI identification


def test_number_date_link_uses_first_part_b_record_for_duplicate_keys(base, capsys):
    coords([("ALPHA STEELS PRIVATE LIMITED", True, "PUNE MAHARASHTRA", "X", "2021-03", 10, "5/3/2021", "f.pdf", 1)]).to_parquet(base / "partA_coords.parquet")
    text([]).to_parquet(base / "partA_text_layout.parquet")
    partb([("ALPHA STEELS LTD", "MAHARASHTRA", "2022-01", 10, "5/3/2021", "01/01/2022", "b1.pdf", True),
           ("ALPHA STEELS LTD", "MAHARASHTRA", "2023-06", 10, "5/3/2021", "01/01/2023", "b2.pdf", True)]).to_parquet(base / "partB_all.parquet")
    c.main()
    capsys.readouterr()
    r = pd.read_parquet(base / "iem_partA_filings_linked.parquet").iloc[0]
    assert r.link == "number+date" and r.b_month == "2022-01" and r.b_commenced == "01/01/2022"
