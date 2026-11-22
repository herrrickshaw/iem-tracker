"""make_article_update: stats(), build_section() and main() on a synthetic company DB, records CSV and article HTML.

ARCH (the blog archive under the home directory) and the script's own directory are redirected to tmp_path."""
import re
import sys

import duckdb
import pandas as pd
import pytest

import make_article_update as m

CIN_A = "U24100GJ2010PTC000001"      # inc 2010
CIN_B = "U24100GJ2012PTC000002"
CIN_C = "U24100MH2011PTC000003"
CIN_D = "U24100GJ2019PTC000004"      # inc 2019 > filing 2017 -> dropped
CIN_E = "U24100KL2005PLC000005"      # LEI-register identification
CIN_F_OLD = "U24100MH2008PTC000006"
CIN_F_NEW = "L24100MH2008PLC000006"  # GLEIF CIN replaces it
CIN_Z = "U24100GJ2009PTC000007"


def build_db(path):
    co = pd.DataFrame([
        # id, key, state, cin, status, src
        (1, "ALPHA STEELS", "GUJARAT", CIN_A, "Active", "web-slug+state"),
        (2, "BETA CEMENT", "GUJARAT", CIN_B, "Strike Off", "web-slug+state"),
        (3, "GAMMA GLASS", "MAHARASHTRA", CIN_C, "Active", "web-slug-name-only"),
        (4, "DELTA DYES", "GUJARAT", CIN_D, "Active", "web-slug+state"),
        (5, "EPSILON METALS", "KERALA", None, None, None),
        (6, "THETA ROPES", "MAHARASHTRA", CIN_F_OLD, "Active", "web-slug-name-only"),
        (7, "ZETA PAPERS", "GUJARAT", CIN_Z, "Active", "web-slug+state"),
    ], columns=["id", "key", "state", "cin", "status", "src"])
    gv = pd.DataFrame([
        (1, "confirmed", CIN_A, "ACTIVE"),
        (2, "confirmed", CIN_B, "INACTIVE"),
        (3, "conflict", "U99999MH1990PTC000099", "ACTIVE"),
        (4, "confirmed", CIN_D, "ACTIVE"),
        (5, "new", CIN_E, "ACTIVE"),
        (6, "cin_changed", CIN_F_NEW, "ACTIVE"),
        (7, "confirmed", CIN_Z, "ACTIVE"),
    ], columns=["id", "verdict", "gleif_cin", "gleif_entity_status"])
    k = duckdb.connect(str(path))
    k.execute("create table co as select * from co"); k.execute("create table gleif_verdict as select * from gv"); k.close()


def records():
    rows = [
        ("A", "ALPHA STEELS PVT LTD", "GUJARAT", "01/03/2018", "none"),
        ("A", "BETA CEMENT PVT LTD", "GUJARAT", "01/03/2019", "none"),
        ("A", "GAMMA GLASS PVT LTD", "MAHARASHTRA", "01/03/2022", "none"),
        ("A", "DELTA DYES PVT LTD", "GUJARAT", "01/03/2017", "none"),
        ("A", "EPSILON METALS PVT LTD", "KERALA", "01/03/2023", "none"),
        ("A", "ZETA PAPERS PVT LTD", "GUJARAT", "01/03/2021", "memo"),
        ("A", "ETA TEXTILES PVT LTD", "GUJARAT", "01/03/2021", "none"),
        ("A", "THETA ROPES PVT LTD", "MAHARASHTRA", "01/03/2022", "none"),
        ("B", "ALPHA STEELS PVT LTD", "GUJARAT", "01/03/2018", "none"),              # Part B rows ignored
        ("A", "OLD ONE PVT LTD", "GUJARAT", "01/03/2010", "none"),                  # before 2017: outside the table
    ]
    return pd.DataFrame(rows, columns=["part", "company", "state", "iem_date", "in_part_b"])


