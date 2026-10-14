import pytest

from bookshelf.cli import main
from bookshelf.pagination import page_bounds, paginate

ITEMS = list(range(1, 11))   # ten items


@pytest.mark.parametrize("page,expected", [
    (1, [1, 2, 3]),
    (2, [4, 5, 6]),
    (4, [10]),
    (5, []),
])
def test_paginate(page, expected):
    assert paginate(ITEMS, page, 3) == expected


def test_default_page_size():
    items = list(range(25))
    assert paginate(items, 1) == list(range(10))
    assert paginate(items, 3) == list(range(20, 25))


def test_page_bounds():
    assert page_bounds(1, 3) == (0, 3)
    assert page_bounds(4, 3) == (9, 12)


@pytest.mark.parametrize("page", [0, -1])
def test_page_zero_is_still_rejected(page):
    with pytest.raises(ValueError):
        paginate(ITEMS, page, 3)


def test_cli_first_page(tmp_path, capsys):
    db = str(tmp_path / "books.json")
    for i in range(1, 11):
        assert main(["--db", db, "add", f"Book {i}", "Author", str(2000 + i)]) == 0
    capsys.readouterr()

    assert main(["--db", db, "list", "--page", "1", "--per-page", "3"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "  1  Book 1  (Author, 2001)",
        "  2  Book 2  (Author, 2002)",
        "  3  Book 3  (Author, 2003)",
        "Page 1 of 4",
    ]
