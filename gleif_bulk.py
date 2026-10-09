#!/usr/bin/env python3
"""
Pull the complete GLEIF register for India (every LEI whose legal address is in India: ~400k records) through the public API
with cursor paging, keeping only the fields that matter, into gleif_india.jsonl (resumable) and gleif_india.parquet.

Why: one ~2,000-request pass replaces tens of thousands of per-name lookups, and gives complete coverage of every LEI holder:
CIN (registeredAs, authority RA000394 = MCA India), legal name AND former names, legal-address state/city, entity status,
LEI registration status and renewal dates (a company that keeps renewing its LEI is still operating and borrowing).

  python3 gleif_bulk.py            # run or resume (1 request/second); then:  python3 gleif_bulk.py --parquet
"""
import argparse, json, sys, time
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
RAW = BASE / "gleif_india.jsonl"; STATE = BASE / "gleif_india.cursor"
U = "https://api.gleif.org/api/v1/lei-records"


def slim(d):
    a = d["attributes"]; e = a["entity"]; r = a["registration"]; la = e.get("legalAddress", {}) or {}; hq = e.get("headquartersAddress", {}) or {}
    return {"lei": a["lei"], "name": (e.get("legalName") or {}).get("name"), "cin": e.get("registeredAs"), "ra": (e.get("registeredAt") or {}).get("id"),
            "e_status": e.get("status"), "r_status": r.get("status"), "r_init": r.get("initialRegistrationDate"), "r_update": r.get("lastUpdateDate"),
            "r_next": r.get("nextRenewalDate"), "region": la.get("region"), "city": la.get("city"), "pin": la.get("postalCode"),
            "hq_region": hq.get("region"), "created": e.get("creationDate"), "form": (e.get("legalForm") or {}).get("id"),
            "other": "|".join(n.get("name", "") for n in (e.get("otherNames") or [])), "cat": e.get("category")}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--parquet", action="store_true"); ap.add_argument("--rps", type=float, default=1.0)
    a = ap.parse_args()
    if a.parquet:
        import pandas as pd
        df = pd.read_json(RAW, lines=True, dtype=False).drop_duplicates("lei"); df.to_parquet(BASE / "gleif_india.parquet", index=False)
        print(len(df), "records;", int((df.ra == "RA000394").sum()), "with an MCA CIN;", df.e_status.value_counts().to_dict()); return
    s = requests.Session(); s.headers.update({"Accept": "application/vnd.api+json", "User-Agent": "iem-research/1.0"})
    url = STATE.read_text().strip() if STATE.exists() else None
    params = None if url else {"filter[entity.legalAddress.country]": "IN", "page[size]": 200, "page[cursor]": "*"}
    n = sum(1 for _ in open(RAW)) if RAW.exists() else 0; t0 = time.time(); pages = 0
    out = open(RAW, "a")
    while True:
        for attempt in range(6):
            r = s.get(url or U, params=None if url else params, timeout=90)
            if r.status_code == 429 or r.status_code >= 500: time.sleep(10 * (attempt + 1)); continue
            r.raise_for_status(); break
        j = r.json(); data = j.get("data", [])
        for d in data: out.write(json.dumps(slim(d)) + "\n")
        out.flush(); n += len(data); pages += 1
        nxt = (j.get("links") or {}).get("next")
        if nxt: STATE.write_text(nxt)
        if pages % 20 == 0: print(f"{n} records, {pages} pages, {time.time()-t0:.0f}s", flush=True)
        if not nxt or not data: break
        url = nxt; time.sleep(1.0 / a.rps)
    print("done:", n, "records"); STATE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
