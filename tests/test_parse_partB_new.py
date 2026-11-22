"""parse_partB_new: header/column helpers with fake pages, and parse_pdf end-to-end on synthetic 9-column PDFs."""
import pymupdf
import pytest

import parse_partB_new as pb
from pdfgen import make_pdf


def W(x0, y0, text, x1=None, y1=None):
    return (x0, y0, x1 if x1 is not None else x0 + 10, y1 if y1 is not None else y0 + 8, text)


class Page:
    def __init__(self, words):
        self.words = words

    def get_text(self, kind):
        assert kind == "words"
        return self.words


# --------------------------------------------------------------------------- header_bottom
def test_header_bottom_none_without_production_word():
    assert pb.header_bottom([W(10, 10, "Sr.")]) is None


def test_header_bottom_is_lowest_header_word_near_production():
    words = [W(10, 40, "Production", y1=48), W(10, 50, "Commenced", y1=58), W(10, 60, "On", y1=70),
             W(10, 90, "ALPHA", y1=98)]                                 # body word (not a header word)
    assert pb.header_bottom(words) == 70


def test_header_bottom_window_is_six_above_and_forty_below_production():
    prod = W(10, 100, "Production", y1=108)
    assert pb.header_bottom([prod, W(10, 94, "Sr.", y1=99)]) == 108                  # inside the window but lower than Production's own bottom
    assert pb.header_bottom([prod, W(10, 93.9, "Sr.", y1=200)]) == 108               # 6.1 above -> outside the window
    assert pb.header_bottom([prod, W(10, 94, "Sr.", y1=150)]) == 150                 # exactly 6 above -> inside
    assert pb.header_bottom([prod, W(10, 140, "Sr.", y1=190)]) == 190                # exactly 40 below -> inside
    assert pb.header_bottom([prod, W(10, 140.1, "Sr.", y1=190)]) == 108


def test_header_bottom_uses_first_production_word_only():
    words = [W(10, 40, "Production", y1=48), W(10, 400, "Production", y1=408), W(10, 410, "Sr.", y1=450)]
    assert pb.header_bottom(words) == 48


def test_header_bottom_ignores_non_header_words_in_window():
    assert pb.header_bottom([W(10, 40, "Production", y1=48), W(10, 50, "ALPHA", y1=90)]) == 48


# --------------------------------------------------------------------------- col_index
@pytest.mark.parametrize("x,idx", [(0, 0), (28, 0), (27.9, 0), (57.9, 0), (58, 1), (167.9, 1), (168, 2), (500, 8)])
def test_col_index(x, idx):
    # a word belongs to the last block whose start minus 2 it has reached
    assert pb.col_index(x, [30, 60, 170, 230, 290, 340, 400, 440, 500]) == idx


def test_col_index_right_of_everything_is_last_column():
    assert pb.col_index(9999, [10, 50, 90]) == 2


def test_col_index_left_of_everything_is_first_column():
    assert pb.col_index(-5, [10, 50, 90]) == 0


# --------------------------------------------------------------------------- blocks_of
def doc_of(*pages):
    return [Page(p) for p in pages]


def table_words(n_cols=9, y=60, gap=20, width=30):
    out = []
    for c in range(n_cols):
        for row in range(5):
            out.append(W(c * (width + gap), y + 12 * row, "x", x1=c * (width + gap) + width, y1=y + 12 * row + 8))
    return out


HDR_WORDS = [W(0, 20, "Production", y1=28), W(0, 30, "Sr.", y1=38)]


def test_blocks_of_returns_left_edge_of_each_column_block():
    assert pb.blocks_of(doc_of(HDR_WORDS + table_words())) == [c * 50 for c in range(9)]


def test_blocks_of_none_without_header():
    assert pb.blocks_of(doc_of(table_words())) is None


def test_blocks_of_ignores_words_above_header_bottom():
    words = HDR_WORDS + [W(5, 10, "title", x1=400)] + table_words()
    assert pb.blocks_of(doc_of(words)) == [c * 50 for c in range(9)]


def test_blocks_of_gutter_must_be_three_points_wide():
    w1 = table_words(n_cols=2, gap=2)                 # columns 2pt apart: no 3-pt empty strip between them
    assert len(pb.blocks_of(doc_of(HDR_WORDS + w1))) == 1


def test_blocks_of_only_reads_first_eight_pages():
    empty = [HDR_WORDS] * 8
    assert pb.blocks_of(doc_of(*empty, HDR_WORDS + table_words())) is None


# --------------------------------------------------------------------------- synthetic PDFs
HDR = [(30, 40, "S.No"), (60, 40, "Name"), (90, 40, "Of"), (105, 40, "Undertaking"), (190, 40, "Location"), (275, 40, "Item"),
       (325, 40, "Capacity"), (365, 40, "Ack"), (383, 40, "No./Date"), (425, 40, "Part-A"), (460, 40, "Part-A"),
       (525, 40, "Production"), (525, 48, "Commenced"), (565, 48, "On")]
