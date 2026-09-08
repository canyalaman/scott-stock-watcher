# Scott Addict Gravel 20 — beden stok takibi

[Scott Addict Gravel 20 Frame Set](https://www.scott-sports.com/de/de/product/scott-addict-gravel-20-frame-set?article=4277387969004)
sayfasındaki **XS** bedeni izler; stoğa girdiği anda telefona push bildirimi yollar.
Stok yokken de her turda sessiz bir "hâlâ yok" bildirimi gönderir, böylece
takibin çalıştığını görürsün.

Bildirim kanalı [ntfy.sh](https://ntfy.sh) — kayıt, hesap ya da API anahtarı gerektirmez.
Çalışma yeri GitHub Actions, yani bilgisayar kapalıyken de kontrol devam eder.

---

## 1. Telefonu hazırla

1. **ntfy** uygulamasını kur:
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) ·
   [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)
2. Uygulamada **Subscribe to topic** de ve konu adını gir.

   > Bu depo public olduğu için gerçek konu adı burada **yazmıyor**. Yerel
   > `topic.txt` dosyasında duruyor (`.gitignore`'da, repoya gitmez).
   > Aşağıdaki komutlarda geçen `KONU-ADIN` yerine onu koy.

3. Test için bir mesaj at, telefona düşmeli. **PowerShell'de** (Windows'ta `curl`,
   `Invoke-WebRequest`'in takma adıdır ve `-d` bayrağını tanımaz — `curl.exe` yaz):

```bash
curl.exe -d "test" ntfy.sh/KONU-ADIN
```

   Tamamen PowerShell'ce alternatifi:

```bash
Invoke-RestMethod -Uri https://ntfy.sh/KONU-ADIN -Method Post -Body "test"
```

   Git Bash / WSL / macOS / Linux'ta düz `curl -d "test" ntfy.sh/...` çalışır.

> ntfy.sh'te konular herkese açıktır — **adını bilen herkes okuyabilir ve
> yazabilir.** Güvenlik tamamen adın tahmin edilemez olmasına dayanıyor, o yüzden
> adı hiçbir yere yazma: bu README'ye, issue'lara, ekran görüntülerine. Daha sıkı
> bir kurulum istersen kendi ntfy sunucunu kurabilir ya da erişim korumalı bir konu
> açabilirsin; `NTFY_SERVER` değişkeni bunun içindir.

## 2. GitHub'a kur

**Depo `public`.** Zamanlanmış işler private depolarda aylık 2000 dakikalık
ücretsiz kotadan yer. Public depolarda
Actions dakikaları ücretsizdir. Bu yüzden repoya konu adı hiç girmiyor: gerçek ad
sadece GitHub secret'ında ve yerel `topic.txt`'te duruyor. `state.json` da
Actions cache'inde tutulur, repoya yazılmaz.

**a.** Kodu gönder:

```bash
git remote add origin https://github.com/canyalaman/scott-stock-watcher.git
git push -u origin main
```

**b.** Konu adını gizli değişken olarak ekle: depoda
**Settings → Secrets and variables → Actions → New repository secret**

- Name: `NTFY_TOPIC`
- Secret: `KONU-ADIN`

**c.** **Actions** sekmesine gel, soldan `Scott stok takibi` işini seç ve
**Run workflow** ile elle bir kez çalıştır. Logda beklenen çıktı:

```
[...] XS: stokta yok (onceki: None)
```

Bu satırı görüyorsan kurulum tamam; bundan sonrası otomatik.
`bot korumasi` hatası görüyorsan aşağıdaki "Bilinen riskler" bölümüne bak.

## 3. Ayarlar

Depo ayarlarından (**Settings → Secrets and variables → Actions**) değiştirilebilir:

| Değişken | Tür | Varsayılan | Açıklama |
|---|---|---|---|
| `NTFY_TOPIC` | secret | — | ntfy konu adı. **Zorunlu.** |
| `NTFY_SERVER` | variable | `https://ntfy.sh` | Kendi ntfy sunucun varsa |
| `WATCH_SIZES` | variable | `XS` | Virgülle birden fazla: `XS,S` |
| `PRODUCT_PATH` | variable | `/de/de/product/scott-addict-gravel-20-frame-set` | Başka bir ürünü izlemek için |
| `NOTIFY_OUT_OF_STOCK` | variable | `1` | Stok yokken de bildirim. Susturmak için `0` |
| `REMIND_AFTER_HOURS` | variable | `8` | Stokta kalırsa kaç saatte bir hatırlatsın |

Kontrol sıklığı `.github/workflows/stock-watch.yml` içindeki `cron` satırındadır.

---

## Nasıl çalışıyor

Site **Imperva/Incapsula** bot koruması arkasında. Düz `curl` ya da `requests`
anında "Request unsuccessful" sayfası alıyor; headless tarayıcı da engelleniyor.
İki şey gerekiyordu:

1. **TLS parmak izi.** `curl_cffi` isteği gerçek Chrome'un TLS/JA3 imzasıyla atar.
2. **Isınma isteği.** Ürün sayfası soğuk bir istemciden istendiğinde reddediliyor;
   önce mağaza ana sayfası (`/de/de`) ziyaret edilip `visid_incap` / `incap_ses`
   çerezleri alınınca geçiyor.

Stok durumu sayfadan iki sinyalle okunuyor — ama bunlar **eşit değerde değil**:

```html
<!-- bedene özel: item-size ile aynı etikette durur. Asıl kaynak bu. -->
data-gtm-item-size="XS"  data-gtm-item-stock="out of stock"
```
```json
// ürün geneli: parent SKU 427738'e ait, bedene özel DEĞİL
{ "offers": { "availability": "http://schema.org/OutOfStock" }, "sku": "427738" }
```

JSON-LD alanı **başka bir beden** stoğa girdiğinde de `InStock` olur; tek başına
kullanılsa XS için yanlış alarm üretirdi. Bu yüzden karar bedene özel analytics
attribute'una göre verilir, JSON-LD yalnızca o attribute kaybolursa yedek olarak
kullanılır.

İkisi birden okunamazsa betik **hata verir** — sessizce "stokta yok" demez.
Sayfa yapısı değişirse stok girip de haberin olmaması en kötü senaryo olurdu.

Ayrıştırma mantığının testleri ağ gerektirmez (siteden kaydedilmiş gerçek bir
sayfa üzerinde çalışır):

```bash
python tests/test_parse.py
```

### İstek sayısı neden bu kadar az

Geliştirme sırasında ölçülen davranış: kısa sürede ~15 istek atınca Imperva IP'yi
**tüm alan adı için 25 dakikadan uzun süre** engelliyor (`/de/de` de `/us/en` de).
Engel sırasında ısrar etmek pencereyi besleyip süreyi uzatıyor; farklı TLS
profillerine geçmek de kurtarmıyor — engel IP seviyesinde. 3 saatte bir tek
istek bu eşiğin çok altında kalıyor.

Bu yüzden betik normal koşulda **kontrol başına tek istek** atar:

- Incapsula çerezleri `state.json`'da saklanır → her seferinde ısınma isteği yok.
- Beden → varyant kodu eşlemesi önbelleğe alınır → liste sayfası çekilmez.
- Varyant sayfası hem stok durumunu hem güncel beden butonlarını içerdiği için
  önbellek doğrulaması aynı istekten yapılır.

Engele takılırsa **en fazla bir kez daha** dener (30 sn sonra, sıfırdan ısınarak)
ve pes eder — toplam en fazla üç istek. Israr etmek yerine bir sonraki cron
turunu beklemek daha hızlı toparlıyor.

### Bildirim davranışı

- **Stoğa girişte** (`yok → var` geçişi) `urgent` öncelikli bildirim, ürün linki tıklanabilir.
- Stokta kalmaya devam ederse **8 saatte bir** hatırlatma (`REMIND_AFTER_HOURS`).
- Stoktan çıkarsa durum sıfırlanır; tekrar girerse yeniden bildirilir.
- **Stok yokken her turda** `low` öncelikli "hâlâ yok" bildirimi. Telefonu
  titretmez, ses çıkarmaz; sadece bildirim listesinde görünür. Amacı haber vermek
  değil, takibin yaşadığını göstermek. Susturmak için `NOTIFY_OUT_OF_STOCK=0`.
  3 saatlik periyotta günde 8 bildirim eder.
- **4 kez üst üste** kontrol başarısız olursa düşük öncelikli "takip çalışmıyor"
  uyarısı gelir (günde en fazla bir kez). Sessiz bozulmaya karşı sigorta.

---

## Bilinen riskler

**IP engeli.** Bu, kurulumun en büyük bilinmeyeniydi: Imperva bulut sağlayıcı IP
aralıklarına ev bağlantılarından daha sert davranır ve GitHub Actions runner'ları
Azure'da çalışır. **İlk gerçek çalıştırmada doğrulandı — Azure IP'leri geçiyor:**
ürün sayfası geldi, XS okundu, çerezler ve yedi varyant kodu önbelleğe alındı.

Yine de kalıcı bir garanti değil; Imperva IP itibar listelerini güncelleyebilir.
Loglarda üst üste `bot korumasi` görürsen (betik zaten 4 hatadan sonra ntfy'den
uyarır) alternatif, aynı betiği kendi bilgisayarında Görev Zamanlayıcı ile
çalıştırmak:

```powershell
schtasks /create /tn "Scott stok takibi" /sc hourly /mo 3 /f `
  /tr "cmd /c cd /d C:\yol\scott-stock-watcher && set NTFY_TOPIC=KONU-ADIN && python check_stock.py >> log.txt 2>&1"
```

**Zamanlanmış işlerin durdurulması.** GitHub, 60 gün boyunca hiç aktivite olmayan
depolarda zamanlanmış işleri devre dışı bırakır ve sana e-posta atar. Arada bir
depoya commit atmak yeterli.

**Bu ürün şu an online satılmıyor olabilir.** Sayfada tüm bedenler için
"Dieses Produkt kann nicht online erworben werden — Fachhändler'e başvurun"
notu var. Stok göstergesi yine de bedene özel ve güvenilir; ama stoğa girdiğinde
satın alma yerine bayi yönlendirmesi çıkma ihtimali var.

**Sitenin kendi bildirim formu.** Ürün sayfasında Scott'ın kendi "stoğa girince
haber ver" e-posta formu da mevcut (`notify-stock-availability-form`). Bu takibe
ek olarak ona da kaydolman iyi olur — iki bağımsız kanal, kaçırma ihtimalini düşürür.

---

## Yerelde çalıştırma / test

```bash
pip install -r requirements.txt
NTFY_TOPIC=KONU-ADIN python check_stock.py
```

Bildirim akışını gerçek stok beklemeden denemek için `state.json` içindeki
`sizes.XS.in_stock` değerini elle `true` yapıp betiği çalıştırma — tam tersi
gerekir: dosyayı sil ve XS gerçekten stoğa girdiğinde ilk çalışma bildirim atar.
Sadece bildirim yolunu test etmek için `curl -d "test" ntfy.sh/<konu>` yeterli.
