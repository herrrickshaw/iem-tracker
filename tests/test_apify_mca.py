"""apify_mca: API helpers and the budgeted batch loop against a fake Apify service (no token, no network)."""
import csv
import sys

import pandas as pd
import pytest

import apify_mca as am


class Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._b


class FakeApify:
    """Routes the three Apify endpoints the script uses. Each run adds `cost_per_run` to the account's monthly usage."""

    def __init__(self, records, start_spend=10.0, cost_per_run=0.25, drop=lambda attempt, cin: False, final_status="SUCCEEDED",
                 polls=("RUNNING", "SUCCEEDED")):
        self.records, self.spend, self.cost, self.drop = records, start_spend, cost_per_run, drop
        self.final_status, self.polls = final_status, list(polls)
        self.runs, self.requests, self.attempt_of, self.attempts = [], [], {}, {}

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.requests.append((method, url, headers, timeout, kw))
        path = url.split("/v2/", 1)[1]
        if path == "users/me/limits":
            return Resp({"data": {"current": {"monthlyUsageUsd": self.spend}}})
        if path.startswith("acts/") and method == "POST":
            body = kw["json"]
            self.runs.append(body)
            self.spend += self.cost
            rid = f"run{len(self.runs)}"
            self.attempt_of[rid] = body
            return Resp({"data": {"id": rid, "defaultDatasetId": f"ds{len(self.runs)}"}})
        if path.startswith("actor-runs/"):
            st = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            if st == self.final_status or not self.polls:
                st = self.final_status if st in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT") else st
            return Resp({"data": {"status": st, "usageTotalUsd": 0.2}})
        if path.startswith("datasets/"):
            run = self.runs[int(path.split("/")[1][2:]) - 1]
            out = []
            for c in run["cins"]:
                self.attempts[c] = self.attempts.get(c, 0) + 1
                if c in self.records and not self.drop(self.attempts[c], c):
                    out.append(self.records[c])
            return Resp(out)
        raise AssertionError(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "BASE", tmp_path)
    monkeypatch.setattr(am, "OUT", tmp_path / "apify_mca.csv")
    monkeypatch.setattr(am.time, "sleep", lambda s: None)
    monkeypatch.setenv("APIFY_TOKEN", "TOK")
    return tmp_path


def filings(base, rows):
    pd.DataFrame(rows, columns=["cin", "month", "support_candidate"]).to_parquet(base / "iem_partA_filings_linked.parquet")


def rec(cin, **kw):
    d = {"cin": cin, "name": cin + " LTD", "status": "Active", "dateOfLastAGM": "06/30/2025", "balanceSheetDate": "03/31/2025",
         "underCIRP": False, "annualReturnDefaulter": False, "balanceSheetDefaulter": False, "extra_field": "dropme"}
    d.update(kw)
    return d


def run(monkeypatch, fake, *argv):
    monkeypatch.setattr(am.requests, "request", fake.request)
    monkeypatch.setattr(sys, "argv", ["apify_mca.py", *argv])
    am.main()


def out_rows(base):
    return list(csv.DictReader(open(base / "apify_mca.csv")))


# --------------------------------------------------------------------------- helpers
def test_api_sends_bearer_token_and_default_get(monkeypatch):
    fake = FakeApify({})
    monkeypatch.setattr(am.requests, "request", fake.request)
    am.api("users/me/limits", "TOK")
    method, url, headers, timeout, kw = fake.requests[0]
    assert (method, url, timeout) == ("GET", "https://api.apify.com/v2/users/me/limits", 60)
    assert headers == {"Authorization": "Bearer TOK"} and kw == {}


def test_spend_reads_monthly_usage(monkeypatch):
    monkeypatch.setattr(am.requests, "request", FakeApify({}, start_spend=3.5).request)
    assert am.spend("T") == 3.5


def test_run_batch_posts_expected_body_and_polls_until_terminal(env, monkeypatch):
    fake = FakeApify({"C1": rec("C1")}, polls=("RUNNING", "RUNNING", "SUCCEEDED"))
    monkeypatch.setattr(am.requests, "request", fake.request)
    status, cost, items = am.run_batch("T", ["C1"])
    assert status == "SUCCEEDED" and cost == 0.2 and [i["cin"] for i in items] == ["C1"]
    body = fake.runs[0]
    assert body == {"mode": "company", "cins": ["C1"], "includeDirectors": False, "directorLeads": False, "maxCompanies": 1,
                    "companyStatuses": [], "proxyConfiguration": {"useApifyProxy": True}}
    posts = [r for r in fake.requests if r[0] == "POST"]
    assert posts[0][1].endswith(f"acts/{am.ACT}/runs")
    polls = [r for r in fake.requests if "actor-runs/" in r[1]]
    assert len(polls) == 3                                     # RUNNING, RUNNING, SUCCEEDED
    ds = [r for r in fake.requests if "datasets/" in r[1]][0]
    assert ds[4]["params"] == {"format": "json", "clean": "1"}


