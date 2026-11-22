"""parse_layout: column positions from the header line, record slicing, and the pdftotext wrapper (faked)."""
import os
from pathlib import Path

import pytest

import parse_layout as pl

COLS = {"Company": 6, "District": 36, "State": 50, "Item": 66, "Capacity": 90, "Unit": 100, "IEM": 110}


def line(**parts):
    """Build a fixed-width line: parts is {col_key or 'Sr': text}; each text starts at its column."""
    pos = dict(COLS, Sr=1)
    out = [" "] * 200
    for k, v in parts.items():
        for i, ch in enumerate(v):
            out[pos[k] + i] = ch
    return "".join(out).rstrip()


HEADER = line(Sr="Sr. No", Company="Company Name and Address", District="District", State="State", Item="Item of Manufacture",
              Capacity="Capacity", Unit="Unit", IEM="IEM No and Date")


# --------------------------------------------------------------------------- header_columns
def test_header_columns_finds_every_key_position():
    assert pl.header_columns([HEADER]) == COLS


def test_header_columns_needs_company_district_and_item_on_one_line():
    assert pl.header_columns(["Company District"]) is None
    assert pl.header_columns(["", "x", HEADER]) == COLS


def test_header_columns_only_looks_at_first_sixty_lines():
    assert pl.header_columns(["x"] * 59 + [HEADER]) == COLS
    assert pl.header_columns(["x"] * 60 + [HEADER]) is None


def test_header_columns_omits_missing_optional_keys():
    h = "  Company          District          Item"
    pos = pl.header_columns([h])
    assert set(pos) == {"Company", "District", "Item"} and pos["Company"] == 2


# --------------------------------------------------------------------------- split_record
def test_split_record_slices_by_column_edges():
    ln = line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE", State="MAHARASHTRA", Item="STEEL BARS",
              Capacity="300 MT", IEM="12/N/SIA/IMO/2019 05/03/2019")
    r = pl.split_record(ln.ljust(300), COLS)
    assert r == {"company_and_address": "ALPHA STEELS PVT LTD", "district": "PUNE", "state_text": "MAHARASHTRA",
                 "item": "STEEL BARS", "capacity_text": "300 MT", "iem_text": "12/N/SIA/IMO/2019 05/03/2019"}


def test_split_record_without_state_header_uses_item_minus_one_as_district_end():
    cols = {"Company": 6, "District": 36, "Item": 66}
    ln = line(Company="ALPHA", District="PUNE", Item="STEEL").ljust(200)
    r = pl.split_record(ln, cols)
    # no State column: state text starts at district end (item-1) and runs to item start -> one character
    assert r["district"] == ln[36:65].strip() and r["state_text"] == ln[65:66].strip()
    assert r["item"] == ln[66:66 + 60].strip()                       # capacity defaults to item + 60
    assert r["iem_text"] == ln[66 + 60 + 30:].strip()                # iem defaults to capacity + 30


def test_split_record_short_line_gives_empty_fields():
    r = pl.split_record("", COLS)
    assert set(r.values()) == {""}


def test_split_record_iem_text_runs_to_end_of_line():
    ln = line(IEM="X" * 50)
    assert pl.split_record(ln, COLS)["iem_text"] == "X" * 50


# --------------------------------------------------------------------------- pdftotext_layout
def test_pdftotext_layout_runs_binary_reads_and_removes_temp(monkeypatch):
    seen = {}

    def fake_run(cmd, check=None, timeout=None):
        seen["cmd"], seen["check"], seen["timeout"] = cmd, check, timeout
        Path(cmd[-1]).write_text("hello\nworld\xe9")
    monkeypatch.setattr(pl.subprocess, "run", fake_run)
    out = pl.pdftotext_layout("/x/f.pdf")
    assert out == "hello\nworld\xe9"
    assert seen["cmd"][:4] == ["/opt/homebrew/bin/pdftotext", "-layout", "/x/f.pdf", seen["cmd"][3]]
    assert seen["check"] is True and seen["timeout"] == 600
    assert not os.path.exists(seen["cmd"][-1])


def test_pdftotext_layout_tolerates_bad_bytes(monkeypatch):
    monkeypatch.setattr(pl.subprocess, "run", lambda cmd, check=None, timeout=None: Path(cmd[-1]).write_bytes(b"ab\xffcd"))
    assert pl.pdftotext_layout("f.pdf") == "ab�cd"


# --------------------------------------------------------------------------- parse
def run_parse(monkeypatch, lines):
    monkeypatch.setattr(pl, "pdftotext_layout", lambda pdf: "\n".join(lines))
    return pl.parse("f.pdf")


