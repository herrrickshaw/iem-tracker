"""Pass 3 characterisation: parse_pdf, build_iem_db.main and compile_iem_ab.main on synthetic inputs (recorded from pre-split code)."""
import json
import os
from pathlib import Path

import pytest

import pass3_fixtures as fx

G = json.loads((Path(__file__).parent / "golden_pass3.json").read_text())


@pytest.fixture(autouse=True)
def _keep_home():
    h = os.environ.get("HOME")
    yield
    os.environ["HOME"] = h


def _norm(x):
    return json.loads(json.dumps(x, sort_keys=True, default=str))


def test_parse_pdf(tmp_path):
    assert _norm(fx.snap_parse(tmp_path)) == G["parse"]


def test_build_iem_db(tmp_path):
    assert _norm(fx.snap_build_db(tmp_path, tmp_path / "home")) == G["build_db"]


def test_compile_minimal(tmp_path):
    assert _norm(fx.snap_compile(tmp_path, False)) == G["compile_min"]


def test_compile_full(tmp_path):
    assert _norm(fx.snap_compile(tmp_path, True)) == G["compile_full"]
