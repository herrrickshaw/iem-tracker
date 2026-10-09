#!/usr/bin/env python3
"""
IEM Part A/B -> operational-company mapping, built to spend LLM tokens ONLY on the ambiguous tail.

Stages (each resumable, all state in iem_verify.duckdb):
  extract   Part A (+Part B-linked) rows -> one row per distinct company: clean name, state, key. NO LLM.
  partb     Mark companies whose name appears in any Part B list (production-commenced evidence). NO LLM.
  master    Load an MCA company-master CSV (data.gov.in OGD export) into mca_master. NO LLM.
  match     Exact key, then state-blocked fuzzy match against mca_master. NO LLM.
  web       For still-unmapped: curl a free site by name-slug, regex CIN + status. NO LLM. Cached, 1 req/s.
  batches   Write ambiguous/unmapped residue as compact JSONL batches for cheap LLM agents.
  ingest    Read agent verdict JSONL back into the table.
  report    Classify + export IEM_operational_status.xlsx.

Usage: python3 iem_verify.py <stage> [--limit N] [--master path.csv]
"""
import argparse, glob, json, re, subprocess, sys, time
from pathlib import Path

import duckdb, pandas as pd
from rapidfuzz import fuzz, process

BASE = Path(__file__).resolve().parent
DB = BASE / "iem_verify.duckdb"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/128 Safari/537.36"
CIN_RE = re.compile(r"\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
STATES = ["ANDHRA PRADESH", "ARUNACHAL PRADESH", "ASSAM", "BIHAR", "CHHATTISGARH", "GOA", "GUJARAT", "HARYANA",
          "HIMACHAL PRADESH", "JHARKHAND", "KARNATAKA", "KERALA", "MADHYA PRADESH", "MAHARASHTRA", "MANIPUR",
          "MEGHALAYA", "MIZORAM", "NAGALAND", "ODISHA", "ORISSA", "PUNJAB", "RAJASTHAN", "SIKKIM", "TAMIL NADU",
          "TELANGANA", "TRIPURA", "UTTAR PRADESH", "UTTARAKHAND", "WEST BENGAL", "DELHI", "JAMMU", "PUDUCHERRY",
          "CHANDIGARH", "DADRA", "DAMAN", "LADAKH"]
CIN_STATE = {"AP": "ANDHRA PRADESH", "TG": "TELANGANA", "AS": "ASSAM", "BR": "BIHAR", "CT": "CHHATTISGARH",
             "GA": "GOA", "GJ": "GUJARAT", "HR": "HARYANA", "HP": "HIMACHAL PRADESH", "JH": "JHARKHAND",
             "KA": "KARNATAKA", "KL": "KERALA", "MP": "MADHYA PRADESH", "MH": "MAHARASHTRA", "OR": "ODISHA",
             "PB": "PUNJAB", "RJ": "RAJASTHAN", "TN": "TAMIL NADU", "UP": "UTTAR PRADESH", "UR": "UTTARAKHAND",
             "WB": "WEST BENGAL", "DL": "DELHI", "JK": "JAMMU", "CH": "CHANDIGARH", "PY": "PUDUCHERRY"}
SUFFIX = re.compile(r"\b(PRIVATE LIMITED|PVT\.? ?LTD\.?|LIMITED|LTD\.?|LLP|PVT)\b")
STOP = re.compile(r"\b(PRIVATE|PVT|LIMITED|LTD|LLP|CO|COMPANY|THE|AND|INDIA|INDUSTRIES)\b")


def con():
    return duckdb.connect(str(DB))


def key(name):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper().replace("&", " AND "))
    return re.sub(r"\s+", " ", STOP.sub(" ", s)).strip()


def split_name(text):
    """Company name = text up to first legal suffix. Returns (name, confident)."""
    t = re.sub(r"\s+", " ", str(text).upper()).strip()
    m = SUFFIX.search(t)
    if m:
        return t[:m.end()].strip(" ,.-"), True
    return " ".join(t.split()[:5]), False


GENERIC = {"FOODS", "TECHNOLOGIES", "ENTERPRISES", "POWER", "BEVERAGES", "STEEL", "STEELS", "PHARMA", "ENERGY",
           "INFRA", "INFRASTRUCTURE", "POLYMERS", "CHEMICALS", "TEXTILES", "ALLOYS", "CEMENT", "PLASTICS", "SOLAR",
           "PROJECTS", "LOGISTICS", "ROAD", "INDUSTRIAL", "ESTATE", "FLOOR", "NAGAR", "PLOT", "NO"}


