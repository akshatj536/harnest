import json

import pytest

from bookshelf.cli import main

DUNE = {"title": "Dune", "author": "Frank Herbert", "year": 1965}
EMMA = {"title": "Emma", "author": "Jane Austen", "year": 1815}
MESSIAH = {"title": "Dune Messiah", "author": "Frank Herbert", "year": 1969}


@pytest.fixture
def db(tmp_path, capsys):
    path = tmp_path / "books.json"
    for book in (DUNE, EMMA, MESSIAH):
        assert main(["--db", str(path), "add", book["title"], book["author"], str(book["year"])]) == 0
    capsys.readouterr()
    return path


def run_json(capsys, db, *args):
    code = main(["--db", str(db), *args])
    out = capsys.readouterr().out
    assert code == 0
    return json.loads(out)   # fails if anything but JSON was printed


def test_list_json(db, capsys):
    assert run_json(capsys, db, "list", "--json") == [DUNE, EMMA, MESSIAH]


def test_list_json_follows_paging(db, capsys):
    assert run_json(capsys, db, "list", "--json", "--page", "2", "--per-page", "2") == [MESSIAH]


def test_search_json(db, capsys):
    assert run_json(capsys, db, "search", "--author", "herbert", "--json") == [DUNE, MESSIAH]


def test_search_json_with_no_matches(db, capsys):
    assert run_json(capsys, db, "search", "--title", "zzz", "--json") == []


def test_list_json_on_empty_catalog(tmp_path, capsys):
    assert run_json(capsys, tmp_path / "empty.json", "list", "--json") == []


def test_year_is_a_number(db, capsys):
    rows = run_json(capsys, db, "list", "--json")
    assert all(type(row["year"]) is int for row in rows)
