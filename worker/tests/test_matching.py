from decimal import Decimal

from conftest import mk_listing, mk_search

from sourcer import matching as m
from sourcer.matching import Blocklist, Matcher, count_brands, load_brands
from sourcer.models import BlockedSeller, SearchOverride
from sourcer.normalize import normalize

BRANDS = load_brands()


def matcher(entries=()):
    return Matcher(BRANDS, Blocklist(entries), spam_threshold=3)


def test_plain_match():
    assert matcher().evaluate(mk_listing("1"), mk_search()) is None


def test_personal_block_only_affects_owner():
    entry = BlockedSeller(marketplace="grailed", seller_id="seller-1", scope="personal", owner_id="u1")
    mt = matcher([entry])
    assert mt.evaluate(mk_listing("1"), mk_search(user_id="u1")) == m.BLOCKED
    assert mt.evaluate(mk_listing("1"), mk_search(user_id="u2")) is None


def test_shared_block_affects_everyone_and_is_marketplace_scoped():
    entry = BlockedSeller(marketplace="grailed", seller_id="seller-1", scope="shared")
    mt = matcher([entry])
    assert mt.evaluate(mk_listing("1"), mk_search(user_id="u9")) == m.BLOCKED
    assert mt.evaluate(mk_listing("1", marketplace="ebay"), mk_search()) is None


def test_block_matches_id_not_username():
    entry = BlockedSeller(marketplace="grailed", seller_id="seller-1", scope="shared")
    renamed = mk_listing("1", seller_name="new_name")
    assert matcher([entry]).evaluate(renamed, mk_search()) == m.BLOCKED


def test_name_entry_matches_username():
    entry = BlockedSeller(marketplace="grailed", seller_id="name:SomeOne", scope="shared")
    assert matcher([entry]).evaluate(mk_listing("1"), mk_search()) == m.BLOCKED


def test_include_terms_all_required():
    s = mk_search(include_terms=["pour homme", "coat"])
    assert matcher().evaluate(mk_listing("1"), s) is None
    s = mk_search(include_terms=["pour homme", "jacket"])
    assert matcher().evaluate(mk_listing("1"), s) == m.INCLUDE


def test_exclude_terms_and_toggle():
    listing = mk_listing("1", title="Yohji Yamamoto pour homme coat replica")
    s = mk_search(exclude_terms=["replica"])
    assert matcher().evaluate(listing, s) == m.EXCLUDED
    assert matcher().evaluate(listing, mk_search(exclude_terms=["replica"], exclude_enabled=False)) is None


def test_exclude_per_marketplace_override():
    listing = mk_listing("1", title="Yohji coat replica")
    s = mk_search(
        exclude_terms=["replica"],
        overrides={"grailed": SearchOverride(marketplace="grailed", exclude_enabled=False)},
    )
    assert matcher().evaluate(listing, s) is None
    s2 = mk_search(overrides={"grailed": SearchOverride(marketplace="grailed", extra_exclude_terms=["coat"])})
    assert matcher().evaluate(mk_listing("1"), s2) == m.EXCLUDED


def test_spam_filter_counts_distinct_brand_families():
    spam = mk_listing("1", title="YOHJI RICK OWENS CDG MARGIELA jacket")
    assert matcher().evaluate(spam, mk_search()) == m.SPAM
    assert matcher().evaluate(spam, mk_search(spam_filter_enabled=False)) is None


def test_spam_filter_does_not_flag_one_house_with_sublines():
    text = normalize("Yohji Yamamoto Pour Homme Y's ヨウジヤマモト coat")
    assert count_brands(text, BRANDS) == 1
    assert matcher().evaluate(mk_listing("1", title="Yohji Yamamoto Y's coat"), mk_search()) is None


def test_spam_threshold_configurable():
    two = mk_listing("1", title="Yohji Yamamoto x Comme des Garcons coat")
    assert Matcher(BRANDS, spam_threshold=3).evaluate(two, mk_search()) is None
    assert Matcher(BRANDS, spam_threshold=2).evaluate(two, mk_search()) == m.SPAM


def test_price_filter_converts_currency():
    # 100 USD ~= 130 SGD
    s = mk_search(min_price=Decimal("120"), max_price=Decimal("140"), currency="SGD")
    assert matcher().evaluate(mk_listing("1"), s) is None
    assert matcher().evaluate(mk_listing("1", price=Decimal("200")), s) == m.PRICE
    # JPY listing: 10000 JPY ~= 88 SGD
    jpy = mk_listing("1", price=Decimal("10000"), currency="JPY")
    assert matcher().evaluate(jpy, s) == m.PRICE


def test_price_filter_keeps_unknown_price():
    s = mk_search(max_price=Decimal("10"))
    assert matcher().evaluate(mk_listing("1", price=None), s) is None


def test_size_filter():
    s = mk_search(sizes=["3", "L"])
    assert matcher().evaluate(mk_listing("1", size="L"), s) is None
    assert matcher().evaluate(mk_listing("1", size="ｌ"), s) is None  # full-width
    assert matcher().evaluate(mk_listing("1", size="M"), s) == m.SIZE
    assert matcher().evaluate(mk_listing("1", size=None), s) is None  # unknown size kept


def test_pipeline_order_blocked_before_spam():
    entry = BlockedSeller(marketplace="grailed", seller_id="seller-1", scope="shared")
    spam = mk_listing("1", title="YOHJI RICK OWENS CDG MARGIELA")
    assert matcher([entry]).evaluate(spam, mk_search()) == m.BLOCKED
