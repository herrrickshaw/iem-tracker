"""parse_partA_coords: name cutting, column inference and record assembly on synthetic PDFs written with pymupdf."""
import pymupdf
import pytest

import parse_partA_coords as pc
from pdfgen import make_pdf

# --------------------------------------------------------------------------- cut_name
@pytest.mark.parametrize("text,out", [
    ("ALPHA STEELS PVT LTD PLOT 5", ("ALPHA STEELS PVT LTD", True)),
    ("ALPHA STEELS PRIVATE LIMITED, PLOT 5", ("ALPHA STEELS PRIVATE LIMITED", True)),
    ("ALPHA STEELS LTD.", ("ALPHA STEELS LTD", True)),
    ("ALPHA STEELS LLP", ("ALPHA STEELS LLP", True)),
    ("  ALPHA   STEELS\n LIMITED  ", ("ALPHA STEELS LIMITED", True)),
    ("alpha steels pvt. ltd. x", ("alpha steels pvt. ltd", True)),
    ("AB LTD", ("AB LTD", True)),                                    # suffix end == 6 is accepted
    ("A LTD", ("A LTD", False)),                                     # end == 5 < 6: falls back to the text itself
    ("ALPHA STEELS WORKS", ("ALPHA STEELS WORKS", False)),
    ("X" * 100, ("X" * 60, False)),                                  # fallback is the first 60 characters
    ("", ("", False)),
])
def test_cut_name(text, out):
    assert pc.cut_name(text) == out


def test_cut_name_suffix_must_be_a_whole_word():
    assert pc.cut_name("PRIVATELIMITED OF X")[1] is False
    assert pc.cut_name("ALPHA LTDX STEELS")[1] is False


def test_cut_name_stops_at_first_suffix():
    assert pc.cut_name("ALPHA PVT LTD BETA PVT LTD") == ("ALPHA PVT LTD", True)


# --------------------------------------------------------------------------- last_phrase
@pytest.mark.parametrize("tokens,out", [
    (["PLOT", "5", "ALPHA", "STEELS", "PVT", "LTD"], "ALPHA STEELS PVT LTD"),          # walks back to the token holding a digit
    (["ALPHA", "STEELS", "PRIVATE", "LIMITED"], "ALPHA STEELS PRIVATE LIMITED"),
    (["400001", "BETA", "CEMENT", "LTD.", "NH", "8"], "BETA CEMENT LTD"),             # trailing address after the suffix is cut
    (["GUJARAT", "ALPHA", "STEELS", "LTD"], "ALPHA STEELS LTD"),                       # leading state token dropped
    (["WEST", "BENGAL", "ALPHA", "STEELS", "LTD"], "ALPHA STEELS LTD"),                # a two-word state is peeled off token by token
    (["WEST", "BENGAL", "ALPHA", "LTD"], "BENGAL ALPHA LTD"),                           # ... but only while more than 3 tokens remain
    (["ALPHA", "STEELS"], None),
    ([], None),
])
def test_last_phrase(tokens, out):
    assert pc.last_phrase(tokens) == out


def test_last_phrase_keeps_short_names_that_start_with_a_state_token():
    # the state token is only dropped while more than 3 tokens remain
    assert pc.last_phrase(["GOA", "STEELS", "LTD"]) == "GOA STEELS LTD"


def test_last_phrase_walks_back_at_most_ten_tokens():
    toks = [chr(65 + i) * 3 for i in range(15)] + ["LTD"]
    assert len(pc.last_phrase(toks).split()) == 11


def test_last_phrase_takes_the_last_suffix_not_the_first():
    assert pc.last_phrase(["1", "ALPHA", "LTD", "2", "BETA", "CEMENT", "PVT", "LTD"]) == "BETA CEMENT PVT LTD"


def test_last_phrase_two_token_suffix_preferred():
    assert pc.last_phrase(["1", "ALPHA", "PRIVATE", "LIMITED"]) == "ALPHA PRIVATE LIMITED"


# --------------------------------------------------------------------------- col_of
COLS = [("company", 58), ("district", 228), ("item", 328), ("capacity", 418), ("iem", 478)]


