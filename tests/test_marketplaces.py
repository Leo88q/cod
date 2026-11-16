"""Подарок с переходом на маркетплейс: комиссия, подпись, печать QR.

Главное: наша ссылка не должна становиться открытым редиректом и не должна
печатать нечитаемый QR.
"""
import pytest

from app import marketplaces as mk


def test_commission_is_concrete_not_marketing():
    assert mk.commission("ozon", 1500) == 180.0
    assert mk.commission("wb", 1500) == 150.0
    assert mk.commission("ymarket", 1500) == 72.0
    assert mk.commission("ozon", 0) == 0.0


def test_unknown_partner_rejected():
    with pytest.raises(mk.PartnerError):
        mk.commission("aliexpress", 100)


def test_link_is_signed_and_verifiable():
    path = mk.offer_path("ozon", "7Kj2mQ", "https://ozon.ru/product/1", "s3cret")
    sig = path.split("sig=")[1].split("&")[0]
    assert mk.verify("ozon", "7Kj2mQ", "https://ozon.ru/product/1", sig, "s3cret")
    # подпись от другого подарка не подходит
    assert not mk.verify("ozon", "ДРУГОЙ", "https://ozon.ru/product/1", sig, "s3cret")


def test_open_redirect_blocked():
    with pytest.raises(mk.PartnerError):
        mk.offer_path("ozon", "c1", "https://evil.example/steal", "s3cret")
    with pytest.raises(mk.PartnerError):
        mk.offer_path("ozon", "c1", "http://ozon.ru/product/1", "s3cret")   # не https
    # подмена домена внутри похожего хоста
    with pytest.raises(mk.PartnerError):
        mk.offer_path("ozon", "c1", "https://ozon.ru.evil.example/x", "s3cret")


def test_verify_rejects_tampered_target():
    path = mk.offer_path("wb", "c2", "https://wildberries.ru/catalog/1", "s3cret")
    sig = path.split("sig=")[1].split("&")[0]
    assert not mk.verify("wb", "c2", "https://wildberries.ru/catalog/2", sig, "s3cret")
    assert not mk.verify("wb", "c2", "https://wildberries.ru/catalog/1", "", "s3cret")


def test_empty_secret_never_verifies():
    assert not mk.verify("ozon", "c", "https://ozon.ru/p", "abc", "")


def test_qr_fits_small_label():
    assert mk.is_printable("https://scena.link/c/7Kj2mQ?m=wb")
    # длинный URL уже не влезет на этикетку 25 мм — это должно быть видно заранее
    assert not mk.is_printable("https://scena.link/c/7Kj2mQ?m=wb&utm=" + "x" * 200)


def test_qr_svg_has_no_external_resources():
    """QR печатается офлайн: в SVG не должно быть внешних картинок, шрифтов и ссылок.
    Единственный допустимый http — namespace самого SVG (это не загрузка)."""
    svg = mk.qr_svg("https://scena.link/c/7Kj2mQ")
    assert "<svg" in svg[:300]
    assert "http://www.w3.org/2000/svg" in svg
    assert svg.count("http") == 1
    for forbidden in ("<image", "xlink:href", "@font-face", "url(http"):
        assert forbidden not in svg
