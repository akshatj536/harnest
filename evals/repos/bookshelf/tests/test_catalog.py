from datetime import date

import pytest

from bookshelf.catalog import Catalog
from bookshelf.loans import checkout


def test_add_assigns_increasing_ids():
    catalog = Catalog()
    dune = catalog.add("Dune", "Frank Herbert", 1965)
    emma = catalog.add("Emma", "Jane Austen", 1815)
    assert (dune.id, emma.id) == (1, 2)
    assert len(catalog) == 2


def test_add_strips_whitespace():
    book = Catalog().add("  Dune ", " Frank Herbert", 1965)
    assert (book.title, book.author) == ("Dune", "Frank Herbert")


@pytest.mark.parametrize("title,author,year", [
    ("", "Frank Herbert", 1965),
    ("Dune", "   ", 1965),
    ("Dune", "Frank Herbert", 0),
    ("Dune", "Frank Herbert", 10000),
])
def test_add_rejects_bad_input(title, author, year):
    with pytest.raises(ValueError):
        Catalog().add(title, author, year)


def test_get_unknown_id_raises_key_error():
    with pytest.raises(KeyError):
        Catalog().get(7)


def test_remove_returns_the_book():
    catalog = Catalog()
    catalog.add("Dune", "Frank Herbert", 1965)
    assert catalog.remove(1).title == "Dune"
    assert len(catalog) == 0


def test_cannot_remove_a_book_on_loan():
    catalog = Catalog()
    catalog.add("Dune", "Frank Herbert", 1965)
    checkout(catalog, 1, "Ana", date(2026, 3, 1))
    with pytest.raises(ValueError):
        catalog.remove(1)