@pytest.mark.parametrize("x,text,col", [
    (30, "1", "serial"), (23, "12", "serial"), (44, "1234", "serial"), (22.9, "1", "company"), (44.1, "1", "company"),
    (30, "12345", "company"), (30, "AB", "company"), (60, "1", "company"), (57.9, "A", "company"),
    (58, "A", "company"), (227.9, "A", "company"), (228, "A", "district"), (328, "A", "item"), (418, "A", "capacity"),
    (478, "A", "iem"), (9000, "A", "iem"),
])
def test_col_of(x, text, col):
    assert pc.col_of(x, COLS, 30, text) == col


def test_col_of_without_serial_edge_never_returns_serial():
    assert pc.col_of(30, COLS, None, "1") == "company"


def test_col_of_left_of_all_columns_is_the_first_role():
    assert pc.col_of(5, COLS, None, "A") == "company"


# --------------------------------------------------------------------------- synthetic Part A PDF
HEADER = [(30, 40, "Sr."), (45, 40, "No."), (100, 40, "Company"), (120, 40, "Name"), (235, 40, "District"), (340, 40, "Item"),
          (425, 40, "Capacity"), (490, 40, "IEM"), (510, 40, "No")]
NAMES = ["ALPHA STEELS PVT LTD", "BETA CEMENT LTD", "GAMMA GLASS LIMITED", "DELTA DYES PRIVATE LIMITED", "EPSILON METALS LLP",
         "ZETA PAPERS PVT LTD"]
DIST = ["PUNE", "SURAT", "KOCHI", "VAPI", "HUBLI", "CUTTACK"]
STATE = ["MAHARASHTRA", "GUJARAT", "KERALA", "GUJARAT", "KARNATAKA", "ODISHA"]


def rec(y, n, name, addr, dist, state, item="STEEL", cap="300", iem=None, date=None, serial_text=None):
    iem = iem or f"{10 + n}/N/SIA/IMO/2019,"
    date = date or f"0{n}/03/2019"
    out = [(30, y, serial_text or str(n)), (60, y, name), (230, y, dist), (230, y + 9, state), (330, y, item), (420, y, cap),
           (480, y, iem), (480, y + 9, date)]
    if addr:
        out.append((60, y + 9, addr))
    return out


def standard(n=6, addr="PLOT 5 MIDC"):
    items = list(HEADER)
    for i in range(1, n + 1):
        items += rec(70 + i * 30, i, NAMES[i - 1], addr, DIST[i - 1], STATE[i - 1])
    return items


@pytest.fixture
def pdf(tmp_path):
    def make(*pages, name="a.pdf", **kw):
        return make_pdf(tmp_path / name, list(pages), **kw)
    return make


@pytest.fixture
def doc(pdf):
    return pymupdf.open(pdf(standard()))


def test_header_positions_finds_first_occurrence_of_each_header_word(doc):
    pos = pc.header_positions(doc)
    assert set(pos) == {"Company", "District", "Item", "Capacity", "IEM"}
    assert pos["Company"][0] == 100 and pos["IEM"][0] == 490


def test_header_positions_requires_company_district_and_item(pdf):
    d = pymupdf.open(pdf([(100, 40, "Company"), (235, 40, "District")]))
    assert pc.header_positions(d) is None


def test_header_positions_searches_first_three_pages_only(pdf):
    blank = [(10, 10, "x")]
    d = pymupdf.open(pdf(blank, blank, blank, HEADER))
    assert pc.header_positions(d) is None
    d = pymupdf.open(pdf(blank, blank, HEADER))
    assert pc.header_positions(d) is not None


def test_header_positions_uses_first_page_with_a_full_header(pdf):
    p1 = [(100, 40, "Company")]
    d = pymupdf.open(pdf(p1, HEADER))
    assert pc.header_positions(d)["Company"][0] == 100


def test_gutter_columns_split_on_empty_strips(doc):
    pos = pc.header_positions(doc)
    cols, serial = pc.gutter_columns(doc, pos, ["company", "district", "item", "capacity", "iem"])
    assert serial == 30
    assert cols == [("company", 58), ("district", 228), ("item", 328), ("capacity", 418), ("iem", 478)]


