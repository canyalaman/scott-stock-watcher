#!/usr/bin/env python3
"""Scott Sports beden stok takibi.

Urun sayfasindaki bedenlerin stok durumunu kontrol eder ve "stokta yok" ->
"stokta var" gecisinde ntfy.sh uzerinden push bildirimi yollar.

Site Imperva/Incapsula arkasinda ve agresif hiz limiti uyguluyor: kisa surede
10-15 istek atinca IP birkac dakikaligina engelleniyor. Bu yuzden betik
istek sayisini minimumda tutar -- normal kosulda kontrol basina TEK istek:

  * Incapsula cerezleri (visid_incap / incap_ses) state.json'da saklanir,
    boylece her calismada ana sayfayi "isitmak" gerekmez.
  * Varyant kodlari onbellege alinir, boylece once liste sayfasini cekip
    beden -> kod eslemesini yeniden kurmaya gerek kalmaz.
  * Varyant sayfasi zaten hem stok durumunu hem de guncel beden butonlarini
    icerdigi icin onbellek dogrulamasi ayni istekten yapilir.

Ayarlar ortam degiskenlerinden okunur (bkz. README.md).
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

BASE = "https://www.scott-sports.com"

PRODUCT_PATH = os.getenv(
    "PRODUCT_PATH", "/de/de/product/scott-addict-gravel-20-frame-set"
)
# Virgulle ayrilmis beden listesi, or. "XS" veya "XS,S"
WATCH_SIZES = [s.strip().upper() for s in os.getenv("WATCH_SIZES", "XS").split(",") if s.strip()]

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")

STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

# Stokta kaldigi surece kac saatte bir hatirlatma yollansin
REMIND_AFTER_HOURS = float(os.getenv("REMIND_AFTER_HOURS", "8"))
# Kac ardisik hatadan sonra "takip bozuldu" uyarisi gitsin
ERROR_ALERT_AFTER = int(os.getenv("ERROR_ALERT_AFTER", "4"))

IMPERSONATE_PROFILE = os.getenv("IMPERSONATE_PROFILE", "chrome131")

# Engele takilinca ikinci (ve son) denemeden once beklenecek sure.
# Olculen davranis: Imperva bu siteye yigin istek gidince IP'yi TUM alan adi
# icin 25+ dakika engelliyor ve engel sirasinda israr etmek pencereyi besliyor.
# O yuzden tekrar denemek anlamsiz: cabuk pes et, bir sonraki cron turunu bekle.
RETRY_AFTER_SECONDS = float(os.getenv("RETRY_AFTER_SECONDS", "30"))


class FetchError(RuntimeError):
    """Sayfa alinamadi ya da bot korumasina takildi."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    print("[" + iso(now()) + "] " + msg, flush=True)


# --------------------------------------------------------------------------- #
# Sayfa cekme
# --------------------------------------------------------------------------- #

def _is_blocked(html: str) -> bool:
    return "_Incapsula_Resource" in html or "Request unsuccessful" in html


def make_session(profile: str, cookies: dict | None = None):
    """Istemci olustur; varsa onceki calismadan kalan Incapsula cerezlerini yukle.

    curl_cffi'nin impersonate profili TLS/JA3 parmak izini gercek Chrome'a
    benzetiyor -- duz requests/curl bu sitede aninda engelleniyor.
    """
    s = requests.Session(impersonate=profile)
    for k, v in (cookies or {}).items():
        s.cookies.set(k, v, domain=".scott-sports.com")
    return s


def warm_up(session) -> None:
    """Magaza ana sayfasini ziyaret ederek Incapsula cerezlerini kur."""
    r = session.get(BASE + "/de/de", timeout=45)
    if _is_blocked(r.text):
        raise FetchError("ana sayfa bot korumasina takildi")


def fetch(session, path: str) -> str:
    r = session.get(BASE + path, headers={"Referer": BASE + "/de/de"}, timeout=45)
    if r.status_code != 200:
        raise FetchError(path + " -> HTTP " + str(r.status_code))
    if _is_blocked(r.text):
        raise FetchError(path + " -> bot korumasi")
    return r.text


def export_cookies(session) -> dict:
    out = {}
    for c in session.cookies.jar:
        if c.name.startswith(("visid_incap", "incap_ses", "nlbi")):
            out[c.name] = c.value
    return out


# --------------------------------------------------------------------------- #
# Ayristirma
# --------------------------------------------------------------------------- #

SIZE_BUTTON_RE = re.compile(
    r'href="[^"]*?article=(?P<code>\d+)"'
    r'[^>]*>\s*<button[^>]*class="sizes--btn[^"]*"[^>]*>\s*(?P<label>[A-Za-z0-9]+)\s*</button>',
    re.S,
)