NAMES = ["ALPHA STEELS PVT LTD", "BETA CEMENT LTD", "GAMMA GLASS LIMITED", "DELTA DYES PRIVATE LIMITED", "EPSILON METALS LLP",
         "ZETA PAPERS PVT LTD"]
LOC = ["PUNE MAHARASHTRA", "SURAT", "KOCHI", "VAPI", "HUBLI", "CUTTACK"]


def body(y, i, serial_y=None, **ov):
    sy = y if serial_y is None else serial_y
    d = dict(ack="B1 12-06-2020", memno=str(100 + i), memdate="05-03-2019", comm="01-04-2021", loc=LOC[i - 1], item="STEEL")
    d.update(ov)
    out = [(30, sy, str(i)), (190, sy, d["loc"]), (275, sy, d["item"]), (325, sy, "300"), (365, sy, d["ack"]),
           (425, sy, d["memno"]), (460, sy, d["memdate"])]
    if d["comm"]:
        out.append((525, sy, d["comm"]))
    return out


def top_layout(n=6, names=NAMES, **ov):
    items = list(HDR)
    for i in range(1, n + 1):
        y = 80 + i * 30
        items += [(60, y, names[i - 1]), (60, y + 9, "PLOT 5 MIDC")] + body(y, i, **ov)
    return items


@pytest.fixture
def pdf(tmp_path):
    def make(*pages, **kw):
        return make_pdf(tmp_path / "b.pdf", list(pages), **kw)
    return make


def test_parse_pdf_top_layout(pdf):
    rows, status = pb.parse_pdf(pdf(top_layout()))
    assert status == "ok" and [r["serial"] for r in rows] == [1, 2, 3, 4, 5, 6]
    r = rows[0]
    assert (r["company_name"], r["name_has_suffix"], r["layout"]) == ("ALPHA STEELS PVT LTD", True, "top")
    assert r["company_text"] == "ALPHA STEELS PVT LTD PLOT 5 MIDC"
    assert r["location"] == "PUNE MAHARASHTRA" and r["item"] == "STEEL"
    assert (r["mem_no"], r["mem_date"], r["part_b_date"], r["commenced"]) == (101, "05/03/2019", "12/06/2020", "01/04/2021")
    assert r["state"] == "MAHARASHTRA" and r["page"] == 1
    assert rows[1]["state"] is None and rows[3]["company_name"] == "DELTA DYES PRIVATE LIMITED"


def test_parse_pdf_dates_with_spaces_and_missing_commenced(pdf):
    rows, _ = pb.parse_pdf(pdf(top_layout(memdate="5 - 3 - 2019", comm=None)))
    assert rows[0]["mem_date"] == "05/03/2019" and rows[0]["commenced"] is None


def test_parse_pdf_unparseable_numbers_become_none(pdf):
    rows, _ = pb.parse_pdf(pdf(top_layout(memno="n/a", memdate="soon", ack="--")))
    r = rows[0]
    assert r["mem_no"] is None and r["mem_date"] is None and r["part_b_date"] is None


def test_parse_pdf_mem_no_takes_first_up_to_six_digits(pdf):
    rows, _ = pb.parse_pdf(pdf(top_layout(memno="1234567")))
    assert rows[0]["mem_no"] == 123456


def test_parse_pdf_state_from_end_of_company_text_when_location_lacks_it(pdf):
    items = list(HDR)
    for i in range(1, 7):
        y = 80 + i * 30
        items += [(60, y, NAMES[i - 1]), (60, y + 9, "MIDC")] + body(y, i, loc="PUNE")
    items += [(60, 80 + 30 + 18, "KERALA")]                             # address tail of record 1
    rows, _ = pb.parse_pdf(pdf(items))
    assert rows[0]["state"] == "KERALA"


def test_parse_pdf_blocks_error_when_no_header(pdf):
    rows, status = pb.parse_pdf(pdf([(10, 10, "nothing")]))
    assert rows == [] and status == "blocks=None"


def test_parse_pdf_blocks_error_when_too_few_columns(pdf):
    items = list(HDR)
    for i in range(1, 7):
        y = 80 + i * 30
        items += [(30, y, str(i)), (60, y, NAMES[i - 1]), (190, y, "PUNE")]
    rows, status = pb.parse_pdf(pdf(items))
    assert rows == [] and status.startswith("blocks=") and status != "blocks=None"


