"""iem_verify stages not covered elsewhere: extract, partb, batches, ingest, report, the curl fetcher, and the
scope options of web/match. DuckDB and every parquet/md input is redirected to tmp_path; curl is faked."""
import json
import sys
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

import iem_verify as iv


def cin(state="GJ", year=2010, cls="PTC", serial="000001", listed="U"):
    return f"{listed}24100{state}{year}{cls}{serial}"


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "BASE", tmp_path)
    monkeypatch.setattr(iv, "DB", tmp_path / "iem_verify.duckdb")
    return tmp_path


def table(name="co"):
    k = iv.con()
    df = k.execute(f"select * from {name}").df()
    k.close()
    return df


A_COLS = ["month", "serial", "company_name", "company_text", "name_has_suffix", "district_state", "item", "source_file"]


def part_a(rows, extra=None):
    df = pd.DataFrame(rows, columns=A_COLS)
    return df


def write_a(base, coords, text=None, partb=None):
    part_a(coords).to_parquet(base / "partA_coords.parquet")
    if text is not None:
        pd.DataFrame(text, columns=["month", "serial", "company_name", "name_has_suffix", "district_state", "item", "source_file"]
                     ).to_parquet(base / "partA_text_layout.parquet")
    if partb is not None:
        pd.DataFrame(partb, columns=["company_name", "state", "month", "name_has_suffix"]).to_parquet(base / "partB_all.parquet")


# --------------------------------------------------------------------------- stage_extract
def test_extract_requires_coords_file(base):
    with pytest.raises(SystemExit) as e:
        iv.stage_extract(SimpleNamespace())
    assert "parse_partA_coords.py" in str(e.value.code)


def test_extract_groups_by_key_and_state(base, capsys):
    write_a(base, [
        ("2018-01", 1, "Alpha  Steels Pvt. Ltd.", "x", True, "SURAT GUJARAT", "STEEL", "f1.pdf"),
        ("2020-05", 2, "ALPHA STEELS PRIVATE LIMITED", "x2", True, "SURAT GUJARAT", "STEEL BARS", "f2.pdf"),
        ("2019-02", 3, "ALPHA STEELS PVT LTD", "x3", False, "PUNE MAHARASHTRA", "OTHER", "f3.pdf"),
        ("2019-03", 4, "AB LTD", "x4", True, "GOA", "X", "f4.pdf"),                         # key too short
    ])
    iv.stage_extract(SimpleNamespace())
    co = table().set_index(["key", "state"])
    assert len(co) == 2
    g = co.loc[("ALPHA STEELS", "GUJARAT")]
    assert (g.n_part_a, g.first_iem, g.last_iem) == (2, "2018-01", "2020-05")
    assert g["name"] == "ALPHA STEELS PVT. LTD"                        # first row: upper-cased, spaces squeezed, trailing "." stripped
    assert bool(g.confident) is True
    m = co.loc[("ALPHA STEELS", "MAHARASHTRA")]
    assert m.n_part_a == 1 and bool(m.confident) is False and bool(m.usable) is False
    assert "4 Part A records -> 2 distinct companies; 1 pass the name-quality gate; state known for 2" in capsys.readouterr().out


def test_extract_state_falls_back_to_company_text(base):
    write_a(base, [("2018-01", 1, "ALPHA STEELS LIMITED", "ALPHA STEELS LIMITED, PLOT 4, HUBLI KARNATAKA", True, "NOWHERE", "X", "f.pdf")])
    iv.stage_extract(SimpleNamespace())
    assert table().state.tolist() == ["KARNATAKA"]


def test_extract_state_unknown_is_null(base):
    write_a(base, [("2018-01", 1, "ALPHA STEELS LIMITED", "ALPHA STEELS LIMITED", True, "NOWHERE", "X", "f.pdf")])
    iv.stage_extract(SimpleNamespace())
    assert table().state.isna().tolist() == [True]


def test_extract_dedups_month_serial_text_keeping_earliest_source_file(base):
    write_a(base, [
        ("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GOA", "LATER", "z.pdf"),
        ("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GOA", "EARLIER", "a.pdf"),
    ])
    iv.stage_extract(SimpleNamespace())
    co = table()
    assert len(co) == 1 and co.n_part_a.iloc[0] == 1 and co.item.iloc[0] == "EARLIER"


def test_extract_item_truncated_to_80_chars(base):
    write_a(base, [("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GOA", "I" * 120, "f.pdf")])
    iv.stage_extract(SimpleNamespace())
    assert table().item.iloc[0] == "I" * 80


