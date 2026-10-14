"""Helpers from the old CSV-based catalog, kept around after the move to JSON."""

import csv
import io


def parse_csv_row(line: str) -> dict:
    row = next(csv.reader(io.StringIO(line)))
    title, author, year = (row + ["", "", ""])[:3]
    return {"title": title.strip(), "author": author.strip(), "year": int(year or 0)}


def slugify(text: str) -> str:
    return "-".join("".join(c.lower() if c.isalnum() else " " for c in text).split())