def test_parse_pdf_eight_blocks_is_enough(pdf):
    # drop the 'commenced' column: 8 blocks remain, which the parser still accepts
    items = list(HDR) + [w for it in range(1, 7) for w in [(60, 80 + it * 30, NAMES[it - 1])] + body(80 + it * 30, it, comm=None)]
    rows, status = pb.parse_pdf(pdf(items))
    assert status == "ok" and len(rows) == 6 and rows[0]["commenced"] is None


def test_parse_pdf_serial_continuation_across_pages(pdf):
    p1 = list(HDR); p2 = list(HDR)
    for i in range(1, 4):
        y = 80 + i * 30
        p1 += [(60, y, NAMES[i - 1])] + body(y, i)
    for i in range(4, 7):
        y = 60 + (i - 3) * 30
        p2 += [(60, y, NAMES[i - 1])] + body(y, i)
    rows, _ = pb.parse_pdf(pdf(p1, p2))
    assert [r["serial"] for r in rows] == [1, 2, 3, 4, 5, 6] and [r["page"] for r in rows] == [1, 1, 1, 2, 2, 2]


def test_parse_pdf_serial_must_increase_by_at_most_six(pdf):
    items = list(HDR)
    for i, s in enumerate(["1", "2", "2", "40", "3", "4"], 1):
        y = 80 + i * 30
        items += [(60, y, NAMES[(i - 1) % 6])] + body(y, 1, serial_y=None)
        items = [it for it in items if not (it[0] == 30 and it[1] == y)] + [(30, y, s)]
    rows, _ = pb.parse_pdf(pdf(items))
    assert [r["serial"] for r in rows] == [1, 2, 3, 4]


# --------------------------------------------------------------------------- mid-serial layout and name cleanup
def mid_layout(names, locs=LOC):
    items = list(HDR); y = 80
    tail = ["PLOT NO 5 INDUSTRIAL", "AREA PHASE TWO NEAR", "MAIN ROAD CROSSING", "POST TALUKA AND", "DISTRICT 400002"]
    for i, n in enumerate(names, 1):
        items.append((60, y, n))
        items += body(y + 9, i, loc=locs[i - 1])
        items += [(60, y + 9 + 9 * k, t) for k, t in enumerate(tail)]
        y += 70
    return items


def test_parse_pdf_mid_serial_layout_takes_name_from_previous_block(pdf):
    rows, status = pb.parse_pdf(pdf(mid_layout(NAMES)))
    assert status == "ok"
    assert [r["company_name"] for r in rows] == NAMES
    assert {r["layout"] for r in rows} == {"mid-serial"} and all(r["name_has_suffix"] for r in rows)
    assert rows[0]["state"] == "MAHARASHTRA" and rows[0]["mem_no"] == 101


def test_parse_pdf_top_layout_wins_when_it_scores_at_least_as_well(pdf):
    rows, _ = pb.parse_pdf(pdf(top_layout()))
    assert {r["layout"] for r in rows} == {"top"}


def test_parse_pdf_leading_junk_tokens_are_removed_from_names(pdf):
    names = ["NU ALPHA STEELS PVT LTD", "NA BETA CEMENT LTD", "GM GAMMA GLASS LIMITED", "NIL DELTA DYES LTD",
             ". EPSILON METALS LLP", "ZETA PAPERS PVT LTD"]
    rows, _ = pb.parse_pdf(pdf(top_layout(names=names)))
    assert [r["company_name"] for r in rows] == ["ALPHA STEELS PVT LTD", "BETA CEMENT LTD", "GAMMA GLASS LIMITED",
                                                 "DELTA DYES LTD", "EPSILON METALS LLP", "ZETA PAPERS PVT LTD"]


def test_parse_pdf_leading_state_word_dropped_only_from_longer_names(pdf):
    names = ["GUJARAT ALPHA STEELS PVT LTD", "GOA BETA LTD", "GAMMA GLASS LIMITED", "DELTA DYES LTD", "EPSILON METALS LLP",
             "ZETA PAPERS PVT LTD"]
    rows, _ = pb.parse_pdf(pdf(top_layout(names=names)))
    assert rows[0]["company_name"] == "ALPHA STEELS PVT LTD"       # > 3 tokens: state word peeled off
    assert rows[1]["company_name"] == "GOA BETA LTD"               # exactly 3 tokens: kept


def test_parse_pdf_glued_commas_are_spaced_and_stripped(pdf):
    names = ["ALPHA STEELS PVT.LTD.,,D.NO.88-", "BETA CEMENT LTD", "GAMMA GLASS LIMITED", "DELTA DYES LTD", "EPSILON METALS LLP",
             "ZETA PAPERS PVT LTD"]
    rows, _ = pb.parse_pdf(pdf(top_layout(names=names)))
    assert rows[0]["company_name"] == "ALPHA STEELS PVT.LTD"
    assert " , " in rows[0]["company_text"]
