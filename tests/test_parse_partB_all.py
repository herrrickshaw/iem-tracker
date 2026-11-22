"""parse_partB_all is a top-level script. Run a copy of it from tmp_path (so its BASE is tmp_path) with the
text-layout parser faked, and check the merged partB_all.parquet and the printed summary."""
import runpy
import shutil
from pathlib import Path

import pandas as pd
import pytest

import extract_partA_text_layout as T

SCRIPT = Path(__file__).resolve().parent.parent / "parse_partB_all.py"
KEEP = ["company_name", "state", "month", "mem_no", "mem_date", "commenced", "source_file", "source", "name_has_suffix"]


def new_rows(rows):
    return pd.DataFrame(rows, columns=["company_name", "state", "month", "mem_no", "mem_date", "commenced", "source_file", "name_has_suffix"])


def old_row(name, state, month, mem_no, mem_date, src, suffix=True):
    return dict(company_name=name, state_header=state, month=month, mem_no=mem_no, mem_date=mem_date, commenced=None,
                source_file=src, name_has_suffix=suffix, serial=1)


@pytest.fixture
def run(tmp_path, monkeypatch):
    shutil.copy(SCRIPT, tmp_path / "parse_partB_all.py")
    (tmp_path / "PartB").mkdir()

    def go(new, files, parsed):
        new_rows(new).to_parquet(tmp_path / "partB_new.parquet")
        for f in files:
            (tmp_path / "PartB" / f).write_bytes(b"%PDF")
        calls = []

        def fake_parse(path):
            calls.append(Path(path).name)
            return [dict(r) for r in parsed.get(Path(path).name, [])], 0
        monkeypatch.setattr(T, "parse_file", fake_parse)
        runpy.run_path(str(tmp_path / "parse_partB_all.py"), run_name="not_main")
        return pd.read_parquet(tmp_path / "partB_all.parquet"), calls
    return go


def test_files_already_in_new_format_are_not_reparsed(run):
    out, calls = run(new=[("ALPHA STEELS LTD", "GUJARAT", "2020-01", 1, "01/01/2019", None, "2020-01_a.pdf", True)],
                     files=["2020-01_a.pdf", "2012-01_b.pdf"],
                     parsed={"2012-01_b.pdf": [old_row("BETA CEMENT LTD", "GOA", "2012-01", 5, "02/02/2011", "2012-01_b.pdf")]})
    assert calls == ["2012-01_b.pdf"]
    assert sorted(out.company_name) == ["ALPHA STEELS LTD", "BETA CEMENT LTD"]
    src = dict(zip(out.company_name, out.source))
    assert src == {"ALPHA STEELS LTD": "new-format", "BETA CEMENT LTD": "text-layout"}


def test_old_format_state_comes_from_state_header_and_columns_are_the_keep_list(run):
    out, _ = run(new=[("ALPHA STEELS LTD", "GUJARAT", "2020-01", 1, "01/01/2019", None, "2020-01_a.pdf", True)],
                 files=["2012-01_b.pdf"],
                 parsed={"2012-01_b.pdf": [old_row("BETA CEMENT LTD", "GOA", "2012-01", 5, "02/02/2011", "2012-01_b.pdf")]})
    assert list(out.columns) == KEEP
    assert out.set_index("company_name").loc["BETA CEMENT LTD", "state"] == "GOA"


def test_short_names_are_dropped_at_three_characters(run):
    out, _ = run(new=[("AB", "GOA", "2020-01", 1, "d", None, "2020-01_a.pdf", True), ("ABC", "GOA", "2020-01", 2, "d", None, "2020-01_a.pdf", True)],
                 files=["2020-01_a.pdf", "2000-01_old.pdf"],
                 parsed={"2000-01_old.pdf": [old_row("XY", "GOA", "2000-01", 9, "d", "2000-01_old.pdf"),
                                              old_row("XYZ", "GOA", "2000-01", 9, "d", "2000-01_old.pdf")]})
    assert sorted(out.company_name) == ["ABC", "XYZ"]


