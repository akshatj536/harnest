"""Splitting long lists into pages. Pages are numbered from 1."""

import math

DEFAULT_PER_PAGE = 10


def page_bounds(page: int, per_page: int) -> tuple[int, int]:
    """The [start, end) slice of the items on `page`.

    Pages are 1-indexed: page 1 holds the first `per_page` items.
    Raises ValueError if `page` or `per_page` is less than 1.
    """
    if page < 1:
        raise ValueError(f"page must be 1 or more, got {page}")
    if per_page < 1:
        raise ValueError(f"per_page must be 1 or more, got {per_page}")
    start = (page - 1) * per_page
    return start, start + per_page


def paginate(items, page: int, per_page: int = DEFAULT_PER_PAGE) -> list:
    """The items on a 1-indexed page. Pages past the end are empty."""
    start, end = page_bounds(page, per_page)
    return list(items)[start:end]


def page_count(total: int, per_page: int = DEFAULT_PER_PAGE) -> int:
    """How many pages `total` items fill. Always at least 1."""
    if per_page < 1:
        raise ValueError(f"per_page must be 1 or more, got {per_page}")
    return max(1, math.ceil(total / per_page))
