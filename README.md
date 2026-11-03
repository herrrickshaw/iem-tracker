# IEM Tracker

Tracks India's Industrial Entrepreneur Memoranda (IEM) for Indian industrial policy research. It reads DPIIT's monthly Part A
(intention to build) and Part B (production started) company lists, links the two, and identifies each company in the company
register (CIN and status), so filings that never reached Part B can be split into companies that no longer exist and
companies that are still active. Outputs are local parquet/DuckDB tables and an Excel workbook (`IEM_operational_status.xlsx`).

> Status: research-only, maintained. Not investment advice. Data files are git-ignored on purpose: the PDFs are re-fetchable, and the
> company tables hold details scraped from third-party sites. No licence file yet — all rights reserved.

Background article (methods, results, caveats): <https://masaladeutsch.blogspot.com/2026/10/indias-factory-intentions-20012024-how_0101046393.html>
New to the repo? See [How to use this repo with AI](HOW_TO_USE_WITH_AI.md).

## What it does
- Parses Part A lists 2018-2024 (PDF word coordinates) and 2001-2018 (`pdftotext -layout`, OCR fallback via `pdftoppm` + `tesseract`).
- Parses Part B lists (2018+ nine-column and older text lists), keeping the Part A memorandum number/date.
- Builds a table of distinct companies and identifies each one in the company register with a two-tier name + state rule.
- Cross-checks against the complete GLEIF India register (offline join) and per-CIN status sources.
- Links Part B to Part A (memorandum no + date, then name) and lists filings with no Part B.
- Generates the blog-article section from the data (never publishes).

## Quick start
```bash
git clone https://github.com/herrrickshaw/iem-tracker.git
cd iem-tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# put DPIIT's monthly lists in PartA/ and PartB/ (SIA Statistics: "List of IEM Filed" / "List of IEM Implemented")
./iem parse && ./iem build        # parse PDFs, build the company table
./iem web 500                     # identify the 500 most-filed companies (resumable, rate-limited)
python3 compile_iem_ab.py         # Part A vs Part B workbook
```
`./iem help` lists every sub-command (`status`, `parse`, `build`, `master`, `ogd`, `match`, `web`, `registry`, `batches`, `ingest`, `report`, `all`).

## How it works
| Stage | Module | Output |
|---|---|---|
| Read Part A, 2018-2024 | `parse_partA_coords.py` | `partA_coords.parquet` (column edges from empty gutters; handles mid-record serial numbers) |
| Read Part A, 2001-2018 | `extract_partA_text_layout.py` | `partA_text_layout.parquet` |
| Read Part B | `parse_partB_new.py`, `parse_partB_all.py` | `partB_all.parquet` |
| Company table + matching | `iem_verify.py` (`extract`, `partb`, `master`, `match`, `web`, `batches`, `ingest`, `report`) | `iem_verify.duckdb`, `IEM_operational_status.xlsx` |
| Registry sources | `registry_match.py`, `ogd_fetch_master.py` | data.gov.in company master (needs `OGD_KEY`; host was unreachable from the author's network) |
| Independent checks | `gleif_check.py`, `gleif_bulk.py`, `gleif_join.py`, `cin_status_check.py`, `apify_mca.py` | GLEIF LEI join; per-CIN status |
| Part A vs Part B | `compile_iem_ab.py` | linked workbook |
| Article section | `make_article_update.py` | blog section text |
| CLI | `iem` | wraps the stages above |

## Matching rule
The IEM state is the **plant** state; the CIN state is the **registered-office** state. So:

1. **Tier 1:** exact legal-name page whose CIN state equals the IEM state.
2. **Tier 2:** exact-name page with a different state, accepted only if the company type in the CIN (private or public) agrees with the name.
3. A match is rejected if the CIN shows incorporation after the filing year.

Validation against the complete GLEIF India register (12,668 matched companies hold an LEI): about 6% of tier 1 and 16% of tier 2
matches pointed to a different company with the same name. Treat a single identification as a lead, not a fact. Status from free company sites is not
an official certificate, and the data.gov.in master is labelled "data up to 3 November 2023".

The tier decision is the pure function `iem_verify.web_match_tier(cin, name, state)`, covered by characterisation tests.

## Repository layout
| Path | Purpose |
|---|---|
| `iem` | single CLI entry point (run from anywhere; always works in the repo folder) |
| `iem_verify.py` | name normalisation, company table, matching stages, report |
| `parse_*.py`, `extract_partA_text_layout.py` | PDF parsers |
| `registry_match.py`, `ogd_fetch_master.py`, `gleif_*.py`, `cin_status_check.py`, `apify_mca.py` | registry and independent-check sources |
| `compile_iem_ab.py`, `make_article_update.py` | Part A/B workbook and article section |
| `IEM_VERIFY_METHOD.md`, `ANALYSIS.md` | full method, source reachability tests and findings |
| `tests/` | pytest unit + characterisation tests (offline) |
| `PartA/`, `PartB/`, `*.parquet`, `*.duckdb`, `*.xlsx` | local data, git-ignored |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| DPIIT SIA Statistics | monthly "List of IEM Filed" / "Implemented" PDFs | manual download into `PartA/`, `PartB/` | government publication |
| data.gov.in Company Master | CIN, name, status | API with `OGD_KEY` (`ogd_fetch_master.py`, `registry_match.py`) | labelled "data up to 3 November 2023"; api.data.gov.in was unreachable from the author's network |
| GLEIF | India LEI register | bulk download, joined offline | open data |
| QuickCompany and similar free sites | CIN and status by name slug | scraped via `curl`, 1 request/s, cached | scraped; not an official certificate; not republished |
| Apify MCA actor | optional MCA record evidence | `APIFY_TOKEN` | optional, third-party |

## Configuration
- `OGD_KEY`: data.gov.in API key (needed for `iem ogd` / `iem registry`).
- `APIFY_TOKEN`: optional, for `apify_mca.py`.
- Keys are read from environment variables only and are never written to disk or committed.

## Testing
```bash
pip install pytest ruff
pytest -q
ruff check .
```
Tests never touch the network.

## Limitations and known issues
- Validation against the complete GLEIF India register (12,668 matched companies hold an LEI): about 6% of tier 1 and 16% of tier 2 matches pointed to a different company with the same name. Treat a single identification as a lead, not a fact.
- Status from free company sites is not an official certificate; the data.gov.in master is labelled "data up to 3 November 2023".
- Part A 2024 only to August; 2020-04/06/12 and 2021-03 to 06 lists absent locally; 2023 cumulative files not parsed.
- Snapshot (10 October 2026): Part A 21,954 records (coordinate parser) + 72,322 (text layout); Part B 19,913 records, 12,708 distinct names; 23,017 of 38,586 usable names identified (18,473 distinct CINs); 2,056 further companies identified from the 401,638-record GLEIF register (94% of name+state and 84% of name-only matches confirmed on the 55% of companies that hold an LEI).

## Licence
No licence file yet — all rights reserved.
