Please add a `--json` option to the `list` and `search` commands of the bookshelf command line.

With `--json`, the command prints only a JSON array, with no other text (not even the "Page 1 of N" line). The array has one object per book, and each object has exactly three keys: `title` (a string), `author` (a string) and `year` (an integer). It contains the same books, in the same order, as the command shows without `--json`, so `list --json --page 2` gives the books on page 2. When no books match, it prints `[]`.

Without `--json`, the output of both commands must stay exactly as it is now. Don't change or remove the existing tests; you can add new ones.
