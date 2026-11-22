"""extract_partA_text_layout: text-layout record parser, OCR fallback and file driver, with pdftotext/tesseract faked."""
import os
from types import SimpleNamespace

import pytest

import extract_partA_text_layout as t


# --------------------------------------------------------------------------- regexes
@pytest.mark.parametrize("text,ok", [
    ("ALPHA STEELS PVT LTD", True), ("ALPHA STEELS PRIVATE LIMITED.", True), ("ALPHA LLP", True), ("ALPHA STEELS PVT.", True),
    ("ALPHA STEELS LTD,", True), ("ALPHA STEELS", False), ("ALPHA LTD PLOT 5", False)])
def test_suffix_must_end_the_name(text, ok):
    assert bool(t.SUFFIX.search(text)) is ok


@pytest.mark.parametrize("text,ok", [("PLOT 5", True), ("D.NO 4", True), ("D NO 4", True), ("DOOR 3", True), ("S.NO 1", True),
                                     ("12/3", True), ("C/O X", True), ("P.O. BOX", True), ("VILL X", True), ("AT X", True),
                                     ("FLOOR 2", True), ("GEMINI", False), ("PRIVATE LIMITED", False)])
def test_addr_start(text, ok):
    assert bool(t.ADDR_START.match(text)) is ok


def test_rec_requires_serial_within_six_spaces():
    assert t.REC.match("   1 ALPHA")
    assert t.REC.match("      12 ALPHA")
    assert not t.REC.match("       1 ALPHA")              # seven spaces
    assert not t.REC.match("   1")                         # nothing after the serial
    assert t.REC.match("99999 X") and not t.REC.match("123456 X")


def test_split_cols_returns_offsets_of_double_space_separated_segments():
    assert t.split_cols("AB CD   EF  G") == [(0, "AB CD"), (8, "EF"), (12, "G")]
    assert t.split_cols("   ") == []


# --------------------------------------------------------------------------- parse_text
def parse(lines, src="2012-05_a.pdf"):
    return t.parse_text([(1, "\n".join(lines))], src)


def test_basic_record_with_state_heading():
    rows = parse([
        "STATE : ANDHRA PRADESH",
        "   1 CHENGALVA AGRO FOODS PVT LTD        CHITTOOR        EDIBLE OIL   300.000  977",
        "     405, SHASHANK RESIDENCY                                          02/05/2001",
    ])
    assert len(rows) == 1
    r = rows[0]
    assert r["serial"] == 1 and r["page"] == 1 and r["company_name"] == "CHENGALVA AGRO FOODS PVT LTD"
    assert r["name_has_suffix"] is True
    assert r["district_state"] == "CHITTOOR ANDHRA PRADESH" and r["state_header"] == "ANDHRA PRADESH"
    assert r["item"] == "EDIBLE OIL" and r["filing_date"] == "02/05/2001"
    assert r["ack_no"] == 977                                # trailing integer of the first line
    assert r["source_file"] == "2012-05_a.pdf" and r["month"] == "2012-05" and r["layout"] == "text-layout"
    assert r["mem_no"] is None and r["mem_date"] is None and r["commenced"] is None


def test_state_heading_applies_to_following_records_until_replaced():
    rows = parse([
        "STATE : ASSAM",
        "   1 ALPHA STEELS PVT LTD   GUWAHATI   STEEL",
        "STATE: GOA",
        "   2 BETA CEMENT LTD   PANAJI   CEMENT",
    ])
    assert [r["state_header"] for r in rows] == ["ASSAM", "GOA"]
    assert rows[1]["district_state"] == "PANAJI GOA"


def test_state_heading_is_uppercased():
    assert parse(["STATE : Goa", "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL"])[0]["state_header"] == "GOA"


def test_wrapped_name_is_rejoined():
    rows = parse([
        "STATE : GUJARAT",
        "   1 GEMINI EDIBILES & FATS INDIA        KRISHNA         OIL",
        "     PRIVATE LIMITED.                                    ",
    ])
    assert rows[0]["company_name"] == "GEMINI EDIBILES & FATS INDIA PRIVATE LIMITED"
    assert rows[0]["name_has_suffix"] is True


def test_wrapped_name_not_joined_when_next_line_is_an_address():
    rows = parse([
        "STATE : GUJARAT",
        "   1 GEMINI EDIBILES & FATS INDIA        KRISHNA         OIL",
        "     PLOT 5 LTD COMPLEX                                  ",
    ])
    assert rows[0]["company_name"] == "GEMINI EDIBILES & FATS INDIA" and rows[0]["name_has_suffix"] is False


def test_wrapped_name_not_joined_when_continuation_is_indented_further():
    rows = parse([
        "STATE : GUJARAT",
        "   1 GEMINI EDIBILES & FATS INDIA        KRISHNA         OIL",
        "          PRIVATE LIMITED.                               ",
    ])
    assert rows[0]["company_name"] == "GEMINI EDIBILES & FATS INDIA"


