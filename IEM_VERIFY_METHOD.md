# IEM Part A/B -> operational-company check (token-efficient)

Goal: for every company in the IEM Part A (intent) and Part B (commenced production) lists, get a CIN and a
current MCA status, then label it operational / active / inactive / unmapped.

Principle: code does 90%+ of the work for zero tokens. LLM agents only see the ambiguous residue, in batches of 40,
with up to 3 candidates each. Never let an agent read a PDF, a list, or a whole company page.

## Pipeline (`iem_verify.py`; state in `iem_verify.duckdb`; every stage resumable)

| # | Stage | Tokens | What it does |
|---|---|---|---|
| 0 | `extract` | 0 | Part A rows -> distinct company, legal-suffix name cut, state, key. A quality gate drops wrap fragments. |
| 1 | `partb` | 0 | Name 3-word shingle found in any Part B list = production-commenced evidence. |
| 2 | `master` + `match` | 0 | Exact key, then state-blocked fuzzy match (rapidfuzz) against the MCA master. Auto-accept only if score >= 96 and margin >= 4. |
| 3 | `web` | 0 | curl a free site by name slug (1 req/s, cached). Auto-accept ONLY if the CIN state code equals the IEM state. State-less hits are parked as candidates. |
| 4 | `batches`, agents, `ingest` | small LLM | Residue only. Haiku-class agents, 40 companies per call, JSON verdicts. Only conf=high is ingested. |
| 5 | `report` | 0 | Verdict column + IEM_operational_status.xlsx. |

Verdict: MCA status Active and Part B hit = OPERATIONAL. Active only = ACTIVE (registered, no Part B yet).
Strike Off / Amalgamated / Under Liquidation / Dissolved = INACTIVE. Dormant = DORMANT. No CIN = UNMAPPED.

## Source reachability from this machine (tested 2026-10-09)

| Source | Result |
|---|---|
| mca.gov.in, zaubacorp.com, thecompanycheck.com | 403 (matches the 2026-07 note) |
| quickcompany.in/company/<slug> | 200, gives CIN and Status. Used by `web`. |
| tofler.in, falconebiz.com | 200, but no usable CIN from plain curl search |
| instafinancials.com | search 403; company pages surface through WebSearch |
| data.gov.in OGD "Company Master Data" | Best bulk source (about 3.6M companies: CIN, name, status, ROC, class). Needs a free data.gov.in API key; the API host did not respond from here. |

## Part A re-parse (done): parse_partA_coords.py

Company names are now read from the company column only, using per-file column edges (empty-gutter detection, with header
fallback) and a separate rule for files where the serial number sits mid-record. Result, written to partA_coords.parquet:

| Check | Before (old table) | After |
|---|---|---|
| Distinct names that pass the quality gate | 4,469 of 15,925 | 8,566 of 9,214 |
| Names with a legal suffix, 2022 / 2023 | not measured | 97% / 96% |
| 2022 records vs Annual Report (1,185) | 2,151 | 1,183 |
| 2023 records vs Annual Report (1,267) | not measured | 1,258 |
| Companies with a known state | about 3.1k | 8,688 |

Known gaps: 211 of 338 Part A PDFs are not parsed. Most are 2001-2017 (older layouts) plus four early-2018 files in a text-dump
format and two 2023 files. 2024 covers January to August only. 2018-2020 IEM numbers are often missing, which does not affect
company matching. 2020-04, 2020-06, 2020-12 and 2021-03 to 2021-06 monthly lists are absent from the local set. 101 records in
mid-serial files could not be resolved and keep a weaker name.

## OCR and the older files (extract_partA_text_layout.py)

All 211 unparsed Part A PDFs turned out to have a text layer, so OCR is not what unlocks them. The 2001-2018 lists are
fixed-width text with the company name leading each record, parsed with `pdftotext -layout`. The script still includes an OCR
fallback (pdftoppm then the tesseract command line) for any page with no text layer; 28 near-empty pages used it. The OCR path
was tested by rasterising a text page: it reads names correctly but adds noise such as "LT1D", so OCR names need fuzzy matching.
Output: `partA_text_layout.parquet`, 62,215 unique records, 37,124 distinct names (30,889 with a legal suffix). The two 2023
cumulative files use a third layout and are excluded.

## Registry matching (registry_match.py)

