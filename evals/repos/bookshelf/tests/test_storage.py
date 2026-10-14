from datetime import date

from bookshelf import storage
from bookshelf.catalog import Catalog
from bookshelf.loans import checkout


def test_missing_file_is_an_empty_catalog(tmp_path):
    assert len(storage.load(tmp_path / "nothing.json")) == 0


def test_round_trip_keeps_books_and_loans(tmp_path):
    catalog = Catalog()
    catalog.add("Dune", "Frank Herbert", 1965)
    catalog.add("Emma", "Jane Austen", 1815)
    checkout(catalog, 2, "Ana", date(2026, 3, 1))

    path = tmp_path / "books.json"
    storage.save(catalog, path)
    loaded = storage.load(path)

    assert [b.to_dict() for b in loaded.books] == [b.to_dict() for b in catalog.books]
    assert loaded.loans == catalog.loans