def test_duplicates_collapse_on_month_name_memno_memdate_keeping_earliest_source_file(run):
    out, _ = run(new=[("ALPHA STEELS LTD", "GUJARAT", "2020-01", 1, "01/01/2019", None, "2020-01_z.pdf", True),
                      ("ALPHA STEELS LTD", "GUJARAT", "2020-01", 1, "01/01/2019", None, "2020-01_a.pdf", True),
                      ("ALPHA STEELS LTD", "GUJARAT", "2020-01", 2, "01/01/2019", None, "2020-01_a.pdf", True),     # other mem_no
                      ("ALPHA STEELS LTD", "GUJARAT", "2020-02", 1, "01/01/2019", None, "2020-02_a.pdf", True)],    # other month
                 files=["2020-01_z.pdf", "2020-01_a.pdf", "2020-02_a.pdf", "2000-01_old.pdf"],
                 parsed={"2000-01_old.pdf": [old_row("FILLER WIDGETS LTD", "GOA", "2000-01", 9, "01/01/1999", "2000-01_old.pdf")]})
    out = out[out.company_name == "ALPHA STEELS LTD"]
    assert len(out) == 3
    kept = out[(out.month == "2020-01") & (out.mem_no == 1)]
    assert list(kept.source_file) == ["2020-01_a.pdf"]


def test_summary_line_counts(run, capsys):
    run(new=[("ALPHA STEELS LTD", "GUJARAT", "2020-01", 1, "01/01/2019", None, "2020-01_a.pdf", True)],
        files=["2020-01_a.pdf", "2012-01_b.pdf", "2012-02_c.pdf"],
        parsed={"2012-01_b.pdf": [old_row("BETA CEMENT LTD", "GOA", "2012-01", 5, "02/02/2011", "2012-01_b.pdf"),
                                  old_row("GAMMA GLASS LTD", "GOA", "2012-01", 6, "02/02/2011", "2012-01_b.pdf")]})
    out = capsys.readouterr().out
    assert "3 unique Part B records; 3 distinct names; old-format 2 | files covered 2 of 3" in out
    assert "2012" in out and "2020" in out


def test_year_table_has_suffix_rate_and_memno_coverage(run, capsys):
    run(new=[("ALPHA STEELS LTD", "GUJARAT", "2020-01", 1, "01/01/2019", None, "2020-01_a.pdf", True),
             ("BETA CEMENT LTD", None, "2020-01", None, None, None, "2020-01_a.pdf", False)],
        files=["2020-01_a.pdf", "2020-01_old.pdf"],
        parsed={"2020-01_old.pdf": [old_row("GAMMA GLASS LTD", "GOA", "2020-01", 3, "01/01/2019", "2020-01_old.pdf", suffix=False)]})
    out = capsys.readouterr().out
    lines = {l.split()[0]: l.split()[1:] for l in out.splitlines() if l.split() and l.split()[0] in ("rows", "suffix", "memno", "state")}
    assert lines["rows"] == ["3.00"] and lines["suffix"] == ["0.33"] and lines["memno"] == ["0.67"] and lines["state"] == ["0.67"]


@pytest.mark.xfail(strict=True, raises=AttributeError, reason=(
    "parse_partB_all.py: when every Part B PDF is already in partB_new.parquet no old-format rows are parsed, so "
    "`old = pd.DataFrame([])` has no `state_header` column and `old['state'] = old.state_header` raises AttributeError "
    "instead of just writing the new-format rows."))
def test_all_files_already_in_new_format_still_writes_output(run):
    out, calls = run(new=[("ALPHA STEELS LTD", "GUJARAT", "2020-01", 1, "01/01/2019", None, "2020-01_a.pdf", True)],
                     files=["2020-01_a.pdf"], parsed={})
    assert calls == [] and list(out.company_name) == ["ALPHA STEELS LTD"]
