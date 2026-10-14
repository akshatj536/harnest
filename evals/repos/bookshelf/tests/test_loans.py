from datetime import date

import pytest

from bookshelf.catalog import Catalog
from bookshelf.loans import LoanError, checkout, return_book

MARCH_1 = date(2026, 3, 1)


def make_catalog():
    catalog = Catalog()
    catalog.add("Dune", "Frank Herbert", 1965)
    return catalog


def test_checkout_sets_due_date_and_marks_book_out():
    catalog = make_catalog()
    loan = checkout(catalog, 1, "Ana", MARCH_1)
    assert loan.due == date(2026, 3, 15)
    assert not catalog.get(1).available


def test_cannot_check_out_twice():
    catalog = make_catalog()
    checkout(catalog, 1, "Ana", MARCH_1)
    with pytest.raises(LoanError):
        checkout(catalog, 1, "Ben", MARCH_1)


def test_on_time_return_costs_nothing():
    catalog = make_catalog()
    checkout(catalog, 1, "Ana", MARCH_1)
    assert return_book(catalog, 1, date(2026, 3, 15)) == 0
    assert catalog.get(1).available


def test_three_days_late_costs_75_cents():
    catalog = make_catalog()
    checkout(catalog, 1, "Ana", MARCH_1)
    assert return_book(catalog, 1, date(2026, 3, 18)) == 75


def test_amount_owed_is_capped():
    catalog = make_catalog()
    checkout(catalog, 1, "Ana", MARCH_1)
    assert return_book(catalog, 1, date(2026, 6, 1)) == 1000


def test_returning_a_book_that_is_not_out_fails():
    with pytest.raises(LoanError):
        return_book(make_catalog(), 1, MARCH_1)
