import pytest

from video_pipeline.publish_store import build_catalog_row

BOOK = {"metadata": {"title": "T", "subtitle": "S", "blurb": "B", "digital_page_count": 20}}


def row(**kw):
    base = dict(access="paid", price_cents=599, publish=False, epub_path="e", cover_path="c")
    return build_catalog_row(BOOK, "t", **{**base, **kw})


def test_paid_row_keeps_price_and_is_draft():
    r = row()
    assert r["price_cents"] == 599 and r["published"] is False and "published_at" not in r


def test_free_row_drops_price():
    assert row(access="free")["price_cents"] is None


def test_paid_needs_price():
    with pytest.raises(ValueError):
        row(price_cents=None)
