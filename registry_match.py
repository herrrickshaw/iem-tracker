#!/usr/bin/env python3
"""
Match every extracted Part A company (new coordinate parse + text-layout/OCR parse) against the MCA company-master
open-data API (data.gov.in, resource 4dbe5667-7b6b-41d7-82af-211562424d9a) by exact registered name.

  export OGD_KEY=<data.gov.in key>                          # env only, never written to disk
  python3 registry_match.py --since 2015 --limit 2000       # newest filings first, resumable (cache in registry.duckdb)
  python3 registry_match.py --export                        # -> IEM_registry_matches.csv

Name handling: IEM spellings ("PVT.LTD", "(P) LTD", "LTD.") are rewritten to MCA style ("PRIVATE LIMITED", "LIMITED").
A hit is accepted only if the returned name equals the query AND the CIN's state code equals the IEM state (when known).
Several different companies with the same exact name and no state to separate them -> AMBIGUOUS (never guessed).

Caveats: (1) the dataset is labelled "data up to 3 Nov 2023", so STATUS is stale; treat it as "registered, status as of
Nov 2023" and re-verify recent status separately. (2) Exact-name search misses IEM typos and renamed companies; those
stay UNMATCHED and go to the fuzzy/agent tail in iem_verify.py. (3) api.data.gov.in refused connections from the Mac on
2026-10-09, so run this from a network that can reach it. Use --base-url to test against a local mock.
"""
import argparse, os, re, sys, time
from pathlib import Path

import duckdb, pandas as pd, requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from iem_verify import CIN_STATE, find_state, key, usable  # noqa: E402

BASE = Path(__file__).resolve().parent
DB = BASE / "registry.duckdb"
RES = "4dbe5667-7b6b-41d7-82af-211562424d9a"


def mca_variants(name):
    n = re.sub(r"\s+", " ", str(name).upper()).strip(" ,.;:-")
    n = re.sub(r"\(\s*P\s*\)\s*LTD\.?", "PRIVATE LIMITED", n)
    n = re.sub(r"\bPVT\.?\s*(LTD\.?|LIMITED)", "PRIVATE LIMITED", n)
    n = re.sub(r"\bPRIVATED\b", "PRIVATE", n)
    n = re.sub(r"\bLTD\.?(?=\W|$)", "LIMITED", n)
    n = re.sub(r"[.,]", " ", n); n = re.sub(r"\s+", " ", n).strip()
    out = [n]
    if "&" in n: out.append(n.replace("&", "AND"))
    if n.startswith("THE "): out.append(n[4:])
    return list(dict.fromkeys(out))


def load_names(since):
    frames = []
    a = BASE / "partA_coords.parquet"
    b = BASE / "partA_text_layout.parquet"
    if a.exists():
        d = pd.read_parquet(a)[["month", "company_name", "name_has_suffix", "district_state", "source_file"]]; frames.append(d)
    if b.exists():
        d = pd.read_parquet(b)[["month", "company_name", "name_has_suffix", "district_state", "source_file"]]
        frames.append(d[~d.source_file.str.startswith("2023")])      # 2023 cumulative files use another layout
    d = pd.concat(frames, ignore_index=True)
    d["state"] = d.district_state.map(find_state)
    d["k"] = d.company_name.map(key)
    d = d[d.month.str[:4].astype(int) >= since]
    g = (d.sort_values("month").groupby(["k", "state"], dropna=False)
          .agg(name=("company_name", "last"), suffix=("name_has_suffix", "max"), last_month=("month", "max"),
               n=("month", "size")).reset_index())
    g = g[[usable(n, s) for n, s in zip(g.name, g.suffix)]]
    return g.sort_values(["last_month", "n"], ascending=False).reset_index(drop=True)


def pick(rec, *needles):
    for k, v in rec.items():
        if any(n in k.lower() for n in needles): return v
    return None


def query(session, base_url, keyv, name, limit=5):
    for attempt in range(4):
        r = session.get(f"{base_url}/resource/{RES}", params={"api-key": keyv, "format": "json", "limit": limit,
                                                              "filters[company_name]": name}, timeout=45)
        if r.status_code == 429: time.sleep(5 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json().get("records", [])
    raise RuntimeError("rate-limited repeatedly")


def decide(records, variants, state):
    """-> (verdict, cin, status, klass, reg_date, matched_name)"""
    want = {re.sub(r"\s+", " ", v) for v in variants}
    hits = []
    for rec in records:
        nm = re.sub(r"[.,]", " ", str(pick(rec, "company_name", "name") or "").upper()); nm = re.sub(r"\s+", " ", nm).strip()
        cin = pick(rec, "identification", "cin")
        if nm in want and cin: hits.append((nm, str(cin), pick(rec, "status"), pick(rec, "class"), pick(rec, "registration")))
    if state:
        same = [h for h in hits if CIN_STATE.get(h[1][6:8]) == state]
        if same: hits = same
        elif hits: return ("STATE_MISMATCH", hits[0][1], hits[0][2], hits[0][3], hits[0][4], hits[0][0])
    if not hits: return ("UNMATCHED", None, None, None, None, None)
    if len({h[1] for h in hits}) > 1: return ("AMBIGUOUS", hits[0][1], hits[0][2], hits[0][3], hits[0][4], hits[0][0])
    h = hits[0]; return ("MATCHED", h[1], h[2], h[3], h[4], h[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=2015); ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--base-url", default="https://api.data.gov.in"); ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--export", action="store_true")
    a = ap.parse_args()
    con = duckdb.connect(str(DB))
    con.execute("""create table if not exists reg(k varchar, state varchar, name varchar, verdict varchar, cin varchar,
                   status varchar, klass varchar, reg_date varchar, matched_name varchar, last_month varchar,
                   primary key(k, state))""")
    if a.export:
        out = BASE / "IEM_registry_matches.csv"; con.execute(f"copy reg to '{out}' (header, delimiter ',')")
        print(con.execute("select verdict,count(*) n from reg group by 1 order by 2 desc").df().to_string(index=False)); print("wrote", out); return
    keyv = os.environ.get("OGD_KEY") or sys.exit("set OGD_KEY (env var) first")
    names = load_names(a.since)
    done = {(r[0], r[1]) for r in con.execute("select k, state from reg").fetchall()}
    todo = [r for r in names.itertuples(index=False) if (r.k, "" if pd.isna(r.state) else r.state) not in done][:a.limit]
    print(f"{len(names)} usable distinct companies since {a.since}; {len(done)} already done; querying {len(todo)}")
    s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"; n = 0
    for r in todo:
        st = None if pd.isna(r.state) else r.state
        variants = mca_variants(r.name)
        try:
            recs = []
            for v in variants:
                recs = query(s, a.base_url, keyv, v)
                if recs: break
        except requests.exceptions.ConnectionError as e:
            sys.exit(f"cannot reach {a.base_url} ({type(e).__name__}). Run from a network that reaches api.data.gov.in, or use the bulk master via ogd_fetch_master.py + iem_verify.py match.")
        v = decide(recs, variants, st)
        con.execute("insert or replace into reg values (?,?,?,?,?,?,?,?,?,?)", [r.k, st or "", r.name, *v, r.last_month]); n += 1
        if n % 50 == 0: print(n, "done"); time.sleep(0)
        time.sleep(a.sleep)
    print(con.execute("select verdict,count(*) n from reg group by 1 order by 2 desc").df().to_string(index=False))


if __name__ == "__main__":
    main()