@pytest.mark.parametrize("final", ["FAILED", "ABORTED", "TIMED-OUT"])
def test_run_batch_stops_polling_on_any_terminal_status(env, monkeypatch, final):
    fake = FakeApify({}, polls=(final,), final_status=final)
    monkeypatch.setattr(am.requests, "request", fake.request)
    assert am.run_batch("T", ["C1"])[0] == final


def test_run_batch_maxcompanies_matches_batch_size(env, monkeypatch):
    fake = FakeApify({})
    monkeypatch.setattr(am.requests, "request", fake.request)
    am.run_batch("T", ["A", "B", "C"])
    assert fake.runs[0]["maxCompanies"] == 3


# --------------------------------------------------------------------------- main
def test_main_requires_token(env, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN")
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, FakeApify({}))
    assert "APIFY_TOKEN" in str(e.value.code)


def test_main_fetches_newest_first_in_batches_and_writes_csv(env, monkeypatch, capsys):
    filings(env, [("C_OLD", "2020-01", True), ("C_NEW", "2022-06", True), ("C_MID", "2021-03", True), ("C_NEW", "2019-01", True)])
    fake = FakeApify({c: rec(c) for c in ("C_OLD", "C_NEW", "C_MID")})
    run(monkeypatch, fake, "--batch", "2", "--budget", "100")
    assert [r["cins"] for r in fake.runs] == [["C_NEW", "C_MID"], ["C_OLD"]]
    rows = out_rows(env)
    assert [r["cin"] for r in rows] == ["C_NEW", "C_MID", "C_OLD"]
    assert list(rows[0]) == am.FIELDS                                   # extra_field dropped, header = FIELDS
    assert rows[0]["status"] == "Active" and rows[0]["underCIRP"] == "False"
    out = capsys.readouterr().out
    assert "3 CINs ranked; 0 cached; account spend so far $10.000; budget $100.0" in out
    assert "pass 1 batch 1: SUCCEEDED, 2/2 records" in out
    assert "pass 1 batch 2: SUCCEEDED, 1/1 records" in out


def test_main_candidates_flag_restricts_to_support_candidates_with_cin(env, monkeypatch):
    filings(env, [("C1", "2022-01", True), ("C2", "2022-02", False), (None, "2022-03", True)])
    fake = FakeApify({"C1": rec("C1"), "C2": rec("C2")})
    run(monkeypatch, fake, "--candidates", "--budget", "100")
    assert [r["cins"] for r in fake.runs] == [["C1"]]


def test_main_without_candidates_flag_takes_every_filing_with_cin(env, monkeypatch):
    filings(env, [("C1", "2022-01", True), ("C2", "2022-02", False), (None, "2022-03", True)])
    fake = FakeApify({"C1": rec("C1"), "C2": rec("C2")})
    run(monkeypatch, fake, "--budget", "100")
    assert [r["cins"] for r in fake.runs] == [["C2", "C1"]]


def test_main_resumes_skipping_cached_cins_without_second_header(env, monkeypatch):
    filings(env, [("C1", "2022-01", True), ("C2", "2022-02", True)])
    run(monkeypatch, FakeApify({"C1": rec("C1"), "C2": rec("C2")}, polls=("SUCCEEDED",)), "--batch", "1", "--limit", "1", "--budget", "100", "--passes", "1")
    assert [r["cin"] for r in out_rows(env)] == ["C2"]
    fake = FakeApify({"C1": rec("C1"), "C2": rec("C2")}, polls=("SUCCEEDED",))
    run(monkeypatch, fake, "--budget", "100")
    assert [r["cins"] for r in fake.runs] == [["C1"]]
    assert [r["cin"] for r in out_rows(env)] == ["C2", "C1"]
    assert (env / "apify_mca.csv").read_text().count("cin,name") == 1


