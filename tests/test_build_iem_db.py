"""build_iem_db: workbook parsing helpers and main() on synthetic catalogue/analysis workbooks, PDFs and md files.

HOME and every module path constant are redirected to tmp_path, so nothing in ~/Downloads or ~/Dropbox is read."""
import duckdb
import openpyxl
import pytest

import build_iem_db as b


# --------------------------------------------------------------------------- ANCHOR / md5
@pytest.mark.parametrize("text,groups", [
    ("12/N/SIA/IMO/2019 05/03/2019", ("12", "2019", "05/03/2019")),
    ("12 / N / SIA / IM O / 2019, 05/03/2019", ("12", "2019", "05/03/2019")),
    ("7/N/SIA/IM O/2001,31/12/2001", ("7", "2001", "31/12/2001")),
])
def test_anchor_matches_spaced_variants(text, groups):
    assert b.ANCHOR.findall(text) == [groups]


@pytest.mark.parametrize("text", ["12/P/SIA/IMO/2019 05/03/2019",              # only N (new) is anchored
                                  "12/N/SIA/IMO/19 05/03/2019"])                # 2-digit year
def test_anchor_rejects_non_matching(text):
    assert b.ANCHOR.findall(text) == []


def test_anchor_number_is_at_most_four_digits():
    # a five-digit number is only matched from its last four digits
    assert b.ANCHOR.findall("12345/N/SIA/IMO/2019 05/03/2019") == [("2345", "2019", "05/03/2019")]


def test_md5_of_streams_in_chunks(tmp_path):
    p = tmp_path / "f.bin"
    data = b"abc" * (1 << 20)                     # 3 MiB spans several 1 MiB chunks
    p.write_bytes(data)
    import hashlib
    assert b.md5_of(p) == hashlib.md5(data).hexdigest()
    (tmp_path / "e").write_bytes(b"")
    assert b.md5_of(tmp_path / "e") == "d41d8cd98f00b204e9800998ecf8427e"