Exact-name lookup against the data.gov.in company-master API, newest filings first, resumable. A hit is accepted only if the
returned name equals the query and the CIN state code equals the IEM state. Same name in several states with no state to
separate them is AMBIGUOUS, never guessed. Tested end to end against a local mock of the API (match, state mismatch, no match);
**not tested against the live API**, which refused connections from this Mac. Typos and renamed companies stay UNMATCHED and fall
to the fuzzy and agent stages above.

## Part B parsed, matched first, then Part A (2026-10-09)

- Part B parsers: `extract_partA_text_layout.py` (2001-2017 text lists, now also reads Memorandum No/Date and production start) and
  `parse_partB_new.py` (2018+ nine-column lists) feed `parse_partB_all.py`, which writes `partB_all.parquet`:
  19,913 records, 12,708 distinct names. Each record keeps the Part A memorandum number and date.
- Part A to Part B link: Part B memorandum no+date equals Part A acknowledgement no+date. 60% of Part B records link this way and
  names agree on 91% of linked pairs. Weaker fallback: same name in a later Part B.
- Matching rule, two tiers: tier 1 = exact-name page whose CIN state equals the IEM state; tier 2 = exact-name page with a different
  registered-office state, accepted only if the private/public class in the CIN agrees with the name. The IEM state is the PLANT state,
  the CIN state is the registered office, so multi-state companies (Varun Beverages: 20+ plant states, one Delhi CIN) are tier 2.
  Tier 2 carries homonym risk; it is labelled `web-slug-name-only` and scored 70.
- `iem web` is threaded (shared rate limit, default 4 req/s), tries the suffix-implied page first, never refetches a page, and is resumable.
  The offline `match` stage indexes the master by (state, name) and matched 150 of 150 hidden answers in about 1 s for 34,000 companies.

## data.gov.in API: why it fails here and the fix

The server accepts the TCP connection and closes it right after the TLS hello, for curl, OpenSSL, Python and Chrome-impersonating
clients alike, even without SNI. That is an IP-level block, not a client or fingerprint problem. This Mac's traffic exits through
NordVPN (default route on utun4, exit Singapore, AS212238 Datacamp), and Indian government gateways commonly drop non-Indian and
data-centre addresses. Fix: disconnect NordVPN, or use its India location, or split-tunnel the terminal, then rerun
`iem ogd` / `iem registry`. One 2026 write-up also reports that the gateway firewall rejects default Python request headers, so
send a descriptive User-Agent. Sources: dev.to "Scraping Indian Government Open Data in 2026", github.com/addypy/datagovindia (issue 9).

## Findings that still hold

1. A name alone is not identity. The rule "CIN state code must equal IEM state" is kept for every auto-accept. State is now
   known for 94% of companies, so most web hits can be auto-accepted rather than parked.
2. Free sites show "Last Balance Sheet: Defaulting" for UltraTech and Shree Cement alike. Ignore it as evidence.
3. Part B name hits: 1,473 of 9,214 companies (3-word shingle match; names under 3 key words are skipped).

## MCA master from data.gov.in (the bulk source)

- Resource: "Registrars of Companies (RoC)-wise Company Master Data", id 4dbe5667-7b6b-41d7-82af-211562424d9a.
  Fields: CIN, company name, status, class, date of registration. Filter by state.
- **The dataset says "data up to 3 November 2023".** Status is about three years stale. Use it to obtain CINs, then re-check
  status only for the IEM-matched companies from a fresher source. Do not report its "Active" as current.
- api.data.gov.in refused connections (port 443) from this Mac, in both the shell and the browser pane. Run `ogd_fetch_master.py`
  on another network or in Colab with `OGD_KEY` set. The state-wise "Preview & Download" on the website is the manual alternative.

## Agent prompt (one call per batch file, about 40 companies, cheapest model)

    For each JSON line in <batch file> return one JSON line {"id","cin","status","evidence_url","conf"}.
    Inputs: name, state, item of manufacture, up to 3 candidate CINs.
    Rules: pick a candidate only if name AND state AND business plausibly agree. Otherwise web-search
    "<name> <state> CIN" and read search snippets from zaubacorp, tofler, instafinancials or MCA pages. Never guess a CIN.
    conf="high" only if the CIN's 2-letter state code matches the state and the registered name matches after
    removing Pvt/Ltd/Private/Limited. Otherwise cin=null, conf="low". JSON lines only, no prose.

Budget: about 45 tokens in and 35 out per company. Residual companies are cut into batches of 40 (see llm_batches/). Cap 5 concurrent agents per wave,
and stop a wave if low-confidence exceeds 60%.