def test_parse_without_header_reports_error(monkeypatch):
    assert run_parse(monkeypatch, ["nothing"]) == (None, "no header")


def test_parse_assembles_multiline_records(monkeypatch):
    recs, err = run_parse(monkeypatch, [
        "STATEMENT ON INDUSTRIAL ENTREPRENEUR MEMORANDUM",
        HEADER,
        line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE", State="MAHARASHTRA", Item="STEEL BARS", Capacity="300 MT",
             IEM="12/N/SIA/IMO/2019"),
        line(Company="PLOT 5 MIDC", State="INDIA", Item="AND WIRES", IEM="05/03/2019"),
        "",
        line(Sr="2", Company="BETA CEMENT LTD", District="SURAT", State="GUJARAT", Item="CEMENT", IEM="7 / N / SIA / IM O / 2020 01/02/2020"),
    ])
    assert err is None and len(recs) == 2
    a, b = recs
    assert a == {"serial": 1, "company_and_address": "ALPHA STEELS PVT LTD PLOT 5 MIDC", "district": "PUNE",
                 "state": "MAHARASHTRA INDIA", "item_of_manufacture": "STEEL BARS AND WIRES", "capacity": "300 MT",
                 "iem_no": 12, "iem_year": 2019, "filing_date": "05/03/2019"}
    assert (b["serial"], b["iem_no"], b["iem_year"], b["filing_date"]) == (2, 7, 2020, "01/02/2020")


def test_parse_iem_without_match_gives_none(monkeypatch):
    recs, _ = run_parse(monkeypatch, [HEADER, line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE", IEM="garbled")])
    assert (recs[0]["iem_no"], recs[0]["iem_year"], recs[0]["filing_date"]) == (None, None, None)


def test_parse_ignores_lines_before_first_record_and_header_repeats(monkeypatch):
    recs, _ = run_parse(monkeypatch, [
        "orphan line before any record", HEADER,
        line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE"),
        HEADER,                                                       # repeated page header: skipped, not appended to the record
        line(Sr="Sr. No", Company="Company Name and Address"),        # header-ish fragment skipped
        line(Company="PLOT 5", District="MORE"),
    ])
    assert len(recs) == 1
    assert recs[0]["company_and_address"] == "ALPHA STEELS PVT LTD PLOT 5"
    assert recs[0]["district"] == "PUNE MORE"


@pytest.mark.parametrize("marker", ["MEMORANDUM", "IN THE MONTH", "STATEMENT ON", "Item of", "Company Name and"])
def test_parse_skips_boilerplate_lines_inside_a_record(monkeypatch, marker):
    recs, _ = run_parse(monkeypatch, [HEADER, line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE"),
                                      line(Company=f"{marker} text")])
    assert recs[0]["company_and_address"] == "ALPHA STEELS PVT LTD"


def test_parse_serial_zero_does_not_start_a_record(monkeypatch):
    recs, _ = run_parse(monkeypatch, [HEADER, line(Sr="0", Company="ZERO PVT LTD", District="X"),
                                      line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE")])
    assert [r["serial"] for r in recs] == [1]


def test_parse_line_guard_is_longer_than_company_column_minus_two(monkeypatch):
    # Company starts at 6, so a record line must be longer than 4 characters
    recs, _ = run_parse(monkeypatch, [HEADER, line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE"), "2  x"])
    assert [r["serial"] for r in recs] == [1]
    recs, _ = run_parse(monkeypatch, [HEADER, line(Sr="1", Company="ALPHA STEELS PVT LTD", District="PUNE"), " 2  x"])
    assert [r["serial"] for r in recs] == [1, 2]


def test_parse_serial_may_be_indented_up_to_eight_spaces(monkeypatch):
    ok = " " * 8 + "5" + " " * 6 + "ALPHA STEELS PVT LTD"
    bad = " " * 9 + "6" + " " * 5 + "BETA CEMENT LTD"
    recs, _ = run_parse(monkeypatch, [HEADER, ok, bad])
    assert [r["serial"] for r in recs] == [5]


def test_split_record_default_district_end_is_one_before_item():
    cols = {"Company": 6, "District": 36, "Item": 66}
    ln = list(" " * 200); ln[64] = "A"; ln[65] = "B"
    r = pl.split_record("".join(ln), cols)
    assert r["district"].endswith("A") and r["state_text"] == "B"


def test_split_record_default_capacity_start_is_item_plus_sixty():
    cols = {"Company": 6, "District": 36, "Item": 66}
    ln = list(" " * 250); ln[66 + 59] = "I"; ln[66 + 60] = "Z"
    r = pl.split_record("".join(ln), cols)
    assert r["item"].endswith("I") and "Z" not in r["item"] and r["capacity_text"].startswith("Z")
