#!/usr/bin/env python3
"""
Part A (intent) vs Part B (implemented), one row per Part A FILING, then rolled up per company.

Link strength, best first:
  number+date  Part B 'Memorandum No/Date' equals the Part A acknowledgement No/Date (checked: names agree on ~91% of such pairs)
  name         the company (same distinctive name, state equal or unknown) appears in a Part B list filed in/after the Part A month
  none         no Part B found

A Part A filing with 'none' is a *possible* pending project. 'none' means "no evidence found", not proof of non-implementation:
about 40% of Part B records cannot be tied to a Part A record by number (formats/coverage gaps), so some 'none' filings did implement.

Outputs  IEM_A_vs_B_support_candidates.xlsx  (Candidates, Part A filings, Companies matched, Summary by year, Notes)
         iem_companies_compiled.parquet      (company level, used by `iem_verify.py web --candidates`)
"""
import re
from pathlib import Path

import duckdb, pandas as pd
from dateutil import parser as dparse

from iem_verify import key, find_state, usable

BASE = Path(__file__).resolve().parent


def norm_date(x):
    if x is None or (isinstance(x, float) and pd.isna(x)): return None
    s = str(x).strip()
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m: return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    try: return dparse.parse(s, dayfirst=True).strftime("%d/%m/%Y")
    except Exception: return None


def load_a():
    parts = []
    c = pd.read_parquet(BASE / "partA_coords.parquet")
    c = c.rename(columns={"iem_no": "a_no"}); c["a_date"] = c.filing_date.map(norm_date); c["src"] = "coords"; parts.append(c)
    t = pd.read_parquet(BASE / "partA_text_layout.parquet"); t = t[~t.source_file.str.startswith("2023")]
    t = t.rename(columns={"ack_no": "a_no"}); t["a_date"] = t.filing_date.map(norm_date); t["src"] = "text"; parts.append(t)
    cols = ["company_name", "name_has_suffix", "district_state", "item", "month", "a_no", "a_date", "src", "source_file", "serial"]
    a = pd.concat([p[cols] for p in parts], ignore_index=True)
    a["state"] = a.district_state.map(find_state).fillna("")
    a["k"] = a.company_name.map(key)
    a = a[a.k.str.len() >= 4].sort_values("source_file").drop_duplicates(["month", "serial", "company_name"])
    return a.reset_index(drop=True)


def load_b():
    b = pd.read_parquet(BASE / "partB_all.parquet")
    b["name"] = b.company_name.str.upper().str.replace(r"\s+", " ", regex=True).str.strip(" ,.;:-")
    b["k"] = b.name.map(key); b = b[b.k.str.len() >= 4].copy()
    b["state"] = b.state.fillna("")
    b["m_date"] = b.mem_date.map(norm_date)
    return b


