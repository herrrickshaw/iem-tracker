#!/usr/bin/env python3
"""
Pull the MCA company master from data.gov.in (resource 4dbe5667-..., "RoC-wise Company Master Data") into one CSV
that `iem_verify.py master --master <csv>` can load.

Run it on a network that can reach api.data.gov.in (it refused connections from the Mac session on 2026-10-09).
The key is read from the environment, never written to disk:
    export OGD_KEY=<your data.gov.in key>
    python3 ogd_fetch_master.py --states "maharashtra,gujarat"        # a few states first
    python3 ogd_fetch_master.py                                       # all states, resumable

Caveats: the dataset is labelled "data up to 3 Nov 2023", so COMPANY_STATUS is stale by ~3 years. Use it to get CINs,
then re-check status for the IEM-matched companies only (iem_verify.py `web`, or a fresh source).
"""
import argparse, csv, os, sys, time
import requests

RES = "4dbe5667-7b6b-41d7-82af-211562424d9a"
URL = f"https://api.data.gov.in/resource/{RES}"
STATES = ["andaman and nicobar islands", "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chandigarh", "chattisgarh",
          "dadra & nagar haveli", "daman and diu", "delhi", "goa", "gujarat", "haryana", "himachal pradesh", "jammu & kashmir",
          "jharkhand", "karnataka", "kerala", "ladakh", "lakshadweep", "madhya pradesh", "maharashtra", "manipur", "meghalaya",
          "mizoram", "nagaland", "orissa", "pondicherry", "punjab", "rajasthan", "sikkim", "tamil nadu", "telangana", "tripura",
          "uttar pradesh", "uttarakhand", "west bengal"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", help="comma list (default all)"); ap.add_argument("--out", default="mca_master_ogd.csv")
    ap.add_argument("--page", type=int, default=1000); ap.add_argument("--filter-field", default="company_state_code")
    a = ap.parse_args()
    key = os.environ.get("OGD_KEY") or sys.exit("set OGD_KEY first")
    states = [s.strip() for s in a.states.split(",")] if a.states else STATES
    done = set()
    if os.path.exists(a.out):
        with open(a.out, newline="") as f:
            done = {r["_state"] for r in csv.DictReader(f)}
    new = not os.path.exists(a.out)
    out = open(a.out, "a", newline=""); w = None
    for st in states:
        if st in done: print("skip", st); continue
        off, n = 0, 0
        while True:
            r = requests.get(URL, params={"api-key": key, "format": "json", "limit": a.page, "offset": off,
                                          f"filters[{a.filter_field}]": st}, timeout=60)
            if r.status_code != 200: print(st, "HTTP", r.status_code, r.text[:200]); break
            recs = r.json().get("records", [])
            if not recs: break
            for rec in recs: rec["_state"] = st
            if w is None:
                w = csv.DictWriter(out, fieldnames=list(recs[0].keys()), extrasaction="ignore")
                if new: w.writeheader()
            w.writerows(recs); n += len(recs); off += a.page; time.sleep(0.3)
        print(st, n, "rows"); out.flush()


if __name__ == "__main__":
    main()
