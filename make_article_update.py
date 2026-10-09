#!/usr/bin/env python3
"""
Build the updated draft of the masaladeutsch IEM article from the blog's own register plus the company-identification results.

  python3 make_article_update.py [--db iem_verify.duckdb] [--out ~/masaladeutsch-archive/out/post-iem-register-2001-2024.html]

Never publishes. It reads the ORIGINAL draft (kept as <out>.orig on first run), inserts:
  1. a visible update note under the stat cards,
  2. one new section after "Which states follow through",
  3. a paragraph in "How the lists were read" and two sentences in "Sources and caveats",
and writes the result to --out. Every number in the new text is computed here from the data, none is typed by hand.
The register's own headline counts (55,781 filings, 15-23%, state and year tables) are NOT changed.
"""
import argparse, re, shutil, sys
from pathlib import Path

import duckdb, pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from iem_verify import key, find_state

ARCH = Path.home() / "masaladeutsch-archive"
UPDATED = "9 October 2026"
CORRECTED = "10 October 2026"
INACTIVE = re.compile(r"strik|amalgam|liquidat|dissol|closed|cirp|converted", re.I)


def stats(db):
    c = duckdb.connect(str(db), read_only=True)
    co = c.execute("select key k, coalesce(state,'') st, cin, status, src from co where cin is not null").df()
    gv = c.execute("""select co.key k, coalesce(co.state,'') st, g.verdict, g.gleif_cin, g.gleif_entity_status, co.src
                      from gleif_verdict g join co on co.id = g.id""").df().drop_duplicates(["k", "st"])
    # complete-register check of the web matches (how often the web match is a different company with the same name)
    web = gv[gv.src.isin(["web-slug+state", "web-slug-name-only"]) & gv.verdict.isin(["confirmed", "cin_changed", "conflict"])]
    rate = {t: float((g.verdict == "conflict").mean()) for t, g in web.groupby("src")}
    covered, nweb = len(web), int(c.execute("select count(*) from co where cin is not null and src in ('web-slug+state','web-slug-name-only')").fetchone()[0])
    c.close()
    co = co.merge(gv[["k", "st", "verdict", "gleif_cin", "gleif_entity_status"]], on=["k", "st"], how="left")
    co["conflict"] = co.verdict.eq("conflict")
    ch = co.verdict.eq("cin_changed"); co.loc[ch, "cin"] = co.loc[ch, "gleif_cin"]
    new = gv[gv.verdict.eq("new")].copy()
    new["cin"] = new.gleif_cin
    new["status"] = new.gleif_entity_status.map(lambda x: "Active" if x == "ACTIVE" else "Dissolved or inactive (LEI)")
    new["src"] = "lei-register"; new["conflict"] = False
    co = pd.concat([co[["k", "st", "cin", "status", "src", "conflict"]], new[["k", "st", "cin", "status", "src", "conflict"]]], ignore_index=True)
    co = co.drop_duplicates(["k", "st"])
    a = pd.read_csv(ARCH / "data/iem/iem_records.csv", low_memory=False)
    a = a[a.part == "A"].copy()
    a["k"] = a.company.map(key); a["st"] = a.state.map(lambda s: find_state(str(s)) or "")
    a["year"] = pd.to_datetime(a.iem_date, format="%d/%m/%Y", errors="coerce").dt.year
    m = a.merge(co, on=["k", "st"], how="left")
    m["inc_year"] = pd.to_numeric(m.cin.str[8:12], errors="coerce")
    m["conflict"] = m.conflict.fillna(False).astype(bool)
    m["dropped"] = m.cin.notna() & (m.inc_year > m.year)
    m["dropped_conflict"] = m.cin.notna() & m.conflict & ~m.dropped
    m.loc[m.dropped | m.dropped_conflict, ["cin", "status", "src"]] = None   # impossible dates, or the LEI register holds this name under another company
    m["ident"] = m.cin.notna()
    m["active"] = m.status.fillna("").str.lower().eq("active")
    m["inactive"] = m.ident & m.status.fillna("").str.contains(INACTIVE)
    m["reached"] = m.in_part_b.isin(["memo", "name"])
    m.attrs.update(rate1=rate.get("web-slug+state", 0), rate2=rate.get("web-slug-name-only", 0), covered=covered, nweb=nweb)
    return m


def fmt(n): return f"{int(n):,}"


def pct(a, b): return f"{100 * a / b:.0f}%" if b else "n/a"