def main():
    a, b = load_a(), load_b()
    print(f"Part A filings: {len(a)}  | Part B records: {len(b)}")
    # --- strong link: number + date
    bk = {}
    for r in b.dropna(subset=["mem_no", "m_date"]).itertuples():
        bk.setdefault((int(r.mem_no), r.m_date), r)
    a["link"] = "none"; a["b_month"] = None; a["b_commenced"] = None
    for i, r in enumerate(a.itertuples()):
        if pd.notna(r.a_no) and r.a_date and (int(r.a_no), r.a_date) in bk:
            hit = bk[(int(r.a_no), r.a_date)]
            a.at[i, "link"] = "number+date"; a.at[i, "b_month"] = hit.month; a.at[i, "b_commenced"] = hit.commenced
    # --- weaker link: same company appears in a later Part B (state equal or unknown on either side)
    byk = {}
    for r in b.itertuples(): byk.setdefault(r.k, []).append((r.state, r.month))
    for i, r in enumerate(a.itertuples()):
        if a.at[i, "link"] != "none": continue
        later = [m for st, m in byk.get(r.k, []) if m >= r.month and (not st or not r.state or st == r.state)]
        if later: a.at[i, "link"] = "name"; a.at[i, "b_month"] = min(later)
    a["year"] = a.month.str[:4]
    a["has_b"] = a.link != "none"

    # --- company level: identification results from the web/registry lookups
    co = pd.DataFrame()
    try:
        c = duckdb.connect(str(BASE / "iem_verify.duckdb"), read_only=True)
        co = c.execute("""select key k, coalesce(state,'') state, name, usable, in_part_b, n_part_a, n_part_b, last_iem, last_part_b,
                                 cin, status as mca_status, src as id_source from co""").df()
        gv = c.execute("""select co.key k, coalesce(co.state,'') state, g.verdict as gverdict, g.gleif_cin, g.gleif_entity_status,
                                 g.lei_status, g.lei_next_renewal from gleif_verdict g join co on co.id = g.id""").df().drop_duplicates(["k", "state"])
        c.close()
        co = co.merge(gv, on=["k", "state"], how="left")
        ch = co.gverdict.eq("cin_changed"); co.loc[ch, "cin"] = co.loc[ch, "gleif_cin"]                  # same company, CIN updated
        nw = co.gverdict.eq("new") & co.cin.isna()                                                     # found only in the LEI register
        co.loc[nw, "cin"] = co.loc[nw, "gleif_cin"]
        co.loc[nw, "mca_status"] = co.loc[nw, "gleif_entity_status"].map(lambda x: "Active" if x == "ACTIVE" else "Dissolved or inactive (LEI)")
        co.loc[nw, "id_source"] = "lei-register"
        co["identity"] = co.gverdict.map({"confirmed": "confirmed by LEI register", "cin_changed": "confirmed (CIN updated)",
                                          "conflict": "CONFLICT: LEI register holds this name under another company",
                                          "new": "identified via LEI register"}).fillna(co.id_source.map(lambda x: "name match, not checkable (no LEI)" if isinstance(x, str) else ""))
    except Exception as e:
        print("note: iem_verify.duckdb or gleif_verdict unavailable:", str(e)[:70])
    reg = BASE / "IEM_registry_matches.csv"
    if reg.exists() and len(co):
        r = pd.read_csv(reg); r["state"] = r.state.fillna(""); r = r[r.verdict == "MATCHED"][["k", "state", "cin", "status"]]
        co = co.merge(r.rename(columns={"cin": "c3", "status": "s3"}), on=["k", "state"], how="left")
        m = co.cin.isna() & co.c3.notna(); co.loc[m, "cin"] = co.loc[m, "c3"]; co.loc[m, "mca_status"] = co.loc[m, "s3"]; co.loc[m, "id_source"] = "registry"
        co = co.drop(columns=["c3", "s3"])
    if len(co):
        keep = [c_ for c_ in ["k", "state", "cin", "mca_status", "id_source", "usable", "identity", "lei_status", "lei_next_renewal"] if c_ in co.columns]
        a = a.merge(co[keep].drop_duplicates(["k", "state"]), on=["k", "state"], how="left")
    else:
        a = a.assign(cin=None, mca_status=None, id_source=None, usable=True)
    # current MCA record (Apify actor, by CIN): live status, last AGM and balance sheet, compliance flags
    ap = BASE / "apify_mca.csv"
    if ap.exists():
        m = pd.read_csv(ap, dtype=str).drop_duplicates("cin")
        m["mca_live_status"] = m.status; m["last_agm"] = pd.to_datetime(m.dateOfLastAGM, format="%m/%d/%Y", errors="coerce")
        m["last_balance_sheet"] = pd.to_datetime(m.balanceSheetDate, format="%m/%d/%Y", errors="coerce")
        m["mca_defaulter"] = (m.annualReturnDefaulter.astype(str).eq("True") | m.balanceSheetDefaulter.astype(str).eq("True") | m.underCIRP.astype(str).eq("True"))
        a = a.merge(m[["cin", "mca_live_status", "last_agm", "last_balance_sheet", "mca_defaulter"]], on="cin", how="left")
        a["mca_defaulter"] = a.mca_defaulter.fillna(False).astype(bool)
        a["operating_evidence"] = "no live MCA record yet"
        has = a.mca_live_status.notna()
        a.loc[has, "operating_evidence"] = "MCA record: Active, AGM 2025+ and balance sheet FY2024-25+"
        weak = has & ~((a.mca_live_status == "Active") & (a.last_agm >= "2025-01-01") & (a.last_balance_sheet >= "2025-03-31") & ~a.mca_defaulter)
        a.loc[weak, "operating_evidence"] = "MCA record: Active but filings lag, or flagged"
        a.loc[has & (a.mca_live_status != "Active"), "operating_evidence"] = "MCA record: not Active"
    a["usable"] = a.usable.fillna(True).astype(bool)
    inactive = a.mca_status.fillna("").str.lower().str.contains("strike|amalgam|liquidat|dissol|closed|cirp")
    a["support_candidate"] = (~a.has_b) & a.usable & ~inactive & (a.month >= "2021-01")
    a["identity"] = a["identity"] if "identity" in a else ""
    a["verified_active"] = a.mca_status.fillna("").str.lower().eq("active") & ~a.identity.fillna("").str.startswith("CONFLICT")

    # --- per-company roll-up
    comp = (a.groupby(["k", "state"]).agg(name=("company_name", "last"), filings=("month", "size"), first_month=("month", "min"),
                                          last_month=("month", "max"), filings_with_b=("has_b", "sum"),
                                          open_filings=("support_candidate", "sum"), cin=("cin", "first"),
                                          mca_status=("mca_status", "first")).reset_index())
    cb = b.groupby(["k", "state"]).agg(name=("name", "last"), n_part_b=("month", "size"), last_part_b=("month", "max")).reset_index()
    only_b = cb.merge(comp[["k", "state"]], on=["k", "state"], how="left", indicator=True)
    only_b = only_b[only_b._merge == "left_only"].drop(columns="_merge")
    only_b["filings"] = 0
    if len(co):
        only_b = only_b.merge(co[["k", "state", "cin", "mca_status"]].drop_duplicates(["k", "state"]), on=["k", "state"], how="left")
    comp = comp.merge(cb[["k", "state", "n_part_b", "last_part_b"]], on=["k", "state"], how="left")
    allc = pd.concat([comp, only_b], ignore_index=True)
    allc["in_part_a"] = allc.filings.fillna(0) > 0
    allc["in_part_b"] = allc.n_part_b.fillna(0) > 0

    # --- outputs
    cand = a[a.support_candidate].copy()
    cand["filing_age_yrs"] = (2026.75 - (cand.month.str[:4].astype(float) + (cand.month.str[5:7].astype(float) - 1) / 12)).round(1)
    cols = ["company_name", "state", "month", "a_no", "a_date", "item", "link", "cin", "mca_status", "verified_active", "identity",
            "lei_status", "lei_next_renewal", "operating_evidence", "last_agm", "last_balance_sheet", "filing_age_yrs"]
    cols = [c_ for c_ in cols if c_ in cand.columns]
    cand = cand.sort_values(["verified_active", "month"], ascending=[False, False])
    summ = a.groupby("year").agg(part_a_filings=("k", "size"), linked_number_date=("link", lambda s: (s == "number+date").sum()),
                                 linked_name_only=("link", lambda s: (s == "name").sum()), no_part_b_found=("has_b", lambda s: (~s).sum())).reset_index()
    summ["share_linked"] = ((summ.linked_number_date + summ.linked_name_only) / summ.part_a_filings).round(2)
    notes = pd.DataFrame({"Note": [
        "One row per Part A filing. 'link' = how a Part B was tied to it: number+date (strong), name (weaker), none.",
        "Candidates = filing from 2021 onward, usable name, no Part B found, not known to be struck off/amalgamated/in insolvency.",
        "'none' is absence of evidence: about 40% of Part B records cannot be tied to Part A by number, so some candidates did implement. Verify before use.",
        "Identity column: 'confirmed by LEI register' = the company's CIN matches the global LEI register; 'CONFLICT' = that register holds the same name under a different company (do not rely); 'name match, not checkable' = no LEI, so unverified; ~6% (name+state) and ~16% (name-only) of checkable matches were a different company. CIN/status only exists for companies the lookups reached. 'verified_active' is as reported by the lookup site on the run date.",
        "The data.gov.in registry data stops at 3 Nov 2023 and its API was unreachable from this machine.",
        "Coverage: Part A 2024 only to August; 2020-04/06/12 and 2021-03..06 absent; 2023 cumulative files not parsed. Part B: 283 of 341 files yielded records."]})
    out = BASE / "IEM_A_vs_B_support_candidates.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        cand[cols].to_excel(w, sheet_name="Candidates", index=False)
        a[["company_name", "state", "month", "a_no", "a_date", "item", "link", "b_month", "b_commenced", "cin", "mca_status", "identity", "support_candidate"]
          ].sort_values("month", ascending=False).to_excel(w, sheet_name="Part A filings", index=False)
        allc[["name", "state", "in_part_a", "in_part_b", "filings", "n_part_b", "first_month", "last_month", "last_part_b", "filings_with_b",
              "open_filings", "cin", "mca_status"]].to_excel(w, sheet_name="Companies matched", index=False)
        summ.to_excel(w, sheet_name="Summary by year", index=False)
        notes.to_excel(w, sheet_name="Notes", index=False)
    a.to_parquet(BASE / "iem_partA_filings_linked.parquet", index=False)
    allc2 = allc.copy(); allc2["support_candidate"] = allc2.open_filings.fillna(0) > 0
    allc2.to_parquet(BASE / "iem_companies_compiled.parquet", index=False)
    print(summ.tail(12).to_string(index=False))
    print(f"candidates (filings): {len(cand)} | companies: {cand.k.nunique()} | with CIN: {int(cand.cin.notna().sum())} "
          f"| verified active: {int(cand.verified_active.sum())}\nwrote {out}")


if __name__ == "__main__":
    main()
