"""The things a catalog keeps track of: books and loans."""

from dataclasses import asdict, dataclass
from datetime import date


@dataclass
class Book:
    id: int
    title: str
    author: str
    year: int
    available: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Book":
        return cls(id=int(data["id"]), title=data["title"], author=data["author"],
                   year=int(data["year"]), available=bool(data.get("available", True)))


@dataclass
class Loan:
    book_id: int
    borrower: str
    checked_out: date
    due: date

    def to_dict(self) -> dict:
        return {"book_id": self.book_id, "borrower": self.borrower,
                "checked_out": self.checked_out.isoformat(), "due": self.due.isoformat()}

    @classmethod
    def from_dict(cls, data: dict) -> "Loan":
        return cls(book_id=int(data["book_id"]), borrower=data["borrower"],
                   checked_out=date.fromisoformat(data["checked_out"]),
                   due=date.fromisoformat(data["due"]))