def usable(name, confident):
    toks = key(name).split()
    # coordinate-parsed names are clean, so one distinctive word is fine ("HAVELLS INDIA LIMITED"); only bare generics are not
    return bool(confident and toks and len(" ".join(toks)) >= 4 and not set(toks) <= GENERIC and len(str(name)) <= 90)


def find_state(text):
    t = str(text).upper()
    for s in STATES:
        if s in t:
            return "ORISSA" if s == "ODISHA" else s if s != "ORISSA" else "ODISHA"
    return None


# --------------------------------------------------------------------------- stages
def stage_extract(a):
    """Distinct companies from the coordinate-parsed Part A (partA_coords.parquet; run parse_partA_coords.py first)."""
    src = BASE / "partA_coords.parquet"
    if not src.exists(): sys.exit("run parse_partA_coords.py first")
    c = pd.read_parquet(src)
    t = BASE / "partA_text_layout.parquet"          # 2001-2018 text-layout files (+ OCR fallback pages)
    if t.exists():
        x = pd.read_parquet(t); x = x[~x.source_file.str.startswith("2023")]
        x["company_text"] = x.company_name
        c = pd.concat([c, x[[col for col in c.columns if col in x.columns]]], ignore_index=True)
    c = c.sort_values("source_file").drop_duplicates(["month", "serial", "company_text"])
    rows = {}
    for r in c.itertuples(index=False):
        name = re.sub(r"\s+", " ", str(r.company_name)).strip(" ,.;:-").upper()
        k = key(name)
        if len(k) < 4: continue
        st = find_state(r.district_state) or find_state(r.company_text)
        d = rows.setdefault((k, st), dict(name=name, key=k, state=st, confident=bool(r.name_has_suffix), n_part_a=0,
                                          first_iem=str(r.month), last_iem=str(r.month), item=str(r.item)[:80]))
        d["n_part_a"] += 1
        d["last_iem"] = max(d["last_iem"], str(r.month))
    df = pd.DataFrame(rows.values())
    # Part B (implemented) companies: add the ones Part A never listed, and flag every company that appears in Part B
    pb_file = BASE / "partB_all.parquet"
    df["in_part_b"] = False; df["n_part_b"] = 0; df["last_part_b"] = None
    if pb_file.exists():
        pb = pd.read_parquet(pb_file)
        pb["name"] = pb.company_name.str.upper().str.replace(r"\s+", " ", regex=True).str.strip(" ,.;:-")
        pb["key"] = pb.name.map(key); pb = pb[pb.key.str.len() >= 4]
        pb["state"] = pb.state.map(lambda x: None if pd.isna(x) else x)
        g = (pb.sort_values("month").groupby(["key", "state"], dropna=False)
               .agg(name=("name", "last"), confident=("name_has_suffix", "max"), n_part_b=("month", "size"), last_part_b=("month", "max"))
               .reset_index())
        idx = {(r.key, r.state if isinstance(r.state, str) else None): i for i, r in enumerate(df.itertuples())}
        extra = []
        for r in g.itertuples(index=False):
            st = r.state if isinstance(r.state, str) else None
            i = idx.get((r.key, st))
            if i is None:
                extra.append(dict(name=r.name, key=r.key, state=st, confident=bool(r.confident), n_part_a=0, first_iem=r.last_part_b,
                                  last_iem=r.last_part_b, item="", in_part_b=True, n_part_b=int(r.n_part_b), last_part_b=r.last_part_b))
            else:
                df.at[i, "in_part_b"] = True; df.at[i, "n_part_b"] = int(r.n_part_b); df.at[i, "last_part_b"] = r.last_part_b
        if extra: df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
        print(f"Part B: {len(g)} distinct companies; {int(df.in_part_b.sum())} flagged in table ({len(extra)} not seen in Part A)")
    df["part_b_hit"] = False
    for col in ("cin", "status", "src", "cand"): df[col] = pd.Series([None] * len(df), dtype="object")
    df["score"] = float("nan")
    df["usable"] = [bool(usable(n, ok)) for n, ok in zip(df.name, df.confident)]
    k = con()
    try:   # keep earlier CIN/status results when the company table is rebuilt
        prior = k.execute("select key, coalesce(state,'') st, cin, status, src, score from co where cin is not null").df()
        df["st"] = df.state.fillna("")
        df = df.drop(columns=["cin", "status", "src", "score"]).merge(prior, on=["key", "st"], how="left").drop(columns=["st"])
        print(f"carried over {int(df.cin.notna().sum())} earlier CIN results")
    except Exception:
        pass
    k.execute("""create or replace table co as select row_number() over () id, name, key, state, confident, usable,
        n_part_a, first_iem, last_iem, item, part_b_hit, in_part_b, n_part_b, last_part_b::varchar last_part_b, cin::varchar cin, status::varchar status, src::varchar src,
        score::double score, cand::varchar cand from df""")
    print(f"{len(c)} Part A records -> {len(df)} distinct companies; {int(df.usable.sum())} pass the name-quality gate; "
          f"state known for {int(df.state.notna().sum())}")


