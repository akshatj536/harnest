"""Library rules: how long a loan lasts and what an overdue return costs."""

from datetime import date, timedelta

LOAN_DAYS = 14
DAILY_RATE_CENTS = 25
MAX_CHARGE_CENTS = 1000


def due_date(checked_out: date) -> date:
    """When a book checked out on `checked_out` has to be back."""
    return checked_out + timedelta(days=LOAN_DAYS)


def overdue_charge(days_overdue: int) -> int:
    """Cents owed for a book that comes back `days_overdue` days after its due date.

    On-time returns cost nothing. The amount grows by DAILY_RATE_CENTS a day and
    never goes above MAX_CHARGE_CENTS.
    """
    if days_overdue <= 0:
        return 0
    return min(days_overdue * DAILY_RATE_CENTS, MAX_CHARGE_CENTS)