def test_main_limit_caps_cins_per_pass(env, monkeypatch):
    filings(env, [(f"C{i}", f"2022-0{i}", True) for i in range(1, 5)])
    fake = FakeApify({f"C{i}": rec(f"C{i}") for i in range(1, 5)})
    run(monkeypatch, fake, "--limit", "2", "--budget", "100", "--passes", "1")
    assert [c for r in fake.runs for c in r["cins"]] == ["C4", "C3"]


def test_main_stops_when_next_batch_would_exceed_budget(env, monkeypatch, capsys):
    filings(env, [(f"C{i}", f"2022-0{i}", True) for i in range(1, 6)])
    # each run costs 0.25; a batch starts only while used + 0.10 <= budget
    fake = FakeApify({f"C{i}": rec(f"C{i}") for i in range(1, 6)}, cost_per_run=0.25, polls=("SUCCEEDED",))
    run(monkeypatch, fake, "--batch", "1", "--budget", "0.6")
    # used: 0 -> run (0.25) -> 0.25+0.1<=0.6 run (0.5) -> 0.5+0.1=0.6 not > 0.6 -> run (0.75) -> 0.85 > 0.6 stop
    assert len(fake.runs) == 3
    out = capsys.readouterr().out
    assert "stopping: spent $0.750 of $0.6" in out
    assert len(out_rows(env)) == 3


@pytest.mark.parametrize("budget,runs", [(0.1, 1), (0.09, 0)])
def test_main_budget_boundary_is_strict_greater(env, monkeypatch, budget, runs):
    filings(env, [("C1", "2022-01", True), ("C2", "2022-02", True)])
    fake = FakeApify({"C1": rec("C1"), "C2": rec("C2")}, cost_per_run=0.0, polls=("SUCCEEDED",))
    run(monkeypatch, fake, "--budget", str(budget))
    # a batch starts when used + 0.10 <= budget; used is 0 here, so 0.10 <= 0.1 runs and 0.10 > 0.09 does not
    assert len(fake.runs) == runs


def test_main_retries_missing_cins_in_later_passes(env, monkeypatch, capsys):
    filings(env, [(f"C{i}", f"2022-{i:02d}", True) for i in range(1, 11)])
    fake = FakeApify({f"C{i}": rec(f"C{i}") for i in range(1, 11)}, polls=("SUCCEEDED",),
                     drop=lambda attempt, cin: attempt == 1 and cin in {"C1", "C2"})      # first attempt drops two
    run(monkeypatch, fake, "--budget", "100", "--passes", "3")
    assert len(out_rows(env)) == 10
    assert len(fake.runs) == 2 and set(fake.runs[1]["cins"]) == {"C1", "C2"}
    out = capsys.readouterr().out
    assert "pass 1: 8 new records; 2 CINs still missing" in out
    assert "pass 2: 2 new records; 0 CINs still missing" in out


def test_main_stops_passes_when_a_pass_yields_under_five_percent(env, monkeypatch):
    filings(env, [(f"C{i}", f"2022-{i:02d}", True) for i in range(1, 11)])
    fake = FakeApify({}, polls=("SUCCEEDED",))                                  # actor returns nothing
    run(monkeypatch, fake, "--budget", "100", "--passes", "4")
    assert len(fake.runs) == 1                                                   # pass 1 got 0 < 5% of 10 -> break


def test_main_continues_when_pass_yield_is_exactly_five_percent(env, monkeypatch):
    filings(env, [(f"C{i}", f"2021-{i:02d}", True) for i in range(1, 11)] + [(f"D{i}", f"2020-{i:02d}", True) for i in range(1, 11)])
    # 20 CINs, one batch of 50. Pass 1 returns exactly 1 record = 5% of 20 -> NOT below 5% -> pass 2 runs
    fake = FakeApify({"C1": rec("C1")}, polls=("SUCCEEDED",))
    run(monkeypatch, fake, "--budget", "100", "--passes", "3")
    assert len(fake.runs) == 2                      # pass 2 ran (and then yielded 0 < 5% so there is no pass 3)


def test_main_zero_passes_does_nothing(env, monkeypatch):
    filings(env, [("C1", "2022-01", True)])
    fake = FakeApify({"C1": rec("C1")})
    run(monkeypatch, fake, "--passes", "0")
    assert fake.runs == [] and out_rows(env) == []


def test_main_everything_cached_makes_no_runs(env, monkeypatch):
    filings(env, [("C1", "2022-01", True)])
    (env / "apify_mca.csv").write_text("cin,name\nC1,x\n")
    fake = FakeApify({"C1": rec("C1")})
    run(monkeypatch, fake, "--budget", "100")
    assert fake.runs == []