def test_wrapped_name_continuation_longer_than_45_chars_is_ignored():
    long = "X" * 40 + " PRIVATE LIMITED"
    rows = parse(["STATE : GUJARAT", "   1 GEMINI EDIBILES        KRISHNA         OIL", "     " + long])
    assert rows[0]["company_name"] == "GEMINI EDIBILES"


def test_wrapped_name_continuation_searched_two_lines_down():
    rows = parse([
        "STATE : GUJARAT",
        "   1 GEMINI EDIBILES & FATS INDIA        KRISHNA         OIL",
        "                                                         ",
        "     PRIVATE LIMITED.                                    ",
    ])
    # the join loop looks at lines i+1 and i+2 only, and a blank line has no segment: the third line is reached
    assert rows[0]["company_name"] == "GEMINI EDIBILES & FATS INDIA PRIVATE LIMITED"


def test_name_with_suffix_is_not_extended():
    rows = parse(["STATE : GUJARAT", "   1 ALPHA STEELS PVT LTD        SURAT         STEEL", "     BETA CEMENT LTD         "])
    assert rows[0]["company_name"] == "ALPHA STEELS PVT LTD"


def test_name_has_suffix_flag_false_without_suffix():
    r = parse(["STATE : GUJARAT", "   1 ALPHA STEELS WORKS        SURAT         STEEL"])[0]
    assert r["name_has_suffix"] is False


def test_name_is_cleaned_and_uppercased():
    r = parse(["STATE : GUJARAT", "   1 alpha steels Ltd.,        SURAT         STEEL"])[0]
    assert r["company_name"] == "ALPHA STEELS LTD"


def test_multiple_records_and_blocks_do_not_leak():
    rows = parse([
        "STATE : GOA",
        "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL    10/01/2001",
        "     ADDRESS ONE                                 ",
        "   2 BETA CEMENT LTD   MARGAO   CEMENT   ",
        "     ADDRESS TWO              11/02/2002",
    ])
    assert [r["serial"] for r in rows] == [1, 2]
    assert rows[0]["filing_date"] == "10/01/2001" and rows[1]["filing_date"] == "11/02/2002"


def test_filing_date_looks_only_at_first_six_block_lines():
    block = [f"     LINE {i}" for i in range(6)] + ["     LATE 09/09/2009"]
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL"] + block)[0]
    assert r["filing_date"] is None
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL"] + block[:5] + ["     IN 08/08/2008"])[0]
    assert r["filing_date"] == "08/08/2008"


def test_filing_date_first_date_wins():
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL   01/01/2001", "     02/02/2002"])[0]
    assert r["filing_date"] == "01/01/2001"


def test_state_from_location_column_when_no_heading():
    rows = parse([
        "   1 ALPHA STEELS PVT LTD        SURAT                 STEEL     12",
        "     PLOT 5                      GUJARAT",
    ])
    r = rows[0]
    assert r["state_header"] == "GUJARAT" and r["district_state"] == "SURAT GUJARAT"


def test_state_from_district_segment_when_no_heading_and_no_block_state():
    r = parse(["   1 ALPHA STEELS PVT LTD        PUNE MAHARASHTRA        STEEL"])[0]
    assert r["state_header"] == "MAHARASHTRA"


def test_state_from_address_block_as_last_resort():
    r = parse(["   1 ALPHA STEELS PVT LTD        SURAT        STEEL",
               "     NR HIGHWAY",
               "     NR HIGHWAY",
               "     NR HIGHWAY",
               "     SURAT KARNATAKA"])[0]
    assert r["state_header"] == "KARNATAKA"


def test_heading_state_wins_over_location_column():
    r = parse(["STATE : ASSAM", "   1 ALPHA STEELS PVT LTD        SURAT                 STEEL", "     PLOT 5                      GUJARAT"])[0]
    assert r["state_header"] == "ASSAM"


def test_part_b_memorandum_fields():
    r = parse([
        "STATE : GOA",
        "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL",
        "     Memorandum No: 123 Date: 01/02/2020 Production Commenced on: 05/06/2021",
    ])[0]
    assert (r["mem_no"], r["mem_date"], r["commenced"]) == (123, "01/02/2020", "05/06/2021")


def test_part_b_memorandum_without_production_date():
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL", "     Memorandum No. 77 Date 03/04/2019"])[0]
    assert (r["mem_no"], r["mem_date"], r["commenced"]) == (77, "03/04/2019", None)


def test_ack_no_is_trailing_integer_of_first_line_only():
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL   55", "     999"])[0]
    assert r["ack_no"] == 55
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD   PANAJI   STEEL"])[0]
    assert r["ack_no"] is None


