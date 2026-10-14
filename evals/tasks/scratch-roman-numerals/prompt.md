Create a module `roman.py` in the project root with two functions for converting between integers and Roman numerals.

- `to_roman(n: int) -> str` returns the standard uppercase Roman numeral for `n`, using subtractive notation (IV = 4, IX = 9, XL = 40, XC = 90, CD = 400, CM = 900). For example, `to_roman(1994)` returns `"MCMXCIV"`. It supports 1 to 3999 inclusive and raises `ValueError` for any other integer.
- `from_roman(s: str) -> int` does the reverse: `from_roman("MCMXCIV")` returns `1994`. It accepts only canonical numerals, meaning exactly the strings `to_roman` can produce. For anything else it raises `ValueError`, for example `""`, `"iv"`, `"IIII"`, `"IC"` or `"ABC"`.

Use only the Python standard library.
