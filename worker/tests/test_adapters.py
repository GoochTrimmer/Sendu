import base64
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from conftest import load_fixture
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from sourcer.adapters.base import check_response
from sourcer.adapters.ebay import EbayAdapter
from sourcer.adapters.grailed import GrailedAdapter
from sourcer.adapters.mercari_jp import SEARCH_URL, DPoPSigner, MercariJPAdapter
from sourcer.adapters.yahoo_auctions import YahooAuctionsAdapter, YahooParseError
from sourcer.config import Settings
from sourcer.ratelimit import MarketplaceBlocked, TransientError


def test_ebay_parse():
    page = EbayAdapter.parse(load_fixture("ebay_search.json"))
    assert len(page.listings) == 2  # item without URL skipped
    first = page.listings[0]
    assert first.external_id == "v1|186543210001|0"
    assert first.price == Decimal("640.00") and first.currency == "USD"
    assert first.seller_id == "tokyo_archive_store"
    assert len(first.image_urls) == 4  # capped
    assert first.listed_at and first.listed_at.year == 2026
    assert page.next_cursor == "50"


def test_grailed_parse():
    page = GrailedAdapter.parse(load_fixture("grailed_search.json"))
    assert [x.external_id for x in page.listings] == ["61234501", "61234502", "61234503"]
    first = page.listings[0]
    assert first.seller_id == "998877" and first.seller_name == "archive_dealer"
    assert first.url == "https://www.grailed.com/listings/61234501"
    assert first.image_urls[0].endswith("/aaa") and len(first.image_urls) == 2  # cover not duplicated
    bumped = page.listings[2]
    # date_added index orders by created_at, so a bump doesn't change listed/sort time
    assert bumped.listed_at.day == 20 and bumped.sort_time == bumped.listed_at
    assert bumped.raw_status == "sold"
    assert page.next_cursor == "1"


def test_mercari_parse():
    page = MercariJPAdapter.parse(load_fixture("mercari_jp_search.json"))
    resurfaced, shop, auction, sold = page.listings
    assert resurfaced.url == "https://jp.mercari.com/item/m41118737938"
    assert resurfaced.price == Decimal("19800") and resurfaced.currency == "JPY"
    assert resurfaced.seller_id == "908278711" and resurfaced.size == "S"
    assert resurfaced.brand == "Yohji Yamamoto"
    assert resurfaced.image_urls[0].startswith("https://static.mercdn.net/item/detail/")  # photos first
    # Created Dec 2025 but updated today: listed_at = created, sort_time = updated
    assert resurfaced.listed_at.year == 2025 and resurfaced.sort_time.year == 2026
    assert shop.url.startswith("https://jp.mercari.com/shops/product/")
    assert shop.seller_id == "shop:shop123" and shop.seller_name is None  # sellerId "0" -> shop id
    assert shop.image_urls == ["https://assets.mercari-shops-static.com/xxx.jpg"]  # thumbnail fallback
    assert auction.price == Decimal("14999")
    assert sold.raw_status == "sold" and sold.sort_time == sold.listed_at
    assert page.next_cursor == "v1:1"


def test_mercari_body_sorts_newest_and_includes_auctions():
    body = MercariJPAdapter().build_body("yohji", None)
    cond = body["searchCondition"]
    assert (cond["sort"], cond["order"], cond["status"]) == ("SORT_CREATED_TIME", "ORDER_DESC", ["STATUS_ON_SALE"])
    assert body["withAuction"] is True and body["pageToken"] == ""


def test_yahoo_parse_skips_featured_and_uses_buy_now_price():
    page = YahooAuctionsAdapter.parse(load_fixture("yahoo_auctions_search.json"))
    assert [x.external_id for x in page.listings] == ["p1250735387", "x1250732563"]
    auc, fixed = page.listings
    assert auc.url == "https://auctions.yahoo.co.jp/jp/auction/p1250735387"
    assert auc.price == Decimal("21000") and auc.currency == "JPY"
    assert auc.seller_id == "D4Exxxxxxxxxxxxxxxxxxxxxxxxxx"
    assert auc.seller_name is None  # masked "********"
    assert auc.listed_at.isoformat() == "2026-11-02T19:49:45+09:00" == auc.sort_time.isoformat()
    assert fixed.price == Decimal("14960")  # price 0 -> buy-now price
    assert fixed.seller_name == "vintage_shop"
    assert page.next_cursor == "51"


def test_yahoo_parse_html_and_last_page():
    import json
    data = load_fixture("yahoo_auctions_search.json")
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'
    page = YahooAuctionsAdapter.parse_html(html, offset=17451)
    assert len(page.listings) == 2 and page.next_cursor is None  # 17451 + 50 > 17476
    with pytest.raises(YahooParseError):
        YahooAuctionsAdapter.parse_html("<html>no data</html>")


def test_yahoo_params_newest_first():
    params = YahooAuctionsAdapter().build_params("ヨウジヤマモト", None)
    assert params == {"p": "ヨウジヤマモト", "s1": "new", "o1": "d", "n": "50", "b": "1"}


