import pytest

from bookshelf.pagination import page_bounds, page_count, paginate

ITEMS = list(range(1, 11))   # ten items


def test_first_page():
    assert paginate(ITEMS, 1, 3) == [1, 2, 3]


def test_second_page():
    assert paginate(ITEMS, 2, 3) == [4, 5, 6]


def test_last_page_is_partial():
    assert paginate(ITEMS, 4, 3) == [10]


def test_default_page_size_is_ten():
    assert paginate(list(range(25)), 1) == list(range(10))


def test_page_past_the_end_is_empty():
    assert paginate(ITEMS, 5, 3) == []


@pytest.mark.parametrize("page", [0, -1])
def test_pages_start_at_one(page):
    with pytest.raises(ValueError):
        page_bounds(page, 3)


def test_page_count():
    assert page_count(10, 3) == 4
    assert page_count(9, 3) == 3
    assert page_count(0, 3) == 1
