# LocaleGuard Türkçe 316 Hücre — Kontrollü Uygulama

## Amaç
Bu paket yalnızca yerelde SHA ile kilitlenmiş 316 Türkçe metin değişikliğini 46 dosyada uygulayabilir. `master/` ve `tr/` klasörlerine aynı doğrulanmış baytları yazar; yani toplam **92 dosya** etkilenir.

**Kapsam dışı:** 1 Dragon Alchemy hücresi, diğer 15 dilin eksik çevirileri, Client/Binary dosyalarına dağıtım ve oyun içi testler.

## İşlem sırası (Türkçe V6 Menüsü)
1. `1 → EVET`: Genel güvenlik kontrolü ve testler.
2. `2 → 2 → EVET`: 316 hücre önizleme kontrolü ve izole prova.
3. `2 → 3 → EVET`: `.locale_guard/master_tr_inceleme_aa529ec168d1.tsv` dosyasını oluştur.
4. **Raporu açıp 316 eski/yeni hücreyi içerik ve bağlam yönünden tek tek incele.**
5. `2 → 4 → EVET`; yalnız gerçekten anlamsal olarak onaylıyorsan `ONAYLIYORUM 316` gir.
6. `2 → 5 → EVET`; canlı uygulama için ikinci kez `UYGULA 316` gir.
7. `2 → 6 → EVET`: 92 dosyanın SHA-256 ve master/TR senkronluğu doğrulansın.
8. Bir sorun varsa `2 → 7 → EVET` ve `GERI AL` ile son işlemi yedekten geri al.

## Güvenlik kuralları
- Çevirinin teknik doğrulaması anlamsal onay değildir.
- Önizleme manifestleri değişmişse, 316/46/1 sayıları uyuşmuyorsa işlem kapanır.
- Uygulama öncesi master ve tr baytları eşit olmalı; farklı encoding/policy varsa işlem durur.
- 92 hedef dosyanın **tüm** yedekleri ve kaynak hashleri hazır olmadan hiçbir canlı dosyaya yazılmaz.
- Uygulama sırasında hata çıkarsa 92 dosya geri alınır; sonradan insan tarafından değiştirilmiş dosyalar varsa geri alma da durur, üzerine yazılmaz.
- Git push, commit, reset veya temizleme yapılmaz. `Project_Client` ve `Project_Binary` değiştirilmez.
- Kapanmış/yarım kalmış işlem journal dosyaları otomatik yok edilmez; manuel inceleme gerekir.
- Kalan `gr/mob_names.txt` kodlama raporu otomatik TR uygulamasından ayrı değerlendirilir.
- `2 → 6` sonucu yalnızca **Türkçe dosyaların runtime ön koşulunu** doğrular; oyun içinde test yapıldığı anlamına gelmez.

## İşlem kayıtları
- Çeviri incelemesi: `.locale_guard/master_tr_inceleme_aa529ec168d1.tsv`
- Kullanıcı onayı: `.locale_guard/master_tr_onay_aa529ec168d1.json`
- Yedek ve işlem durumu: `.locale_guard/master_tr_canli_islemler/<işlem-kimliği>/`
- Yedekler repo dışında değil, ignore edilen `.locale_guard/` altında tutulur; bunları silmeyin.