def stage_partb(a):
    sh = set()
    for f in glob.glob(str(BASE / "md/PartB/*.md")):
        w = re.sub(r"[^A-Z0-9 ]", " ", Path(f).read_text(errors="ignore").upper().replace("&", " AND ")).split()
        w = [x for x in w if x not in ("PRIVATE", "PVT", "LIMITED", "LTD", "LLP")]
        sh.update(" ".join(w[i:i + 3]) for i in range(len(w) - 2))
    k = con(); df = k.execute("select id,key from co").df()
    hit = [(" ".join(x.split()[:3]) in sh) if len(x.split()) >= 3 else False for x in df.key]
    df["h"] = hit
    k.execute("update co set part_b_hit=h from df where co.id=df.id")
    print(f"Part B name-shingle hits: {sum(hit)} / {len(df)}  (3-word shingles; companies with <3 key words are skipped)")


def stage_master(a):
    """CSV from data.gov.in 'Company Master Data' (any column casing). Needs: CIN, name, status (+state optional)."""
    d = pd.read_csv(a.master, dtype=str, low_memory=False)
    d.columns = [x.strip().upper().replace(" ", "_") for x in d.columns]
    pick = lambda *c: next((x for x in c if x in d.columns), None)
    cin, nm, stt = pick("CIN", "CORPORATE_IDENTIFICATION_NUMBER"), pick("COMPANY_NAME", "NAME"), pick("COMPANY_STATUS", "STATUS")
    assert cin and nm, f"need CIN + name columns, have {list(d.columns)}"
    out = pd.DataFrame({"cin": d[cin], "name": d[nm], "status": d[stt] if stt else None})
    out["key"] = out["name"].map(key)
    out["state"] = out["cin"].str[6:8].map(CIN_STATE)
    con().execute("create or replace table mca_master as select * from out")
    print(f"mca_master: {len(out)} rows, status counts:\n{out.status.value_counts().head(8)}")


def stage_match(a):
    """Offline match against the MCA master using the pair (state, name key) as the index.
    CIN characters 7-8 are the state code, so every master row gets a state for free and a lookup is one dict hit.
      1. exact   (state, key)            -> accept if one CIN
      2. fuzzy   within (state, first word) bucket only (about 1/30 of the master), accept at >=96 with margin >=4
      3. no state known -> exact name only, accept only if the key is unique across all states
    Anything ambiguous keeps its top-3 candidates for the review tail."""
    k = con()
    if not k.execute("select count(*) from information_schema.tables where table_name='mca_master'").fetchone()[0]:
        sys.exit("no mca_master - run `master --master file.csv` first (see METHOD notes), or skip to `web`")
    m = k.execute("select cin,name,status,key,state from mca_master where key <> ''").df()
    exact, bucket, by_key = {}, {}, {}
    for r in m.itertuples(index=False):
        exact.setdefault((r.state, r.key), []).append(r)
        bucket.setdefault((r.state, r.key.split(" ")[0]), []).append(r)
        by_key.setdefault(r.key, []).append(r)
    todo = k.execute("select id,key,state from co where cin is null and usable").df()
    if a.limit: todo = todo.head(a.limit)
    res, t0 = [], time.time()
    for r in todo.itertuples(index=False):
        st = None if pd.isna(r.state) else r.state
        hits = exact.get((st, r.key), []) if st else []
        if not hits and not st:
            hits = by_key.get(r.key, [])
            if len({h.state for h in hits}) > 1: hits = []          # same name in several states: not identity
        if len({h.cin for h in hits}) == 1:
            x = hits[0]; res.append((r.id, x.cin, x.status, "master-exact", 100.0, None)); continue
        pool = bucket.get((st, r.key.split(" ")[0]), []) if st else []
        best = process.extract(r.key, [x.key for x in pool], scorer=fuzz.token_sort_ratio, limit=3) if pool else []
        if best and best[0][1] >= 96 and (len(best) == 1 or best[0][1] - best[1][1] >= 4):
            x = pool[best[0][2]]; res.append((r.id, x.cin, x.status, "master-fuzzy", float(best[0][1]), None))
        elif len(hits) > 1 or (best and best[0][1] >= 80):
            cand = [{"cin": h.cin, "name": h.name, "status": h.status} for h in hits[:3]] or \
                   [{"cin": pool[i].cin, "name": pool[i].name, "status": pool[i].status} for _, sc, i in best if sc >= 80]
            res.append((r.id, None, None, None, float(best[0][1]) if best else 0.0, json.dumps(cand)))
    df = pd.DataFrame(res, columns=["id", "cin", "status", "src", "score", "cand"])
    k.execute("update co set cin=df.cin,status=df.status,src=df.src,score=df.score,cand=df.cand from df where co.id=df.id")
    print(f"matched {int(df.cin.notna().sum())}, ambiguous->review {int(df.cand.notna().sum())}, of {len(todo)} tried "
          f"in {time.time()-t0:.1f}s")


