#!/usr/bin/env python3
"""
Parser for DPIIT IEM Part A monthly lists using poppler `pdftotext -layout`.

pdftotext keeps each company record on lines that start with its serial number
and keeps the columns at stable character positions, so records can be split by
column position taken from the header line.

Columns are located per file from the header words:
  Sr. No. | Company Name and Address | District | State | Item of Manufacture | Capacity | Unit | IEM No and Date
A new record starts at a line whose first token is an integer.
"""
import re
import subprocess
import tempfile
from pathlib import Path

HEADER_KEYS = ["Company", "District", "State", "Item", "Capacity", "IEM"]
IEM_RE = re.compile(r"(\d{1,5})\s*/\s*([NP])\s*/\s*SIA\s*/\s*IM\s*O\s*/?\s*(\d{4})")
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
START_RE = re.compile(r"^\s{0,8}(\d{1,5})\s{2,}\S")


def pdftotext_layout(pdf):
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as t:
        out = t.name
    subprocess.run(["/opt/homebrew/bin/pdftotext", "-layout", str(pdf), out], check=True, timeout=600)
    text = Path(out).read_text(errors="replace")
    Path(out).unlink(missing_ok=True)
    return text


def header_columns(lines):
    """Return char positions of the column starts, from the first header block."""
    for line in lines[:60]:
        if "Company" in line and "District" in line and "Item" in line:
            # positions of each header word on the line
            pos = {}
            for key in ["Company", "District", "State", "Item", "Capacity", "Unit", "IEM"]:
                m = re.search(key, line)
                if m:
                    pos[key] = m.start()
            return pos
    return None


def split_record(line, cols):
    """Slice a record line into fields by column start positions."""
    c = cols
    def seg(a, b):
        return line[a:b].strip() if a is not None and b is not None else ""
    sr_end = c["Company"]
    dist_end = c.get("State", c["Item"] - 1)
    if "State" in c:
        state_start = c["State"]
    else:
        state_start = dist_end
    item_start = c["Item"]
    cap_start = c.get("Capacity", item_start + 60)
    iem_start = c.get("IEM", cap_start + 30)
    return {
        "company_and_address": seg(sr_end, c["District"]),
        "district": seg(c["District"], state_start),
        "state_text": seg(state_start, item_start),
        "item": seg(item_start, cap_start),
        "capacity_text": seg(cap_start, iem_start),
        "iem_text": line[iem_start:].strip(),
    }


def parse(pdf):
    lines = pdftotext_layout(pdf).splitlines()
    cols = header_columns(lines)
    if not cols:
        return None, "no header"
    records, current = [], None
    for line in lines:
        m = START_RE.match(line)
        if m and len(line) > cols["Company"] - 2:
            # serial at start of a record line (guard against stray numbers)
            if int(m.group(1)) >= 1:
                current = {"serial": int(m.group(1)), "lines": []}
                records.append(current)
        if current is None:
            continue
        if not line.strip():
            continue
        if re.search(r"Sr\. No|Company Name and|Item of|MEMORANDUM|IN THE MONTH|STATEMENT ON", line):
            continue
        current["lines"].append(line)
    out = []
    for r in records:
        # build a full-width record from its lines, splitting each line by column position
        fields = {k: [] for k in ["company_and_address", "district", "state_text", "item", "capacity_text", "iem_text"]}
        for ln in r["lines"]:
            parts = split_record(ln.ljust(max(cols.values()) + 60), cols)
            for k in fields:
                if parts[k]:
                    fields[k].append(parts[k])
        text_iem = " ".join(fields["iem_text"])
        im = IEM_RE.search(re.sub(r"\s+", " ", text_iem))
        dm = DATE_RE.search(text_iem)
        out.append({
            "serial": r["serial"],
            "company_and_address": " ".join(fields["company_and_address"]),
            "district": " ".join(fields["district"]),
            "state": " ".join(fields["state_text"]),
            "item_of_manufacture": " ".join(fields["item"]),
            "capacity": " ".join(fields["capacity_text"]),
            "iem_no": int(im.group(1)) if im else None,
            "iem_year": int(im.group(3)) if im else None,
            "filing_date": dm.group(0) if dm else None,
        })
    return out, None


if __name__ == "__main__":
    import sys
    recs, err = parse(sys.argv[1])
    print(err or f"{len(recs)} records")
    for r in (recs or [])[:4]:
        print(r)