def test_extract_includes_text_layout_files_except_2023(base):
    write_a(base,
            [("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GOA", "X", "2018-01_a.pdf")],
            text=[("2012-01", 1, "BETA CEMENT LIMITED", True, "GOA", "X", "2012-01_t.pdf"),
                  ("2023-01", 1, "GAMMA GLASS LIMITED", True, "GOA", "X", "2023-01_cum.pdf")])
    iv.stage_extract(SimpleNamespace())
    assert sorted(table()["name"]) == ["ALPHA STEELS LIMITED", "BETA CEMENT LIMITED"]


def test_extract_flags_part_b_and_adds_part_b_only_companies(base, capsys):
    write_a(base,
            [("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GUJARAT", "X", "f.pdf"),
             ("2018-01", 2, "BETA CEMENT LIMITED", "t", True, "GUJARAT", "X", "f.pdf")],
            partb=[("alpha steels ltd", "GUJARAT", "2020-02", True), ("ALPHA STEELS LTD", "GUJARAT", "2021-07", True),
                   ("BETA CEMENT LTD", "KERALA", "2020-02", True),                      # other state: not the same company
                   ("ONLYB WIRES LIMITED", None, "2022-01", True),
                   ("AB LTD", "GOA", "2022-01", True)])                                 # key too short
    iv.stage_extract(SimpleNamespace())
    co = table().set_index("name")
    a = co.loc["ALPHA STEELS LIMITED"]
    assert bool(a.in_part_b) is True and a.n_part_b == 2 and a.last_part_b == "2021-07"
    b = co.loc["BETA CEMENT LIMITED"]
    assert bool(b.in_part_b) is False and b.n_part_b == 0
    o = co.loc["ONLYB WIRES LIMITED"]
    assert bool(o.in_part_b) is True and o.n_part_a == 0 and o.first_iem == "2022-01" and o.last_iem == "2022-01"
    assert pd.isna(o.state) and o["item"] == ""
    assert "AB LTD" not in co.index and "BETA CEMENT LTD" in co.index                 # state-different Part B company kept apart
    out = capsys.readouterr().out
    assert "Part B: 3 distinct companies; 3 flagged in table (2 not seen in Part A)" in out


def test_extract_without_part_b_file_has_no_flags(base):
    write_a(base, [("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GOA", "X", "f.pdf")])
    iv.stage_extract(SimpleNamespace())
    co = table()
    assert bool(co.in_part_b.iloc[0]) is False and co.n_part_b.iloc[0] == 0 and bool(co.part_b_hit.iloc[0]) is False


def test_extract_usable_gate_and_ids(base, capsys):
    write_a(base, [
        ("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GOA", "X", "f.pdf"),
        ("2018-01", 2, "STEEL LIMITED", "t", True, "GOA", "X", "f.pdf"),                 # bare generic
        ("2018-01", 3, "GAMMA GLASS LIMITED", "t", False, "GOA", "X", "f.pdf"),          # not confident
    ])
    iv.stage_extract(SimpleNamespace())
    co = table().set_index("name")
    assert co.usable.to_dict() == {"ALPHA STEELS LIMITED": True, "STEEL LIMITED": False, "GAMMA GLASS LIMITED": False}
    assert sorted(co.id) == [1, 2, 3]
    assert "3 Part A records -> 3 distinct companies; 1 pass the name-quality gate; state known for 3" in capsys.readouterr().out


def test_extract_carries_over_earlier_cin_results(base, capsys):
    write_a(base, [("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GUJARAT", "X", "f.pdf"),
                   ("2018-01", 2, "BETA CEMENT LIMITED", "t", True, "GUJARAT", "X", "f.pdf")])
    iv.stage_extract(SimpleNamespace())
    k = iv.con()
    k.execute("update co set cin=?, status='Active', src='web-slug+state', score=90 where key='ALPHA STEELS'", [cin()])
    k.close()
    iv.stage_extract(SimpleNamespace())                                    # rebuild keeps the earlier result
    co = table().set_index("key")
    assert co.loc["ALPHA STEELS", "cin"] == cin() and co.loc["ALPHA STEELS", "src"] == "web-slug+state"
    assert co.loc["ALPHA STEELS", "score"] == 90
    assert pd.isna(co.loc["BETA CEMENT", "cin"])
    assert "carried over 1 earlier CIN results" in capsys.readouterr().out


