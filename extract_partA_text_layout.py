#!/usr/bin/env python3
"""
Extract company names from the Part A PDFs that parse_partA_coords.py could not read (2001-2018 'LIST OF IEM' layout,
and any other file), using the PDF text layer via `pdftotext -layout`. Pages with NO text layer fall back to OCR
(pdftoppm -> tesseract CLI, --psm 6 with preserved spacing), so scanned files are covered too.

Finding (2026-10-09): all 211 unparsed Part A PDFs have a text layer, so OCR is a safety net, not the main path.

Layout handled:   "   1 CHENGALVA AGRO FOODS PVT LTD        CHITTOOR        ITEM ...   300.000  977"
                  "     405, SHASHANK RESIDENCY            ANDHRA PRADESH  ...                02/05/2001"
Names that wrap ("GEMINI EDIBILES & FATS INDIA" / "PRIVATE LIMITED.") are re-joined; state comes from the
"STATE : X" heading, which is more reliable than the location column.

Usage:  python3 extract_partA_text_layout.py                 # all files not already in partA_coords.parquet
        python3 extract_partA_text_layout.py FILE.pdf        # one file, print rows
        python3 extract_partA_text_layout.py --ocr-test FILE # rasterise page 1 and run the OCR path on it
"""
import glob, os, re, subprocess, sys, tempfile
from pathlib import Path

import pandas as pd

from iem_verify import find_state

BASE = Path(__file__).resolve().parent
TESS = "/opt/homebrew/bin/tesseract"
PDFTOTEXT = "/opt/homebrew/bin/pdftotext"
PDFTOPPM = "/opt/homebrew/bin/pdftoppm"
REC = re.compile(r"^\s{0,6}(\d{1,5})\s+(\S.*)$")
STATE_HDR = re.compile(r"^\s*STATE\s*:\s*(.+?)\s*$")
SUFFIX = re.compile(r"(PRIVATE\s+LIMITED|PVT\.?\s*LTD\.?|PVT\.?\s*LIMITED|LIMITED|LTD\.?|LLP|PVT\.?)\W*$", re.I)
SUFFIX_ANY = re.compile(r"\b(PRIVATE\s+LIMITED|PVT\.?\s*LTD\.?|LIMITED|LTD\.?|LLP|PVT)\b", re.I)
DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
ADDR_START = re.compile(r"^(PLOT|D\.?\s?NO|DOOR|SY\.?|S\.?\s?NO|SURVEY|FLAT|H\.?\s?NO|NO\.|\d|C/O|P\.?O|VILL|AT |BLOCK|SHED|UNIT|FLOOR)", re.I)


