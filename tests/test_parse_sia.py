"""parse_sia: column classification, record assembly across lines and pages, and finalize()."""
import pytest

import parse_sia as ps
from pdfgen import make_pdf


# --------------------------------------------------------------------------- col_of
@pytest.mark.parametrize("x,col", [
    (0, "serial"), (84.9, "serial"), (85, "company"), (217.9, "company"), (218, "dist_state"), (299.9, "dist_state"),
    (300, "item"), (411.9, "item"), (412, "capacity"), (477.9, "capacity"), (478, "iem"), (9998, "iem"),
    (9999, "iem"), (20000, "iem"), (-5, "iem"),
])
def test_col_of_boundaries(x, col):
    assert ps.col_of(x) == col


# --------------------------------------------------------------------------- regexes
@pytest.mark.parametrize("text,out", [
    ("12/N/SIA/IMO/2019", ("12", "N", "2019")),
    ("12 / P / SIA / IM O / 2019", ("12", "P", "2019")),
    ("99999/N/SIA/IMO/2001", ("99999", "N", "2001")),
])
def test_iem_re(text, out):
    assert ps.IEM_RE.search(text).groups() == out


@pytest.mark.parametrize("text", ["12/X/SIA/IMO/2019", "12/N/SIA/IMO/19", "12/N/SIA/IMO", "/N/SIA/IMO/2019"])
def test_iem_re_rejects(text):
    assert ps.IEM_RE.search(text) is None


@pytest.mark.parametrize("t,ok", [("300", True), ("1,200", True), ("1,200.50", True), ("0.5", True), ("MT", False), ("12MT", False), (".5", False), ("", False)])
def test_cap_re(t, ok):
    assert bool(ps.CAP_RE.match(t)) is ok


# --------------------------------------------------------------------------- finalize
def rec(**over):
    d = {"serial": 3, "page": 2, "company": ["ALPHA", "STEELS"], "district_state": ["PUNE", "MAHARASHTRA"], "item": ["STEEL", "BARS"],
         "capacity": ["300", "MT"], "iem": ["12/N/SIA/IMO/2019", "05/03/2019"], "raw": []}
    d.update(over)
    return d


def test_finalize_full_record():
    assert ps.finalize(rec()) == {
        "serial": 3, "company_and_address": "ALPHA STEELS", "district": "PUNE", "state": "MAHARASHTRA", "item_of_manufacture": "STEEL BARS",
        "capacity": "300", "capacity_unit": "MT", "iem_no": 12, "iem_year": 2019, "filing_date": "05/03/2019", "source_page": 2}


def test_finalize_capacity_commas_removed_and_unit_joined():
    r = ps.finalize(rec(capacity=["1,200.5", "Tonnes", "per", "annum"]))
    assert r["capacity"] == "1200.5" and r["capacity_unit"] == "Tonnes per annum"


def test_finalize_capacity_number_without_unit():
    r = ps.finalize(rec(capacity=["300"]))
    assert r["capacity"] == "300" and r["capacity_unit"] is None


def test_finalize_non_numeric_capacity_is_all_unit():
    r = ps.finalize(rec(capacity=["NIL", "MT"]))
    assert r["capacity"] is None and r["capacity_unit"] == "NIL MT"


def test_finalize_empty_capacity():
    r = ps.finalize(rec(capacity=[]))
    assert r["capacity"] is None and r["capacity_unit"] is None


@pytest.mark.parametrize("ds,district,state", [
    ([], None, None), (["PUNE"], "PUNE", None), (["PUNE", "MAHARASHTRA"], "PUNE", "MAHARASHTRA"),
    (["PUNE", "WEST", "BENGAL"], "PUNE", "WEST BENGAL")])
def test_finalize_district_state_split(ds, district, state):
    r = ps.finalize(rec(district_state=ds))
    assert (r["district"], r["state"]) == (district, state)


def test_finalize_no_iem_or_date():
    r = ps.finalize(rec(iem=["nothing", "here"]))
    assert (r["iem_no"], r["iem_year"], r["filing_date"]) == (None, None, None)


def test_finalize_iem_without_date_keeps_number():
    r = ps.finalize(rec(iem=["12/N/SIA/IMO/2019"]))
    assert (r["iem_no"], r["iem_year"], r["filing_date"]) == (12, 2019, None)


def test_finalize_iem_split_over_words_still_matches():
    r = ps.finalize(rec(iem=["12", "/", "N", "/", "SIA", "/", "IM", "O", "/", "2019", "01/02/2019"]))
    assert (r["iem_no"], r["iem_year"], r["filing_date"]) == (12, 2019, "01/02/2019")


def test_finalize_date_regex_requires_two_digit_day_and_month():
    assert ps.finalize(rec(iem=["12/N/SIA/IMO/2019", "5/3/2019"]))["filing_date"] is None


