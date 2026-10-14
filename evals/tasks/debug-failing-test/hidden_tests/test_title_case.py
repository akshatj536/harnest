import pytest

from bookshelf.catalog import Catalog
from bookshelf.search import by_title, search


def make_books():
    catalog = Catalog()
    catalog.add("Dune", "Frank Herbert", 1965)
    catalog.add("Dune Messiah", "Frank Herbert", 1969)
    catalog.add("Emma", "Jane Austen", 1815)
    catalog.add("The Left Hand of Darkness", "Ursula K. Le Guin", 1969)
    return catalog.books


def titles(books):
    return [b.title for b in books]


@pytest.mark.parametrize("query,expected", [
    ("DUNE", ["Dune", "Dune Messiah"]),
    ("dUnE mEsSiAh", ["Dune Messiah"]),
    ("hand of dark", ["The Left Hand of Darkness"]),
    ("THE LEFT HAND OF DARKNESS", ["The Left Hand of Darkness"]),
    ("Emma", ["Emma"]),
    ("zzz", []),
])
def test_title_search_ignores_case(query, expected):
    assert titles(by_title(make_books(), query)) == expected


def test_search_title_filter_ignores_case():
    assert titles(search(make_books(), title="the LEFT", year_to=1970)) == ["The Left Hand of Darkness"]
