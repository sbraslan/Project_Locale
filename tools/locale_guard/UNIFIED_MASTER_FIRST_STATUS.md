# LocaleGuard — Master Önce Türkçe / 16 Dil Entegrasyon V1

## Kaynaklar
Yeni dal, güvenlik modülleri bulunan `automation/locale-safe-menu-rehearsal-20261029` sürümünden türetilmiştir. Kullanıcının yeni push ettiği `automation/locale-full-code-audit-20261029` dalı, kontrol tarihinde yalnızca 14 `tools/locale_guard` dosyası içermekteydi; V6, Express ve 316 hücrelik modüller yoktu. Bu nedenle yeni dalda onların korumaları saklandı.

Proto örneklerinden alınan prensipler: `BUILD_ALL.py` VNUM eşleme, ekleme/silme farkları, TR→EN→TR köprü; `translation_engine.py` sağlayıcı katmanı; `translation_cache.json` artımlı önbellek fikri; `INSTALL_TRANSLATORS.bat` ve `UPDATE_TRANSLATION_SYSTEM.bat` servis hazırlığı. Bu dosyaların **direkt canlı silme ve encoding tahmini** kodu kullanılmıyor.

## Gerçek durum (henüz tam otomatik canlı uygulama değil)
1. `unified_master_first_plan_v1.py`: 200 civarında Master TXT ve **16** hedef dizini tarar. Bilinen TSV, wiki, özel filtre ve tanımsız dosyaları ayırır; eksik/fazla anahtar ve dosyaları silmeden listeler.
2. `unified_translation_contract_v1.py`: `%u`, `%d`, Python named printf, `{name}`, `|c`, wiki köşeli direktifleri, HTML tag, URL, tab ve escape kodlarını çeviriden izole eder. TR↔EN ve EN↔hedef geri kontrolünde düşük benzerliği **anlamsal inceleme** olarak işaretler. Bu işlem tek başına anlam doğruluğunu garanti etmez.
3. `unified_master_tr_stage_v1.py`: yalnız EN/DE olarak **güçlü tanımlanmış** Master hücreleri için yerel LibreTranslate'le ilk Türkçe staging provası yapar; orijinal Master dosyalarını değiştirmez. Diğer diller veya belirsiz metinler otomatik çevrilmez.
4. `unified_targets_stage_v1.py`: **bütün Türkçe Master dosyalarının SHA-256 onay manifesti** olmadan çalışmayı reddeder. İngilizce köprüyle hedef dil kodlamasını ve geri çeviriyi doğrular; yalnız `.locale_guard/` altında staging hazırlayabilir. Henüz canlı uygulama ve istemci paketleme yapmaz.
5. Önceki `incremental_scan_v1.py`, `master_tr_rehearsal.py`, `runtime_apply.py` ve onay/rollback denetimleri **aynen durur**; ancak 316 hücrelik eski uygulama motorunu genel 16 dil uygulaması sanmayın.

## Windows'ta güncelleme ve sadece okuma
```powershell
cd "C:\Users\skill\OneDrive\Masaüstü\Project_Seferihisar\Project_Locale"
git fetch origin automation/locale-unified-master-first-20261029
# Yerel çalışma dalını ezmeden, uzak kaynaktan sadece salt-okunur modülü çalıştırmak için
git show origin/automation/locale-unified-master-first-20261029:tools/locale_guard/unified_master_first_plan_v1.py
```
Modülleri **önce güvenli bir ayrı worktree** içinde test etmek gerekir; live locale'a kopyalamayın.

## Yeni bir çalışma dalı açarak test etmek için
```powershell
git status --short
# Aşağıdaki checkout komutu yalnızca temiz bir çalışma kopyasında ve kullanıcı onayıyla:
git switch -c automation/locale-unified-local-test origin/automation/locale-unified-master-first-20261029
python -B -m unittest discover -s tools/locale_guard/tests -q
python -B -m tools.locale_guard.unified_master_first_plan_v1 --root .
```

## Uygulama öncesi kalan zorunlu işler
- [ ] Master'daki EN/DE dışındaki yabancı metinleri de güvenilir biçimde sınıflandırmak ve belirsizleri incelemek.
- [ ] Her TXT türü için parser ve çevrilebilir hücre sözleşmesini tamamlamak (`FREEFORM_OR_UNREGISTERED_REVIEW`).
- [ ] `master/` için tüm onaylı staging'i transaction/backup ile canlıya alma ve rollback motoru.
- [ ] Master tam Türkçe olduktan sonra 16 dilin eksiksiz, insan tarafından onaylanan ve tüm QA engelleri çözülmüş staging paketlerini tamamlamak.
- [ ] Hedefteki silme/ekleme işlemlerini ayrı onay, SHA drift, yedek ve atomik rollback ile canlıya alma.
- [ ] İstemci ve server locale paket/encoding uyumluluğu ve oyun içi runtime testleri.

**Uyarı:** Bu dalda tek tuşla bütün 16 dilin otomatik canlı güncellendiğini iddia eden hiçbir yol yoktur. Önce güvenlik ve dil sınıflandırması tamamlanmalıdır.
