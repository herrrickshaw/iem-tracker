#!/usr/bin/env python3
"""
Coordinate-based record parser for the SIA/IMO-layout DPIIT IEM Part A lists.

Layout (x positions in PDF points, stable across the SIA/IMO-era files):
  serial      x < 85        integer, starts a new record
  company     85  .. 218    company name + address (may wrap over several lines)
  district/   218 .. 300    first line = district, second line = state
  state
  item        300 .. 412    item of manufacture (wraps)
  capacity    412 .. 478    numeric capacity, then unit words
  iem         478 ..        IEM number "NNN/N/SIA/IM O/YYYY," and date dd/mm/yyyy

Records are assembled across page breaks because the serial number keeps counting.
Each record also keeps the raw line text for auditing.
"""
import glob
import re
import sys
from pathlib import Path

import pymupdf

HEADER_WORDS = {"Sr.", "No.", "Company", "Name", "and", "Address", "District", "State",
                "Item", "of", "Manufacture", "Capacity", "Unit", "IEM", "No", "Date",
                "Sr", "STATEMENT", "ON", "STATE", "WISE", "DETAILED", "BREAK-UP", "FOR",
                "INDUSTRIAL", "ENTREPRENUERS", "MEMORANDUM", "(IEM)", "IN", "THE", "MONTH",
                "Product", "Sl."}

COL = {"serial": (0, 85), "company": (85, 218), "dist_state": (218, 300),
       "item": (300, 412), "capacity": (412, 478), "iem": (478, 9999)}

IEM_RE = re.compile(r"(\d{1,5})\s*/\s*([NP])\s*/\s*SIA\s*/\s*IM\s*O\s*/\s*(\d{4})")
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
CAP_RE = re.compile(r"^[\d,]+(?:\.\d+)?$")


def col_of(x):
    for name, (lo, hi) in COL.items():
        if lo <= x < hi:
            return name
    return "iem"


def parse_pdf(path):
    doc = pymupdf.open(path)
    records = []
    current = None
    for pno, page in enumerate(doc, 1):
        words = page.get_text("words")
        # group words into lines by y (rounded), keep x order
        lines = {}
        for w in words:
            key = round(w[1] / 2.0)
            lines.setdefault(key, []).append(w)
        for key in sorted(lines):
            lw = sorted(lines[key], key=lambda w: w[0])
            first = lw[0]
            # new record: integer serial in the serial column
            if col_of(first[0]) == "serial" and first[4].isdigit():
                # skip header rows that happen to start with a number (rare)
                current = {"serial": int(first[4]), "page": pno, "company": [], "district_state": [],
                           "item": [], "capacity": [], "iem": [], "raw": []}
                records.append(current)
                lw = lw[1:]
            if current is None:
                continue
            if all(w[4] in HEADER_WORDS for w in lw if w[0] > 0):
                continue
            for w in lw:
                c = col_of(w[0])
                if c == "serial":
                    continue  # stray number in serial column on a continuation line
                current.get(c if c != "dist_state" else "district_state", []).append(w[4])
            current["raw"].append(" ".join(w[4] for w in lw))
    return records


def finalize(rec):
    text_iem = " ".join(rec["iem"])
    m = IEM_RE.search(text_iem)
    iem_no = iem_year = None
    if m:
        iem_no, iem_year = int(m.group(1)), int(m.group(3))
    dm = DATE_RE.search(text_iem)
    cap = rec["capacity"]
    cap_num, cap_unit = None, None
    if cap:
        if CAP_RE.match(cap[0]):
            cap_num = cap[0].replace(",", "")
            cap_unit = " ".join(cap[1:]) or None
        else:
            cap_unit = " ".join(cap)
    dist_state = rec["district_state"]
    district = dist_state[0] if dist_state else None
    state = " ".join(dist_state[1:]) if len(dist_state) > 1 else None
    return {
        "serial": rec["serial"],
        "company_and_address": " ".join(rec["company"]),
        "district": district,
        "state": state,
        "item_of_manufacture": " ".join(rec["item"]),
        "capacity": cap_num,
        "capacity_unit": cap_unit,
        "iem_no": iem_no,
        "iem_year": iem_year,
        "filing_date": dm.group(0) if dm else None,
        "source_page": rec["page"],
    }


if __name__ == "__main__":
    f = sys.argv[1]
    recs = [finalize(r) for r in parse_pdf(f)]
    print(len(recs), "records")
    for r in recs[:8]:
        print(r)
