#!/usr/bin/env python3
"""
Coordinate-based Part A parser. Column boundaries come from each file's header row, so it handles the different
page sizes and column orders of the 2018-2024 lists (and any other era with a Company/District/Item/Capacity/IEM header).

Per record we keep the COMPANY column as its own text, so the company name is read from the name column only,
never mixed with district/state/item words (the cause of the fragment names in the earlier parse).

Usage:  python3 parse_partA_coords.py            # all PartA/*.pdf -> partA_coords.parquet + report
        python3 parse_partA_coords.py FILE.pdf   # one file, print rows
"""
import glob, re, sys
from pathlib import Path

import pandas as pd
import pymupdf

BASE = Path(__file__).resolve().parent
HDR_WORDS = {"Sr.", "Sr", "Sl.", "S.No", "No.", "No", "Company", "Name", "and", "Address", "District", "State", "Item",
             "of", "Manufacture", "Capacity", "Unit", "IEM", "Number", "Date", "Product", "Nos"}
SUFFIX_FULL = re.compile(r"(PRIVATE\s+LIMITED|PVT\.?\s*LTD\.?|LIMITED|LTD\.?|LLP|PVT\.?|PVT\.?LTD\.?)", re.I)
STATE_TOKENS = {"ANDHRA", "ARUNACHAL", "ASSAM", "BIHAR", "CHHATTISGARH", "GOA", "GUJARAT", "HARYANA", "HIMACHAL", "PRADESH",
                "JHARKHAND", "KARNATAKA", "KERALA", "MADHYA", "MAHARASHTRA", "MANIPUR", "MEGHALAYA", "MIZORAM", "NAGALAND",
                "ODISHA", "ORISSA", "PUNJAB", "RAJASTHAN", "SIKKIM", "TAMIL", "NADU", "TELANGANA", "TRIPURA", "UTTAR",
                "UTTARAKHAND", "WEST", "BENGAL", "DELHI", "JAMMU", "KASHMIR", "PUDUCHERRY", "CHANDIGARH"}
SUFFIX = re.compile(r"\b(PRIVATE\s+LIMITED|PVT\.?\s*LTD\.?|LIMITED|LTD\.?|LLP|PVT\.?)(?=[\s,.;:(\-]|$)", re.I)
IEM_NO_RE = re.compile(r"(\d{1,5})\s*/\s*([NP])\s*/\s*SIA\s*/\s*IM\s*O", re.I)
IEM_RE = re.compile(r"(\d{1,5})\s*/\s*([NP])\s*/\s*SIA\s*/\s*IM\s*O\s*/?\s*(\d{4})", re.I)
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")


def header_positions(doc):
    """x of the Company / District / Item / Capacity / IEM header words, from the first page that has them."""
    for page in doc[:3]:
        pos = {}
        for w in page.get_text("words"):
            if w[4] in ("Company", "District", "Item", "Capacity", "IEM") and w[4] not in pos:
                pos[w[4]] = (w[0], w[1])
        if {"Company", "District", "Item"} <= set(pos):
            return pos
    return None


ROLE_ORDER = ["company", "district", "item", "capacity", "iem"]


def gutter_columns(doc, pos, roles):
    """Column left edges from empty vertical strips between columns. Needs exactly serial + len(roles) text blocks."""
    cov = {}
    for page in list(doc)[:8]:
        words = page.get_text("words")
        hdr = [w for w in words if w[4] == "Company" and abs(w[0] - pos["Company"][0]) < 15]
        cut = -1
        if hdr:
            y0 = min(w[1] for w in hdr)
            cut = max((w[3] for w in words if w[4] in HDR_WORDS and y0 - 3 <= w[1] <= y0 + 48), default=-1)
        for w in words:
            if w[1] <= cut: continue
            for x in range(int(w[0]), int(w[2]) + 1): cov[x] = cov.get(x, 0) + 1
    if not cov: return None
    peak = max(cov.values()); lo, hi = min(cov), max(cov)
    low = [x for x in range(lo, hi + 1) if cov.get(x, 0) <= 0.02 * peak]
    blocks, start, prev = [], None, None
    for x in range(lo, hi + 1):
        empty = cov.get(x, 0) <= 0.02 * peak
        if not empty and start is None: start = x
        if empty and start is not None:
            # a gutter must be >= 3pt wide to split blocks
            if all(cov.get(x + k, 0) <= 0.02 * peak for k in range(3)):
                blocks.append(start); start = None
    if start is not None: blocks.append(start)
    if len(blocks) != len(roles) + 1: return None
    return list(zip(roles, [b - 2 for b in blocks[1:]])), blocks[0]


