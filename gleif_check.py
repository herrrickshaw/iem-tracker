#!/usr/bin/env python3
"""
GLEIF (Global LEI Foundation) as an independent source of CIN + entity status for Indian companies.

GLEIF's public API is free, needs no key and answers from this network. Each Indian LEI record carries the company's
CIN (registeredAs, registration authority RA000394 = MCA India), its legal name, legal-address region and entity status
(ACTIVE / INACTIVE). Coverage is limited to companies that hold an LEI (large borrowers, listed and export-facing firms),
so it complements, not replaces, the registry lookups.

  python3 gleif_check.py validate --n 300     # test the existing web-lookup CINs against GLEIF (random sample, tier 1 and tier 2)
  python3 gleif_check.py identify --limit 500 # look up still-unidentified Part B companies in GLEIF (writes gleif_identified.csv)

Rate: one request per second (GLEIF documents 60/minute). Results cached in gleif_cache.csv; reruns skip cached names.
"""
import argparse, csv, re, sys, time
from pathlib import Path

import duckdb, pandas as pd, requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from iem_verify import CIN_STATE, key
from registry_match import mca_variants

BASE = Path(__file__).resolve().parent
API = "https://api.gleif.org/api/v1/lei-records"
CACHE = BASE / "gleif_cache.csv"
REGION = {"IN-" + c: s for c, s in {"AP": "ANDHRA PRADESH", "AR": "ARUNACHAL PRADESH", "AS": "ASSAM", "BR": "BIHAR", "CH": "CHANDIGARH",
          "CT": "CHHATTISGARH", "DL": "DELHI", "GA": "GOA", "GJ": "GUJARAT", "HP": "HIMACHAL PRADESH", "HR": "HARYANA", "JH": "JHARKHAND",
          "JK": "JAMMU", "KA": "KARNATAKA", "KL": "KERALA", "MH": "MAHARASHTRA", "MP": "MADHYA PRADESH", "OR": "ODISHA", "OD": "ODISHA",
          "PB": "PUNJAB", "PY": "PUDUCHERRY", "RJ": "RAJASTHAN", "TN": "TAMIL NADU", "TG": "TELANGANA", "TS": "TELANGANA", "UP": "UTTAR PRADESH",
          "UT": "UTTARAKHAND", "UK": "UTTARAKHAND", "WB": "WEST BENGAL"}.items()}


def load_cache():
    if not CACHE.exists(): return {}
    return {r["q"]: r for r in csv.DictReader(open(CACHE))}


def query(sess, name):
    for attempt in range(4):
        r = sess.get(API, params={"filter[entity.legalName]": name, "filter[entity.legalAddress.country]": "IN", "page[size]": 5}, timeout=40)
        if r.status_code == 429: time.sleep(8 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json().get("data", [])
    raise RuntimeError("GLEIF rate limit")


def lookup(sess, cache, name):
    """Try MCA-style spellings; return the cached/new best record: dict(q, found, cin, status, lei_status, legal_name, region)."""
    out = None
    for v in mca_variants(name):
        if v in cache: rec = cache[v]
        else:
            data = query(sess, v); time.sleep(1.0)
            rec = {"q": v, "found": "0", "cin": "", "status": "", "lei_status": "", "legal_name": "", "region": "", "n": str(len(data))}
            for d in data:
                a = d["attributes"]; e = a["entity"]
                if e.get("registeredAt", {}).get("id") == "RA000394" and e.get("registeredAs"):
                    rec.update(found="1", cin=e["registeredAs"], status=e.get("status", ""), lei_status=a["registration"].get("status", ""),
                               legal_name=e["legalName"]["name"], region=e["legalAddress"].get("region", ""))
                    if a["registration"].get("status") == "ISSUED": break      # prefer a live LEI
            cache[v] = rec
            with open(CACHE, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rec.keys()))
                if f.tell() == 0: w.writeheader()
                w.writerow(rec)
        if rec["found"] == "1": return rec
        out = rec
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["validate", "identify"])
    ap.add_argument("--n", type=int, default=300); ap.add_argument("--limit", type=int, default=500)
    a = ap.parse_args()
    cache = load_cache(); sess = requests.Session(); sess.headers.update({"Accept": "application/vnd.api+json", "User-Agent": "iem-research/1.0"})
    con = duckdb.connect(str(BASE / "iem_verify.duckdb"), read_only=True)
    if a.mode == "validate":
        df = con.execute("select name,state,cin,status,src from co where cin is not null and usable").df()
        half = a.n // 2
        s = pd.concat([df[df.src == "web-slug+state"].sample(half, random_state=7), df[df.src == "web-slug-name-only"].sample(half, random_state=7)])
        rows = []
        for i, r in enumerate(s.itertuples(), 1):
            g = lookup(sess, cache, r.name)
            rows.append(dict(name=r.name, state=r.state, tier=r.src, cin=r.cin, mca_status=r.status, g_found=g["found"] == "1", g_cin=g["cin"],
                             g_status=g["status"], g_region=g["region"]))
            if i % 50 == 0: print(f"  {i}/{len(s)}", flush=True)
        v = pd.DataFrame(rows); v.to_csv(BASE / "gleif_validation.csv", index=False)
        f = v[v.g_found]
        print(f"\nsample {len(v)}: GLEIF holds {len(f)} ({len(f)/len(v):.0%}) of them")
        for t, g in f.groupby("tier"):
            same = (g.cin == g.g_cin).mean()
            print(f"  {t:20s} found {len(g):3d} | same CIN {same:.0%} | status agrees (ACTIVE vs Active) "
                  f"{((g.g_status == 'ACTIVE') == (g.mca_status == 'Active')).mean():.0%}")
        bad = f[f.cin != f.g_cin]
        print("\nCIN disagreements:\n", bad[["name", "state", "tier", "cin", "g_cin"]].head(12).to_string(index=False))
    else:
        df = con.execute("select id,name,state,in_part_b from co where cin is null and usable order by in_part_b desc, n_part_b desc, id").df().head(a.limit)
        out = []
        for i, r in enumerate(df.itertuples(), 1):
            g = lookup(sess, cache, r.name)
            if g["found"] == "1":
                st = None if pd.isna(r.state) else r.state
                same_state = st is not None and REGION.get(g["region"]) == st or CIN_STATE.get(g["cin"][6:8]) == st
                out.append(dict(id=r.id, name=r.name, state=st, in_part_b=r.in_part_b, cin=g["cin"], gleif_status=g["status"],
                                lei_status=g["lei_status"], gleif_name=g["legal_name"], state_agrees=bool(same_state)))
            if i % 50 == 0: print(f"  {i}/{len(df)}  found {len(out)}", flush=True)
        o = pd.DataFrame(out); o.to_csv(BASE / "gleif_identified.csv", index=False)
        print(f"\n{len(o)} of {len(df)} previously unidentified companies found in GLEIF; state agrees for {int(o.state_agrees.sum()) if len(o) else 0}")


if __name__ == "__main__":
    main()
