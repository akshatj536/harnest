CI is failing on main. This is the output of `python -m pytest -q`:

```
..............................F......                                    [100%]
=================================== FAILURES ===================================
________________________ test_title_search_ignores_case ________________________

    def test_title_search_ignores_case():
>       assert titles(by_title(make_books(), "dune")) == ["Dune", "Dune Messiah"]
E       AssertionError: assert [] == ['Dune', 'Dune Messiah']
E
E         Right contains 2 more items, first extra item: 'Dune'
E         Use -v to get more diff

tests/test_search.py:23: AssertionError
=========================== short test summary info ============================
FAILED tests/test_search.py::test_title_search_ignores_case - AssertionError:...
1 failed, 36 passed in 0.07s
```

Please make the test suite pass again. Fix the code, not the tests.
