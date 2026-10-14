"""Command line: python -m bookshelf [--db FILE] <command> ..."""

import argparse
import sys
from datetime import date

from . import formatting, loans, storage
from .pagination import DEFAULT_PER_PAGE, page_count, paginate
from .search import search


def _date(text: str) -> date:
    return date.fromisoformat(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bookshelf", description="A small library catalog.")
    parser.add_argument("--db", default="books.json", help="catalog file (default: books.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="add a book")
    p.add_argument("title")
    p.add_argument("author")
    p.add_argument("year", type=int)

    p = sub.add_parser("list", help="list books, one page at a time")
    p.add_argument("--page", type=int, default=1, help="page number, starting at 1")
    p.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE,
                   help=f"books per page (default {DEFAULT_PER_PAGE})")

    p = sub.add_parser("search", help="find books")
    p.add_argument("--title", help="part of the title, any case")
    p.add_argument("--author", help="part of the author's name, any case")
    p.add_argument("--year-from", type=int, help="published in or after this year")
    p.add_argument("--year-to", type=int, help="published in or before this year")

    p = sub.add_parser("checkout", help="lend a book")
    p.add_argument("book_id", type=int)
    p.add_argument("borrower")
    p.add_argument("--date", type=_date, help="YYYY-MM-DD (default: today)")

    p = sub.add_parser("return", help="take a book back")
    p.add_argument("book_id", type=int)
    p.add_argument("--date", type=_date, help="YYYY-MM-DD (default: today)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = storage.load(args.db)
    today = getattr(args, "date", None) or date.today()

    try:
        if args.command == "add":
            book = catalog.add(args.title, args.author, args.year)
            storage.save(catalog, args.db)
            print(f"Added {book.id}: {book.title}")

        elif args.command == "list":
            books = paginate(catalog.books, args.page, args.per_page)
            print(formatting.book_table(books))
            print(f"Page {args.page} of {page_count(len(catalog), args.per_page)}")

        elif args.command == "search":
            found = search(catalog.books, title=args.title, author=args.author,
                           year_from=args.year_from, year_to=args.year_to)
            print(formatting.book_table(found))

        elif args.command == "checkout":
            loan = loans.checkout(catalog, args.book_id, args.borrower, today)
            storage.save(catalog, args.db)
            print(f"Checked out to {loan.borrower}, due {loan.due.isoformat()}")

        elif args.command == "return":
            owed = loans.return_book(catalog, args.book_id, today)
            storage.save(catalog, args.db)
            print("Returned.")
            if owed:
                print(f"Late fee: {formatting.money(owed)}")

    except (KeyError, ValueError, loans.LoanError) as e:
        print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
        return 1

    return 0
