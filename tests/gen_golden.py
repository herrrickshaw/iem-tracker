"""Regenerate tests/golden.json from a checkout of the ORIGINAL code: python tests/gen_golden.py <checkout-dir>."""
import json, re, sys
sys.path.insert(0, sys.argv[1])
import iem_verify as v, registry_match as r

def orig_tier(cin, nm, state):
    """Verbatim copy of the inline logic in the ORIGINAL stage_web (pre-refactor)."""
    want = "PTC" if re.search(r"\b(PVT|PRIVATE)\b|\(P\)", nm.upper()) else "PLC"
    class_ok = cin[12:15] in (want, "NPL", "GAP", "GOI", "FTC", "FLC", "PTC" if want == "PTC" else "PLC")
    if state and v.CIN_STATE.get(cin[6:8]) == state: return "tier1"
    elif state and class_ok: return "tier2"
    elif not state: return "parked"
    return None


NAMES = ["Tata Steel Ltd.", "ABC (P) LTD", "XYZ PVT.LTD", "Foods Private Limited", "Reliance Industries Limited",
         "A & B Co. Pvt Ltd", "Sun Pharma Limited, Mumbai", "THE Bharat Ltd", "SOLAR PROJECTS LIMITED", "Xy"]
TEXTS = ["Plot 5, Sanand, GUJARAT", "Cuttack, ODISHA", "Old ORISSA", "nowhere", "Pune, Maharashtra", "DELHI NCR"]
CINS = ["U12345MH2001PTC123456", "L12345GJ1995PLC123456", "U12345DL2010FTC123456", "U12345KA2010NPL123456", "U12345KA2010OPC123456"]
STATES = ["MAHARASHTRA", "GUJARAT", "KARNATAKA", None]
g = {
    "key": {n: v.key(n) for n in NAMES},
    "split_name": {n: list(v.split_name(n)) for n in NAMES},
    "usable": {n: [v.usable(n, c) for c in (True, False)] for n in NAMES},
    "find_state": {t: v.find_state(t) for t in TEXTS},
    "slug_variants": {n: v.slug_variants(n) for n in NAMES},
    "classify": [[s, h, c, v.classify(s, h, c)] for s in (None, "Active", "Strike Off", "Dormant under 455", "Weird")
                 for h in (True, False) for c in (None, "U1")],
    "mca_variants": {n: r.mca_variants(n) for n in NAMES},
    "tier": [[c, n, s, orig_tier(c, n, s)]
             for c in CINS for n in ("ABC PVT LTD", "XYZ LIMITED") for s in STATES],
}
json.dump(g, open("tests/golden.json", "w"), indent=1, sort_keys=True)