def infer_columns(doc, pos):
    """Column left edges from header x (headers are centred over left-aligned data, so use a margin).
    District/Item boundary is the midpoint of their headers when District comes first (item words start left of
    the centred 'Item' header). Returns (sorted [(role,left_edge)], serial_edge, method)."""
    hx = {"company": pos["Company"][0], "district": pos["District"][0], "item": pos["Item"][0]}
    if "Capacity" in pos: hx["capacity"] = pos["Capacity"][0]
    if "IEM" in pos: hx["iem"] = pos["IEM"][0]
    roles = [r for r, _ in sorted(hx.items(), key=lambda kv: kv[1])]
    g = gutter_columns(doc, pos, roles)
    if g: return g[0], g[1], "gutter"
    cols = {r: x - 12 for r, x in hx.items()}
    if hx["district"] < hx["item"]:
        cols["item"] = (hx["district"] + hx["item"]) / 2
    cols = sorted(cols.items(), key=lambda c: c[1])
    return cols, cols[0][1] if cols[0][0] == "company" else cols[0][1] - 4, "header"


def iem_left_of(doc, cols, hdr_x):
    """Left edge of the IEM No/Date block: the leftmost strong line-start cluster within 40pt of the rightmost one."""
    starts, n_lines = {}, 0
    for page in list(doc)[:6]:
        lines = {}
        for w in page.get_text("words"):
            lines.setdefault(round(w[1] / 3), []).append(w)
        for lw in lines.values():
            lw.sort(key=lambda w: w[0]); prev = -99
            for w in lw:
                if w[0] - prev > 9:
                    k = round(w[0] / 3) * 3; starts[k] = starts.get(k, 0) + 1
                prev = w[2]
            n_lines += 1
    strong = sorted(x for x, c in starts.items() if c >= max(5, 0.03 * n_lines))
    if not strong: return None
    near = [x for x in strong if hdr_x - 25 <= x <= hdr_x + 3]
    return (min(near) if near else strong[-1]) - 3


def serial_x_of(doc, company_hdr_x):
    """x of the serial-number column: the most common x of digit-only words left of the Company header."""
    cnt = {}
    for page in list(doc)[:8]:
        for w in page.get_text("words"):
            if w[4].isdigit() and len(w[4]) <= 4 and w[0] < company_hdr_x:
                cnt[round(w[0] / 3) * 3] = cnt.get(round(w[0] / 3) * 3, 0) + 1
    return max(cnt, key=cnt.get) if cnt else None


def col_of(x, cols, serial_edge, text=""):
    """serial_edge is the serial column x (or None). Digit-only words at that x are serial numbers."""
    if serial_edge is not None and text.isdigit() and len(text) <= 4 and serial_edge - 7 <= x <= serial_edge + 14: return "serial"
    name = cols[0][0]
    for n, left in cols:
        if x >= left: name = n
    return name


def cut_name(text):
    """Company name = text up to and including the first legal suffix. Falls back to the first line."""
    t = re.sub(r"\s+", " ", text).strip()
    m = SUFFIX.search(t)
    if m and m.end() >= 6:
        return t[:m.end()].strip(" ,.;:-"), True
    return t[:60], False


def last_phrase(txt):
    """Last legal-suffix-terminated name in a token list, trimmed back to the previous record's tail."""
    end = None
    for i in range(len(txt), 0, -1):
        for span in (2, 1):
            if i - span >= 0 and SUFFIX_FULL.fullmatch(" ".join(txt[i - span:i]).strip(" ,.;:")):
                end = i; break
        if end: break
    if not end: return None
    j = end - 1
    while j > 0 and end - j < 11 and not re.search(r"\d", txt[j - 1]):
        j -= 1
    seg = txt[j:end]
    while len(seg) > 3 and seg[0].upper().strip(",.") in STATE_TOKENS: seg = seg[1:]
    return " ".join(seg).strip(" ,.;:-")