# --------------------------------------------------------------------------- load_catalogue_rows
def sheet(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    return ws


HDR = ["Period from", "Period to", "Granularity", "DPIIT title", "DPIIT category", "Pages", "Size (KB)", "PDF link",
       "Duplicate of DPIIT id", "DPIIT doc id", "Notes"]


def test_load_catalogue_rows_maps_columns_and_skips_blank_first_cell():
    ws = sheet([HDR,
                ["2021-01", "2021-01", "monthly", "Title A", "Cat", 10, 100, "http://x/a.pdf", None, 111, "n"],
                [None, "2021-02", "monthly", "skipped", "Cat", 1, 1, "l", None, 2, ""],          # empty first cell -> skipped
                [],                                                                              # empty row -> skipped
                ["2021-03", "2021-03", "monthly", "Title C", "Cat", 5, 50, "http://x/c.pdf", 111, 333, None]])
    rows = b.load_catalogue_rows(ws.parent, ws.title, "A")
    assert [r["dpiit_doc_id"] for r in rows] == [111, 333]
    r = rows[0]
    assert r == {"part": "A", "period_from": "2021-01", "period_to": "2021-01", "granularity": "monthly", "dpiit_title": "Title A",
                 "dpiit_category": "Cat", "pages": 10, "size_kb": 100, "pdf_link": "http://x/a.pdf", "duplicate_of_dpiit_id": None,
                 "dpiit_doc_id": 111, "notes": "n"}
    assert rows[1]["duplicate_of_dpiit_id"] == 111


def test_load_catalogue_rows_stringifies_missing_periods():
    ws = sheet([HDR, ["x", None, "g", "t", "c", 1, 1, "l", None, 9, None]])
    r = b.load_catalogue_rows(ws.parent, ws.title, "B")[0]
    assert r["period_from"] == "x" and r["period_to"] == "None"


# --------------------------------------------------------------------------- parse_wide_annual
def test_parse_wide_annual_pairs_per_year_and_skips_totals_and_none_pairs():
    ws = sheet([["title"], ["hdr"], ["hdr2"],
                ["GUJARAT", 1, 10.0, 2, 20.0, None, None, 4, 40.0, 5, 50.0, 99, 999],
                ["Total", 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9],
                ["GRAND TOTAL", 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9],
                ["  grand Total ", 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9],
                [None, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                ["GOA", None, 5.0, 3, None, None, None, None, None, None, None, None]])
    got = b.parse_wide_annual(ws, "State")
    assert got == [("GUJARAT", 2021, 1, 10.0), ("GUJARAT", 2022, 2, 20.0), ("GUJARAT", 2024, 4, 40.0), ("GUJARAT", 2025, 5, 50.0),
                   ("GOA", 2021, None, 5.0), ("GOA", 2022, 3, None)]


def test_parse_wide_annual_skips_first_three_rows():
    ws = sheet([["A", 1, 1], ["B", 1, 1], ["C", 1, 1], ["D", 1, 1, None, None, None, None, None, None, None, None]])
    assert b.parse_wide_annual(ws, "x") == [("D", 2021, 1, 1)]


# --------------------------------------------------------------------------- main
@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads" / "DPIIT_IEM_lists" / "PartA").mkdir(parents=True)
    (home / "Downloads" / "DPIIT_IEM_lists" / "PartB").mkdir(parents=True)
    dbx = home / "Dropbox" / "Zotero_Unsorted_Bulk_Files"
    dbx.mkdir(parents=True)
    base = tmp_path / "repo"
    (base / "md" / "PartA").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(b, "BASE", base)
    monkeypatch.setattr(b, "DB", base / "iem.duckdb")
    monkeypatch.setattr(b, "CATALOGUE", home / "cat.xlsx")
    monkeypatch.setattr(b, "ANALYSIS", home / "analysis.xlsx")
    monkeypatch.setattr(b, "DROPBOX_IEM_DIR", dbx)
    return dict(home=home, base=base, dbx=dbx)


def build_catalogue(path):
    wb = openpyxl.Workbook()
    a = wb.active; a.title = "Part A – company lists"
    for r in [HDR,
              ["2021-01", "2021-01", "monthly", "A Jan", "Cat", 10, 100, "http://x/a1", None, 111, "n"],
              ["2021-02", "2021-02", "monthly", "A Feb", "Cat", 12, 120, "http://x/a2", None, 222, None],
              ["2021-03", "2021-03", "monthly", "A Mar (no local file)", "Cat", 1, 1, "http://x/a3", None, None, None]]:
        a.append(r)
    bb = wb.create_sheet("Part B – company lists")
    for r in [HDR, ["2021-01", "2021-01", "monthly", "B Jan", "Cat", 3, 30, "http://x/b1", None, 333, None]]:
        bb.append(r)
    ag = wb.create_sheet("Aggregate statistics")
    for r in [["part", "header", "title", "x", "pages", "y", "link", "z", "docid"],
              ["A", "State-wise", "Agg title", None, 7, None, "http://x/agg", None, 999],
              [None, "skipped", "x", None, 1, None, "l", None, 1]]:
        ag.append(r)
    wb.save(path)


def build_analysis(path):
    wb = openpyxl.Workbook()
    wb.active.title = "States_PartA"
    pad = [["t"], ["h"], ["h2"]]
    def fill(ws, rows):
        for r in pad + rows:
            ws.append(r)
    fill(wb["States_PartA"], [["GUJARAT", 1, 10, 2, 20, 3, 30, 4, 40, 5, 50], ["Total", 9, 9, 9, 9, 9, 9, 9, 9, 9, 9]])
    fill(wb.create_sheet("States_PartB"), [["GOA", 1, 5, None, None, None, None, None, None, None, None]])
    fill(wb.create_sheet("Sectors_PartA"), [["STEEL", 2, 20, None, None, None, None, None, None, 7, 70]])
    fill(wb.create_sheet("Sectors_PartB"), [["CEMENT", 3, 30, None, None, None, None, None, None, None, None]])
    tr = wb.create_sheet("Trends")
    for r in [["Year", "IEMs"], [None, None], [2021, 100], [None, "x"]]:
        tr.append(r)
    wb.save(path)


def populate(env, anchored_text=None):
    build_catalogue(env["home"] / "cat.xlsx"); build_analysis(env["home"] / "analysis.xlsx")
    (env["home"] / "Downloads" / "DPIIT_IEM_lists" / "PartA" / "2021-01_x_111.pdf").write_bytes(b"%PDF a")
    (env["home"] / "Downloads" / "DPIIT_IEM_lists" / "PartA" / "2021-02_x_222.pdf").write_bytes(b"%PDF b")
    (env["home"] / "Downloads" / "DPIIT_IEM_lists" / "PartB" / "2021-01_y_333.pdf").write_bytes(b"%PDF c")
    (env["home"] / "Downloads" / "DPIIT_IEM_lists" / "PartA" / "no_id_suffix.pdf").write_bytes(b"x")
    (env["base"] / "md" / "PartA" / "2021-01_x_111.md").write_text(
        anchored_text or "1/N/SIA/IMO/2021 05/01/2021\n2 / N / SIA / IM O / 2021, 06/01/2021 other text")
    (env["dbx"] / "2024-01_IEM_PartA_Detailed.pdf").write_bytes(b"same")
    (env["dbx"] / "2024-02_IEM_PartA_Detailed_copy.pdf").write_bytes(b"same")           # byte-identical -> not canonical
    (env["dbx"] / "2024-01_IEM_PartB_Detailed.pdf").write_bytes(b"diff")
    (env["dbx"] / "unrelated.pdf").write_bytes(b"zz")


def q(env, sql):
    c = duckdb.connect(str(env["base"] / "iem.duckdb"), read_only=True)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_main_builds_all_tables(env, capsys):
    populate(env)
    b.main()
    out = capsys.readouterr().out
    assert "iem_documents: 4 rows" in out
    assert "iem_dropbox_copies: 3 files, 2 canonical" in out
    assert "annual_states: 6 | annual_sectors: 3" in out
    assert "part_a_records: 2" in out
    docs = {r[0]: r for r in q(env, "select dpiit_title, part, local_pdf is not null, md_chars is not null, anchor_records, dpiit_doc_id from iem_documents")}
    assert docs["A Jan"][1:] == ("A", True, True, 2, 111)
    assert docs["A Feb"][2:5] == (True, False, None)                    # pdf present, md absent
    assert docs["A Mar (no local file)"][2:] == (False, False, None, None)
    assert docs["B Jan"][1] == "B" and docs["B Jan"][2] is True


def test_main_md_chars_counts_raw_text_but_anchors_use_normalised_text(env):
    populate(env, anchored_text="1/N/SIA/IMO/2021\n05/01/2021")          # anchor split by a newline
    b.main()
    row = q(env, "select md_chars, anchor_records from iem_documents where dpiit_title='A Jan'")[0]
    assert row == (len("1/N/SIA/IMO/2021\n05/01/2021"), 1)


def test_main_part_a_records_rows(env):
    populate(env)
    b.main()
    assert q(env, "select iem_seq, iem_year, filing_date, source_doc from part_a_records order by iem_seq") == [
        (1, 2021, "05/01/2021", "2021-01_x_111.md"), (2, 2021, "06/01/2021", "2021-01_x_111.md")]


def test_main_dropbox_copies_flags_duplicates(env):
    populate(env)
    b.main()
    rows = q(env, "select file_name, part, month, is_canonical, size_bytes from iem_dropbox_copies order by file_name")
    assert rows == [("2024-01_IEM_PartA_Detailed.pdf", "A", "2024-01", True, 4),
                    ("2024-01_IEM_PartB_Detailed.pdf", "B", "2024-01", True, 4),
                    ("2024-02_IEM_PartA_Detailed_copy.pdf", "A", "2024-02", False, 4)]


def test_main_annual_tables(env):
    populate(env)
    b.main()
    st = q(env, "select part, state, year, iem_count, investment_rs_cr from annual_states order by part, state, year")
    assert st == [("A", "GUJARAT", y, float(n), float(inv)) for y, n, inv in
                  [(2021, 1, 10), (2022, 2, 20), (2023, 3, 30), (2024, 4, 40), (2025, 5, 50)]] + [("B", "GOA", 2021, 1.0, 5.0)]
    sec = q(env, "select part, sector, year, iem_count from annual_sectors order by part, year")
    assert sec == [("A", "STEEL", 2021, 2.0), ("A", "STEEL", 2025, 7.0), ("B", "CEMENT", 2021, 3.0)]
    assert q(env, "select raw from annual_trends") == [("Year | IEMs",), ("2021 | 100",), (" | x",)]


def test_main_catalogue_aggregates(env):
    populate(env)
    b.main()
    assert q(env, "select part, header, dpiit_title, pages, pdf_link, dpiit_doc_id from catalogue_aggregates") == [
        ("A", "State-wise", "Agg title", 7, "http://x/agg", 999)]


def test_main_replaces_an_existing_database(env):
    populate(env)
    (env["base"] / "iem.duckdb").write_text("not a database")
    b.main()                                                             # unlinks the stale file first
    assert q(env, "select count(*) from iem_documents") == [(4,)]


def test_main_is_idempotent(env):
    populate(env)
    b.main(); b.main()
    assert q(env, "select count(*) from iem_documents") == [(4,)]
    assert q(env, "select count(*) from part_a_records") == [(2,)]
