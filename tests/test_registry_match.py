import pytest

from registry_match import mca_variants


@pytest.mark.parametrize("raw,first", [
    ("Alpha Steels Pvt. Ltd.", "ALPHA STEELS PRIVATE LIMITED"),
    ("Alpha Steels (P) Ltd", "ALPHA STEELS PRIVATE LIMITED"),
    ("Alpha Steels PVT LIMITED", "ALPHA STEELS PRIVATE LIMITED"),
    ("Alpha Steels Privated Limited", "ALPHA STEELS PRIVATE LIMITED"),
    ("Beta, Cement Ltd.", "BETA CEMENT LIMITED"),
])
def test_mca_variants_normalise_suffix(raw, first):
    assert mca_variants(raw)[0] == first


def test_mca_variants_ampersand_and_the():
    v = mca_variants("The A & B Mills Ltd")
    assert v == ["THE A & B MILLS LIMITED", "THE A AND B MILLS LIMITED", "A & B MILLS LIMITED"]