def test_gutter_columns_none_when_block_count_differs(doc):
    pos = pc.header_positions(doc)
    assert pc.gutter_columns(doc, pos, ["company", "district", "item"]) is None


def test_gutter_columns_none_for_blank_body(pdf):
    d = pymupdf.open(pdf(HEADER))
    assert pc.gutter_columns(d, pc.header_positions(d), ["company", "district", "item", "capacity", "iem"]) is None


def test_infer_columns_uses_gutters_when_available(doc):
    cols, serial, method = pc.infer_columns(doc, pc.header_positions(doc))
    assert method == "gutter" and serial == 30 and dict(cols)["iem"] == 478


def test_infer_columns_falls_back_to_header_positions(pdf):
    items = standard() + [(60, 100, "M" * 40)]                   # one long word fills the company/district gutter, merging two blocks
    d = pymupdf.open(pdf(items))
    cols, serial, method = pc.infer_columns(d, pc.header_positions(d))
    assert method == "header"
    assert cols == [("company", 88), ("district", 223), ("item", 287.5), ("capacity", 413), ("iem", 478)]
    assert serial == 88                                          # company is first -> its edge


def test_infer_columns_header_fallback_item_before_district(pdf):
    hdr = [(30, 40, "Sr."), (100, 40, "Company"), (200, 40, "Item"), (300, 40, "District")]
    body = [(30, 70, "1"), (60, 70, "ALPHA LTD"), (190, 70, "STEEL"), (290, 70, "PUNE")]
    d = pymupdf.open(pdf(hdr + body + [(60, 85, "M" * 50)]))          # one very long word fuses all blocks -> gutters unusable
    cols, serial, method = pc.infer_columns(d, pc.header_positions(d))
    assert method == "header"
    assert cols == [("company", 88), ("item", 188), ("district", 288)]       # x - 12 each; no midpoint when item is left of district
    assert serial == 88


def test_serial_x_of_is_most_common_digit_column_left_of_company(doc):
    assert pc.serial_x_of(doc, 100) == 30


def test_serial_x_of_none_when_no_digits(pdf):
    assert pc.serial_x_of(pymupdf.open(pdf(HEADER)), 100) is None


def test_serial_x_of_ignores_long_numbers_and_numbers_right_of_header(pdf):
    d = pymupdf.open(pdf([(30, 70, "12345"), (200, 70, "7"), (30, 80, "8")]))
    assert pc.serial_x_of(d, 100) == 30                           # only the 1-digit "8" counts


def test_iem_left_of_finds_the_line_start_cluster_near_the_iem_header(doc):
    pos = pc.header_positions(doc)
    cols = pc.infer_columns(doc, pos)[0]
    assert pc.iem_left_of(doc, cols, pos["IEM"][0]) == 477


def test_iem_left_of_none_without_strong_clusters(pdf):
    d = pymupdf.open(pdf([(10, 10, "x")]))
    assert pc.iem_left_of(d, [], 490) is None


# --------------------------------------------------------------------------- parse_pdf: standard layout
def test_parse_pdf_no_header(pdf):
    assert pc.parse_pdf(pdf([(10, 10, "nothing")])) == ([], "no header")


def test_parse_pdf_standard_records(pdf):
    rows, status = pc.parse_pdf(pdf(standard()))
    assert status == "ok" and [r["serial"] for r in rows] == [1, 2, 3, 4, 5, 6]
    r = rows[0]
    assert (r["company_name"], r["name_has_suffix"]) == ("ALPHA STEELS PVT LTD", True)
    assert r["company_text"] == "ALPHA STEELS PVT LTD PLOT 5 MIDC"
    assert r["district_state"] == "PUNE MAHARASHTRA" and r["item"] == "STEEL"
    assert (r["iem_no"], r["iem_year"], r["filing_date"]) == (11, 2019, "01/03/2019")
    assert r["layout"] == "gutter" and r["page"] == 1
    assert rows[3]["company_name"] == "DELTA DYES PRIVATE LIMITED"
    assert rows[5]["district_state"] == "CUTTACK ODISHA"


