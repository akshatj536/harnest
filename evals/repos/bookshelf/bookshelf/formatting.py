"""How books and money are shown in the terminal."""


def book_line(book) -> str:
    line = f"{book.id:>3}  {book.title}  ({book.author}, {book.year})"
    return line if book.available else line + "  [on loan]"


def book_table(books) -> str:
    books = list(books)
    if not books:
        return "No books."
    return "\n".join(book_line(b) for b in books)


def money(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"
