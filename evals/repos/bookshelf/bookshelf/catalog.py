"""The catalog: every book the library owns, and which ones are on loan."""

from .models import Book, Loan


class Catalog:
    def __init__(self, books: list[Book] | None = None, loans: list[Loan] | None = None):
        self.books: list[Book] = list(books or [])
        self.loans: list[Loan] = list(loans or [])

    def __len__(self) -> int:
        return len(self.books)

    def __iter__(self):
        return iter(self.books)

    def add(self, title: str, author: str, year: int) -> Book:
        title, author = title.strip(), author.strip()
        if not title:
            raise ValueError("title must not be empty")
        if not author:
            raise ValueError("author must not be empty")
        if not 0 < year <= 9999:
            raise ValueError(f"year out of range: {year}")

        book = Book(id=self._next_id(), title=title, author=author, year=year)
        self.books.append(book)
        return book

    def get(self, book_id: int) -> Book:
        for book in self.books:
            if book.id == book_id:
                return book
        raise KeyError(f"no book with id {book_id}")

    def remove(self, book_id: int) -> Book:
        book = self.get(book_id)
        if not book.available:
            raise ValueError(f"'{book.title}' is on loan and cannot be removed")
        self.books.remove(book)
        return book

    def _next_id(self) -> int:
        return max((b.id for b in self.books), default=0) + 1