def test_parse_pdf_iem_year_comes_from_the_date_not_the_number(pdf):
    items = list(HEADER)
    for i in range(1, 7):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1], iem=f"{i}/N/SIA/IMO/2010,", date=f"0{i}/03/2012")
    rows, _ = pc.parse_pdf(pdf(items))
    assert rows[0]["iem_year"] == 2012 and rows[0]["filing_date"] == "01/03/2012" and rows[0]["iem_no"] == 1


def test_parse_pdf_place_tehsil_lines_are_dropped_from_company_text(pdf):
    items = list(HEADER)
    for i in range(1, 7):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    items += [(60, 70 + 30 + 18, "Place (Tehsil) Haveli")]
    rows, _ = pc.parse_pdf(pdf(items))
    assert "Haveli" not in rows[0]["company_text"] and "Tehsil" not in rows[0]["company_text"]


def test_parse_pdf_record_without_iem_has_none(pdf):
    items = list(HEADER)
    for i in range(1, 7):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    items = [it for it in items if not (it[0] == 480 and 100 <= it[1] <= 111)]       # drop record 1's IEM words
    rows, _ = pc.parse_pdf(pdf(items))
    r = rows[0]
    assert (r["iem_no"], r["iem_year"], r["filing_date"]) == (None, None, None)


def test_parse_pdf_serial_numbers_must_increase_and_not_jump_more_than_six(pdf):
    items = list(HEADER)
    serials = ["1", "2", "2", "30", "3", "4", "5"]
    for i, s in enumerate(serials, 1):
        items += rec(70 + i * 30, i, NAMES[(i - 1) % 6], "PLOT 5", DIST[(i - 1) % 6], STATE[(i - 1) % 6], serial_text=s)
    rows, _ = pc.parse_pdf(pdf(items))
    assert [r["serial"] for r in rows] == [1, 2, 3, 4, 5]


def test_parse_pdf_serial_may_skip_up_to_six(pdf):
    items = list(HEADER)
    for i, s in enumerate(["1", "7", "8", "9", "10", "11"], 1):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1], serial_text=s)
    rows, _ = pc.parse_pdf(pdf(items))
    assert [r["serial"] for r in rows] == [1, 7, 8, 9, 10, 11]                          # 1 -> 7 is a jump of exactly 6
    items = list(HEADER)
    for i, s in enumerate(["1", "8", "9", "10", "11", "12"], 1):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1], serial_text=s)
    rows, _ = pc.parse_pdf(pdf(items))
    assert [r["serial"] for r in rows] == [1]                                           # 1 -> 8 exceeds the window; nothing after counts either


def test_parse_pdf_multi_digit_serial_split_into_side_by_side_digits(pdf):
    items = list(HEADER)
    for i in range(1, 5):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    items += [(30, 220, "1"), (40, 220, "0"), (60, 220, "EPSILON METALS LLP"), (230, 220, "HUBLI"), (480, 220, "15/N/SIA/IMO/2019,")]
    rows, _ = pc.parse_pdf(pdf(items))
    assert [r["serial"] for r in rows] == [1, 2, 3, 4, 10]


def test_parse_pdf_multi_digit_serial_stacked_digits(pdf):
    items = list(HEADER)
    for i in range(1, 5):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    items += [(30, 220, "1"), (30, 228, "0"), (60, 220, "EPSILON METALS LLP"), (480, 220, "15/N/SIA/IMO/2019,")]
    rows, _ = pc.parse_pdf(pdf(items))
    assert [r["serial"] for r in rows] == [1, 2, 3, 4, 10]


def test_parse_pdf_digit_chain_falls_back_to_individual_digits(pdf):
    # chain "5","6" is read as 56 (too far from 4) -> falls back to digit 5, then 6 is the next record
    items = list(HEADER)
    for i in range(1, 5):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    items += [(30, 220, "5"), (40, 220, "6"), (60, 220, "EPSILON METALS LLP")]
    rows, _ = pc.parse_pdf(pdf(items))
    assert [r["serial"] for r in rows] == [1, 2, 3, 4, 5, 6]