def test_item_is_none_when_fewer_than_three_segments():
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD   PANAJI"])[0]
    assert r["item"] is None and r["district_state"] == "PANAJI GOA"
    r = parse(["STATE : GOA", "   1 ALPHA STEELS PVT LTD"])[0]
    assert r["district_state"] == "GOA"


def test_page_number_comes_from_the_page_tuple():
    rows = t.parse_text([(1, "STATE : GOA\n   1 ALPHA STEELS PVT LTD   PANAJI   STEEL"), (2, "   2 BETA CEMENT LTD   PANAJI   CEMENT")], "2012-05_a.pdf")
    assert [r["page"] for r in rows] == [1, 2] and rows[1]["state_header"] == "GOA"       # state carries across pages


def test_no_records_in_plain_text():
    assert parse(["nothing here", "", "Telephone 12345"]) == []


# --------------------------------------------------------------------------- text_pages / ocr_page / parse_file
class Run:
    def __init__(self, out=""):
        self.stdout, self.calls = out, []

    def __call__(self, cmd, capture_output=None, text=None, check=None):
        self.calls.append(cmd)
        return SimpleNamespace(stdout=self.stdout, returncode=0)


def test_text_pages_uses_text_layer_and_skips_empty_trailing_page(monkeypatch):
    page = "x" * 150
    run = Run(page + "\f" + page + "\f")                   # pdftotext ends with a form feed: last chunk empty
    monkeypatch.setattr(t.subprocess, "run", run)
    got = list(t.text_pages("f.pdf"))
    assert [i for i, _ in got] == [1, 2] and run.calls[0] == [t.PDFTOTEXT, "-layout", "f.pdf", "-"]


def test_text_pages_ocrs_pages_without_a_usable_text_layer(monkeypatch):
    run = Run("x" * 150 + "\f" + "tiny" + "\f")
    monkeypatch.setattr(t.subprocess, "run", run)
    monkeypatch.setattr(t, "ocr_page", lambda pdf, i: f"OCR page {i}")
    got = dict(t.text_pages("f.pdf"))
    assert got[1] == "x" * 150 and got[2] == "OCR page 2"


def test_text_pages_keeps_short_text_when_ocr_returns_nothing(monkeypatch):
    monkeypatch.setattr(t.subprocess, "run", Run("tiny\f"))
    monkeypatch.setattr(t, "ocr_page", lambda pdf, i: None)
    assert dict(t.text_pages("f.pdf")) == {1: "tiny"}


def test_text_pages_page_of_exactly_100_chars_is_usable(monkeypatch):
    monkeypatch.setattr(t.subprocess, "run", Run("y" * 100 + "\f" + "z" * 99 + "\f"))
    monkeypatch.setattr(t, "ocr_page", lambda pdf, i: "OCR")
    got = dict(t.text_pages("f.pdf"))
    assert got[1] == "y" * 100 and got[2] == "OCR"


def test_ocr_page_returns_none_without_tesseract(monkeypatch):
    monkeypatch.setattr(t.os.path, "exists", lambda p: False)
    assert t.ocr_page("f.pdf", 1) is None


def test_ocr_page_rasterises_then_reads_png(monkeypatch):
    monkeypatch.setattr(t.os.path, "exists", lambda p: True)
    calls = []

    def fake_run(cmd, capture_output=None, text=None, check=None):
        calls.append(cmd)
        if cmd[0] == t.PDFTOPPM:
            stem = cmd[-1]
            open(stem + "-1.png", "wb").write(b"png")
            return SimpleNamespace(stdout="")
        return SimpleNamespace(stdout="  1 ALPHA STEELS PVT LTD  GOA  STEEL")
    monkeypatch.setattr(t.subprocess, "run", fake_run)
    out = t.ocr_page("f.pdf", 3, dpi=150)
    assert out.strip().startswith("1 ALPHA")
    pp, tess = calls
    assert pp[:9] == [t.PDFTOPPM, "-r", "150", "-f", "3", "-l", "3", "-png", "f.pdf"]
    assert tess[0] == t.TESS and tess[2:] == ["stdout", "--psm", "6", "-c", "preserve_interword_spaces=1"]


def test_parse_file_returns_rows_and_ocr_page_count(monkeypatch):
    txt = "STATE : GOA\n   1 ALPHA STEELS PVT LTD   PANAJI   STEEL\n" + "." * 120
    monkeypatch.setattr(t.subprocess, "run", Run(txt + "\f" + "short" + "\f"))
    monkeypatch.setattr(t, "ocr_page", lambda pdf, i: None)
    rows, n_ocr = t.parse_file("/x/2012-05_a.pdf")
    assert len(rows) == 1 and rows[0]["source_file"] == "2012-05_a.pdf" and rows[0]["month"] == "2012-05"
    assert n_ocr == 1                                   # the short page still counts as needing OCR even if OCR failed