def stream_names(doc, pos, cols, serial_edge):
    """For layouts where the serial sits mid-record: read the company column as one stream per file and, for each
    legal suffix, walk back to the previous record's tail (a token with digits = pin/house no.)."""
    names = []
    comp_left = dict(cols)["company"]
    right = [l for r, l in cols if r != "company"]
    comp_right = min(right) if right else 1e9
    for pno, page in enumerate(doc, 1):
        words = page.get_text("words")
        hdr = [w for w in words if w[4] in ("Company", "District") and abs(w[0] - pos[w[4]][0]) < 15]
        cut = -1
        if hdr:
            y0 = min(w[1] for w in hdr)
            cut = max((w[3] for w in words if w[4] in HDR_WORDS and y0 - 3 <= w[1] <= y0 + 48), default=-1)
        toks = sorted([w for w in words if w[1] > cut and comp_left - 14 <= w[0] < comp_right and
                       not (w[4].isdigit() and len(w[4]) <= 4 and serial_edge is not None and abs(w[0] - serial_edge) <= 14)],
                      key=lambda w: (round(w[1] / 3), w[0]))
        txt = [w[4] for w in toks]
        i = 0
        while i < len(txt):
            # find a token (or two) that closes a legal name
            end = None
            for span in (2, 1):
                if i + span <= len(txt) and SUFFIX_FULL.fullmatch(" ".join(txt[i:i + span]).strip(" ,.;:")):
                    end = i + span; break
            if end is None:
                i += 1; continue
            j = i
            while j > 0 and i - j < 10 and not re.search(r"\d", txt[j - 1]):
                j -= 1
            seg = txt[j:end]
            while len(seg) > 3 and seg[0].upper().strip(",.") in STATE_TOKENS: seg = seg[1:]
            names.append((pno, " ".join(seg).strip(" ,.;:-")))
            i = end
    return names