def test_parse_pdf_month_name_dates(pdf):
    items = list(HEADER)
    for i in range(1, 7):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1], iem=f"{100 + i}", date="05 March 2019")
    rows, _ = pc.parse_pdf(pdf(items))
    r = rows[0]
    assert (r["iem_no"], r["iem_year"], r["filing_date"]) == (101, 2019, "05 March 2019")


def test_parse_pdf_split_month_name_is_repaired(pdf):
    items = list(HEADER)
    for i in range(1, 7):
        items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1], iem=f"{100 + i}", date="05 Januar y 2019")
    rows, _ = pc.parse_pdf(pdf(items))
    assert rows[0]["filing_date"] == "05 January 2019" and "January" in rows[0]["iem_text"]


# --------------------------------------------------------------------------- continuation across pages
def two_page(name_p2=True):
    p1 = list(HEADER)
    for i in range(1, 5):
        p1 += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    p1 += [(30, 800, "5"), (60, 800, "EPSILON METALS"), (230, 800, "HUBLI"), (330, 800, "STEEL"), (480, 800, "15/N/SIA/IMO/2019,")]
    p2 = list(HEADER) + [(230, 70, "KARNATAKA"), (480, 70, "05/03/2019")]
    if name_p2:
        p2 += [(60, 70, "LIMITED")]
    return p1, p2


def test_parse_pdf_record_continues_onto_next_page_district_and_iem(pdf):
    rows, _ = pc.parse_pdf(pdf(*two_page(name_p2=False)))
    r = rows[4]
    assert r["serial"] == 5 and r["page"] == 1
    assert "KARNATAKA" in r["district_state"] and r["filing_date"] == "05/03/2019" and r["iem_no"] == 15


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "parse_partA_coords.parse_pdf keeps a company line only if `min(y) >= sy - 3 or r['page'] != r.get('p0', r['page'])`; "
    "'p0' is never set on the record dict so the second clause is always False. A company-name line that continues on the "
    "next page has a smaller y than the serial line and is dropped, while district/item/IEM words from the same page are kept."))
def test_parse_pdf_company_name_wrapping_onto_next_page_is_kept(pdf):
    rows, _ = pc.parse_pdf(pdf(*two_page()))
    r = rows[4]
    assert r["company_name"] == "EPSILON METALS LIMITED" and r["name_has_suffix"] is True


def test_parse_pdf_header_repeated_on_each_page_is_not_data(pdf):
    p1, p2 = two_page(name_p2=False)
    rows, _ = pc.parse_pdf(pdf(p1, p2))
    assert all("Company" not in r["company_text"] and "District" not in r["district_state"] for r in rows)


def test_parse_pdf_continuation_page_without_header(pdf):
    p1 = list(HEADER)
    for i in range(1, 4):
        p1 += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    p2 = []
    for i in range(4, 7):
        p2 += rec(40 + (i - 3) * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1])
    rows, _ = pc.parse_pdf(pdf(p1, p2))
    assert [r["serial"] for r in rows] == [1, 2, 3, 4, 5, 6] and [r["page"] for r in rows] == [1, 1, 1, 2, 2, 2]


# --------------------------------------------------------------------------- serial in the middle of the record
def mid_serial(names=NAMES, with_phrase=True):
    items = list(HEADER)
    y = 70
    for i, n in enumerate(names, 1):
        items.append((60, y, n))
        items += [(30, y + 9, str(i)), (230, y + 9, DIST[i - 1]), (230, y + 18, STATE[i - 1]), (330, y + 9, "STEEL"), (420, y + 9, "300"),
                  (480, y + 9, f"{10 + i}/N/SIA/IMO/2019,"), (480, y + 18, f"0{i}/03/2019")]
        items += [(60, y + 9, "PLOT NO 5 INDUSTRIAL AREA PHASE TWO"), (60, y + 18, "NEAR MAIN ROAD CROSSING POST 400001")]
        y += 40
    return items


