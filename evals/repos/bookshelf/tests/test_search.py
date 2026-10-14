from bookshelf.catalog import Catalog
from bookshelf.search import by_author, by_title, by_year, search


def make_books():
    catalog = Catalog()
    catalog.add("Dune", "Frank Herbert", 1965)
    catalog.add("Dune Messiah", "Frank Herbert", 1969)
    catalog.add("Emma", "Jane Austen", 1815)
    catalog.add("The Left Hand of Darkness", "Ursula K. Le Guin", 1969)
    return catalog.books


def titles(books):
    return [b.title for b in books]


def test_title_search_matches_part_of_the_title():
    assert titles(by_title(make_books(), "Messiah")) == ["Dune Messiah"]


def test_title_search_ignores_case():
    assert titles(by_title(make_books(), "dune")) == ["Dune", "Dune Messiah"]


def test_author_search_ignores_case():
    assert titles(by_author(make_books(), "le guin")) == ["The Left Hand of Darkness"]


def test_year_range_includes_both_ends():
    assert titles(by_year(make_books(), 1965, 1969)) == [
        "Dune", "Dune Messiah", "The Left Hand of Darkness"]


def test_search_combines_filters():
    assert titles(search(make_books(), title="Dune", year_from=1966)) == ["Dune Messiah"]


def test_search_without_filters_returns_everything():
    assert len(search(make_books())) == 4
