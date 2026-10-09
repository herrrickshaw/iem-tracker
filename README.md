# IEM Tracker

Tracks India's Industrial Entrepreneur Memoranda (IEM). It reads DPIIT's monthly Part A (intention to build) and Part B
(production started) company lists, links the two, and identifies each company in the company register (CIN and status),
so that filings which never reached Part B can be separated into companies that no longer exist and companies that are still active.

Background article (methods, results, caveats): <https://masaladeutsch.blogspot.com/2026/10/indias-factory-intentions-20012024-how_0101046393.html>

## What it does

| Stage | Script | Notes |
|---|---|---|
| Read Part A, 2018-2024 | `parse_partA_coords.py` | PDF word positions; column edges from empty gutters; handles mid-record serial numbers |
| Read Part A, 2001-2018 | `extract_partA_text_layout.py` | text layer via `pdftotext -layout`; OCR fallback (`pdftoppm` + `tesseract`) for pages with no text |
| Read Part B | `parse_partB_new.py`, `parse_partB_all.py` | 2018+ nine-column lists and older text lists; keeps the Part A memorandum no/date |
| Company table + matching | `iem_verify.py` | name + state index; threaded web lookup; two-tier matching (see below) |
| Registry sources | `registry_match.py`, `ogd_fetch_master.py` | data.gov.in company master API (needs `OGD_KEY`; host was unreachable from the author's network) |
| Independent checks | `gleif_check.py`, `gleif_bulk.py`, `gleif_join.py`, `cin_status_check.py`, `apify_mca.py` | GLEIF LEI register for all of India, joined offline; per-CIN status sources |
| Part A vs Part B | `compile_iem_ab.py` | links Part B to Part A by memorandum no + date, then by name; lists filings with no Part B |
| Article section | `make_article_update.py` | builds the blog section from the data; never publishes |
| CLI | `iem` | `iem help`, `iem status`, `iem parse`, `iem build`, `iem web`, `iem registry`, `iem report` |

## Matching rule

The IEM state is the **plant** state; the CIN state is the **registered-office** state. So:

1. **Tier 1:** exact legal-name page whose CIN state equals the IEM state.
2. **Tier 2:** exact-name page with a different state, accepted only if the company type in the CIN (private or public) agrees with the name.
3. A match is rejected if the CIN shows incorporation after the filing year.

Validation against GLEIF on a 300-company sample (152 hold an LEI): about 12% of tier 1 and 24% of tier 2 matches pointed to a
different company with the same name. Treat a single identification as a lead, not a fact. Status from free company sites is not
an official certificate, and the data.gov.in master is labelled "data up to 3 November 2023".

## Running it

```bash
pip install -r requirements.txt
# put DPIIT's monthly lists in PartA/ and PartB/ (SIA Statistics: "List of IEM Filed" / "List of IEM Implemented")
./iem parse && ./iem build        # parse PDFs, build the company table
./iem web 500                     # identify the 500 most-filed companies (resumable, rate-limited)
python3 compile_iem_ab.py         # Part A vs Part B workbook
export OGD_KEY=...                # data.gov.in key, environment only
export APIFY_TOKEN=...            # optional
```

Keys are read from environment variables only and are never written to disk. Data files (PDFs, parquet, DuckDB, xlsx, csv) are
git-ignored on purpose: the PDFs are re-fetchable, and the company tables contain details scraped from third-party sites.

## Status (10 October 2026)

- Part A: 21,954 records (coordinate parser) + 72,322 (text layout); Part B: 19,913 records, 12,708 distinct names.
- Companies identified: 23,017 of 38,586 usable names (18,473 distinct CINs).
- Full GLEIF India register (401,638 LEI records) being joined for independent confirmation.
- Known gaps: Part A 2024 only to August; 2020-04/06/12 and 2021-03 to 06 lists absent locally; 2023 cumulative files not parsed.

See `IEM_VERIFY_METHOD.md` for the full method, source reachability tests and findings.