def test_extract_carry_over_matches_on_state(base):
    write_a(base, [("2018-01", 1, "ALPHA STEELS LIMITED", "t", True, "GUJARAT", "X", "f.pdf"),
                   ("2018-01", 2, "ALPHA STEELS LIMITED", "t", True, "KERALA", "X", "f.pdf")])
    iv.stage_extract(SimpleNamespace())
    k = iv.con(); k.execute("update co set cin=?, status='Active' where state='GUJARAT'", [cin()]); k.close()
    iv.stage_extract(SimpleNamespace())
    co = table().set_index("state")
    assert co.loc["GUJARAT", "cin"] == cin() and pd.isna(co.loc["KERALA", "cin"])


# --------------------------------------------------------------------------- stage_partb
def make_co(rows):
    """rows: dict(name, state, ...). Builds a minimal co table."""
    df = pd.DataFrame([dict(id=i + 1, name=r["name"], key=iv.key(r["name"]), state=r.get("state"), confident=True,
                            usable=r.get("usable", True), n_part_a=r.get("n_part_a", 1), first_iem="2015-01", last_iem=r.get("last_iem", "2015-01"),
                            item=r.get("item", ""), part_b_hit=False, in_part_b=r.get("in_part_b", False), n_part_b=0,
                            last_part_b=r.get("last_part_b"), cin=r.get("cin"), status=r.get("status"), src=r.get("src"),
                            score=None, cand=r.get("cand")) for i, r in enumerate(rows)])
    k = iv.con()
    k.execute("""create or replace table co as select id, name, key, state, confident, usable, n_part_a, first_iem, last_iem,
                 item, part_b_hit, in_part_b, n_part_b, last_part_b::varchar last_part_b, cin::varchar cin, status::varchar status,
                 src::varchar src, score::double score, cand::varchar cand from df""")
    k.close()


def test_partb_three_word_shingles(base, capsys):
    (base / "md" / "PartB").mkdir(parents=True)
    (base / "md" / "PartB" / "a.md").write_text("List: Alpha Steels Works Pvt. Ltd., Surat\nBeta &\nCement Co Limited")
    make_co([{"name": "ALPHA STEELS WORKS PVT LTD"},      # key ALPHA STEELS WORKS -> hit
             {"name": "GAMMA GLASS WORKS LTD"},            # not in text
             {"name": "ALPHA STEELS LTD"},                 # 2-word key: skipped even though it is a prefix of a hit
             {"name": "BETA AND CEMENT WORKS LTD"}])      # key BETA CEMENT WORKS; text has "BETA AND CEMENT" -> no
    iv.stage_partb(SimpleNamespace())
    co = table().set_index("name")
    assert bool(co.loc["ALPHA STEELS WORKS PVT LTD", "part_b_hit"]) is True
    assert bool(co.loc["GAMMA GLASS WORKS LTD", "part_b_hit"]) is False
    assert bool(co.loc["ALPHA STEELS LTD", "part_b_hit"]) is False
    assert "Part B name-shingle hits: 1 / 4" in capsys.readouterr().out


def test_partb_ampersand_in_text_becomes_and(base):
    (base / "md" / "PartB").mkdir(parents=True)
    (base / "md" / "PartB" / "a.md").write_text("Sharma & Sons Steel Ltd")
    make_co([{"name": "SHARMA AND SONS STEEL LTD"}])          # key SHARMA SONS STEEL (AND is a stop word)
    iv.stage_partb(SimpleNamespace())
    # text shingle is "SHARMA AND SONS" (AND kept), key shingle is "SHARMA SONS STEEL" -> no hit; documents the asymmetry
    assert bool(table().part_b_hit.iloc[0]) is False


def test_partb_suffix_words_dropped_from_text_before_shingling(base):
    (base / "md" / "PartB").mkdir(parents=True)
    (base / "md" / "PartB" / "a.md").write_text("Delta Dye Works Private Limited Delta Dye Works LLP")
    make_co([{"name": "DELTA DYE WORKS LTD"}])
    iv.stage_partb(SimpleNamespace())
    assert bool(table().part_b_hit.iloc[0]) is True


def test_partb_no_md_files_marks_nothing(base):
    make_co([{"name": "ALPHA STEELS WORKS LTD"}])
    iv.stage_partb(SimpleNamespace())
    assert bool(table().part_b_hit.iloc[0]) is False


