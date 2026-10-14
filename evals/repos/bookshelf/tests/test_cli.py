from bookshelf.cli import main


def run(capsys, db, *args):
    code = main(["--db", str(db), *args])
    out, err = capsys.readouterr()
    return code, out, err


def add_two(capsys, db):
    run(capsys, db, "add", "Dune", "Frank Herbert", "1965")
    run(capsys, db, "add", "Emma", "Jane Austen", "1815")


def test_add_prints_the_new_id(tmp_path, capsys):
    code, out, _ = run(capsys, tmp_path / "books.json", "add", "Dune", "Frank Herbert", "1965")
    assert code == 0
    assert out == "Added 1: Dune\n"


def test_list_shows_books_and_page(tmp_path, capsys):
    db = tmp_path / "books.json"
    add_two(capsys, db)
    code, out, _ = run(capsys, db, "list")
    assert code == 0
    assert out.splitlines() == [
        "  1  Dune  (Frank Herbert, 1965)",
        "  2  Emma  (Jane Austen, 1815)",
        "Page 1 of 1",
    ]


def test_list_empty_catalog(tmp_path, capsys):
    code, out, _ = run(capsys, tmp_path / "books.json", "list")
    assert code == 0
    assert out.splitlines() == ["No books.", "Page 1 of 1"]


def test_search_by_author(tmp_path, capsys):
    db = tmp_path / "books.json"
    add_two(capsys, db)
    code, out, _ = run(capsys, db, "search", "--author", "Austen")
    assert code == 0
    assert out.splitlines() == ["  2  Emma  (Jane Austen, 1815)"]


def test_late_return_prints_the_fee(tmp_path, capsys):
    db = tmp_path / "books.json"
    add_two(capsys, db)
    run(capsys, db, "checkout", "1", "Ana", "--date", "2026-03-01")
    code, out, _ = run(capsys, db, "return", "1", "--date", "2026-03-18")
    assert code == 0
    assert out.splitlines() == ["Returned.", "Late fee: $0.75"]


def test_unknown_book_is_an_error(tmp_path, capsys):
    code, _, err = run(capsys, tmp_path / "books.json", "checkout", "7", "Ana")
    assert code == 1
    assert "no book with id 7" in err
