#!/usr/bin/env python3
"""
Second, independent status source keyed by CIN: Tofler company page (the CIN alone resolves the page, any slug works).
Returns status, the last AGM date, paid-up capital, and a one-line business description. A recent AGM date is evidence the
company is still filing, which is stronger than the status word alone. (InstaFinancials was tried first: it serves a generic
page after about ten rapid requests, so it is unusable in bulk.)

  python3 cin_status_check.py sample --n 120        # compare against the first source on a random sample
  python3 cin_status_check.py run --candidates      # all CINs behind Part A filings with no Part B (2021+)
  python3 cin_status_check.py run --limit 5000      # any identified CINs, Part B first
Results cached in cin_status.csv (resumable). 4 requests/second shared across 4 threads.
"""
import argparse, csv, html, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import duckdb, pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "cin_status.csv"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/128 Safari/537.36"
FIELDS = ["cin", "status2", "balance_sheet", "agm", "compliant", "cirp", "nic", "industry", "ok"]   # compliant=paid-up capital, cirp=page updated date
gate = {"lock": threading.Lock(), "next": 0.0, "gap": 0.25}


def fetch(cin):
    with gate["lock"]:
        w = gate["next"] - time.time()
        if w > 0: time.sleep(w)
        gate["next"] = time.time() + gate["gap"]
    h = subprocess.run(["curl", "-sL", "-m", "30", "-A", UA, f"https://www.tofler.in/x/company/{cin}"], capture_output=True, text=True).stdout
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    x = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
    g = lambda p: (re.search(p, x).group(1).strip() if re.search(p, x) else "")
    status = g(r"current status of .{3,120}? is - (.{3,40}?)\. ")
    agm = g(r"was held on (\d{1,2} [A-Za-z]+,? \d{4})")
    ok = (cin in x) and bool(status)
    return dict(cin=cin, status2=status, balance_sheet="", agm=agm, compliant=g(r"Paid up Capital (₹ [\d.,]+ ?[A-Za-z]*)"),
                cirp=g(r"Updated: (\d{1,2} [A-Za-z]+ \d{4})"), nic="", industry=g(r"› ([^›]{3,60}) › [A-Z0-9][^›]{3,80} (?:Active|Strike Off|Amalgamated|Under)"), ok=str(ok))


def cached():
    # only successful reads count as cached, so reruns retry pages that failed or were throttled
    return {r["cin"]: r for r in csv.DictReader(open(OUT)) if r["ok"] == "True"} if OUT.exists() else {}


def run(cins):
    done = cached(); todo = [c for c in cins if c not in done]
    print(f"{len(cins)} CINs; {len(done)} cached; fetching {len(todo)}", flush=True)
    new = open(OUT, "a", newline=""); w = csv.DictWriter(new, fieldnames=FIELDS)
    if OUT.stat().st_size == 0: w.writeheader()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(fetch, c) for c in todo]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result(); w.writerow(r); done[r["cin"]] = r
            if i % 100 == 0: new.flush(); print(f"  {i}/{len(todo)}", flush=True)
    new.close(); return done


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["sample", "run"]); ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--limit", type=int, default=2000); ap.add_argument("--candidates", action="store_true")
    a = ap.parse_args(); OUT.touch(exist_ok=True)
    con = duckdb.connect(str(BASE / "iem_verify.duckdb"), read_only=True)
    if a.mode == "sample":
        df = con.execute("select distinct cin, status, src from co where cin is not null").df().sample(a.n, random_state=3)
        d = run(list(df.cin)); r = pd.DataFrame([d[c] for c in df.cin]); m = df.merge(r, on="cin")
        ok = m[m.ok == "True"]; print(f"\npages parsed: {len(ok)}/{len(m)}")
        agree = ((ok.status.str.lower() == ok.status2.str.lower())).mean()
        print(f"status word agrees with first source: {agree:.0%}")
        print(ok.assign(pair=ok.status + " | " + ok.status2).pair.value_counts().head(8).to_string())
        act = ok[ok.status2.str.lower() == "active"]; ag = pd.to_datetime(act.agm.str.replace(",", ""), format="%d %B %Y", errors="coerce")
        print(f"\nAmong Active ({len(act)}): last AGM in 2025 or later: {(ag >= '2025-01-01').mean():.0%} | 2024 or later: {(ag >= '2024-01-01').mean():.0%} | no AGM date: {ag.isna().mean():.0%}")
    else:
        if a.candidates:
            c = pd.read_parquet(BASE / "iem_partA_filings_linked.parquet"); cins = list(c[c.support_candidate & c.cin.notna()].cin.unique())
        else:
            cins = list(con.execute("select cin from co where cin is not null group by cin order by max(in_part_b::int) desc, count(*) desc").df().cin)[:a.limit]
        run(cins)


if __name__ == "__main__":
    main()