def slug_variants(name):
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower()); s = re.sub(r"\s+", " ", s).strip()
    base = re.sub(r"\b(private limited|pvt ltd|pvt|limited|ltd)\b", "", s).strip().replace(" ", "-")
    suff = "private-limited" if re.search(r"private|pvt", s) else "limited"
    return [f"{base}-{suff}", f"{base}-limited", f"{base}-private-limited"]


def _fetch(slug, gate):
    """One page fetch, rate-limited across all threads. Returns (cin, status, last_bs, agm)."""
    with gate["lock"]:
        wait = gate["next"] - time.time()
        if wait > 0: time.sleep(wait)
        gate["next"] = time.time() + gate["gap"]
    html = subprocess.run(["curl", "-sL", "-m", "15", "-A", UA, f"https://www.quickcompany.in/company/{slug}"],
                          capture_output=True, text=True).stdout
    cin = (CIN_RE.search(html) or [None])[0] if html else None
    st = re.search(r"Status\s*</?[^>]*>\s*(?:<[^>]+>\s*)*([A-Za-z][A-Za-z \-]{2,30}?)\s*<", html)
    return cin, (st.group(1).strip() if st and "Paid" not in st.group(1) else None), None, None


def stage_web(a):
    """Name + state lookup on a free site. Faster than the first version: threads share one rate limit (--rps),
    the slug variant implied by the legal suffix is tried first (one request for most companies), pages already
    fetched are never repeated, and a hit is accepted only if the CIN state code equals the IEM state."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    k = con()
    k.execute("create table if not exists web_cache(slug varchar primary key, cin varchar, status varchar, last_bs varchar, agm varchar)")
    if a.candidates:
        cand = pd.read_parquet(BASE / "iem_companies_compiled.parquet")
        cand = cand[cand.support_candidate & cand.cin.isna()][["k", "state", "last_month"]]
        k.register("cand_df", cand)
        todo = k.execute("""select co.id, co.name, co.state from co join cand_df c on c.k = co.key and c.state = coalesce(co.state,'')
                            where co.cin is null and co.usable order by c.last_month desc, co.id""").fetchall()
    else:
        scope = "and in_part_b" if a.partb else ""
        todo = k.execute(f"select id,name,state from co where cin is null and cand is null and usable {scope} "
                         "order by in_part_b desc, n_part_b desc, n_part_a desc, id").fetchall()
    if a.limit: todo = todo[:a.limit]
    cache = {r[0]: (r[1], r[2]) for r in k.execute("select slug,cin,status from web_cache").fetchall()}
    gate = {"lock": threading.Lock(), "next": 0.0, "gap": 1.0 / a.rps}
    need = sum(1 for _, nm, _ in todo if not any(sl in cache for sl in slug_variants(nm)))
    print(f"{len(todo)} companies to check; {need} have no cached page yet (these need network)", flush=True)

    def work(item):
        cid, name, state = item
        out = []
        for slug in dict.fromkeys(slug_variants(name)):
            if slug in cache: cin, st = cache[slug]
            else:
                cin, st, bs, ag = _fetch(slug, gate); out.append((slug, cin, st, bs, ag)); cache[slug] = (cin, st)
            if cin: return cid, state, cin, st, out
        return cid, state, None, None, out

    got = parked = tier2 = 0; names = {t[0]: t[1] for t in todo}; t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, it) for it in todo]
        for i, f in enumerate(as_completed(futs), 1):
            cid, state, cin, st, fetched = f.result()
            for row in fetched: k.execute("insert or replace into web_cache values(?,?,?,?,?)", list(row))
            if cin:
                nm = names[cid]
                want = "PTC" if re.search(r"\b(PVT|PRIVATE)\b|\(P\)", nm.upper()) else "PLC"
                class_ok = cin[12:15] in (want, "NPL", "GAP", "GOI", "FTC", "FLC", "PTC" if want == "PTC" else "PLC")
                if state and CIN_STATE.get(cin[6:8]) == state:
                    k.execute("update co set cin=?,status=?,src='web-slug+state',score=90 where id=?", [cin, st, cid]); got += 1
                elif state and class_ok:
                    # plant state (IEM) differs from registered-office state (CIN): the page is the exact-name company; keep as tier 2
                    k.execute("update co set cin=?,status=?,src='web-slug-name-only',score=70 where id=?", [cin, st, cid]); tier2 += 1
                elif not state:
                    k.execute("update co set cand=? where id=? and cin is null",
                              [json.dumps([{"cin": cin, "name": "web", "status": st}]), cid]); parked += 1
            if i % 50 == 0: print(f"  {i}/{len(todo)}  accepted {got}  ({time.time()-t0:.0f}s)", flush=True)
    print(f"web: auto-accepted {got} (name+state agree), {tier2} name-only (plant state differs from registered office), "
          f"parked {parked} state-less, of {len(todo)} tried in {time.time()-t0:.0f}s")


def stage_batches(a):
    k = con(); out = BASE / "llm_batches"; out.mkdir(exist_ok=True)
    rows = k.execute("""select id,name,state,item,cand from co where cin is null and usable order by n_part_a desc""").fetchall()
    n = 0
    for i in range(0, len(rows), a.size):
        with open(out / f"batch_{n:04d}.jsonl", "w") as f:
            for id_, name, st, item, cand in rows[i:i + a.size]:
                f.write(json.dumps({"id": id_, "name": name, "state": st, "item": item,
                                    "cand": json.loads(cand) if cand else []}) + "\n")
        n += 1
    print(f"{len(rows)} residual companies -> {n} batches of <= {a.size} in {out}/  (see AGENT_PROMPT in METHOD doc)")


def stage_ingest(a):
    k = con(); n = 0
    for f in glob.glob(str(BASE / "llm_results/*.jsonl")):
        for line in open(f):
            v = json.loads(line)   # {"id":..,"cin":"..|null","status":"..|null","evidence_url":"..","conf":"high|low"}
            if v.get("cin") and CIN_RE.fullmatch(v["cin"]) and v.get("conf") == "high":
                k.execute("update co set cin=?,status=?,src='llm-web',score=80 where id=? and cin is null",
                          [v["cin"], v.get("status"), v["id"]]); n += 1
    print(f"ingested {n} high-confidence agent verdicts (low-confidence stay unmapped on purpose)")


def classify(status, hit, cin):
    if not cin: return "UNMAPPED"
    s = (status or "").lower()
    if any(x in s for x in ("strike", "amalgam", "liquidat", "dissolv", "closed", "inactive")): return "INACTIVE"
    if "dormant" in s: return "DORMANT"
    if "active" in s or s == "":
        return "OPERATIONAL (active + Part B)" if hit else "ACTIVE (registered, no Part B yet)"
    return "CHECK"


def stage_report(a):
    k = con(); df = k.execute("select * from co").df()
    df["verdict"] = [classify(s, h, c) for s, h, c in zip(df.status, df.part_b_hit, df.cin)]
    print(df.verdict.value_counts().to_string()); print(df.groupby("src", dropna=False).size().to_string())
    p = BASE / "IEM_operational_status.xlsx"
    df.drop(columns=["cand"]).to_excel(p, index=False); print("wrote", p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("stage", choices=["extract", "partb", "master", "match", "web", "batches", "ingest", "report"])
    ap.add_argument("--limit", type=int); ap.add_argument("--master"); ap.add_argument("--size", type=int, default=40); ap.add_argument("--candidates", action="store_true"); ap.add_argument("--partb", action="store_true")
    ap.add_argument("--workers", type=int, default=6); ap.add_argument("--rps", type=float, default=4.0)
    a = ap.parse_args(); globals()["stage_" + a.stage](a)
