import pytest

from roman import from_roman, to_roman

KNOWN = [
    (1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"), (90, "XC"),
    (400, "CD"), (900, "CM"), (1994, "MCMXCIV"), (2024, "MMXXIV"), (3999, "MMMCMXCIX"),
]


@pytest.mark.parametrize("n,numeral", KNOWN)
def test_to_roman_known_values(n, numeral):
    assert to_roman(n) == numeral


@pytest.mark.parametrize("n,numeral", KNOWN)
def test_from_roman_known_values(n, numeral):
    assert from_roman(numeral) == n


def test_round_trip_every_value():
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n


def test_every_value_has_its_own_numeral():
    assert len({to_roman(n) for n in range(1, 4000)}) == 3999


@pytest.mark.parametrize("n", [0, -1, 4000, 10_000])
def test_to_roman_rejects_out_of_range(n):
    with pytest.raises(ValueError):
        to_roman(n)


@pytest.mark.parametrize("s", [
    "", "iv", "IIII", "IC", "VX", "IL", "VV", "XXXX", "MMMM", "ABC", "I V", "MCMXCIVI",
])
def test_from_roman_rejects_non_canonical(s):
    with pytest.raises(ValueError):
        from_roman(s)