# --------------------------------------------------------------------------- parse_pdf on a synthetic PDF
HEADER = [(30, 40, "Sr."), (45, 40, "No."), (90, 40, "Company"), (130, 40, "Name"), (222, 40, "District"), (260, 40, "State"),
          (305, 40, "Item"), (330, 40, "of"), (345, 40, "Manufacture"), (415, 40, "Capacity"), (482, 40, "IEM"), (505, 40, "No")]


def record(y, serial, company, district, state, item, cap, unit, iem, date):
    return [(30, y, str(serial)), (90, y, company), (222, y, district), (305, y, item), (415, y, cap), (440, y, unit), (482, y, iem),
            (222, y + 9, state), (482, y + 9, date)]


def test_parse_pdf_reads_records_across_lines(tmp_path):
    items = HEADER + record(70, 1, "ALPHA STEELS PVT LTD", "PUNE", "MAHARASHTRA", "STEEL", "300", "MT", "12/N/SIA/IMO/2019", "05/03/2019") \
        + record(100, 2, "BETA CEMENT LTD", "SURAT", "GUJARAT", "CEMENT", "1,000", "TPA", "13/N/SIA/IMO/2019", "06/03/2019")
    recs = [ps.finalize(r) for r in ps.parse_pdf(make_pdf(tmp_path / "a.pdf", [items]))]
    assert [r["serial"] for r in recs] == [1, 2]
    a, b = recs
    assert a["company_and_address"] == "ALPHA STEELS PVT LTD" and a["district"] == "PUNE" and a["state"] == "MAHARASHTRA"
    assert a["item_of_manufacture"] == "STEEL" and a["capacity"] == "300" and a["capacity_unit"] == "MT"
    assert (a["iem_no"], a["iem_year"], a["filing_date"], a["source_page"]) == (12, 2019, "05/03/2019", 1)
    assert b["capacity"] == "1000" and b["state"] == "GUJARAT" and b["filing_date"] == "06/03/2019"


def test_parse_pdf_header_before_first_record_is_ignored(tmp_path):
    raw = ps.parse_pdf(make_pdf(tmp_path / "a.pdf", [HEADER]))
    assert raw == []


def test_parse_pdf_record_continues_across_pages_and_repeated_headers_are_dropped(tmp_path):
    p1 = HEADER + record(70, 1, "ALPHA STEELS PVT LTD", "PUNE", "MAHARASHTRA", "STEEL", "300", "MT", "12/N/SIA/IMO/2019", "05/03/2019")
    p2 = HEADER + [(90, 70, "PLOT"), (115, 70, "5"), (90, 80, "MIDC")] + record(110, 2, "BETA CEMENT LTD", "SURAT", "GUJARAT", "CEMENT", "10", "MT",
                                                                              "13/N/SIA/IMO/2019", "06/03/2019")
    recs = ps.parse_pdf(make_pdf(tmp_path / "a.pdf", [p1, p2]))
    assert [r["serial"] for r in recs] == [1, 2] and [r["page"] for r in recs] == [1, 2]
    assert recs[0]["company"][:4] == ["ALPHA", "STEELS", "PVT", "LTD"] and recs[0]["company"][4:] == ["PLOT", "5", "MIDC"]
    assert "Company" not in recs[1]["company"] and "District" not in sum((r["district_state"] for r in recs), [])


def test_parse_pdf_stray_number_in_serial_column_does_not_start_a_record(tmp_path):
    items = HEADER + record(70, 1, "ALPHA STEELS PVT LTD", "PUNE", "MAHARASHTRA", "STEEL", "300", "MT", "12/N/SIA/IMO/2019", "05/03/2019") \
        + [(30, 95, "x"), (90, 95, "ANNEX")]
    recs = ps.parse_pdf(make_pdf(tmp_path / "a.pdf", [items]))
    assert len(recs) == 1 and "ANNEX" in recs[0]["company"]


def test_parse_pdf_raw_lines_kept_for_audit(tmp_path):
    items = HEADER + record(70, 1, "ALPHA STEELS PVT LTD", "PUNE", "MAHARASHTRA", "STEEL", "300", "MT", "12/N/SIA/IMO/2019", "05/03/2019")
    r = ps.parse_pdf(make_pdf(tmp_path / "a.pdf", [items]))[0]
    assert len(r["raw"]) == 2 and "ALPHA STEELS PVT LTD" in r["raw"][0] and "05/03/2019" in r["raw"][1]


def test_parse_pdf_records_ten_points_apart_are_not_merged_into_one_line(tmp_path):
    items = list(HEADER)
    for i in (1, 2, 3):
        y = 60 + 10 * i
        items += [(30, y, str(i)), (90, y, f"CO{i} LTD"), (222, y, "PUNE"), (482, y, f"{i}/N/SIA/IMO/2019")]
    recs = ps.parse_pdf(make_pdf(tmp_path / "a.pdf", [items]))
    assert [r["serial"] for r in recs] == [1, 2, 3]
