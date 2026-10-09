# IEM (Industrial Entrepreneurs Memorandum) analysis

Source of numbers: DPIIT Annual Report 2025-26 appendices (`~/Dropbox/IEM_State_Sector_Analysis.xlsx`). 2025 is partial (Part A/states to Dec; sector Part B to Nov). Investment in Rs crore.

## All-India trend, 2021–2025 (Part A = intentions filed, Part B = implemented)

| Year | Part A count | Part A inv (Rs cr) | Part B count | Part B inv (Rs cr) |
|---|---|---|---|---|
| 2021 | 1,489 | 771,428 | 812 | 347,978 |
| 2022 | 1,185 | 424,101 | 916 | 250,064 |
| 2023 | 1,267 | 510,852 | 968 | 276,925 |
| 2024 | 986 | 602,082 | 1,165 | 493,765 |
| 2025* | 702 | 530,416 | 998 | 583,096 |

## Top 10 states by Part A investment intention, 2021–2025

| State | Part A count | Part A inv (Rs cr) |
|---|---|---|
| Maharashtra | 1,119 | 682,104 |
| Odisha | 240 | 560,153 |
| Gujarat | 873 | 371,363 |
| Karnataka | 390 | 199,364 |
| Andhra Pradesh | 216 | 164,467 |
| Chhattisgarh | 227 | 122,028 |
| Rajasthan | 218 | 104,668 |
| UttarPradesh | 355 | 101,656 |
| Madhya Pradesh | 338 | 94,889 |
| TamilNadu | 374 | 90,467 |

## Top 10 states by Part B implemented investment, 2021–2025

| State | Part B count | Part B inv (Rs cr) |
|---|---|---|
| Gujarat | 893 | 660,254 |
| Odisha | 149 | 336,487 |
| Maharashtra | 893 | 237,988 |
| Andhra Pradesh | 251 | 140,445 |
| Uttar Pradesh | 364 | 73,954 |
| Rajasthan | 180 | 71,373 |
| Madhya Pradesh | 373 | 64,828 |
| Punjab | 153 | 57,293 |
| Tamil Nadu | 251 | 44,627 |
| Chhattisgarh | 166 | 43,550 |

## Top 10 sectors by Part A investment intention, 2021–2025

| Sector | Part A count | Part A inv (Rs cr) |
|---|---|---|
| Others | 637 | 726,228 |
| Electricals Equipment | 564 | 561,177 |
| Chemicals (Except Fertilizers) | 501 | 415,090 |
| Metallurgical Industries | 410 | 406,090 |
| Fermentation Industries | 519 | 103,689 |
| Transportation Industry | 316 | 99,436 |
| Fertilizers | 47 | 62,058 |
| Food Processing Industry | 535 | 55,025 |
| Cement & Gypsum Products | 108 | 52,085 |
| Textiles | 347 | 44,535 |

## Top 10 sectors by Part B implemented investment, 2021–2025

| Sector | Part B count | Part B inv (Rs cr) |
|---|---|---|
| Metallurgical Industries | 378 | 469,693 |
| Chemicals (Except Fertilizers) | 583 | 286,230 |
| Electricals Equipment | 416 | 189,548 |
| Glass | 27 | 131,208 |
| Food Processing Industry | 606 | 102,893 |
| Cement & Gypsum Products | 175 | 101,094 |
| Fuels | 14 | 88,451 |
| Misc. Industries | 279 | 82,669 |
| Transportation Industry | 233 | 81,764 |
| Others | 315 | 67,996 |

## Data coverage and reliability

- Monthly list PDFs catalogued: 695 catalogue rows, 678 with a local PDF converted to .md.
- Dropbox 2024 'Detailed Report' copies: 21 files collapse to 9 byte-identical canonicals (the rest are `__suffix` duplicates).
- Google Drive: no IEM/DPIIT-named files found by name search.
- **Record-level parsing is NOT validated.** `part_a_records` (6,482 rows) disagrees with the Annual Report (e.g. 2022: 2,151 parsed vs 1,185 reported), and the IEM-number anchor only matches 64 of 678 documents because the list layout changes across eras. Use the Annual Report tables for counts; treat `part_a_records` as unvalidated.