# --------------------------------------------------------------------------- stage_batches / ingest / report
def test_batches_split_residue_by_size_in_priority_order(base, capsys):
    make_co([{"name": "ALPHA STEELS LTD", "n_part_a": 1},
             {"name": "BETA CEMENT LTD", "n_part_a": 9, "cand": json.dumps([{"cin": cin(), "name": "web", "status": "Active"}])},
             {"name": "GAMMA GLASS LTD", "n_part_a": 5},
             {"name": "DONE LTD", "cin": cin()},                                  # already identified
             {"name": "BAD LTD", "usable": False}])                               # unusable
    iv.stage_batches(SimpleNamespace(size=2))
    files = sorted((base / "llm_batches").glob("batch_*.jsonl"))
    assert [f.name for f in files] == ["batch_0000.jsonl", "batch_0001.jsonl"]
    rows = [json.loads(l) for f in files for l in f.read_text().splitlines()]
    assert [r["name"] for r in rows] == ["BETA CEMENT LTD", "GAMMA GLASS LTD", "ALPHA STEELS LTD"]
    assert rows[0]["cand"] == [{"cin": cin(), "name": "web", "status": "Active"}] and rows[1]["cand"] == []
    assert set(rows[0]) == {"id", "name", "state", "item", "cand"}
    assert len((files[0]).read_text().splitlines()) == 2 and len((files[1]).read_text().splitlines()) == 1
    assert "3 residual companies -> 2 batches of <= 2" in capsys.readouterr().out


def test_batches_exact_multiple_makes_no_empty_batch(base):
    make_co([{"name": "ALPHA STEELS LTD"}, {"name": "BETA CEMENT LTD"}])
    iv.stage_batches(SimpleNamespace(size=2))
    assert len(list((base / "llm_batches").glob("batch_*.jsonl"))) == 1