def text_pages(pdf):
    """Yield (page_no, text) using the text layer; OCR a page only when it has no usable text."""
    out = subprocess.run([PDFTOTEXT, "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    pages = out.split("\f")
    for i, t in enumerate(pages, 1):
        if i == len(pages) and not t.strip(): continue
        if len(t.strip()) < 100:
            t = ocr_page(pdf, i) or t
        yield i, t


def ocr_page(pdf, pno, dpi=200):
    """Rasterise one page and OCR it with the tesseract CLI. Returns text, or None if tesseract is missing."""
    if not os.path.exists(TESS): return None
    with tempfile.TemporaryDirectory() as td:
        stem = os.path.join(td, "p")
        subprocess.run([PDFTOPPM, "-r", str(dpi), "-f", str(pno), "-l", str(pno), "-png", str(pdf), stem], check=True)
        png = next(Path(td).glob("p*.png"))
        r = subprocess.run([TESS, str(png), "stdout", "--psm", "6", "-c", "preserve_interword_spaces=1"],
                           capture_output=True, text=True)
        return r.stdout


def split_cols(line):
    """Segments separated by 2+ spaces, with their start offsets."""
    return [(m.start(), m.group(0).strip()) for m in re.finditer(r"\S+(?: \S+)*", line)]


MEMO = re.compile(r"Memorandum\s*No\.?\s*:?\s*(\d+)\s*Date\s*:?\s*(\d{2}/\d{2}/\d{4})(?:\s*Production\s*Commenced\s*on\s*:?\s*(\d{2}/\d{2}/\d{4}))?", re.I)


def parse_text(pages, source_file):
    rows, state, cur = [], None, None
    lines = [(p, ln) for p, t in pages for ln in t.splitlines()]
    i = 0
    while i < len(lines):
        p, ln = lines[i]
        sm = STATE_HDR.match(ln)
        if sm:
            state = sm.group(1).upper(); i += 1; continue
        m = REC.match(ln)
        if m and not re.match(r"^\s*(Telephone|LOCATION)", ln, re.I):
            serial = int(m.group(1))
            base = m.start(2)
            segs = [(base + o, t) for o, t in split_cols(m.group(2))]     # segs[0] = name, segs[1] = district ...
            name = segs[0][1]
            district = segs[1][1] if len(segs) > 1 else None
            # re-join a wrapped name: next line starts at the same indent and carries the legal suffix
            j = i + 1
            while not SUFFIX.search(name) and j < min(i + 3, len(lines)):
                nxt = lines[j][1]
                ns = split_cols(nxt)
                if ns and ns[0][0] <= segs[0][0] + 2 and not ADDR_START.match(ns[0][1]) and len(ns[0][1]) <= 45 \
                        and SUFFIX_ANY.search(ns[0][1]):
                    name = f"{name} {ns[0][1]}"; break
                j += 1
            blk = []
            k = i + 1
            while k < len(lines) and not REC.match(lines[k][1]) and not STATE_HDR.match(lines[k][1]):
                blk.append(lines[k][1]); k += 1
            dm = DATE.findall(ln + " " + " ".join(blk[:6]))
            ackm = re.search(r"(\d{1,6})\s*$", ln)                 # Part A 'Ack No.' = trailing integer on the first line
            # plant state = text under the Location column on the first lines of the record (Part B has no STATE heading)
            off = max(0, (segs[1][0] if len(segs) > 1 else 0) - 10)
            loc_state = next((find_state(l[off:off + 46]) for l in blk[:3] if find_state(l[off:off + 46])), None)
            if state is None:
                if len(segs) > 1: loc_state = loc_state or find_state(segs[1][1])
                loc_state = loc_state or find_state(" ".join(blk[:6]))      # last resort: state in the address block
            memo = MEMO.search(" ".join(blk))          # Part B lists: Part A memorandum no/date + production start
            nm = re.sub(r"\s+", " ", name).strip(" ,.;:-").upper()
            rows.append({"serial": serial, "page": p, "company_name": nm, "name_has_suffix": bool(SUFFIX_ANY.search(nm)),
                         "district_state": f"{district or ''} {state or loc_state or ''}".strip(), "state_header": state or loc_state,
                         "item": segs[2][1] if len(segs) > 2 else None, "filing_date": dm[0] if dm else None,
                         "ack_no": int(ackm.group(1)) if ackm else None,
                         "mem_no": int(memo.group(1)) if memo else None, "mem_date": memo.group(2) if memo else None,
                         "commenced": memo.group(3) if memo else None,
                         "source_file": source_file, "month": source_file[:7], "layout": "text-layout"})
            i = k; continue
        i += 1
    return rows


def parse_file(pdf):
    pages = list(text_pages(pdf))
    return parse_text(pages, os.path.basename(pdf)), sum(1 for _, t in pages if len(t.strip()) < 100)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--ocr-test":
        t = ocr_page(sys.argv[2], 1) or ""
        rows = parse_text([(1, t)], os.path.basename(sys.argv[2]))
        print(f"OCR path: {len(t)} chars, {len(rows)} records; first:", [r["company_name"] for r in rows[:4]]); sys.exit()
    if len(sys.argv) > 1:
        rows, ocr_pages = parse_file(sys.argv[1]); print(len(rows), "records;", ocr_pages, "OCR pages")
        for r in rows[:int(os.environ.get("N", 8))]: print(r["serial"], "|", r["company_name"], "|", r["district_state"], "|", r["filing_date"])
        sys.exit()
    have = set()
    pq = BASE / "partA_coords.parquet"
    if pq.exists(): have = set(pd.read_parquet(pq).source_file)
    todo = [f for f in sorted(glob.glob(str(BASE / "PartA/*.pdf"))) if os.path.basename(f) not in have]
    allrows, ocr_total = [], 0
    for f in todo:
        rows, ocr = parse_file(f); ocr_total += ocr; allrows += rows
    df = pd.DataFrame(allrows)
    df.to_parquet(BASE / "partA_text_layout.parquet", index=False)
    df["year"] = df.month.str[:4]
    print(f"{len(df)} records from {len(todo)} files; {ocr_total} pages needed OCR")
    print(df.groupby("year").agg(rows=("serial", "size"), suffix_rate=("name_has_suffix", "mean")).round(2).T.to_string())