def parse_size_variants(html: str) -> dict:
    """Beden etiketi -> varyant kodu eslemesi.

    Kodlari sayfadan okuyoruz; Scott varyant kodlarini degistirirse takip
    kendiliginden uyum saglasin diye sabit yazmiyoruz.
    """
    out = {}
    for m in SIZE_BUTTON_RE.finditer(html):
        out.setdefault(m.group("label").upper(), m.group("code"))
    return out


def parse_shown_size(html: str) -> str | None:
    """Varyant sayfasinin hangi bedeni gosterdigi (onbellek dogrulamasi icin)."""
    m = re.search(r'data-gtm-item-size="([^"]*)"', html)
    return m.group(1).strip().upper() if m else None


IN_STOCK_WORDS = ("in stock", "instock", "limitedavailability", "presale", "backorder")
OUT_OF_STOCK_WORDS = ("out of stock", "outofstock", "soldout", "discontinued")


def _read_signal(value: str | None):
    """Metni True/False/None (= anlasilmadi) olarak yorumla."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in IN_STOCK_WORDS:
        return True
    if v in OUT_OF_STOCK_WORDS:
        return False
    return None


def parse_stock(html: str) -> bool:
    """Varyant sayfasindan bedene ozel stok durumunu cikarir.

    Iki sinyal var ama esit degerde degiller:

    * ``data-gtm-item-stock`` analytics attribute'u ``data-gtm-item-size`` ile
      ayni etikette durur, yani BEDENE ozeldir -> asil kaynak budur.
    * JSON-LD ``offers.availability`` ise parent SKU'ya (or. 427738) ait, yani
      URUN GENELI. Baska bir beden stoga girdiginde de "InStock" olur; tek
      basina kullanilirsa XS icin yanlis alarm uretir. Sadece analytics
      attribute'u kaybolursa yedek olarak kullanilir.

    Ikisi de okunamazsa sayfa yapisi degismis demektir -> sessizce "stokta yok"
    demek yerine hata veriyoruz, yoksa stok girdiginde haberimiz olmaz.
    """
    gtm_m = re.search(r'data-gtm-item-stock="([^"]*)"', html)
    ld_m = re.search(r'"availability"\s*:\s*"[^"]*?/(\w+)"', html)

    gtm = _read_signal(gtm_m.group(1) if gtm_m else None)
    ld = _read_signal(ld_m.group(1) if ld_m else None)

    if gtm is not None:
        if ld is True and gtm is False:
            # Urun genelinde bir seyler stoga girmis ama bu beden degil.
            log("not: urun genelinde stok var, bu bedende yok (baska beden girmis olabilir)")
        return gtm

    if ld is not None:
        log("UYARI: bedene ozel sinyal yok, urun geneli availability kullaniliyor")
        return ld

    raise FetchError("stok sinyali bulunamadi (sayfa yapisi degismis olabilir)")


# --------------------------------------------------------------------------- #
# Bildirim
# --------------------------------------------------------------------------- #

def _encode_header(value: str) -> str:
    """ntfy basliklari ASCII ister; gerekirse RFC 2047 ile kodla."""
    if value.isascii():
        return value
    from base64 import b64encode

    return "=?UTF-8?B?" + b64encode(value.encode("utf-8")).decode("ascii") + "?="


def notify(title: str, message: str, priority: str = "default",
           tags: str = "", click: str = "") -> bool:
    if not NTFY_TOPIC:
        log("UYARI: NTFY_TOPIC tanimsiz, bildirim atlaniyor")
        return False
    headers = {"Title": _encode_header(title), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    for attempt in range(3):
        try:
            r = requests.post(
                NTFY_SERVER + "/" + NTFY_TOPIC,
                data=message.encode("utf-8"),
                headers=headers,
                timeout=20,
            )
            if r.status_code < 300:
                log("bildirim gonderildi: " + title)
                return True
            log("bildirim HTTP " + str(r.status_code) + ": " + r.text[:200])
        except Exception as e:
            log("bildirim hatasi: " + str(e))
        time.sleep(2 * (attempt + 1))
    log("BILDIRIM GONDERILEMEDI")
    return False


# --------------------------------------------------------------------------- #
# Durum dosyasi
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("UYARI: state.json bozuk, sifirdan baslaniyor")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def hours_since(ts) -> float:
    if not ts:
        return float("inf")
    try:
        return (now() - datetime.fromisoformat(ts)).total_seconds() / 3600
    except (ValueError, TypeError):
        return float("inf")


# --------------------------------------------------------------------------- #
# Kontrol
# --------------------------------------------------------------------------- #

def _collect(session, variants: dict) -> tuple:
    """Izlenen bedenleri oku. (sonuclar, guncel_varyant_eslemesi) dondurur."""
    results = {}
    for i, size in enumerate(WATCH_SIZES):
        if i:
            time.sleep(random.uniform(1.5, 3.5))  # siteye nazik davran

        if size not in variants:
            log("beden->varyant eslemesi cekiliyor")
            variants = parse_size_variants(fetch(session, PRODUCT_PATH))
            if size not in variants:
                raise FetchError(
                    "beden " + size + " sayfada yok "
                    "(bulunanlar: " + str(sorted(variants)) + ")"
                )
            time.sleep(random.uniform(1.5, 3.5))

        query = PRODUCT_PATH + "?article=" + variants[size]
        html = fetch(session, query)

        # Onbellekteki kod eskimisse sayfa baska bedeni gosterir.
        shown = parse_shown_size(html)
        if shown and shown != size:
            log("onbellek eskimis (" + str(shown) + " != " + size + "), yenileniyor")
            variants = parse_size_variants(html)
            if size not in variants:
                raise FetchError("beden " + size + " artik sayfada yok")
            time.sleep(random.uniform(1.5, 3.5))
            query = PRODUCT_PATH + "?article=" + variants[size]
            html = fetch(session, query)

        results[size] = (parse_stock(html), BASE + query)
    return results, variants


def check_sizes(state: dict) -> dict:
    """Beden -> (stokta_mi, urun_url). Basarisiz olursa FetchError firlatir.

    En fazla iki deneme, toplam en fazla uc istek:

    1. Onbellekteki cerez + varyant koduyla dogrudan varyant sayfasi (1 istek).
    2. Olmazsa sifirdan: ana sayfa isitma + liste + varyant.

    Daha fazlasi denenmiyor. Imperva engeli 25+ dakika surdugu icin ayni
    calisma icinde israr etmenin faydasi yok; dahasi engel sirasinda gelen
    istekler pencereyi besleyip sureyi uzatiyor. Engellenirsek sessizce
    pes edip 20 dakika sonraki cron turunu beklemek daha hizli toparliyor.
    """
    variants = dict(state.get("variants") or {})
    cookies = state.get("cookies") or {}
    last_err = None

    if cookies and variants:
        try:
            session = make_session(IMPERSONATE_PROFILE, cookies)
            results, variants = _collect(session, variants)
            state["variants"] = variants
            state["cookies"] = export_cookies(session) or cookies
            return results
        except Exception as e:
            last_err = e
            log("onbellekli deneme basarisiz: " + str(e))
            time.sleep(RETRY_AFTER_SECONDS + random.uniform(0, 5))

    try:
        session = make_session(IMPERSONATE_PROFILE)
        log("sifirdan: ana sayfa isitiliyor")
        warm_up(session)
        results, variants = _collect(session, {})
        state["variants"] = variants
        state["cookies"] = export_cookies(session)
        return results
    except Exception as e:
        last_err = e
        log("sifirdan deneme basarisiz: " + str(e))

    # Onbellekteki cerezler ise yaramadi; sonraki calisma temiz baslasin.
    state.pop("cookies", None)
    raise FetchError(str(last_err))


def main() -> int:
    state = load_state()
    state.setdefault("sizes", {})

    try:
        results = check_sizes(state)
    except Exception as e:
        state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
        n = state["consecutive_errors"]
        log("HATA: kontrol basarisiz (" + str(n) + ". kez ust uste): " + str(e))
        # Sessiz bozulma en kotu senaryo: stok girer, haberimiz olmaz.
        # Bu yuzden hatalar birikince dusuk oncelikli uyari yollariz.
        if n >= ERROR_ALERT_AFTER and hours_since(state.get("last_error_notified")) >= 12:
            if notify(
                "Scott stok takibi calismiyor",
                str(n) + " kez ust uste kontrol basarisiz oldu.\n\nSon hata: "
                + str(e) + "\n\nSayfa yapisi degismis veya bot korumasi engelliyor olabilir.",
                priority="low",
                tags="warning",
            ):
                state["last_error_notified"] = iso(now())
        save_state(state)
        return 1

    state["consecutive_errors"] = 0
    state["last_ok"] = iso(now())

    for size, (in_stock, url) in results.items():
        prev = state["sizes"].get(size, {})
        was = prev.get("in_stock")
        entry = {
            "in_stock": in_stock,
            "checked_at": iso(now()),
            "last_notified": prev.get("last_notified"),
        }
        log(size + ": " + ("STOKTA VAR" if in_stock else "stokta yok")
            + " (onceki: " + str(was) + ")")

        if in_stock:
            fresh = was is not True                                  # yeni girmis
            stale = hours_since(prev.get("last_notified")) >= REMIND_AFTER_HOURS
            if fresh or stale:
                if notify(
                    "SCOTT " + size + " STOKTA!",
                    "Addict Gravel 20 Frame Set - beden " + size
                    + " satin alinabilir.\n\n" + url,
                    priority="urgent" if fresh else "high",
                    tags="rotating_light,bike",
                    click=url,
                ):
                    entry["last_notified"] = iso(now())
        else:
            entry["last_notified"] = None       # tekrar girerse yeniden bildirilsin

        state["sizes"][size] = entry

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
