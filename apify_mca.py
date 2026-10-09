#!/usr/bin/env python3
"""
Current MCA company record by CIN through the Apify actor factden/mca-company-director-scraper (about $0.01 per company on the
free tier). Returns status, last AGM date, last balance-sheet date, under-CIRP, annual-return and balance-sheet defaulter flags.
This is the freshest source tried: Tofler/QuickCompany mirrors and the data.gov.in file all lag behind it.

  export APIFY_TOKEN=...                          # env only; never written to disk
  python3 apify_mca.py --candidates --budget 4.40 # newest support candidates first, stops at the USD budget
Results: apify_mca.csv (cache; reruns skip CINs already fetched).  Batches of 50 so spend is checked after each one.
"""
import argparse, csv, os, sys, time
from pathlib import Path
import pandas as pd, requests

BASE = Path(__file__).resolve().parent
OUT = BASE / "apify_mca.csv"
ACT = "factden~mca-company-director-scraper"
FIELDS = ["cin", "name", "status", "dateOfLastAGM", "balanceSheetDate", "underCIRP", "annualReturnDefaulter", "balanceSheetDefaulter",
          "inc22AFiled", "smallCompany", "listed", "industryCode", "rocName", "state", "paidUpCapital", "dateOfIncorporation"]


def api(path, token, **kw):
    return requests.request(kw.pop("method", "GET"), "https://api.apify.com/v2/" + path, headers={"Authorization": "Bearer " + token}, timeout=60, **kw)


def spend(token): return api("users/me/limits", token).json()["data"]["current"]["monthlyUsageUsd"]


def run_batch(token, cins):
    body = {"mode": "company", "cins": cins, "includeDirectors": False, "directorLeads": False, "maxCompanies": len(cins), "companyStatuses": [],
            "proxyConfiguration": {"useApifyProxy": True}}
    r = api(f"acts/{ACT}/runs", token, method="POST", json=body); r.raise_for_status()
    run = r.json()["data"]; rid, ds = run["id"], run["defaultDatasetId"]
    while True:
        st = api(f"actor-runs/{rid}", token).json()["data"]
        if st["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"): break
        time.sleep(5)
    items = api(f"datasets/{ds}/items", token, params={"format": "json", "clean": "1"}).json()
    return st["status"], st.get("usageTotalUsd"), items


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--candidates", action="store_true"); ap.add_argument("--budget", type=float, default=4.40)
    ap.add_argument("--batch", type=int, default=50); ap.add_argument("--limit", type=int, default=100000); ap.add_argument("--passes", type=int, default=4)
    a = ap.parse_args()
    token = os.environ.get("APIFY_TOKEN") or sys.exit("set APIFY_TOKEN")
    f = pd.read_parquet(BASE / "iem_partA_filings_linked.parquet")
    f = f[f.support_candidate & f.cin.notna()] if a.candidates else f[f.cin.notna()]
    order = f.groupby("cin").month.max().sort_values(ascending=False)
    done = set(pd.read_csv(OUT).cin) if OUT.exists() else set()
    todo = [c for c in order.index if c not in done][:a.limit]
    start = spend(token); print(f"{len(order)} CINs ranked; {len(done)} cached; account spend so far ${start:.3f}; budget ${a.budget}", flush=True)
    new = not OUT.exists(); fh = open(OUT, "a", newline=""); w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
    if new: w.writeheader()
    for p in range(1, a.passes + 1):           # the actor drops some CINs at random: later passes retry only what is still missing
        todo = [c for c in order.index if c not in done][:a.limit]
        if not todo: break
        got_pass = 0
        for i in range(0, len(todo), a.batch):
            used = spend(token) - start
            if used + 0.40 > a.budget:
                print(f"stopping: spent ${used:.3f} of ${a.budget}"); fh.close(); return
            status, cost, items = run_batch(token, todo[i:i + a.batch])
            for it in items: w.writerow(it); done.add(it["cin"])
            got_pass += len(items); fh.flush()
            print(f"pass {p} batch {i // a.batch + 1}: {status}, {len(items)}/{len(todo[i:i + a.batch])} records, total spent ${spend(token) - start:.3f}", flush=True)
        print(f"pass {p}: {got_pass} new records; {len([c for c in order.index if c not in done])} CINs still missing", flush=True)
        if got_pass < 0.05 * len(todo): break
    fh.close()


if __name__ == "__main__":
    main()
