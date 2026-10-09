#!/usr/bin/env python3
"""
Join the complete India GLEIF register (gleif_india.jsonl / .parquet) onto the company table, offline.

For every company in iem_verify.duckdb (Part A and Part B names):
  confirmed   its CIN (from the web lookups) is the CIN of a GLEIF record with the same name        -> identity independently confirmed
  cin_changed same state+year+registration number, different class/listing letter (L<->U, PTC<->PLC)  -> same company, CIN updated
  conflict    GLEIF holds the same legal name under a DIFFERENT company                               -> possible homonym error in the web match
  new         no CIN yet; exactly one GLEIF company has this name (and state agrees, or only one in India) -> new identification
  ambiguous   no CIN yet; several GLEIF companies share the name
  none        not in GLEIF (most small companies: they hold no LEI)
Also carries each LEI holder's entity status, LEI registration status and next renewal date, and uses former names so renamed companies match.
A match is rejected if the CIN's incorporation year is after the company's last Part A / Part B year (cannot be the filer).

  python3 gleif_join.py            # writes gleif_join.csv and a summary; add --apply to store verdicts in a table `gleif_verdict`
"""
import argparse, re, sys
from pathlib import Path
import duckdb, pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from iem_verify import key, CIN_STATE
from gleif_check import REGION

BASE = Path(__file__).resolve().parent


def load():
    pq = BASE / "gleif_india.parquet"
    g = pd.read_parquet(pq) if pq.exists() else pd.read_json(BASE / "gleif_india.jsonl", lines=True, dtype=False).drop_duplicates("lei")
    g = g[(g.ra == "RA000394") & g.cin.notna() & (g.cin.str.len() == 21)].copy()
    return g


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    g = load(); print(f"GLEIF India records with an MCA CIN: {len(g)}")
    # indices: name key -> records (legal name and every former name)
    by_key, by_cin = {}, {}
    for r in g.itertuples(index=False):
        by_cin[r.cin] = r
        for nm in {r.name, *(r.other.split("|") if isinstance(r.other, str) and r.other else [])}:
            k = key(nm)
            if len(k) >= 4: by_key.setdefault(k, []).append(r)
    con = duckdb.connect(str(BASE / "iem_verify.duckdb"), read_only=True)
    co = con.execute("select id, key, name, state, cin, status, src, first_iem, last_iem, last_part_b, in_part_b from co where usable").df()
    rows = []
    for r in co.itertuples(index=False):
        recs = by_key.get(r.key, [])
        st = r.state if isinstance(r.state, str) else None
        last_year = max([int(x[:4]) for x in (r.last_iem, r.last_part_b) if isinstance(x, str) and x[:4].isdigit()] or [3000])
        recs = [x for x in recs if int(x.cin[8:12]) <= last_year]                     # cannot be incorporated after it filed
        verdict, g_cin, g_status, lei, nxt = "none", None, None, None, None
        same_state = [x for x in recs if st and (REGION.get(x.region) == st or CIN_STATE.get(x.cin[6:8]) == st)]
        if isinstance(r.cin, str):
            if r.cin in by_cin and any(x.cin == r.cin for x in recs) or (r.cin in by_cin and key(by_cin[r.cin].name) == r.key):
                x = by_cin[r.cin]; verdict = "confirmed"
            else:
                twin = [x for x in recs if x.cin[6:12] == r.cin[6:12] and x.cin[15:21] == r.cin[15:21]]
                if twin: x = twin[0]; verdict = "cin_changed"
                elif recs:
                    pool = same_state or recs; x = pool[0]; verdict = "conflict"
                else: x = None
        else:
            pool = same_state if same_state else (recs if len({y.cin for y in recs}) == 1 and not st else [])
            if len({y.cin for y in pool}) == 1: x = pool[0]; verdict = "new"
            elif len({y.cin for y in recs}) > 1: x = None; verdict = "ambiguous"
            else: x = None
        if x is not None: g_cin, g_status, lei, nxt = x.cin, x.e_status, x.r_status, x.r_next
        rows.append((r.id, r.name, st, r.cin, r.status, verdict, g_cin, g_status, lei, nxt))
    out = pd.DataFrame(rows, columns=["id", "name", "state", "cin", "status", "verdict", "gleif_cin", "gleif_entity_status", "lei_status", "lei_next_renewal"])
    out.to_csv(BASE / "gleif_join.csv", index=False)
    print(out.verdict.value_counts().to_string())
    ident = out[out.cin.notna()]
    print(f"\nof {len(ident)} companies identified by the web lookups: confirmed {int((ident.verdict=='confirmed').sum())}, CIN changed {int((ident.verdict=='cin_changed').sum())}, "
          f"conflict {int((ident.verdict=='conflict').sum())}, not in GLEIF {int((ident.verdict=='none').sum())}")
    cov = ident[ident.verdict.isin(['confirmed', 'cin_changed', 'conflict'])]
    if len(cov): print(f"among those GLEIF covers: {(cov.verdict.isin(['confirmed','cin_changed'])).mean():.0%} confirmed / {(cov.verdict=='conflict').mean():.0%} conflict")
    if a.apply:
        c = duckdb.connect(str(BASE / "iem_verify.duckdb")); c.execute("create or replace table gleif_verdict as select * from out"); print("stored table gleif_verdict")


if __name__ == "__main__":
    main()