def apify(path):
    pd.DataFrame([
        (CIN_E, "Active", "06/01/2025", "03/31/2025"),                             # strong
        (CIN_F_NEW, "Strike Off", "06/01/2025", "03/31/2025"),                    # not active
        (CIN_E, "Strike Off", "01/01/2000", "01/01/2000"),                         # duplicate cin: first kept
    ], columns=["cin", "status", "dateOfLastAGM", "balanceSheetDate"]).to_csv(path, index=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    arch = tmp_path / "arch"
    (arch / "data" / "iem").mkdir(parents=True)
    (arch / "out").mkdir(); (arch / "posts").mkdir()
    records().to_csv(arch / "data/iem/iem_records.csv", index=False)
    db = tmp_path / "iem_verify.duckdb"
    build_db(db)
    fake_script = tmp_path / "scripts" / "make_article_update.py"
    fake_script.parent.mkdir()
    apify(fake_script.parent / "apify_mca.csv")
    monkeypatch.setattr(m, "ARCH", arch)
    monkeypatch.setattr(m, "__file__", str(fake_script))
    return dict(arch=arch, db=db, script_dir=fake_script.parent, tmp=tmp_path)


# --------------------------------------------------------------------------- tiny helpers
def test_fmt_and_pct():
    assert m.fmt(1234567) == "1,234,567" and m.fmt(0) == "0" and m.fmt(12.0) == "12"
    assert m.pct(1, 3) == "33%" and m.pct(2, 3) == "67%" and m.pct(1, 8) == "12%" and m.pct(0, 5) == "0%"
    assert m.pct(5, 0) == "n/a"


@pytest.mark.parametrize("s,hit", [("Strike Off", True), ("Striking Off", True), ("Amalgamated", True), ("Under Liquidation", True),
                                   ("Dissolved or inactive (LEI)", True), ("Closed", True), ("Under CIRP", True),
                                   ("Converted to LLP", True), ("Active", False), ("Dormant", False), ("", False)])
def test_inactive_regex(s, hit):
    assert bool(m.INACTIVE.search(s)) is hit


# --------------------------------------------------------------------------- stats
@pytest.fixture
def mm(env):
    return m.stats(env["db"])


def row(mm, name):
    return mm[mm.company == name].iloc[0]


def test_stats_keeps_part_a_only(mm):
    assert len(mm) == 9 and set(mm.part) == {"A"}


def test_stats_identified_and_active_flags(mm):
    a = row(mm, "ALPHA STEELS PVT LTD")
    assert bool(a.ident) and bool(a.active) and not bool(a.inactive) and a.cin == CIN_A and a.year == 2018
    b = row(mm, "BETA CEMENT PVT LTD")
    assert bool(b.ident) and not bool(b.active) and bool(b.inactive)


def test_stats_conflict_match_is_removed(mm):
    g = row(mm, "GAMMA GLASS PVT LTD")
    assert not bool(g.ident) and pd.isna(g.cin) and bool(g.dropped_conflict) and not bool(g.dropped)


def test_stats_cin_incorporated_after_filing_is_dropped(mm):
    d = row(mm, "DELTA DYES PVT LTD")
    assert bool(d.dropped) and not bool(d.dropped_conflict) and not bool(d.ident) and pd.isna(d.cin) and pd.isna(d.src)


def test_stats_incorporation_year_equal_to_filing_year_is_kept(env):
    k = duckdb.connect(str(env["db"]))
    k.execute("update co set cin='U24100GJ2017PTC000004' where key='DELTA DYES'")
    k.execute("update gleif_verdict set gleif_cin='U24100GJ2017PTC000004' where id=4")
    k.close()
    d = row(m.stats(env["db"]), "DELTA DYES PVT LTD")
    assert not bool(d.dropped) and bool(d.ident)


def test_stats_lei_register_adds_new_companies(mm):
    e = row(mm, "EPSILON METALS PVT LTD")
    assert e.cin == CIN_E and e.src == "lei-register" and e.status == "Active" and bool(e.active)


def test_stats_inactive_lei_entity_status_label(env):
    k = duckdb.connect(str(env["db"]))
    k.execute("update gleif_verdict set gleif_entity_status='INACTIVE' where id=5")
    k.close()
    e = row(m.stats(env["db"]), "EPSILON METALS PVT LTD")
    assert e.status == "Dissolved or inactive (LEI)" and bool(e.inactive) and not bool(e.active)


def test_stats_cin_changed_uses_gleif_cin(mm):
    t = row(mm, "THETA ROPES PVT LTD")
    assert t.cin == CIN_F_NEW and bool(t.ident)


def test_stats_reached_flag(mm):
    assert bool(row(mm, "ZETA PAPERS PVT LTD").reached) is True
    assert bool(row(mm, "ETA TEXTILES PVT LTD").reached) is False
    assert bool(row(mm, "ALPHA STEELS PVT LTD").reached) is False


@pytest.mark.parametrize("val,reached", [("memo", True), ("name", True), ("none", False), ("", False)])
def test_stats_reached_only_for_memo_or_name(env, val, reached):
    r = records(); r.loc[r.company == "ETA TEXTILES PVT LTD", "in_part_b"] = val
    r.to_csv(env["arch"] / "data/iem/iem_records.csv", index=False)
    assert bool(row(m.stats(env["db"]), "ETA TEXTILES PVT LTD").reached) is reached


def test_stats_unmatched_company_is_not_identified(mm):
    e = row(mm, "ETA TEXTILES PVT LTD")
    assert not bool(e.ident) and not bool(e.active) and not bool(e.inactive)


def test_stats_conflict_rates_and_coverage(mm):
    # name-and-state tier: ALPHA, BETA, DELTA, ZETA confirmed -> 0% conflict; name-only: GAMMA conflict + THETA cin_changed -> 50%
    assert mm.attrs["rate1"] == 0.0 and mm.attrs["rate2"] == 0.5
    assert mm.attrs["covered"] == 6                 # web-matched, verdict in confirmed/cin_changed/conflict
    assert mm.attrs["nweb"] == 6                    # co rows with a cin and a web source


def test_stats_year_parsed_day_first(mm):
    assert row(mm, "ALPHA STEELS PVT LTD").year == 2018 and row(mm, "OLD ONE PVT LTD").year == 2010


# --------------------------------------------------------------------------- build_section
@pytest.fixture
def section(env, mm):
    return m.build_section(mm)


def test_build_section_counts(section):
    _, s = section
    # filings 2017-2024 with no Part B: ALPHA, BETA, GAMMA, DELTA, EPSILON, ETA, THETA = 7 (ZETA reached, OLD ONE 2010)
    assert s == dict(n=7, idn=4, act=3, ina=1, tier1=2, tier2=1, r_id=1, r_act=1)


def test_build_section_html_rows_and_totals(section):
    html, _ = section
    assert "<tr><td>2018</td><td>1</td><td>1</td><td>1</td><td>0</td><td>100%</td></tr>" in html
    assert "<tr><td>2019</td><td>1</td><td>1</td><td>0</td><td>1</td><td>100%</td></tr>" in html
    assert "<tr><td>2022</td><td>2</td><td>1</td><td>1</td><td>0</td><td>50%</td></tr>" in html           # GAMMA dropped, THETA kept
    assert "<tr><td>2017</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0%</td></tr>" in html           # DELTA dropped
    assert "<td><b>7</b></td><td><b>4</b></td><td><b>3</b></td><td><b>1</b></td><td><b>57%</b></td>" in html
    assert "<tr><td>2024</td><td>0</td><td>0</td><td>0</td><td>0</td><td>n/a</td></tr>" in html


def test_build_section_narrative_numbers(section):
    html, _ = section
    assert "Of 7 such filings, 4 (57%) could be tied to a registered company. Of those, 3 (75%) are listed as Active and 1 (25%) are struck off" in html
    assert "100% of companies are also Active" in html
    assert "2 of the identified filings matched on legal name and on a registered office" in html
    assert "1 matched on legal name alone" in html
    assert "1 came from the global Legal Entity Identifier" in html
    assert "(1 filings)" in html                                                      # DELTA dropped as registered after filing
    assert "1 such filings were removed from the counts" in html                      # GAMMA conflict
    assert "100% of the name-and-state matches and 50% of the name-only matches are the same company" in html
    assert "6 of the 6 matched company records" in html


def test_build_section_live_record_table(section):
    html, _ = section
    # identified 2021-2024 with no Part B: EPSILON (2023, strong), THETA (2022, Strike Off live)
    assert "<tr><td>2022</td><td>1</td><td>1</td><td>0</td><td>0</td><td>1</td></tr>" in html
    assert "<tr><td>2023</td><td>1</td><td>1</td><td>1</td><td>1</td><td>0</td></tr>" in html
    assert "<td><b>2021&ndash;2024</b></td><td><b>2</b></td><td><b>2</b></td><td><b>1</b></td><td><b>1</b></td><td><b>1</b></td>" in html
    assert "Not Active: 1 struck off." in html
    assert "A record came back for 2 of them (100%)" in html
    assert "Of the 2 records, 1 (50%) are Active and 1 (50%) are Active and still filing" in html
    assert "1 are not Active, and all of them had appeared as Active" in html


def test_build_section_balanced_html(section):
    html, _ = section
    for tag in ("div", "table", "tr", "td", "th", "p", "h2"):
        assert len(re.findall(rf"<{tag}[ >]", html)) == html.count(f"</{tag}>"), tag


def test_build_section_strong_requires_agm_and_balance_sheet_cutoffs(env):
    # AGM exactly on 2025-01-01 and balance sheet exactly 2025-03-31 count as strong; a day earlier does not
    pd.DataFrame([(CIN_E, "Active", "01/01/2025", "03/31/2025"), (CIN_F_NEW, "Active", "12/31/2024", "03/31/2025")],
                 columns=["cin", "status", "dateOfLastAGM", "balanceSheetDate"]).to_csv(env["script_dir"] / "apify_mca.csv", index=False)
    html, _ = m.build_section(m.stats(env["db"]))
    assert "Of the 2 records, 2 (100%) are Active and 1 (50%) are Active and still filing" in html
    pd.DataFrame([(CIN_E, "Active", "01/01/2025", "03/30/2025")], columns=["cin", "status", "dateOfLastAGM", "balanceSheetDate"]
                 ).to_csv(env["script_dir"] / "apify_mca.csv", index=False)
    html, _ = m.build_section(m.stats(env["db"]))
    assert "are Active and still filing" in html and "(0%) are Active and still filing" in html


# --------------------------------------------------------------------------- main
ARTICLE = """<html><body>
<div class="gb-card"><p>stat cards</p></div>

<h2 class="gb-h2">What an IEM is, and who files one</h2>
<p>Industrial Policy &middot; Data &middot; 7 October 2026</p>
<h2 class="gb-h2">Which states follow through</h2>
<p>states</p>

<h2 class="gb-h2">How the register compares with DPIIT&rsquo;s own totals</h2>
<p>totals</p>

<h2 class="gb-h2">How the lists were read</h2>
<p>method</p>

<h2 class="gb-h2">Sources and caveats</h2>
<div><p>Sources. A filing with no Part B found may still have been built.</p>
</div>
</body></html>"""


def run_main(monkeypatch, env, *argv):
    monkeypatch.setattr(sys, "argv", ["make_article_update.py", *argv])
    m.main()


def article_env(env):
    orig = env["arch"] / "out/post-iem-register-2001-2024.html.orig"
    orig.write_text(ARTICLE)
    return orig


def test_main_writes_draft_with_all_insertions(env, monkeypatch, capsys):
    article_env(env)
    out = env["tmp"] / "draft.html"
    run_main(monkeypatch, env, "--db", str(env["db"]), "--out", str(out),
             "--src", str(env["arch"] / "out/post-iem-register-2001-2024.html.orig"))
    h = out.read_text()
    assert "<b>Update, 9 October 2026.</b>" in h and "<b>Correction, 10 October 2026:</b>" in h
    assert "Industrial Policy &middot; Data &middot; 7 October 2026, updated 9 October 2026" in h
    assert h.index("Behind the filings with no Part B") < h.index("How the register compares with DPIIT")
    assert h.index("stat cards") < h.index("<b>Update, 9 October 2026.</b>") < h.index("What an IEM is")
    assert h.index("The company check added on 9 October 2026") < h.index("Sources and caveats")
    assert "QuickCompany" in h and "factden/mca-company-director-scraper" in h
    stdout = capsys.readouterr().out
    assert f"draft written: {out}" in stdout
    assert "{'n': 7, 'idn': 4, 'act': 3, 'ina': 1, 'tier1': 2, 'tier2': 1, 'r_id': 1, 'r_act': 1}" in stdout


def test_main_does_not_touch_the_original(env, monkeypatch):
    orig = article_env(env)
    run_main(monkeypatch, env, "--db", str(env["db"]), "--out", str(env["tmp"] / "d.html"), "--src", str(orig))
    assert orig.read_text() == ARTICLE


def test_main_first_run_copies_live_post_to_orig(env, monkeypatch):
    (env["arch"] / "posts/indias-factory-intentions-20012024-how_0101046393.html").write_text(ARTICLE)
    out = env["arch"] / "out/post-iem-register-2001-2024.html"
    run_main(monkeypatch, env, "--db", str(env["db"]), "--out", str(out))
    orig = env["arch"] / "out/post-iem-register-2001-2024.html.orig"
    assert orig.read_text() == ARTICLE and out.exists()


@pytest.mark.parametrize("missing", [
    "</div>\n\n<h2 class=\"gb-h2\">What an IEM is, and who files one</h2>",
    "<h2 class=\"gb-h2\">How the register compares with DPIIT&rsquo;s own totals</h2>",
    "\n\n<h2 class=\"gb-h2\">Sources and caveats</h2>",
])
def test_main_fails_loudly_when_an_anchor_is_missing(env, monkeypatch, missing):
    orig = env["arch"] / "out/post-iem-register-2001-2024.html.orig"
    orig.write_text(ARTICLE.replace(missing, "<!-- gone -->"))
    with pytest.raises(AssertionError):
        run_main(monkeypatch, env, "--db", str(env["db"]), "--out", str(env["tmp"] / "d.html"), "--src", str(orig))
    assert not (env["tmp"] / "d.html").exists()


def test_main_unbalanced_tags_abort_before_writing(env, monkeypatch):
    orig = env["arch"] / "out/post-iem-register-2001-2024.html.orig"
    orig.write_text(ARTICLE.replace("<p>states</p>", "<p>states"))
    with pytest.raises(AssertionError, match="tag imbalance p"):
        run_main(monkeypatch, env, "--db", str(env["db"]), "--out", str(env["tmp"] / "d.html"), "--src", str(orig))
    assert not (env["tmp"] / "d.html").exists()


def test_main_missing_final_sentence_means_sources_not_extended(env, monkeypatch):
    orig = env["arch"] / "out/post-iem-register-2001-2024.html.orig"
    orig.write_text(ARTICLE.replace("A filing with no Part B found may still have been built.", "Something else."))
    with pytest.raises(AssertionError, match="sources not extended"):
        run_main(monkeypatch, env, "--db", str(env["db"]), "--out", str(env["tmp"] / "d.html"), "--src", str(orig))