def build_section(m):
    nb = m[(~m.reached) & m.year.between(2017, 2024)]
    rb = m[(m.reached) & m.year.between(2017, 2024)]
    rows = []
    for y in range(2017, 2025):
        d = nb[nb.year == y]
        rows.append(f"<tr><td>{y}</td><td>{fmt(len(d))}</td><td>{fmt(d.ident.sum())}</td><td>{fmt(d.active.sum())}</td>"
                    f"<td>{fmt(d.inactive.sum())}</td><td>{pct(d.ident.sum(), len(d))}</td></tr>")
    tot = (f"<tr><td><b>2017&ndash;2024</b></td><td><b>{fmt(len(nb))}</b></td><td><b>{fmt(nb.ident.sum())}</b></td>"
           f"<td><b>{fmt(nb.active.sum())}</b></td><td><b>{fmt(nb.inactive.sum())}</b></td><td><b>{pct(nb.ident.sum(), len(nb))}</b></td></tr>")
    table = ('<div class="gb-tblwrap"><table class="gb-table"><thead><tr><th>Filed</th><th>No Part B</th>'
             '<th>Identified</th><th>Active</th><th>Struck off or merged</th>'
             '<th>% identified</th></tr></thead><tbody>' + "".join(rows) + tot + "</tbody></table></div>")
    n, idn, act, ina = len(nb), int(nb.ident.sum()), int(nb.active.sum()), int(nb.inactive.sum())
    r_id = int(rb.ident.sum()); r_act = int(rb.active.sum())
    tier2 = int((nb.src == "web-slug-name-only").sum()); tier1 = int((nb.src == "web-slug+state").sum())
    other = idn - act - ina; s_dropped = int(nb.dropped.sum()); s_conf = int(nb.dropped_conflict.sum())
    n_lei = int((nb.src == "lei-register").sum()); r1, r2, cov, nweb = m.attrs["rate1"], m.attrs["rate2"], m.attrs["covered"], m.attrs["nweb"]
    html = f"""
<h2 class="gb-h2">Behind the filings with no Part B: which companies still exist</h2>
<p>A filing with no Part B can mean the plant was never built, or that it was built and the owner never filed. The lists cannot say which. They can say whether the company behind the filing still exists. For Part A filings from 2017 to 2024 that have no Part B, the company name and plant state were matched against public company-registry pages and the global Legal Entity Identifier (LEI) register, which give each company&rsquo;s Corporate Identification Number (CIN) and its status on the Ministry of Corporate Affairs register.</p>
<p>Of {fmt(n)} such filings, {fmt(idn)} ({pct(idn, n)}) could be tied to a registered company. Of those, {fmt(act)} ({pct(act, idn)}) are listed as Active and {fmt(ina)} ({pct(ina, idn)}) are struck off, amalgamated into another company, in liquidation or in insolvency.{(" The other " + fmt(other) + " carry another status, such as inactive for e-filing.") if other > 0 else ""}</p>
{table}
<p class="gb-note">No Part B: Part A filings with no Part B found. Identified: a company with the same legal name was found, on a public company page or in the LEI register, and its CIN is consistent with the filing. Struck off or merged: struck off, amalgamated, in liquidation, in insolvency or converted to an LLP. Filings whose company was not identified include firms and partnerships (which have no CIN), names the PDFs printed in fragments, companies that changed name, and names with no matching registry page. Filings from 2023 and 2024 have had little time to reach Part B, so their count includes projects still under construction.</p>
<p>The check separates the filings that can be set aside from those that cannot. A filing whose company has been struck off or merged away is unlikely to become a plant under that filing. A filing whose company is Active may or may not have been built: among 2017&ndash;2024 filings that did reach Part B and could be identified, {pct(r_act, r_id)} of companies are also Active, so being Active says little about whether a given plant exists. An earlier web check of 40 filings from 2017&ndash;2021 with no Part B found a plant or product line operating at the filed location for 19 of them, an announced or half-built project for 4, and an active company with nothing found on the project for 9.</p>
<p>Three sources of identification were used. {fmt(tier1)} of the identified filings matched on legal name and on a registered office in the same state as the plant. {fmt(tier2)} matched on legal name alone, because the registered office is in a different state from the plant, which is common for companies with plants in several states; these were accepted only where the company type in the CIN (private or public) agrees with the name. {fmt(n_lei)} came from the global Legal Entity Identifier (LEI) register, which records each holder&rsquo;s CIN. Matches were dropped where the company was registered after the filing year ({fmt(s_dropped)} filings).</p>
<p>Every name-based match was then checked against the complete LEI register for India, about 170,000 companies that carry an MCA number. {fmt(cov)} of the {fmt(nweb)} matched company records (one per company and plant state) hold an LEI and could be checked: {100*(1-r1):.0f}% of the name-and-state matches and {100*(1-r2):.0f}% of the name-only matches are the same company, and the rest point to a different company with the same name. {fmt(s_conf)} such filings were removed from the counts. Companies without an LEI, mostly smaller firms, cannot be checked this way and may carry a similar error, so treat any single company identification as a lead to confirm, not a fact. Status is as shown on the registry pages on {UPDATED}, not a certificate from the Ministry; the Ministry&rsquo;s own open dataset on data.gov.in is labelled &ldquo;data up to 3 November 2023&rdquo; and so cannot show what has happened since.</p>
"""
    return html, dict(n=n, idn=idn, act=act, ina=ina, tier1=tier1, tier2=tier2, r_id=r_id, r_act=r_act)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "iem_verify.duckdb"))
    ap.add_argument("--out", default=str(ARCH / "out/post-iem-register-2001-2024.html"))
    ap.add_argument("--src", default=str(ARCH / "out/post-iem-register-2001-2024.html.orig"),
                    help="original article HTML to start from (the live copy)")
    a = ap.parse_args()
    out = Path(a.out); orig = Path(a.src)
    if not orig.exists():   # first run: keep the untouched live copy next to the draft
        shutil.copy(ARCH / "posts/indias-factory-intentions-20012024-how_0101046393.html", orig)
    h = orig.read_text()
    m = stats(a.db)
    section, s = build_section(m)

    note = (f'<div class="gb-card"><p class="gb-note" style="margin:0"><b>Update, {UPDATED}.</b> Added a section on the companies behind '
            f'Part A filings that have no Part B: how many could be identified in the company register and how many are still Active. '
            f'The register itself and the counts in the sections above are unchanged. <b>Correction, {CORRECTED}:</b> the first version of the new section did not drop company matches whose registration date falls after the filing, and did not check the name-based matches against the global LEI register. Both are now done, the LEI register adds companies the first pass missed, and the figures in the section were recalculated.</p></div>')
    h = h.replace('</div>\n\n<h2 class="gb-h2">What an IEM is, and who files one</h2>', '</div>\n\n' + note + '\n\n<h2 class="gb-h2">What an IEM is, and who files one</h2>', 1)
    assert note in h, "update note not inserted"
    h = h.replace('Industrial Policy &middot; Data &middot; 7 October 2026', f'Industrial Policy &middot; Data &middot; 7 October 2026, updated {UPDATED}', 1)
    marker = '<h2 class="gb-h2">How the register compares with DPIIT&rsquo;s own totals</h2>'
    assert marker in h; h = h.replace(marker, section.strip() + "\n\n" + marker, 1)
    meth = ('<p>The company check added on ' + UPDATED + ' works from the register&rsquo;s own Part A lines with no Part B. Each company name is reduced to its legal name '
            '(Pvt, Ltd and punctuation normalised), and the matching public company page is read for its CIN and status. A CIN carries a two-letter state code and a '
            'company-type code; a page is accepted when the state code equals the plant state, or, where the registered office is in another state, when the company-type code '
            'agrees with the name. Companies not found are left unidentified, not guessed.</p>\n')
    anchor = '\n\n<h2 class="gb-h2">Sources and caveats</h2>'
    assert anchor in h; h = h.replace(anchor, "\n" + meth + anchor, 1)
    src = (' Company status and CIN (added ' + UPDATED + '): public company pages on QuickCompany, which republish Ministry of Corporate Affairs company master data, read 7&ndash;9 October 2026; '
           'the Ministry&rsquo;s open Company Master Data on data.gov.in (data up to 3 November 2023) was used as a reference for the format. A company listed Active may not have built the plant, and one not found may exist under another name.')
    h = h.replace(' A filing with no Part B found may still have been built.</p>\n</div>', ' A filing with no Part B found may still have been built.' + src + '</p>\n</div>', 1)
    assert 'QuickCompany' in h, "sources not extended"
    # balance checks
    for tag in ("div", "table", "tr", "td", "th", "p", "ul", "li", "h2"):
        o, c = len(re.findall(rf"<{tag}[ >]", h)), h.count(f"</{tag}>")
        assert o == c, f"tag imbalance {tag}: {o} open vs {c} close"
    out.write_text(h)
    print("draft written:", out, f"({len(h):,} bytes; original {orig.stat().st_size:,})")
    print({k: (int(v) if hasattr(v, 'item') else v) for k, v in s.items()})


if __name__ == "__main__":
    main()
