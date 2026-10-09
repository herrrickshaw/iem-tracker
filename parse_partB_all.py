#!/usr/bin/env python3
"""Run every Part B PDF through the right parser and write one table, partB_all.parquet.
   old text layout (2001-2017, 'PRODUCTION MEMORANDUM' header) -> extract_partA_text_layout.parse_file
   new 9-column layout (2018+)                                  -> parse_partB_new.parse_pdf (already in partB_new.parquet)
   anything the new parser skipped                              -> text-layout fallback
"""
import glob, os, subprocess
from pathlib import Path
import pandas as pd
import extract_partA_text_layout as T

BASE = Path(__file__).resolve().parent
new = pd.read_parquet(BASE / "partB_new.parquet"); done = set(new.source_file)
rows = []
for f in sorted(glob.glob(str(BASE / "PartB/*.pdf"))):
    n = os.path.basename(f)
    if n in done: continue
    r, _ = T.parse_file(f)
    for x in r: x["source"] = "text-layout"
    rows += r
old = pd.DataFrame(rows)
new["source"] = "new-format"
keep = ["company_name", "state", "month", "mem_no", "mem_date", "commenced", "source_file", "source", "name_has_suffix"]
old["state"] = old.state_header
allb = pd.concat([new[keep], old[[c for c in keep if c in old.columns]]], ignore_index=True)
allb = allb[allb.company_name.str.len() >= 3]
allb = allb.sort_values("source_file").drop_duplicates(["month", "company_name", "mem_no", "mem_date"])
allb.to_parquet(BASE / "partB_all.parquet", index=False)
allb["year"] = allb.month.str[:4]
print(len(allb), "unique Part B records;", allb.company_name.nunique(), "distinct names;",
      "old-format", int((allb.source == "text-layout").sum()), "| files covered", allb.source_file.nunique(), "of", len(glob.glob(str(BASE / "PartB/*.pdf"))))
print(allb.groupby("year").agg(rows=("company_name", "size"), suffix=("name_has_suffix", "mean"), memno=("mem_no", lambda x: x.notna().mean()),
                               state=("state", lambda x: x.notna().mean())).round(2).T.to_string())
