"""Saving the catalog to a JSON file and loading it back."""

import json
from pathlib import Path

from .catalog import Catalog
from .models import Book, Loan


def load(path) -> Catalog:
    """Read a catalog file. A missing file is an empty catalog."""
    path = Path(path)
    if not path.exists():
        return Catalog()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Catalog(books=[Book.from_dict(b) for b in data.get("books", [])],
                   loans=[Loan.from_dict(entry) for entry in data.get("loans", [])])


def save(catalog: Catalog, path) -> None:
    data = {"books": [b.to_dict() for b in catalog.books],
            "loans": [entry.to_dict() for entry in catalog.loans]}
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