def parse_pdf(path):
    doc = pymupdf.open(path)
    pos = header_positions(doc)
    if not pos:
        return [], "no header"
    cols, _, method = infer_columns(doc, pos)
    serial_edge = serial_x_of(doc, pos["Company"][0])
    iem_left = iem_left_of(doc, cols, pos["IEM"][0]) if "IEM" in pos else None
    iem_left = iem_left or (dict(cols).get("iem", 1e9) - 10)
    recs, cur, last_serial, pre = [], None, 0, []
    for pno, page in enumerate(doc, 1):
        words = page.get_text("words")
        # skip the repeated header block on this page
        hdr_y = [w for w in words if w[4] in ("Company", "District") and abs(w[0] - pos[w[4]][0]) < 15]
        hdr_bottom = -1
        if hdr_y:
            y0 = min(w[1] for w in hdr_y)
            hdr_bottom = max((w[3] for w in words if w[4] in HDR_WORDS and y0 - 3 <= w[1] <= y0 + 48), default=-1)
        title_cut = hdr_bottom if hdr_bottom > 0 else (-1)
        body = [w for w in words if w[1] > title_cut]
        if not hdr_y:   # continuation page without header: drop any "STATEMENT ON ..." title lines
            body = [w for w in body if w[1] > 0]
        raw = sorted([w for w in body if col_of(w[0], cols, serial_edge, w[4]) == "serial"], key=lambda w: (w[1], w[0]))
        # Serial numbers can be split into single digits, side by side or stacked ("1" over "0" = 10).
        # Chain digits that touch (same line, or same x with a <=14pt vertical gap); try the whole chain first,
        # then fall back to each digit, and always require the running count to go up.
        chains, cur_ch = [], []
        for w in raw:
            if cur_ch and (abs(w[1] - cur_ch[-1][1]) < 2 or (abs(w[0] - cur_ch[-1][0]) < 3 and 0 < w[1] - cur_ch[-1][1] <= 14)):
                cur_ch.append(w)
            else:
                if cur_ch: chains.append(cur_ch)
                cur_ch = [w]
        if cur_ch: chains.append(cur_ch)
        starts = []
        for ch in chains:
            cands = ["".join(x[4] for x in ch)] + ([x[4] for x in ch] if len(ch) > 1 else [])
            whole = cands[0]
            if whole.isdigit() and last_serial < int(whole) <= last_serial + 6:
                starts.append((ch[0][1] - 3, int(whole))); last_serial = int(whole)
            else:
                for x in ch:
                    n = int(x[4])
                    if last_serial < n <= last_serial + 6:
                        starts.append((x[1] - 3, n)); last_serial = n
        buckets = {}
        for w in body:
            c = col_of(w[0], cols, serial_edge, w[4])
            if c == "serial": continue
            # find record whose start y is the last one <= word y; words before first serial continue previous record
            idx = None
            for i, (sy, _) in enumerate(starts):
                if w[1] >= sy: idx = i
                else: break
            buckets.setdefault(idx, []).append((c, w))
        if None in buckets and cur is not None:
            for c, w in buckets[None]: cur["w"].append((c, w))
        elif None in buckets and cur is None:
            pre = buckets[None]
        for i, (sy, sn) in enumerate(starts):
            cur = {"serial": sn, "page": pno, "sy": sy + 3, "w": list(buckets.get(i, []))}
            recs.append(cur)
    out = []
    for r in recs:
        by = {}
        for c, w in sorted(r["w"], key=lambda t: (round(t[1][1] / 3), t[1][0])):
            by.setdefault(c, []).append(w[4])
        lines = {}
        for c, w in r["w"]:
            if c == "company": lines.setdefault(round(w[1] / 3), []).append(w)
        sy = r["sy"]
        ltxt = [" ".join(x[4] for x in sorted(lw, key=lambda x: x[0])) for _, lw in sorted(lines.items())
                if min(x[1] for x in lw) >= sy - 3 or r["page"] != r.get("p0", r["page"])]
        ltxt = [t for t in ltxt if not re.match(r"\s*Place\s*\(Tehsil\)", t, re.I)]
        comp = " ".join(ltxt)
        name, ok = cut_name(comp)
        iw = sorted([(round(w[1] / 3), w[0], w[4]) for c, w in r["w"] if w[0] >= iem_left])
        iem_text = " ".join(t for _, _, t in iw)
        iem_text = re.sub(r"\b([A-Za-z]{3,8}) ([a-z])\b(?=\s+\d{4}\b)", r"\1\2", iem_text)   # "Januar y" -> "January"
        m = IEM_NO_RE.search(iem_text)
        dm = DATE_RE.findall(iem_text)
        mon = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", iem_text)
        lead = re.match(r"\s*(\d{1,5})\b", iem_text)
        if not m and mon:
            rest = re.sub(r"\b(19|20)\d{2}\b", " ", iem_text)
            rest = re.sub(r"\b\d{1,2}\s+[A-Za-z]{3,9}\b", " ", rest)
            nums = re.findall(r"\b\d{1,5}\b", rest)
            lead = re.match(r"(\d+)", nums[0]) if nums else None
        out.append({"serial": r["serial"], "page": r["page"], "company_name": name, "name_has_suffix": ok,
                    "company_text": comp, "district_state": " ".join(by.get("district", [])),
                    "item": " ".join(by.get("item", [])),
                    "iem_no": int(m.group(1)) if m else (int(lead.group(1)) if lead and mon else None),
                    "iem_year": (int(dm[-1][-4:]) if dm else (int(mon.group(3)) if mon else None)) if (m or mon) else None,
                    "filing_date": dm[-1] if dm else (" ".join(mon.groups()) if mon else None),
                    "iem_text": iem_text, "layout": method})
    bad = sum(1 for r in out if len(r["company_name"]) > 60 or not r["name_has_suffix"])
    if out and bad / len(out) > 0.25:
        # Serial sits mid-record: record i's name is the last legal-name phrase of block i-1 (record 1: pre-serial lines).
        def toks(ws):
            cw = sorted([w for c, w in ws if c == "company"], key=lambda w: (round(w[1] / 3), w[0]))
            return [w[4] for w in cw]
        blocks = [toks(pre)] + [toks(r["w"]) for r in recs]
        fixed = 0
        for i, r in enumerate(out):
            nm = last_phrase(blocks[i])
            if nm:
                r["company_name"], r["name_has_suffix"], r["layout"] = nm, True, "mid-serial"; fixed += 1
        for r in out:
            if r["layout"] != "mid-serial": r["layout"] = "mid-serial-unresolved"
    return out, "ok"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        rows, st = parse_pdf(sys.argv[1]); print(st, len(rows))
        for r in rows[:int(sys.argv[2]) if len(sys.argv) > 2 else 8]: print({k: r[k] for k in ("serial", "company_name", "name_has_suffix", "district_state", "iem_no", "iem_year", "filing_date")})
        sys.exit()
    allrows, bad = [], []
    for f in sorted(glob.glob(str(BASE / "PartA/*.pdf"))):
        try:
            rows, st = parse_pdf(f)
        except Exception as e:
            rows, st = [], f"error {e}"
        if not rows: bad.append((Path(f).name, st)); continue
        for r in rows: r["source_file"] = Path(f).name; r["month"] = Path(f).name[:7]
        allrows += rows
    df = pd.DataFrame(allrows)
    df.to_parquet(BASE / "partA_coords.parquet", index=False)
    df["year"] = df.month.str[:4]
    print(f"{len(df)} records from {df.source_file.nunique()} files; {len(bad)} files unparsed")
    print(df.groupby("year").agg(rows=("serial", "size"), suffix_rate=("name_has_suffix", "mean")).round(2).T.to_string())
    for b in bad[:12]: print("  unparsed:", b)
