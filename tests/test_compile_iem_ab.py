import pytest

from compile_iem_ab import norm_date


@pytest.mark.parametrize("raw,out", [
    ("3/7/2021", "03/07/2021"),
    ("03-07-2021", "03/07/2021"),
    (" 3/7/2021 ", "03/07/2021"),
    ("3 July 2021", "03/07/2021"),
    pytest.param("2021-07-03", "03/07/2021", marks=pytest.mark.xfail(strict=True, reason=(
        "dateutil dayfirst=True reads ISO YYYY-MM-DD as YYYY-DD-MM (-> 07/03/2021). Unconfirmed as a live bug: "
        "the repo's parsers emit d/m/yyyy, which the regex branch handles before dateutil is reached."))),
    (None, None),
    (float("nan"), None),
    ("not a date", None),
])
def test_norm_date_day_first(raw, out):
    assert norm_date(raw) == out
