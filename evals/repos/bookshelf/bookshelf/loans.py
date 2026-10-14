"""Checking books out and back in."""

from datetime import date

from . import policies
from .catalog import Catalog
from .models import Loan


class LoanError(Exception):
    pass


def checkout(catalog: Catalog, book_id: int, borrower: str, today: date) -> Loan:
    book = catalog.get(book_id)
    if not book.available:
        raise LoanError(f"'{book.title}' is already checked out")

    borrower = borrower.strip()
    if not borrower:
        raise LoanError("borrower must not be empty")

    book.available = False
    loan = Loan(book_id=book.id, borrower=borrower, checked_out=today,
                due=policies.due_date(today))
    catalog.loans.append(loan)
    return loan


def return_book(catalog: Catalog, book_id: int, today: date) -> int:
    """Check a book back in. Returns what the borrower owes, in cents."""
    loan = next((entry for entry in catalog.loans if entry.book_id == book_id), None)
    if loan is None:
        raise LoanError(f"book {book_id} is not checked out")

    catalog.loans.remove(loan)
    catalog.get(book_id).available = True
    return policies.overdue_charge((today - loan.due).days)
