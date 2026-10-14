# bookshelf

A small library catalog with a command line. Books and loans are stored together in one JSON file.

## Usage

```bash
python -m bookshelf --db books.json add "Dune" "Frank Herbert" 1965
python -m bookshelf --db books.json list [--page N] [--per-page N]
python -m bookshelf --db books.json search [--title T] [--author A] [--year-from Y] [--year-to Y]
python -m bookshelf --db books.json checkout BOOK_ID BORROWER [--date YYYY-MM-DD]
python -m bookshelf --db books.json return BOOK_ID [--date YYYY-MM-DD]
```

- Pages are numbered from 1 and hold 10 books unless `--per-page` says otherwise.
- Title and author searches ignore case and match part of the text.
- A loan lasts 14 days. Returning a book after its due date costs a small amount per day, up to a cap.

## Development

```bash
python -m pytest
```
