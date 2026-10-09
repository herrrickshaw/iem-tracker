#!/usr/bin/env python3
"""
Part B lists in the NEW FORMAT (2018 onward, 9 columns):
  S.No | Company name + address | Location | Item | Capacity | Part-B ack no/date | Part-A (memorandum) no | Part-A date | Production commenced
Column edges come from the empty gutters between columns (header words are centred, so they are not used).
Records are cut by serial number; when the serial sits mid-record the name is taken from the end of the previous block.
Output rows keep the Part A reference (mem_no + mem_date) so Part B can be joined to Part A by number/date.

Usage: python3 parse_partB_new.py [FILE.pdf]     # no arg = every Part B PDF in new format -> partB_new.parquet
"""
import glob, re, sys
from pathlib import Path

import pandas as pd
import pymupdf

from iem_verify import find_state
from parse_partA_coords import SUFFIX_FULL, STATE_TOKENS, cut_name, last_phrase

BASE = Path(__file__).resolve().parent
HDR = {"Sr.", "Sr", "S.No", "No.", "No", "Name", "Of", "Undertaking", "/", "Address", "Location", "Item", "Manufacture", "Actual",
       "Capacity", "Installed", "Ack", "No./Date", "Mem", "Date", "Production", "Commenced", "On", "Company", "and", "Part-B", "Part-A",
       "Number", "A", "Part-", "(NEW", "FORMAT)", "State", "Wise", "Detail", "Report", "from", "in", "respect", "IEM", "To", "From"}
DATE = re.compile(r"(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{4})")


def header_bottom(words):
    prod = [w for w in words if w[4] == "Production"]
    if not prod: return None
    y0 = prod[0][1]
    return max((w[3] for w in words if w[4] in HDR and y0 - 6 <= w[1] <= y0 + 40), default=y0 + 12)


def blocks_of(doc):
    cov, n = {}, 0
    for page in list(doc)[:8]:
        words = page.get_text("words"); hb = header_bottom(words)
        if hb is None: continue
        for w in words:
            if w[1] <= hb: continue
            n += 1
            for x in range(int(w[0]), int(w[2]) + 1): cov[x] = cov.get(x, 0) + 1
    if not cov: return None
    peak = max(cov.values()); lo, hi = min(cov), max(cov); thr = 0.02 * peak
    starts, st = [], None
    for x in range(lo, hi + 1):
        if cov.get(x, 0) > thr and st is None: st = x
        if st is not None and cov.get(x, 0) <= thr and all(cov.get(x + k, 0) <= thr for k in range(3)):
            starts.append(st); st = None
    if st is not None: starts.append(st)
    return starts


def col_index(x, starts):
    i = 0
    for j, s in enumerate(starts):
        if x >= s - 2: i = j
    return i


