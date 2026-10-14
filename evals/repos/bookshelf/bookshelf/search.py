"""Finding books by title, author or year."""


def by_title(books, query: str) -> list:
    """Books whose title contains `query`, ignoring case."""
    needle = query.strip().lower()
    return [b for b in books if needle in b.title.lower()]


def by_author(books, name: str) -> list:
    """Books whose author contains `name`, ignoring case."""
    needle = name.strip().lower()
    return [b for b in books if needle in b.author.lower()]


def by_year(books, start: int | None = None, end: int | None = None) -> list:
    """Books published from `start` to `end`, both included. Either may be None."""
    return [b for b in books
            if (start is None or b.year >= start) and (end is None or b.year <= end)]


def search(books, title=None, author=None, year_from=None, year_to=None) -> list:
    """Apply every filter that is given, keeping the catalog's order."""
    results = list(books)
    if title:
        results = by_title(results, title)
    if author:
        results = by_author(results, author)
    if year_from is not None or year_to is not None:
        results = by_year(results, year_from, year_to)
    return results