def test_parse_pdf_mid_serial_layout_reads_name_from_previous_block(pdf):
    rows, status = pc.parse_pdf(pdf(mid_serial()))
    assert status == "ok"
    assert [r["company_name"] for r in rows] == NAMES
    assert all(r["layout"] == "mid-serial" and r["name_has_suffix"] for r in rows)


def test_parse_pdf_mid_serial_unresolved_when_a_block_has_no_suffix(pdf):
    names = list(NAMES); names[2] = "GAMMA GLASS WORKS"
    rows, _ = pc.parse_pdf(pdf(mid_serial(names)))
    by = {r["serial"]: r for r in rows}
    assert by[3]["layout"] == "mid-serial-unresolved" and by[1]["layout"] == "mid-serial"
    # the unresolved record keeps its first-pass name: address text up to and including the NEXT record's legal suffix
    assert by[3]["company_name"] == "PLOT NO 5 INDUSTRIAL AREA PHASE TWO NEAR MAIN ROAD CROSSING POST 400001 DELTA DYES PRIVATE LIMITED"
    assert by[4]["company_name"] == "DELTA DYES PRIVATE LIMITED" and by[2]["company_name"] == "BETA CEMENT LTD"


def test_parse_pdf_standard_layout_is_not_rewritten(pdf):
    rows, _ = pc.parse_pdf(pdf(standard()))
    assert {r["layout"] for r in rows} == {"gutter"}


def test_stream_names_reads_legal_names_from_the_company_column(pdf):
    d = pymupdf.open(pdf(mid_serial()))
    pos = pc.header_positions(d)
    cols, serial_edge, _ = pc.infer_columns(d, pos)
    got = pc.stream_names(d, pos, cols, serial_edge)
    assert [n for _, n in got] == NAMES and {p for p, _ in got} == {1}


def test_stream_names_walks_back_at_most_ten_tokens(pdf):
    items = [it for it in mid_serial(NAMES) if it[2] != NAMES[0]]
    items += [(60, 62, "AA BB CC DD EE FF GG"), (60, 70, "HH II JJ KK LL MM PVT LTD")]
    d = pymupdf.open(pdf(items))
    pos = pc.header_positions(d)
    cols, serial_edge, _ = pc.infer_columns(d, pos)
    first = pc.stream_names(d, pos, cols, serial_edge)[0][1].split()
    assert first == "CC DD EE FF GG HH II JJ KK LL MM PVT LTD".split()[-12:]      # 10 tokens back from the suffix, plus the 2-token suffix


def _std(pdf, names):
    items = list(HEADER)
    for i, n in enumerate(names, 1):
        items += rec(70 + i * 30, i, n, "PLOT 5", DIST[i - 1], STATE[i - 1])
    return pc.parse_pdf(pdf(items))[0]


def test_two_bad_names_of_six_trigger_the_mid_serial_pass(pdf):
    names = list(NAMES); names[1] = "BETA CEMENT WORKS"; names[3] = "DELTA DYES WORKS"
    assert {r["layout"] for r in _std(pdf, names)} <= {"mid-serial", "mid-serial-unresolved"}


def test_one_bad_name_of_four_is_exactly_the_threshold_and_does_not_trigger(pdf):
    names = list(NAMES[:4]); names[1] = "BETA CEMENT WORKS"
    assert {r["layout"] for r in _std(pdf, names)} == {"gutter"}


def test_iem_column_cluster_needs_five_line_starts(pdf):
    def with_unit(n):
        items = list(HEADER)
        for i in range(1, n + 1):
            items += rec(70 + i * 30, i, NAMES[i - 1], "PLOT 5", DIST[i - 1], STATE[i - 1]) + [(445, 70 + i * 30, "UNIT"), (470, 70 + i * 30, "MT")]
        return pc.parse_pdf(pdf(items))[0]
    assert "MT" in with_unit(4)[0]["iem_text"]          # 4 line starts: no strong cluster -> fallback edge (iem column - 10)
    assert "MT" not in with_unit(5)[0]["iem_text"]      # 5 line starts: cluster found, edge = cluster - 3
