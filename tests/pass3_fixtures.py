"""Synthetic inputs + snapshot helpers for the Pass 3 characterisation tests (no real PDFs, no network, temp dirs only)."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pymupdf

HEADER = [("Sr.", 30), ("Company", 80), ("District", 250), ("Item", 350), ("Capacity", 430), ("IEM", 500)]


def _cell(page, x, y, text):
    page.insert_text((x, y), text, fontsize=8)


def make_pdf(path, kind):
    """kind: 'gutter' (clean columns), 'header' (a bridging word defeats gutter detection), 'midserial' (name precedes serial)."""
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    for w, x in HEADER:
        _cell(p, x, 100, w)
    if kind in ("gutter", "header"):
        rows = [(1, ["ALPHA STEEL PRIVATE LIMITED", "Plot 4 Ring Road"], "Pune MAHARASHTRA", "Steel bars", "100 MT", "12/N/SIA/IMO/2019 05/03/2019"),
                (2, ["BETA FOODS PVT LTD"], "Cuttack ODISHA", "Rice", "50 MT", "7/P/SIA/IMO/2019 11/03/2019"),
                (3, ["GAMMA POWER LIMITED"], "Surat GUJARAT", "Solar", "5 MW", "4 March 2019")]
        for i, (sn, comp, dist, item, cap, iem) in enumerate(rows):
            y = 130 + 40 * i
            _cell(p, 35, y, str(sn))
            for j, line in enumerate(comp):
                _cell(p, 80, y + 10 * j, line)
            _cell(p, 250, y, dist); _cell(p, 350, y, item); _cell(p, 430, y, cap); _cell(p, 500, y, iem)
        if kind == "header":
            _cell(p, 215, 160, "bridge")  # ink in the gutter between Company and District -> falls back to header positions
        p2 = doc.new_page(width=595, height=842)   # continuation page without header
        _cell(p2, 35, 60, "4"); _cell(p2, 80, 60, "DELTA TEXTILES LTD."); _cell(p2, 250, 60, "Erode TAMIL NADU")
        _cell(p2, 350, 60, "Yarn"); _cell(p2, 430, 60, "9 T"); _cell(p2, 500, 60, "3/N/SIA/IMO/2018 01/02/2018")
    else:
        _cell(p, 80, 120, "OMEGA FORGE LIMITED")                       # pre-serial tail: belongs to record 1
        for i, (sn, comp, dist, item, cap, iem) in enumerate([
                (1, "Plot 4 Ring Road 400001 BETA WORKS LIMITED", "Pune MAHARASHTRA", "Forgings", "10 T", "5/N/SIA/IMO/2020 01/01/2020"),
                (2, "Sector 9 Noida 201301 GAMMA ALLOYS PVT LTD", "Noida UTTAR PRADESH", "Alloys", "9 T", "6/N/SIA/IMO/2020 02/01/2020"),
                (3, "Zone 2 Pin 560001 no more", "Bengaluru KARNATAKA", "Parts", "1 T", "7/N/SIA/IMO/2020 03/01/2020")]):
            y = 140 + 40 * i
            _cell(p, 35, y, str(sn)); _cell(p, 80, y, comp); _cell(p, 250, y, dist); _cell(p, 350, y, item)
            _cell(p, 430, y, cap); _cell(p, 500, y, iem)
    doc.save(str(path)); doc.close()


def snap_parse(tmp):
    import parse_partA_coords as m
    out = {}
    for kind in ("gutter", "header", "midserial"):
        f = Path(tmp) / f"{kind}.pdf"
        make_pdf(f, kind)
        rows, status = m.parse_pdf(str(f))
        out[kind] = [status, rows]
    nohdr = Path(tmp) / "nohdr.pdf"
    d = pymupdf.open(); pg = d.new_page(); _cell(pg, 50, 50, "nothing"); d.save(str(nohdr)); d.close()
    out["nohdr"] = list(m.parse_pdf(str(nohdr)))
    return json.loads(json.dumps(out, default=str))


def snap_build_db(tmp, home):
    """Run build_iem_db.main against a synthetic HOME (catalogue + analysis workbooks + md files); dump every table."""
    import duckdb
    import openpyxl
    home = Path(home)
    (home / "Downloads" / "DPIIT_IEM_lists" / "PartA").mkdir(parents=True)
    (home / "Downloads" / "DPIIT_IEM_lists" / "PartB").mkdir(parents=True)
    (home / "Dropbox" / "Zotero_Unsorted_Bulk_Files").mkdir(parents=True)
    hdr = ["Period from", "Period to", "Granularity", "DPIIT title", "DPIIT category", "Pages", "Size (KB)", "PDF link",
           "Duplicate of DPIIT id", "DPIIT doc id", "Notes"]
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    a = wb.create_sheet("Part A – company lists"); a.append(hdr)
    a.append(["2019-01", "2019-01", "monthly", "Jan list", "cat", 3, 10, "http://x/1", None, 101, "n1"])
    a.append(["2019-02", "2019-02", "monthly", "Feb list", "cat", 4, 11, "http://x/2", None, 102, None])
    a.append([None])
    b = wb.create_sheet("Part B – company lists"); b.append(hdr)
    b.append(["2019-01", "2019-01", "monthly", "B Jan", "cat", 2, 5, "http://x/3", 101, 201, None])
    g = wb.create_sheet("Aggregate statistics"); g.append(["part", "header", "title", "x", "pages", "y", "link", "z", "id"])
    g.append(["A", "h", "Agg title", None, 9, None, "http://x/agg", None, 301]); g.append([None])
    cat = home / "Downloads" / "DPIIT_IEM_PartA_PartB_catalogue_2026-10-07.xlsx"; wb.save(cat)
    (home / "Downloads" / "DPIIT_IEM_lists" / "PartA" / "Jan_101.pdf").write_bytes(b"x")
    (home / "Downloads" / "DPIIT_IEM_lists" / "PartA" / "Feb_102.pdf").write_bytes(b"y")
    (home / "Downloads" / "DPIIT_IEM_lists" / "PartB" / "BJan_201.pdf").write_bytes(b"z")
    md = Path(tmp) / "md"
    (md / "PartA").mkdir(parents=True); (md / "PartB").mkdir(parents=True)
    (md / "PartA" / "Jan_101.md").write_text("rec 12 /N/ SIA / IMO / 2019, 05/03/2019 and\n 7/N/SIA/IMO/2019 11/03/2019 plus 9/P/SIA/IMO/2019 01/01/2019")
    (md / "PartB" / "BJan_201.md").write_text("nothing here")
    dr = home / "Dropbox" / "Zotero_Unsorted_Bulk_Files"
    (dr / "2024-01_IEM_PartA_Detailed.pdf").write_bytes(b"same"); (dr / "2024-01_IEM_PartA_Detailed (1).pdf").write_bytes(b"same")
    (dr / "2024-02_IEM_PartB_Detailed.pdf").write_bytes(b"other")
    aw = openpyxl.Workbook(); aw.remove(aw.active)
    for nm, lab in (("States_PartA", "State"), ("States_PartB", "State"), ("Sectors_PartA", "Sector"), ("Sectors_PartB", "Sector")):
        s = aw.create_sheet(nm); s.append([nm]); s.append([]); s.append([lab, "2021", None])
        s.append(["Goa", 1, 2.5, 3, 4.5, None, None, 7, 8, 9, 10, 11])
        s.append(["Total", 9, 9]); s.append([None, 1]); s.append(["Bihar", None, None, 5, 6])
    t = aw.create_sheet("Trends"); t.append(["a", 1, None]); t.append([None, None]); t.append(["b", 2.5])
    aw.save(home / "Dropbox" / "IEM_State_Sector_Analysis.xlsx")

    import build_iem_db as m
    m.BASE = Path(tmp); m.DB = Path(tmp) / "iem.duckdb"
    m.CATALOGUE = cat; m.ANALYSIS = home / "Dropbox" / "IEM_State_Sector_Analysis.xlsx"; m.DROPBOX_IEM_DIR = dr
    os.environ["HOME"] = str(home)
    m.DB.write_bytes(b"stale")   # main() must unlink and rebuild
    m.main()
    c = duckdb.connect(str(m.DB), read_only=True)
    snap = {}
    for (t_,) in c.execute("select table_name from information_schema.tables order by 1").fetchall():
        rows = c.execute(f"select * from {t_}").fetchall()
        rows = [[str(v).replace(str(home), "<HOME>").replace(str(tmp), "<TMP>") if v is not None else None for v in r] for r in rows]
        snap[t_] = sorted(rows)
    c.close()
    return snap


def snap_compile(tmp, full):
    """Run compile_iem_ab.main on synthetic parquet/duckdb/csv inputs; dump the xlsx sheets and both parquet outputs."""
    import duckdb
    import openpyxl
    import pandas as pd
    base = Path(tmp)
    ca = pd.DataFrame({"iem_no": [1, 2, 3, 4], "filing_date": ["05/03/2021", "2021-04-10", "1 Jun 2022", "01/01/2024"],
                       "company_name": ["ALPHA STEEL PRIVATE LIMITED", "BETA FOODS LIMITED", "GAMMA POWER LTD", "DELTA CHEM LIMITED"],
                       "name_has_suffix": [True] * 4,
                       "district_state": ["Pune MAHARASHTRA", "Cuttack ODISHA", "Surat GUJARAT", "Erode TAMIL NADU"],
                       "item": ["steel", "rice", "solar", "chem"], "month": ["2021-03", "2021-04", "2022-06", "2024-01"],
                       "source_file": ["2021-03_a.pdf", "2021-04_a.pdf", "2022-06_a.pdf", "2024-01_a.pdf"], "serial": [1, 1, 1, 1]})
    ca.to_parquet(base / "partA_coords.parquet", index=False)
    tx = pd.DataFrame({"ack_no": [5, 6], "filing_date": ["01/02/2021", "02/02/2021"],
                       "company_name": ["EPSILON TEXTILES LIMITED", "ALPHA STEEL PRIVATE LIMITED"], "name_has_suffix": [True, True],
                       "district_state": ["Erode TAMIL NADU", "Pune MAHARASHTRA"], "item": ["yarn", "steel"],
                       "month": ["2021-02", "2021-05"], "source_file": ["2021-02_t.pdf", "2023-05_t.pdf"], "serial": [2, 3]})
    tx.to_parquet(base / "partA_text_layout.parquet", index=False)
    pb = pd.DataFrame({"company_name": ["Alpha Steel Pvt Ltd", "Gamma Power Limited", "Zeta Mills Limited"], "mem_no": [1, 99, 8],
                       "mem_date": ["05/03/2021", "01/01/2000", "03/03/2021"], "state": ["MAHARASHTRA", None, "GUJARAT"],
                       "month": ["2021-06", "2022-09", "2021-08"], "commenced": ["Y", "N", "Y"]})
    pb.to_parquet(base / "partB_all.parquet", index=False)
    if full:
        c = duckdb.connect(str(base / "iem_verify.duckdb"))
        c.execute("create table co(id int, key varchar, state varchar, name varchar, usable boolean, in_part_b boolean, n_part_a int, "
                  "n_part_b int, last_iem varchar, last_part_b varchar, cin varchar, status varchar, src varchar)")
        import iem_verify as v
        ks = {n: v.key(n) for n in ["ALPHA STEEL PRIVATE LIMITED", "BETA FOODS LIMITED", "GAMMA POWER LTD", "DELTA CHEM LIMITED", "EPSILON TEXTILES LIMITED"]}
        rows = [(1, ks["ALPHA STEEL PRIVATE LIMITED"], "MAHARASHTRA", "ALPHA", True, True, 2, 1, "2021-05", "2021-06", None, None, "web"),
                (2, ks["BETA FOODS LIMITED"], "ODISHA", "BETA", True, False, 1, 0, "2021-04", None, "U1", "Strike Off", "web"),
                (3, ks["GAMMA POWER LTD"], "GUJARAT", "GAMMA", True, False, 1, 1, "2022-06", "2022-09", "U2", "Active", "web"),
                (4, ks["DELTA CHEM LIMITED"], "TAMIL NADU", "DELTA", False, False, 1, 0, "2024-01", None, None, None, None),
                (5, ks["EPSILON TEXTILES LIMITED"], "TAMIL NADU", "EPS", True, False, 1, 0, "2021-02", None, None, None, "web")]
        c.executemany("insert into co values (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.execute("create table gleif_verdict(id int, verdict varchar, gleif_cin varchar, gleif_entity_status varchar, lei_status varchar, lei_next_renewal varchar)")
        c.executemany("insert into gleif_verdict values (?,?,?,?,?,?)",
                      [(1, "new", "U9", "ACTIVE", "ISSUED", "2027-01-01"), (2, "conflict", None, None, None, None),
                       (3, "cin_changed", "U3", "ACTIVE", "LAPSED", "2026-01-01"), (5, "confirmed", None, None, "ISSUED", "2027-02-02")])
        c.close()
        pd.DataFrame({"k": [ks["DELTA CHEM LIMITED"]], "state": ["TAMIL NADU"], "verdict": ["MATCHED"], "cin": ["U4"], "status": ["Active"]}
                     ).to_csv(base / "IEM_registry_matches.csv", index=False)
        pd.DataFrame({"cin": ["U9", "U3", "U4"], "status": ["Active", "Active", "Dissolved"],
                      "dateOfLastAGM": ["09/30/2025", "09/30/2024", "01/01/2025"], "balanceSheetDate": ["03/31/2025", "03/31/2025", "03/31/2024"],
                      "annualReturnDefaulter": ["False", "True", "False"], "balanceSheetDefaulter": ["False", "False", "False"],
                      "underCIRP": ["False", "False", "False"]}).to_csv(base / "apify_mca.csv", index=False)
    import compile_iem_ab as m
    m.BASE = base
    m.main()
    out = {}
    wb = openpyxl.load_workbook(base / "IEM_A_vs_B_support_candidates.xlsx")
    for ws in wb:
        out["xlsx:" + ws.title] = json.loads(json.dumps([list(r) for r in ws.iter_rows(values_only=True)], default=str))
    for f in ("iem_partA_filings_linked.parquet", "iem_companies_compiled.parquet"):
        d = pd.read_parquet(base / f)
        out[f] = {"cols": list(d.columns), "dtypes": [str(t) for t in d.dtypes],
                  "rows": json.loads(d.astype(object).where(d.notna(), None).to_json(orient="values", date_format="iso"))}
    return out


def run_all(tmp_root):
    import tempfile
    res = {}
    for name, fn in (("parse", lambda d: snap_parse(d)),
                     ("build_db", lambda d: snap_build_db(d, Path(d) / "home")),
                     ("compile_min", lambda d: snap_compile(d, False)),
                     ("compile_full", lambda d: snap_compile(d, True))):
        with tempfile.TemporaryDirectory(dir=tmp_root) as d:
            res[name] = fn(d)
    return res


if __name__ == "__main__":   # python tests/pass3_fixtures.py <checkout> <out.json>   (record from ORIGINAL code)
    sys.path.insert(0, sys.argv[1])
    home0 = os.environ.get("HOME")
    import tempfile
    r = run_all(tempfile.gettempdir())
    os.environ["HOME"] = home0
    Path(sys.argv[2]).write_text(json.dumps(r, indent=1, sort_keys=True, default=str))
