"""One-off: convert the old books.csv (title,author,year) into books.json.

The CSV format was retired in version 1.0.
"""

import csv
import json
import sys


def migrate(src: str, dest: str) -> int:
    with open(src, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    books = [{"id": i, "title": t.strip(), "author": a.strip(), "year": int(y), "available": True}
             for i, (t, a, y) in enumerate(rows, start=1)]

    with open(dest, "w", encoding="utf-8") as f:
        json.dump({"books": books, "loans": []}, f, indent=2)
    return len(books)


if __name__ == "__main__":
    print(f"Migrated {migrate(sys.argv[1], sys.argv[2])} books")
