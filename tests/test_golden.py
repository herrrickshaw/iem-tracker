"""Characterisation tests: current pure functions must reproduce outputs recorded from the pre-refactor code."""
import json
from pathlib import Path

import pytest

import iem_verify as v
import registry_match as r

G = json.loads((Path(__file__).parent / "golden.json").read_text())


def test_key():
    assert {n: v.key(n) for n in G["key"]} == G["key"]


def test_split_name():
    assert {n: list(v.split_name(n)) for n in G["split_name"]} == G["split_name"]


def test_usable():
    assert {n: [v.usable(n, c) for c in (True, False)] for n in G["usable"]} == G["usable"]


def test_find_state():
    assert {t: v.find_state(t) for t in G["find_state"]} == G["find_state"]


def test_slug_variants():
    assert {n: v.slug_variants(n) for n in G["slug_variants"]} == G["slug_variants"]


def test_classify():
    for s, h, c, want in G["classify"]:
        assert v.classify(s, h, c) == want


def test_mca_variants():
    assert {n: r.mca_variants(n) for n in G["mca_variants"]} == G["mca_variants"]


@pytest.mark.parametrize("cin,name,state,want", G["tier"])
def test_two_tier_matching_is_unchanged(cin, name, state, want):
    assert v.web_match_tier(cin, name, state) == want
