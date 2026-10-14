Users report that the first page of results skips books.

To reproduce: add ten books in order ("Book 1" to "Book 10"), then run

    python -m bookshelf --db books.json list --page 1 --per-page 3

It prints Book 4, Book 5 and Book 6, but page 1 should show Book 1, Book 2 and Book 3. Pages are numbered from 1, as the README says.

Please find and fix the bug. Don't change or remove the existing tests; you can add new ones.
