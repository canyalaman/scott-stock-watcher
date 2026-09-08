"""Ayristirma testleri.

Ag baglantisi gerektirmez: siteden kaydedilmis gercek bir varyant sayfasi
uzerinde calisir. Calistirmak icin:  python tests/test_parse.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_stock as cs  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "product_xs_out_of_stock.html")
html = open(FIXTURE, encoding="utf-8").read()

GTM_OUT = 'data-gtm-item-stock="out of stock"'
GTM_IN = 'data-gtm-item-stock="in stock"'
LD_OUT = "schema.org/OutOfStock"
LD_IN = "schema.org/InStock"


def check(label, cond):
    if not cond:
        raise SystemExit("BASARISIZ: " + label)
    print("  ok  " + label)


print("beden -> varyant eslemesi")
variants = cs.parse_size_variants(html)
check("7 beden bulundu: " + str(list(variants)), len(variants) == 7)
check("XS -> 4277387969004", variants.get("XS") == "4277387969004")
check("XXL -> 4277387969014", variants.get("XXL") == "4277387969014")

print("\nsayfanin gosterdigi beden")
check("XS", cs.parse_shown_size(html) == "XS")

print("\nstok tespiti")
check("fixture: stokta yok", cs.parse_stock(html) is False)
check(
    "bedene ozel sinyal 'in stock' -> STOKTA",
    cs.parse_stock(html.replace(GTM_OUT, GTM_IN)) is True,
)
check(
    "her iki sinyal de 'in stock' -> STOKTA",
    cs.parse_stock(html.replace(GTM_OUT, GTM_IN).replace(LD_OUT, LD_IN)) is True,
)
# JSON-LD parent SKU'ya ait: baska bir beden stoga girdiginde de InStock olur.
# Tek basina tetiklememeli, yoksa XS icin yanlis alarm uretir.
check(
    "sadece urun geneli InStock -> tetiklemez",
    cs.parse_stock(html.replace(LD_OUT, LD_IN)) is False,
)
# Analytics attribute'u kaybolursa urun geneli yedege duser.
check(
    "bedene ozel sinyal yoksa urun geneline duser",
    cs.parse_stock(html.replace("data-gtm-item-stock", "x-yok").replace(LD_OUT, LD_IN))
    is True,
)

print("\nsessiz bozulmaya karsi koruma")
broken = html.replace("data-gtm-item-stock", "x-yok").replace('"availability"', '"x"')
try:
    cs.parse_stock(broken)
    raise SystemExit("BASARISIZ: sinyalsiz sayfada FetchError bekleniyordu")
except cs.FetchError as e:
    check("sinyal yoksa hata firlatir ('yok' demez): " + str(e), True)

print("\nbot korumasi tespiti")
check("Incapsula sayfasi", cs._is_blocked('<iframe src="/_Incapsula_Resource?a=1">') is True)
check("normal sayfa", cs._is_blocked(html) is False)

print("\nTUM TESTLER GECTI")
