from autotrader.filters import apply, check, is_significant_drop
from autotrader.listing import Listing


def car(**kw):
    base = dict(id="1", url="u", title="2021 BMW M5 Competition", year=2021,
                make="BMW", model="M5", price=98995, mileage_km=52000,
                seller="Big Dealer Inc", color="Alpine White")
    base.update(kw)
    return Listing(**base)


def test_no_filters_keeps_everything():
    assert check(car(), None).keep and check(car(), {}).keep


def test_price_and_year_bounds():
    assert not check(car(), {"max_price": 90000}).keep
    assert not check(car(), {"min_price": 120000}).keep
    assert not check(car(), {"min_year": 2022}).keep
    assert not check(car(), {"max_year": 2020}).keep
    assert check(car(), {"min_price": 90000, "max_price": 100000, "min_year": 2020}).keep


def test_a_rejection_explains_itself():
    verdict = check(car(), {"max_price": 90000})
    assert "$98,995" in verdict.reason and "$90,000" in verdict.reason


def test_cars_with_no_price_are_kept_unless_asked_otherwise():
    assert check(car(price=None), {"max_price": 90000}).keep
    assert not check(car(price=None), {"require_price": True}).keep


def test_keyword_rules():
    assert not check(car(), {"exclude_keywords": ["competition"]}).keep
    assert not check(car(), {"include_keywords": ["manual"]}).keep
    assert check(car(), {"include_keywords": ["competition", "manual"]}).keep


def test_keyword_matching_ignores_case():
    assert not check(car(), {"exclude_keywords": ["COMPETITION"]}).keep


def test_seller_exclusion_is_a_substring_match():
    assert not check(car(), {"exclude_sellers": ["big dealer"]}).keep
    assert check(car(), {"exclude_sellers": ["other dealer"]}).keep


def test_blank_filter_entries_are_ignored():
    assert check(car(), {"exclude_keywords": ["", "  "], "include_keywords": []}).keep


def test_apply_splits_and_reports_reasons():
    kept, dropped = apply([car(id="1"), car(id="2", price=200000)], {"max_price": 100000})
    assert [l.id for l in kept] == ["1"]
    assert dropped[0][0].id == "2" and "above maximum" in dropped[0][1]


def test_a_drop_must_clear_both_thresholds():
    assert is_significant_drop(100000, 95000, 1.0, 250)
    assert not is_significant_drop(100000, 99900, 1.0, 250)   # too small in dollars
    assert not is_significant_drop(100000, 99500, 1.0, 250)   # too small in percent
    assert not is_significant_drop(100000, 105000, 1.0, 250)  # not a drop
    assert not is_significant_drop(0, 0, 1.0, 250)