def test_ingest_accepts_only_high_confidence_valid_cins(base, capsys):
    make_co([{"name": f"CO{i} WIDGETS LTD"} for i in range(6)] + [{"name": "HAS CIN LTD", "cin": cin("MH"), "status": "Active"}])
    (base / "llm_results").mkdir()
    lines = [
        {"id": 1, "cin": cin(), "status": "Active", "conf": "high"},              # accepted
        {"id": 2, "cin": cin(), "status": "Active", "conf": "low"},               # low confidence -> ignored
        {"id": 3, "cin": "NOT-A-CIN", "status": "Active", "conf": "high"},        # malformed -> ignored
        {"id": 4, "cin": None, "status": None, "conf": "high"},                   # no cin -> ignored
        {"id": 5, "cin": cin() + "X", "status": "A", "conf": "high"},             # fullmatch: trailing char rejected
        {"id": 7, "cin": cin("KA"), "status": "Strike Off", "conf": "high"},      # id 7 already has a CIN -> not overwritten
    ]
    (base / "llm_results" / "r.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    iv.stage_ingest(SimpleNamespace())
    co = table().set_index("id")
    assert co.loc[1, "cin"] == cin() and co.loc[1, "src"] == "llm-web" and co.loc[1, "score"] == 80 and co.loc[1, "status"] == "Active"
    for i in (2, 3, 4, 5, 6):
        assert pd.isna(co.loc[i, "cin"])
    assert co.loc[7, "cin"] == cin("MH")
    assert "ingested 2 high-confidence agent verdicts" in capsys.readouterr().out    # id 7 counts as processed but is not applied


def test_ingest_without_results_dir_is_a_noop(base, capsys):
    make_co([{"name": "ALPHA STEELS LTD"}])
    iv.stage_ingest(SimpleNamespace())
    assert "ingested 0 high-confidence" in capsys.readouterr().out


def test_report_classifies_and_writes_workbook(base, capsys):
    make_co([{"name": "ALPHA STEELS LTD", "cin": cin(), "status": "Active", "src": "web-slug+state"},
             {"name": "BETA CEMENT LTD", "cin": cin("MH"), "status": "Strike Off", "src": "web-slug+state"},
             {"name": "GAMMA GLASS LTD", "cin": cin("KA"), "status": "Dormant", "src": "web-slug+state"}])
    k = iv.con(); k.execute("update co set part_b_hit = true where name = 'ALPHA STEELS LTD'"); k.close()
    iv.stage_report(SimpleNamespace())
    out = capsys.readouterr().out
    assert "OPERATIONAL (active + Part B)" in out and "INACTIVE" in out and "DORMANT" in out
    x = pd.read_excel(base / "IEM_operational_status.xlsx").set_index("name")
    assert x.loc["ALPHA STEELS LTD", "verdict"] == "OPERATIONAL (active + Part B)"
    assert x.loc["BETA CEMENT LTD", "verdict"] == "INACTIVE"
    assert x.loc["GAMMA GLASS LTD", "verdict"] == "DORMANT"
    assert "cand" not in x.columns


@pytest.mark.xfail(int(pd.__version__.split(".")[0]) >= 3, strict=True, raises=AttributeError, reason=(
    "iem_verify.classify(): with pandas>=3 duckdb's .df() returns NaN (not None) for NULL string columns, so for an unmapped "
    "company `not cin` is False and `(status or '').lower()` is called on a float NaN -> AttributeError. stage_report() "
    "therefore crashes on pandas 3 whenever any company has no CIN, i.e. on every real run."))
def test_report_handles_unmapped_companies(base, capsys):
    make_co([{"name": "ALPHA STEELS LTD", "cin": cin(), "status": "Active", "src": "web-slug+state"}, {"name": "GAMMA GLASS LTD"}])
    iv.stage_report(SimpleNamespace())
    x = pd.read_excel(base / "IEM_operational_status.xlsx").set_index("name")
    assert x.loc["GAMMA GLASS LTD", "verdict"] == "UNMAPPED"


# --------------------------------------------------------------------------- _fetch (curl faked)
def run_fetch(monkeypatch, html, gate=None, now=100.0, sleeps=None):
    seen = []

    def fake_run(cmd, capture_output=None, text=None):
        seen.append(cmd)
        return SimpleNamespace(stdout=html)
    monkeypatch.setattr(iv.subprocess, "run", fake_run)
    monkeypatch.setattr(iv.time, "time", lambda: now)
    monkeypatch.setattr(iv.time, "sleep", (sleeps if sleeps is not None else []).append)
    import threading
    g = gate or {"lock": threading.Lock(), "next": 0.0, "gap": 0.5}
    return iv._fetch("alpha-steels-limited", g), seen, g


def test_fetch_extracts_first_cin_and_status(monkeypatch):
    html = f"<td>Status</td><td><span> Active </span></td> CIN {cin()} and later {cin('MH')}"
    (c, st, a, b), seen, g = run_fetch(monkeypatch, html)
    assert c == cin() and st == "Active" and a is None and b is None
    assert seen[0] == ["curl", "-sL", "-m", "15", "-A", iv.UA, "https://www.quickcompany.in/company/alpha-steels-limited"]
    assert g["next"] == 100.5


def test_fetch_status_marked_paid_is_discarded(monkeypatch):
    (c, st, *_), _, _ = run_fetch(monkeypatch, f"Status</td><td>Paid up capital<")
    assert st is None


def test_fetch_empty_page_gives_all_none(monkeypatch):
    assert run_fetch(monkeypatch, "")[0] == (None, None, None, None)


def test_fetch_no_cin_in_page(monkeypatch):
    (c, st, *_), _, _ = run_fetch(monkeypatch, "Status</td><td>Active<")
    assert c is None and st == "Active"


def test_fetch_waits_for_gate(monkeypatch):
    import threading
    sleeps = []
    gate = {"lock": threading.Lock(), "next": 100.3, "gap": 0.5}
    _, _, g = run_fetch(monkeypatch, "", gate=gate, now=100.0, sleeps=sleeps)
    assert sleeps == [pytest.approx(0.3)] and g["next"] == 100.5
    sleeps2 = []
    gate = {"lock": threading.Lock(), "next": 99.0, "gap": 0.5}
    run_fetch(monkeypatch, "", gate=gate, now=100.0, sleeps=sleeps2)
    assert sleeps2 == []                                               # gate already open: no sleep


# --------------------------------------------------------------------------- stage_web scope options
def web_ns(**kw):
    d = dict(candidates=False, partb=False, limit=None, rps=1000.0, workers=1)
    d.update(kw)
    return SimpleNamespace(**d)


def serve(monkeypatch, pages):
    asked = []

    def fake(slug, gate):
        asked.append(slug)
        return pages.get(slug), ("Active" if slug in pages else None), None, None
    monkeypatch.setattr(iv, "_fetch", fake)
    return asked


def cin_of(name):
    return {r[0]: r[1] for r in iv.con().execute("select name, cin from co").fetchall()}[name]


def test_web_partb_scope_only_checks_part_b_companies(base, monkeypatch):
    make_co([{"name": "ALPHA STEELS LIMITED", "state": "GUJARAT", "in_part_b": True},
             {"name": "BETA CEMENT LIMITED", "state": "GUJARAT", "in_part_b": False}])
    asked = serve(monkeypatch, {"alpha-steels-limited": cin("GJ", cls="PLC"), "beta-cement-limited": cin("GJ", cls="PLC")})
    iv.stage_web(web_ns(partb=True))
    assert asked == ["alpha-steels-limited"]


def test_web_default_scope_skips_unusable_parked_and_identified(base, monkeypatch):
    make_co([{"name": "ALPHA STEELS LIMITED", "state": "GUJARAT"},
             {"name": "BETA CEMENT LIMITED", "state": "GUJARAT", "usable": False},
             {"name": "GAMMA GLASS LIMITED", "state": "GUJARAT", "cand": "[]"},                # parked: has candidates
             {"name": "DONE LIMITED", "state": "GUJARAT", "cin": cin("GJ", cls="PLC")}])
    asked = serve(monkeypatch, {})
    iv.stage_web(web_ns())
    assert {s.split("-")[0] for s in asked} == {"alpha"}


def test_web_orders_part_b_first_then_by_counts(base, monkeypatch):
    make_co([{"name": "ALPHA STEELS LIMITED", "state": "GUJARAT", "n_part_a": 1},
             {"name": "BETA CEMENT LIMITED", "state": "GUJARAT", "n_part_a": 9},
             {"name": "GAMMA GLASS LIMITED", "state": "GUJARAT", "n_part_a": 2, "in_part_b": True}])
    asked = serve(monkeypatch, {})
    iv.stage_web(web_ns())
    first_per_company = list(dict.fromkeys(s.split("-")[0] for s in asked))
    assert first_per_company == ["gamma", "beta", "alpha"]


def test_web_limit(base, monkeypatch):
    make_co([{"name": "ALPHA STEELS LIMITED", "state": "GUJARAT"}, {"name": "BETA CEMENT LIMITED", "state": "GUJARAT"}])
    asked = serve(monkeypatch, {})
    iv.stage_web(web_ns(limit=1))
    assert len({s.split("-")[0] for s in asked}) == 1


def test_web_candidates_scope_uses_compiled_parquet(base, monkeypatch):
    make_co([{"name": "ALPHA STEELS LIMITED", "state": "GUJARAT"},
             {"name": "BETA CEMENT LIMITED", "state": "GUJARAT"},
             {"name": "GAMMA GLASS LIMITED", "state": None},
             {"name": "DELTA DYES LIMITED", "state": "GUJARAT"}])
    pd.DataFrame([("ALPHA STEELS", "GUJARAT", True, None, "2022-01"),
                  ("BETA CEMENT", "GUJARAT", False, None, "2022-02"),                       # not a support candidate
                  ("GAMMA GLASS", "", True, None, "2023-01"),                               # state-less (co.state NULL) joins on ''
                  ("DELTA DYES", "GUJARAT", True, cin("GJ", cls="PLC"), "2021-01")],        # already has a CIN in the parquet
                 columns=["k", "state", "support_candidate", "cin", "last_month"]).to_parquet(base / "iem_companies_compiled.parquet")
    asked = serve(monkeypatch, {})
    iv.stage_web(web_ns(candidates=True))
    order = list(dict.fromkeys(s.split("-")[0] for s in asked))
    assert order == ["gamma", "alpha"]                                   # newest last_month first; BETA and DELTA excluded


def test_web_persists_fetched_pages_in_cache(base, monkeypatch):
    make_co([{"name": "ALPHA STEELS LIMITED", "state": "GUJARAT"}])
    serve(monkeypatch, {"alpha-steels-limited": cin("GJ", cls="PLC")})
    iv.stage_web(web_ns())
    k = iv.con()
    rows = k.execute("select slug, cin, status from web_cache order by slug").fetchall()
    k.close()
    assert ("alpha-steels-limited", cin("GJ", cls="PLC"), "Active") in rows
    assert rows == [("alpha-steels-limited", cin("GJ", cls="PLC"), "Active")]       # stops at the first slug that yields a CIN


def test_web_summary_line(base, monkeypatch, capsys):
    make_co([{"name": "ALPHA STEELS PRIVATE LIMITED", "state": "GUJARAT"},
             {"name": "GAMMA GLASS LIMITED", "state": None}])
    serve(monkeypatch, {"alpha-steels-private-limited": cin("GJ"), "gamma-glass-limited": cin("GJ", cls="PLC")})
    iv.stage_web(web_ns())
    out = capsys.readouterr().out
    assert "2 companies to check; 2 have no cached page yet" in out
    assert "web: auto-accepted 1 (name+state agree), 0 name-only (plant state differs from registered office), parked 1 state-less, of 2 tried" in out


def test_web_cached_page_does_not_count_as_needing_network(base, monkeypatch, capsys):
    make_co([{"name": "ALPHA STEELS LIMITED", "state": "GUJARAT"}])
    k = iv.con()
    k.execute("create table web_cache(slug varchar primary key, cin varchar, status varchar, last_bs varchar, agm varchar)")
    k.execute("insert into web_cache values ('alpha-steels-limited', ?, 'Active', null, null)", [cin("GJ", cls="PLC")])
    k.close()
    asked = serve(monkeypatch, {})
    iv.stage_web(web_ns())
    assert asked == []
    assert "1 have no cached page yet" not in capsys.readouterr().out and cin_of("ALPHA STEELS LIMITED") == cin("GJ", cls="PLC")


# --------------------------------------------------------------------------- stage_match extras
def write_master(base, rows):
    df = pd.DataFrame(rows, columns=["cin", "name", "status"])
    df["key"] = df.name.map(iv.key)
    df["state"] = df.cin.str[6:8].map(iv.CIN_STATE)
    k = iv.con(); k.execute("create or replace table mca_master as select * from df"); k.close()


def test_match_without_master_exits(base):
    make_co([{"name": "ALPHA STEELS LTD"}])
    with pytest.raises(SystemExit) as e:
        iv.stage_match(SimpleNamespace(limit=None))
    assert "master" in str(e.value.code)


def test_match_fuzzy_accepts_at_96_with_margin_and_reviews_the_rest(base, capsys):
    write_master(base, [(cin("GJ", serial="000001"), "NATIONAL FERTILISERS CHEMICALS PLASTICS PRIVATE LIMITED", "Active"),
                        (cin("GJ", serial="000002"), "GAMMA GLASS MAKERS PRIVATE LIMITED", "Active"),
                        (cin("GJ", serial="000003"), "OMEGA ROPES CABLES WIRES PLASTICA PRIVATE LIMITED", "Active"),
                        (cin("GJ", serial="000004"), "OMEGA ROPES CABLES WIRES PLASTICS PRIVATE LIMITED", "Active")])
    make_co([{"name": "NATIONAL FERTILISERS CHEMICALS PLASTICQ PVT LTD", "state": "GUJARAT"},   # one close master, score ~97 -> fuzzy accept
             {"name": "GAMMA GLASS MAKERS SONS PVT LTD", "state": "GUJARAT"},                   # score ~88 -> review only
             {"name": "OMEGA ROPES CABLES WIRES PLASTICQ PVT LTD", "state": "GUJARAT"},         # two masters tie -> margin 0 -> review
             {"name": "ZETA UNRELATED NAME PVT LTD", "state": "GUJARAT"}])                      # nothing similar -> untouched
    iv.stage_match(SimpleNamespace(limit=None))
    co = table().set_index("name")
    acc = co.loc["NATIONAL FERTILISERS CHEMICALS PLASTICQ PVT LTD"]
    assert acc.src == "master-fuzzy" and acc.cin == cin("GJ", serial="000001") and 96 <= acc.score < 100
    for n in ("GAMMA GLASS MAKERS SONS PVT LTD", "OMEGA ROPES CABLES WIRES PLASTICQ PVT LTD", "ZETA UNRELATED NAME PVT LTD"):
        assert pd.isna(co.loc[n, "cin"]) and pd.isna(co.loc[n, "src"])
    g = json.loads(co.loc["GAMMA GLASS MAKERS SONS PVT LTD", "cand"])
    assert [c["cin"] for c in g] == [cin("GJ", serial="000002")] and set(g[0]) == {"cin", "name", "status"}
    assert co.loc["GAMMA GLASS MAKERS SONS PVT LTD", "score"] == pytest.approx(87.8, abs=0.1)
    o = json.loads(co.loc["OMEGA ROPES CABLES WIRES PLASTICQ PVT LTD", "cand"])
    assert {c["cin"] for c in o} == {cin("GJ", serial="000003"), cin("GJ", serial="000004")}
    assert pd.isna(co.loc["ZETA UNRELATED NAME PVT LTD", "cand"])
    assert "matched 1, ambiguous->review 2, of 4 tried" in capsys.readouterr().out


def test_match_limit_and_skips_already_identified_and_unusable(base):
    write_master(base, [(cin("GJ", serial="000001"), "ALPHA STEELS PRIVATE LIMITED", "Active"),
                        (cin("GJ", serial="000002"), "BETA CEMENT PRIVATE LIMITED", "Active"),
                        (cin("GJ", serial="000003"), "GAMMA GLASS PRIVATE LIMITED", "Active"),
                        (cin("GJ", serial="000004"), "DELTA DYES PRIVATE LIMITED", "Active")])
    make_co([{"name": "ALPHA STEELS PVT LTD", "state": "GUJARAT"}, {"name": "BETA CEMENT PVT LTD", "state": "GUJARAT"},
             {"name": "GAMMA GLASS PVT LTD", "state": "GUJARAT", "usable": False},
             {"name": "DELTA DYES PVT LTD", "state": "GUJARAT", "cin": cin("MH")}])
    iv.stage_match(SimpleNamespace(limit=1))
    co = table().set_index("name")
    assert co.cin.notna().sum() == 2                       # DELTA (preset) + exactly one new match from the limit of 1
    assert pd.isna(co.loc["GAMMA GLASS PVT LTD", "cin"])
    assert co.loc["DELTA DYES PVT LTD", "cin"] == cin("MH")


def test_match_same_name_twice_in_one_state_is_not_accepted(base):
    write_master(base, [(cin("GJ", serial="000001"), "ALPHA STEELS PRIVATE LIMITED", "Active"),
                        (cin("GJ", serial="000002"), "ALPHA STEELS PRIVATE LIMITED", "Active")])
    make_co([{"name": "ALPHA STEELS PVT LTD", "state": "GUJARAT"}])
    iv.stage_match(SimpleNamespace(limit=None))
    co = table()
    assert pd.isna(co.cin.iloc[0])
    assert len(json.loads(co.cand.iloc[0])) == 2           # both candidates handed to review


def test_master_accepts_alternative_column_names(base, tmp_path, capsys):
    p = tmp_path / "m.csv"
    pd.DataFrame({"CIN": [cin("GJ")], "NAME": ["ALPHA STEELS PRIVATE LIMITED"]}).to_csv(p, index=False)
    iv.stage_master(SimpleNamespace(master=str(p)))
    m = table("mca_master")
    assert m.status.isna().all() and m.state.tolist() == ["GUJARAT"] and m.key.tolist() == ["ALPHA STEELS"]


def test_master_without_required_columns_asserts(base, tmp_path):
    p = tmp_path / "m.csv"
    pd.DataFrame({"foo": [1]}).to_csv(p, index=False)
    with pytest.raises(AssertionError, match="need CIN"):
        iv.stage_master(SimpleNamespace(master=str(p)))


# --------------------------------------------------------------------------- thresholds (found by mutation testing)
def _match_one(base, query_name, masters):
    write_master(base, [(cin("GJ", serial=f"{i:06d}"), n + " PRIVATE LIMITED", "Active") for i, n in enumerate(masters, 1)])
    make_co([{"name": query_name + " PVT LTD", "state": "GUJARAT"}])
    iv.stage_match(SimpleNamespace(limit=None))
    return table().iloc[0]


def test_match_score_just_below_96_is_reviewed_not_accepted(base):
    q = "ALPHA " + "B" * 14                                     # token_sort_ratio against the master is exactly 95.0
    r = _match_one(base, q, ["ALPHA " + "B" * 13 + "C"])
    assert pd.isna(r.cin) and r.score == 95.0
    assert [c["cin"] for c in json.loads(r.cand)] == [cin("GJ", serial="000001")]


def test_match_margin_of_exactly_four_points_is_accepted(base):
    q = "ALPHA " + "B" * 19                                     # 96.0 vs the best master, 92.0 vs the runner-up
    r = _match_one(base, q, ["ALPHA " + "B" * 18 + "C", "ALPHA " + "B" * 17 + "CC"])
    assert r.src == "master-fuzzy" and r.cin == cin("GJ", serial="000001") and r.score == 96.0


def test_match_margin_below_four_points_is_reviewed(base):
    q = "ALPHA " + "B" * 19
    r = _match_one(base, q, ["ALPHA " + "B" * 18 + "C", "ALPHA " + "B" * 18 + "D"])
    assert pd.isna(r.cin) and len(json.loads(r.cand)) == 2


def test_match_score_of_exactly_80_goes_to_review(base):
    r = _match_one(base, "ALPHA BBBB", ["ALPHA BBCC"])
    assert pd.isna(r.cin) and r.score == 80.0 and len(json.loads(r.cand)) == 1


def test_match_score_below_80_is_dropped(base):
    r = _match_one(base, "ALPHA BBBB", ["ALPHA BCCC"])
    assert pd.isna(r.cin) and pd.isna(r.cand)


def test_extract_key_length_boundary(base):
    write_a(base, [("2018-01", 1, "ABCD LTD", "t", True, "GOA", "X", "f.pdf"), ("2018-01", 2, "ABC LTD", "t", True, "GOA", "X", "f.pdf")])
    iv.stage_extract(SimpleNamespace())
    assert list(table()["name"]) == ["ABCD LTD"]