def test_yahoo_region_block_page_counts_as_blocked():
    with pytest.raises(MarketplaceBlocked):
        check_response(YahooAuctionsAdapter(), _resp(200, "<html>このサービスはご利用いただけません</html>", "text/html"))


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_mercari_dpop_proof_is_valid_es256():
    signer = DPoPSigner()
    token = signer.proof("POST", SEARCH_URL)
    h, p, sig = token.split(".")
    header, payload = json.loads(_b64d(h)), json.loads(_b64d(p))
    assert header["alg"] == "ES256" and header["typ"] == "dpop+jwt"
    assert payload["htm"] == "POST" and payload["htu"] == SEARCH_URL
    raw = _b64d(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    jwk = header["jwk"]
    pub = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_b64d(jwk["x"]), "big"), int.from_bytes(_b64d(jwk["y"]), "big"), ec.SECP256R1()
    ).public_key()
    pub.verify(der, f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if invalid


def _resp(status: int, body: str = "{}", ctype: str = "application/json") -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": ctype},
                          request=httpx.Request("GET", "https://x"))


@pytest.mark.parametrize("status", [403, 429])
def test_check_response_blocked_status(status):
    with pytest.raises(MarketplaceBlocked):
        check_response(GrailedAdapter(Settings()), _resp(status))


def test_check_response_captcha_page():
    page = _resp(200, "<html>Please complete the CAPTCHA</html>", "text/html")
    with pytest.raises(MarketplaceBlocked):
        check_response(GrailedAdapter(Settings()), page)
    with pytest.raises(MarketplaceBlocked):  # Mercari: any HTML instead of JSON
        check_response(MercariJPAdapter(), _resp(200, "<html>hi</html>", "text/html"))


def test_check_response_5xx_is_transient():
    with pytest.raises(TransientError):
        check_response(EbayAdapter(Settings()), _resp(503))


def test_check_response_ok():
    check_response(EbayAdapter(Settings()), _resp(200))


# --- Carousell -----------------------------------------------------------------------------

def test_carousell_parse_verified_shape():
    from sourcer.adapters.carousell import CarousellAdapter

    page = CarousellAdapter("carousell_ph").parse(load_fixture("carousell_search.json"))
    assert [x.external_id for x in page.listings] == ["1471993317", "1380444751", "1468672573"]  # id "0" skipped
    assert page.next_cursor is None  # page 1 only
    a, bumped, cheap = page.listings
    assert a.marketplace == "carousell_ph" and a.currency == "PHP" and a.price == Decimal("1280")
    assert a.seller_id == "25493233" and a.seller_name == "nextflip"
    assert a.url == (
        "https://www.carousell.ph/p/yohji-yamamoto-y-saccs-pour-homme-nylon-backpack-in-black-ysaccs-1471993317/"
    )
    assert len(a.image_urls) == 2 and a.image_urls[0].startswith("https://media.karousell.com/")
    assert a.listed_at == a.sort_time == datetime.fromtimestamp(1793702920, tz=UTC)
    # Bumped cards: the bump time orders the results; the real listing time is unknown.
    assert bumped.listed_at is None and bumped.sort_time == datetime.fromtimestamp(1793694132, tz=UTC)
    assert cheap.price == Decimal("450.50") and cheap.image_urls[0].endswith("p1468672573_thumbnail.jpg")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("PHP 1,280", "1280"), ("RM39.90", "39.90"), ("Rp 110,700", "110700"), ("S$110", "110"),
     ("HK$1,080", "1080"), ("", None), (None, None), ("Free", None)],
)
def test_carousell_price_formats(text, expected):
    from sourcer.adapters.carousell import parse_price

    assert parse_price(text) == (Decimal(expected) if expected else None)


def test_carousell_countries_and_urls():
    from sourcer.adapters.carousell import CarousellAdapter

    my = CarousellAdapter("carousell_my")
    assert my.country.currency == "MYR"
    assert my.search_url("yohji yamamoto") == "https://www.carousell.com.my/search/yohji%20yamamoto"
    assert CarousellAdapter("carousell_id").country.host == "id.carousell.com"


def test_carousell_parse_html_and_block_detection():
    from sourcer.adapters.carousell import CarousellAdapter, CarousellParseError

    ad = CarousellAdapter("carousell_hk")
    state = json.dumps(load_fixture("carousell_search.json"))
    html = f'<html><script type="application/json">{{"other":1}}</script><script type="application/json">{state}</script></html>'
    page = ad.parse_html(html)
    assert len(page.listings) == 3 and page.listings[0].url.startswith("https://www.carousell.com.hk/p/")
    assert page.listings[0].currency == "HKD"

    req = httpx.Request("GET", "https://www.carousell.com.hk/search/x")
    assert not ad.is_blocked_response(httpx.Response(200, text=html, request=req))
    assert ad.is_blocked_response(httpx.Response(200, text="<html>Just a moment...</html>", request=req))
    with pytest.raises(CarousellParseError):
        ad.parse_html("<html>Just a moment...</html>")