def parse_pdf(path):
    doc = pymupdf.open(path); starts = blocks_of(doc)
    if not starts or len(starts) < 8: return [], f"blocks={None if not starts else len(starts)}"
    recs, last, pre, cur = [], 0, [], None
    for pno, page in enumerate(doc, 1):
        words = page.get_text("words"); hb = header_bottom(words)
        body = [w for w in words if (hb is None or w[1] > hb) and w[1] > 40]
        cols = [(col_index(w[0], starts), w) for w in body]
        raw = sorted([w for c, w in cols if c == 0 and w[4].isdigit()], key=lambda w: (w[1], w[0]))
        chains, ch = [], []
        for w in raw:
            if ch and abs(w[0] - ch[-1][0]) < 3 and 0 < w[1] - ch[-1][1] <= 14: ch.append(w)
            else:
                if ch: chains.append(ch)
                ch = [w]
        if ch: chains.append(ch)
        sts = []
        for ch in chains:
            whole = "".join(x[4] for x in ch)
            if whole.isdigit() and last < int(whole) <= last + 6: sts.append((ch[0][1] - 3, int(whole))); last = int(whole)
            else:
                for x in ch:
                    if last < int(x[4]) <= last + 6: sts.append((x[1] - 3, int(x[4]))); last = int(x[4])
        buckets = {}
        for c, w in cols:
            if c == 0: continue
            idx = None
            for i, (sy, _) in enumerate(sts):
                if w[1] >= sy: idx = i
                else: break
            buckets.setdefault(idx, []).append((c, w))
        if None in buckets:
            (cur["w"] if cur is not None else pre).extend(buckets[None])
        for i, (sy, sn) in enumerate(sts):
            cur = {"serial": sn, "page": pno, "w": list(buckets.get(i, []))}; recs.append(cur)

    def colsorted(ws, ci):
        t = " ".join(w[4] for c, w in sorted(ws, key=lambda t: (round(t[1][1] / 3), t[1][0])) if c == ci)
        # name and address are glued with commas in the 2018-19 lists ("PVT.LTD.,,D.NO.88-"): split them apart
        return re.sub(r"\s+", " ", re.sub(r",+", " , ", t)).strip() if ci == 1 else t

    def toks(ws, ci): return colsorted(ws, ci).split()
    out = []
    for r in recs:
        comp = colsorted(r["w"], 1); name, ok = cut_name(comp)
        out.append({"serial": r["serial"], "page": r["page"], "company_name": name, "name_has_suffix": ok, "company_text": comp,
                    "location": colsorted(r["w"], 2), "item": colsorted(r["w"], 3),
                    "ack": colsorted(r["w"], 5), "mem_no_txt": colsorted(r["w"], 6), "mem_date_txt": colsorted(r["w"], 7),
                    "commenced_txt": colsorted(r["w"], 8)})
    # name: try the serial-line block ("top") and the end of the previous block ("mid"); keep whichever gives cleaner names
    blocks = [toks(pre, 1)] + [toks(r["w"], 1) for r in recs]
    mid = [last_phrase(blocks[i]) for i in range(len(out))]
    def score(names): return sum(1 for n in names if n and SUFFIX_FULL.search(n.split()[-1].strip(",.")) and len(n) <= 60)
    top_score = score([r["company_name"] for r in out]); mid_score = score(mid)
    use_mid = mid_score > top_score
    if use_mid:
        for r, nm in zip(out, mid):
            if nm: r["company_name"], r["name_has_suffix"] = nm, True
        # centred cells: attach each Location word to the nearest serial line instead of the bucket it fell in
        ys = [(sy, i) for i, (sy, _) in enumerate(((x["w"][0][1][1] if x["w"] else 0, 0) for x in recs))]
    for r in out: r["layout"] = "mid-serial" if use_mid else "top"
    LEAD = {"NU", "NA", "GM", "NIL", "NULL", ",", ".", "-"}
    def clean(nm):
        t = [x for x in re.sub(r"\s+", " ", nm.replace(" , ", " ")).split(" ") if x]
        while t and (t[0].upper().strip(",.") in LEAD or (len(t) > 3 and t[0].upper().strip(",.") in STATE_TOKENS)): t = t[1:]
        return " ".join(t).strip(" ,.;:-")
    for r in out: r["company_name"] = clean(r["company_name"])
    for r in out:
        def d(t):
            m = DATE.search(t or ""); return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}" if m else None
        r["part_b_date"] = d(r["ack"]); r["mem_date"] = d(r["mem_date_txt"]); r["commenced"] = d(r["commenced_txt"])
        mn = re.search(r"\d{1,6}", r["mem_no_txt"] or ""); r["mem_no"] = int(mn.group(0)) if mn else None
        r["state"] = find_state(r["location"]) or find_state(r["company_text"][-60:])
    return out, "ok"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        rows, st = parse_pdf(sys.argv[1]); print(st, len(rows))
        for r in rows[:6]: print(r["serial"], "|", r["company_name"][:48], "|", r["state"], "| A:", r["mem_no"], r["mem_date"], "| B:", r["part_b_date"], "| prod:", r["commenced"])
        sys.exit()
    allrows, skipped = [], []
    for f in sorted(glob.glob(str(BASE / "PartB/*.pdf"))):
        import subprocess
        t = subprocess.run(["/opt/homebrew/bin/pdftotext", "-l", "1", f, "-"], capture_output=True, text=True).stdout
        if "PRODUCTION MEMORANDUM" in t.upper(): continue                  # old text layout -> extract_partA_text_layout.py
        try: rows, st = parse_pdf(f)
        except Exception as e: rows, st = [], f"error {e}"
        if not rows: skipped.append((Path(f).name, st)); continue
        for r in rows: r["source_file"] = Path(f).name; r["month"] = Path(f).name[:7]
        allrows += rows
    df = pd.DataFrame(allrows); df.to_parquet(BASE / "partB_new.parquet", index=False)
    print(len(df), "records from", df.source_file.nunique(), "files;", len(skipped), "skipped")
    for s in skipped[:8]: print("  skipped", s)
    df["year"] = df.month.str[:4]
    print(df.groupby("year").agg(rows=("serial", "size"), suffix=("name_has_suffix", "mean"), memno=("mem_no", lambda x: x.notna().mean()),
                                 state=("state", lambda x: x.notna().mean())).round(2).T.to_string())
