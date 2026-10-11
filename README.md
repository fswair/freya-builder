# Freya — Build the Builder

Python 3.13 / uv. Ana ortam PyPI'daki **Autobench 0.4.0**, **pydantic-gepa 0.1.1** ve
standart **GEPA 0.1.4** kullanır. Ana kurulum kardeş checkout veya Git bağımlılığı gerektirmez.

11 Ekim güncel final formatı: tek vaka klasörü ve takımın Cloudflare Workers üzerinde
çalıştırdığı mock servis. Klasör 17:00 UTC'de açılır, teslim 20:00 UTC'de kapanır.
Kurallar: https://www.freyavoice.ai/hackathon#submit . Final içeriği builder kaynak
koduna veya sabit promptlarına eklenmez; klasör ve mock adresi çalışma girdisidir.
Mock uygulaması ayrı `mock-worker/` dizininde, yayımlanan servis sözleşmesinden hazırlanır.
Bu dizindeki mevcut kod yalnız yerel kurulum iskeletidir; henüz banka servisi değildir.

```bash
make sync
make check
make smoke RECORD=runs/stack-check
```

`make check` ve `make smoke` çevrimdışıdır. `environments/local` ve `*-local` Make hedefleri
paket geliştirmesi için isteğe bağlı eski editable ortamı korur; builder için gerekmez.
Önceki hazırlık ve canlı smoke kayıtları `documents/environment-history.md` içinde anlatılır.

## İlk builder: yerel workflow üretimi

`vendor/freya-v3`, kitin brief, API, doküman, örnek agent ve yardımcı istemci dosyalarının
yerel kopyasıdır. Örnek Claude builder/skill ve env dosyaları kopyalanmadı.
`--practice-cases` verilmezse builder yalnızca yerel çıktı üretir; Freya veya banka API'sini çağırmaz.

```bash
uv run --locked freya-builder \
  vendor/freya-v3/briefs/brief-a.md \
  vendor/freya-v3/tools/openapi.json \
  --out runs/brief-a-001
```

Bu komut gerçek builder modeline istek gönderir. Önceden kullanılan
`codex-auth-helper==1.10.1` ve `~/.codex/auth.json` oturumuyla çalışır; Freya anahtarı gerekmez.
Varsayılan model `gpt-6-astra`, reasoning `high`; `--model`, `--reasoning`, `--seconds`,
`--call-seconds` ve `--attempts` ayarlanabilir.
Varsayılan üretim süresi 1200 saniye, aşama başına iki deneme, model çağrısı başına en fazla
600 saniyedir ve kalan toplam süreyle sınırlanır. Süre bütçesi asyncio ile aktif model bekleyişini iptal eder; işletim sistemi
düzeyinde süreç izolasyonu değildir. Freya testleri ayrı bir komutla çalışır.

Brief B, A'nın üstüne eklenir:

```bash
uv run --locked freya-builder \
  vendor/freya-v3/briefs/brief-a.md \
  vendor/freya-v3/tools/openapi.json \
  --additional-brief vendor/freya-v3/briefs/brief-b.md \
  --out runs/brief-b-001
```

Her koşu yeni bir çıktı dizini ister. OpenAPI dosyası açıkça verilebilir. Klasör ve
`--mock-url` verilirse builder servis sözleşmesini kaynak alıntılarıyla kendisi çıkarır;
alan ve enum değerleri public API'den kopyalanmaz. Konuşma dili belgelerden belirlenir.
MD/metin, DOCX, XLSX/XLSM ve metin içeren PDF belgeleri okunur. Word tabloları ve notlar,
Excel sekmeleri, formüller ve önbellek değerleri, PDF sayfa sınırları korunur. Belgelerin
özgün byte hash'leri kaydedilir. Taranmış PDF için OCR, eski DOC/XLS için dönüşüm gerekir;
okunamayan bir dosya sessizce atlanmaz.

1. Pydantic AI, kuralları kaynak alıntıları ve değişiklik ilişkileriyle çıkarır.
2. Kaynak alıntıları, kural kimlikleri ve çelişkiler yerelde kontrol edilir.
3. İkinci Pydantic AI aşaması orijinal brief, çıkarılan kurallar ve Freya referanslarından
   workflow üretir. Geçersiz adaylar sınırlı sayıda geri bildirimle düzeltilir.
4. Geçiş hedefleri, düğüm/araç yerleşimi, API adresleri, zorunlu alanlar ve belirtilen enum
   değerleri kontrol edilir. Python parçaları yalnızca sözdizimi açısından incelenir.

Başarılı koşu: `policy.json`, `workflow.json`, `rule-nodes.json`, `validation.json`,
`inputs.json`, `trace.jsonl` ve model mesaj kayıtlarını üretir. Hatalı adaylar inceleme için
saklanır; son `workflow.json` yalnızca yerel kontroller geçerse yazılır. Trace, model çağrılarının
amaçlarını, başlangıç/bitişlerini, kullanımlarını ve hata durumlarını kaydeder.

**`locally_validated` yarışma başarısı değildir.** Kural-düğüm eşlemesi modelin beyanıdır;
tüm kuralların doğru uygulanmasını, üretilen Python'ın davranışını veya platformun workflow'u
kabul edeceğini kanıtlamaz. Bu CLI henüz nihai teslim komutu değildir.

## Freya metin pratiği

Üretilmiş workflow için platform doğrulaması, publish ve resmi kitin sentetik testleri:

```bash
uv run --locked python -m freya_hackathon.practice runs/brief-a-001 \
  --cases /Users/mert/Downloads/build-the-builder-1/public-test-set.json --brief A
```

Bu komut Freya'ya workflow kaydeder ve yalnızca `mode=chat` simülasyonu başlatır.
`.env` içindeki `FREYA_API_KEY` kullanılır. Takım anahtarı verilmediyse yerelde
`.freya-team-key` dosyasında bir kere oluşturulur; git'e girmez. Aynı slot güncellenerek
workflow kotası korunur. `--only` virgülle ayrılmış case kimlikleri kabul eder.
Platform export'u, sonuçlar, konuşmalar ve banka kayıtları koşunun `practice/` dizinine;
workflow/simülasyon kimlikleri aynı `trace.jsonl` dosyasına yazılır. Tek tekrar bir baseline'dır.
Üretim ve pratik tek komutta, ortak 30 dakikalık bütçeyle de çalışır:

```bash
uv run --locked freya-builder \
  vendor/freya-v3/briefs/brief-a.md vendor/freya-v3/tools/openapi.json \
  --out runs/brief-a-full-001 \
  --practice-cases /Users/mert/Downloads/build-the-builder-1/public-test-set.json \
  --practice-brief A --total-seconds 1800
```

Model üretimi varsayılan olarak en fazla 1200 saniye sürer; pratik yalnızca toplam bütçede
kalan süreyi alır. `pipeline.json` bütün komutun süresini ve durumunu kaydeder. Yerel süre
dolması, Freya'daki simülasyonu iptal etmez. Mevcut sonuçlar yeni test başlatmadan alınabilir:
`uv run --locked python -m freya_hackathon.practice runs/brief-a-full-001 --collect`.
Sonradan alınan sonuçlar ilk koşunun süre sınırını geçmiş olmasını değiştirmez.
Varsayılan 1800 saniyelik toplam bütçede, üretim tavanı pratik için yaklaşık 600 saniye bırakır.
Bu, simülasyonun mutlaka biteceği anlamına gelmez; yerel timeout uzak çalışmayı durdurmaz.

Her değerlendirme `practice/runtime-health.json` üretir. Snekbox bağlantı hatası içeren
konuşmalar, geçenler dahil, altyapıdan etkilenmiş olarak işaretlenir; resmi puan değiştirilmez.
Bu koşular temiz model karşılaştırması veya doğrudan optimizasyon geri bildirimi sayılmaz.
Kayıtlı koşuya sonradan denetim: `uv run --locked python -m freya_hackathon.runtime_audit runs/brief-a-full-001`.

Builder, model adayını Freya'ya uygun bir derleme aşamasından geçirir: bütün `data`
nesnesinin eşlenmesini JSON metniyle taşır ve tüketilen `vars` değerlerini açıkça temizler.
İlk canlı metin testinde nesne eşlemesinin boş kalması ve `vars.pop` sonrasında eski
değerlerin kalması gözlendi. Dönüşümler `compiled.json` ve trace içinde kayıtlıdır.
Derleyici, açık boş metin sıfırlamalarına karşılık gelen string extraction enum'larının
bu değeri kabul etmesini de sağlar. Freya uyumsuz sıfırlamayı reddedip eski değeri
koruduğundan, yalnızca kodda sıfırlama yapmak yeterli değildir. Banka API enum'ları
değiştirilmez; boş değerle çelişen bir doğrulama deseni varsa derleme hata verir.
`python -m freya_hackathon.recompile` kayıtlı bir adayı yeni compiler ile tekrar üretir;
bu geliştirme denemesi ana model üretim trace'ine referans verir, kendi başına nihai
teslim veya baştan üretilmiş bağımsız bir koşu sayılmaz.

Builder, `practice`, `--collect` ve birleşik pipeline artık otomatik Autobench kaydı üretir.
Her çağrı `runs/<koşu>/autobench/<aşama>-<benzersiz-kimlik>/` altında yeni ve değişmez
kayıt oluşturur. Birleşik pipeline, builder ve pratik adımlarını aynı execution trace'inde
tutar; iç içe kayıtlarla model/HTTP tüketimini iki kez saymaz. Hata ve zaman aşımında
o ana kadarki kanıtlar kaydedilir ve özgün istisna çağırana geri iletilir.

Pydantic AI/OpenAI model çağrıları, kullanım bilgileri ve davranış varlıkları; HTTPX istek
süreleri, durum kodları ve hataları native instrumentation ile yakalanır. HTTP gövdeleri
ve kimlik doğrulama başlıkları yakalanmaz. Kaydedilen kanıt kopyalarından bilinen API/takım
anahtarları temizlenir; mevcut ham dosyalar değişmez. Model mesajları, workflow, trace,
konuşmalar ve banka kayıtları Autobench artifact'larına kopyalanır. Kaynak dosya hash'leri
saklanır. Freya'nın uzaktaki model çağrıları native yerel span değildir: API'nin verdiği
konuşma/tool olayları artifact olarak korunur; verilmeyen token/maliyet bilgisi uydurulmaz.

Tamamlanan her pratik koşusu ayrıca `cases-<kimlik>` kaydı üretir: her müşteri ayrı case,
workflow adayı ayrı variant olur. `official_pass` resmi banka+görüşme sonucudur; Snekbox
hataları, extraction uyarıları ve reddedilen değişken güncellemeleri ayrı diagnostic
metric'lerdir. Extraction sayacı hem `extraction_error` hem `extraction_failed` olaylarını
sayar; `freya.variable_validation_errors`, tamamlanan fonksiyon sonuçlarındaki
`validation_errors` sayısını tutar. Bunlar tek başına altyapı hatası kanıtı değildir.
Altyapı hatası puanı
değiştirmez. İcra kaydı ile case kayıtlarının amaçları ayrıdır; süreleri toplanmamalıdır.
Case raporundaki `official_pass_rate` ve correctness matrisi resmi başarıyı gösterir;
`official-case-passed` politikası başarısız vakaları değerlendirme hatası olarak işaretler.
Çalıştırmanın hatasız tamamlanması, vakanın başarılı olduğu anlamına gelmez.

Eski kanıtları ağ isteği veya model çağrısı yapmadan aktarmak:

```bash
uv run --locked python -m freya_hackathon.recording import \
  runs/astra-a-004-repair runs/astra-b-002 \
  runs/astra-a-006-full runs/astra-b-003-full
```

Eski kayıtlar `evidence.origin=imported` taşır; geçmişte native span yakalanmış gibi
gösterilmez. Yeni case kayıtları da `postprocessed-live` işaretlidir: uzaktaki konuşmanın
zamanı ile yerelde kanıt işleme zamanı farklıdır. Aynı senaryo setini kullanan iki adayı
yeniden simüle etmeden karşılaştırma kaydına almak:

```bash
uv run --locked python -m freya_hackathon.recording cases \
  runs/astra-a-003 runs/astra-a-006-full --out runs/autobench-a-comparison
uv run --locked autobench report runs/autobench-a-comparison
uv run --locked autobench compare runs/autobench-a-comparison \
  --baseline astra-a-003 --candidate astra-a-006-full
```

`autobench replay <kayıt-dizini>` ve `autobench report <kayıt-dizini>` subject'i çalıştırmaz.
Taşınmış/eski ham dosyalara ihtiyaç duymadan kayıtlı kanıtlar üzerinden çalışırlar.
Tanımlar `benchmarks/freya-execution.yaml` ve `benchmarks/freya-cases.yaml` içindedir;
gerçek çalışma girdilerini uygulama komutları sağlar. `smoke.yaml` ve `live.yaml` ise
ayrı paket entegrasyon kontrolleridir. GEPA'nın builder rehber metni üzerindeki optimizasyon
döngüsü aşağıda açıklanan `freya-optimize` komutuna bağlıdır.
`runs/` gitignore kapsamındadır; final teslim izleri ayrıca seçilip sürümlenmelidir.

Güncel FAQ, pratik bankanın yalnız PUB-01–PUB-13 müşterilerini sunduğunu belirtiyor.
Yeni banka müşterisi oluşturmayın. Aşağıdaki geliştirme mekanizması mevcut public
müşterinin konuşma varyasyonları içindir; final mock'u ve final değerlendirmesi ayrıdır.
Ek konuşma varyasyonları, farklı `id` ve `script` ile `practice_session_case: "PUB-02"`
gibi bir alan kullanabilir. Böylece banka API'sindeki kayıtlı sentetik müşteri verisi
kullanılırken persona, değerlendirme ve Autobench vaka kimliği ayrı kalır. Banka API'si
keyfi yeni vaka kimliği kabul etmez; seçilen seed ile hesap bilgileri ve beklentiler
uyumlu olmalıdır. Örnek: `diagnostics/callback-probes.json`.

## Builder optimizasyonu

`--workflow-reference PATH` tekrarlanarak public geliştirme workflow'ları örnek
olarak verilebilir. Bu mod her yeni brief için politikayı yeniden çıkarır; model
bir referans seçip tam workflow yerine kesin metin değişiklikleri, gereken JSON
alan değişiklikleri ve yeni kural/node eşlemesini üretir. Bütün çıktı yeniden
derlenip aynı doğrulamalardan geçer. Referanslar politika kaynağı sayılmaz;
brief ve verilen OpenAPI belirleyicidir. Referans dosyaları, hash'leri, seçimi
ve değişiklikleri trace/artefaktlarda tutulur. Public vakalarda geliştirilmiş
referanslarla alınan public puanlar bağımsız genelleme kanıtı değildir.

`--review` seçeneği taze üretimden sonra deneysel bir model incelemesi çalıştırır;
varsayılan kapalıdır. İlk canlı denemede kapsam hatası ve süre aşımı görüldüğünden
henüz başarı artışı kanıtlanmış değildir. İnceleme, brief/API kaynaklarına göre durum geçişlerini, doğrulama ve teklif
sınırlarını, onaylanan araç yükünü ve kapanış sırasını kontrol edip gerekirse düzeltir.
`before-review.json`, `review.json` ve ilgili model mesajları aynı koşuda saklanır;
inceleme de builder'ın mevcut süre/çağrı sınırlarına tabidir. Çözülemeyen bulgular veya
yerel doğrulamadan geçmeyen düzeltmeler final workflow üretimini durdurur. Bu statik
model incelemesi davranış testi değildir; Freya practice hâlâ gerekir. Önceki adayları
kullanan geliştirme onarımları bu ek aşamayı çalıştırmaz.

Derleyici her `code_exec` parçasına kendi fonksiyon kapsamını verir. Yardımcıların
yerel değişkenleri host kapsamından bağımsız kalır; yalnızca anlamı açık olan global
bağlantılar yerel kapsama dönüştürülür. Belirsiz bağlar reddedilir. Python derleme
kontrolü kapsam hatalarını da yakalar; bu kontrol kodu çalıştırmaz.

`freya-optimize` standart pydantic-gepa/GEPA döngüsünü kullanır. Arama yalnızca builder'ın
workflow üretim talimatlarına eklenen, en fazla 12.000 karakterlik genel rehber metnini
değiştirir. Briefler, API şeması, kaynak doğrulaması, derleyici, Freya modeli ve puanlama
sabit kalır. Politika çıkarımı değişmez. Rehberin brief/API kurallarına üstünlüğü yoktur.
Vaka kimliği ve ISO tarihleri içeren adaylar reddedilir; bu basit kontrol, başka biçimde
kodlanmış vaka ezberini tamamen engellediğini iddia etmez. Ayrılmış verilerle sınama gerekir.

Bir GEPA örneği tek müşteriyi değil, **bir briefin bir veri bölümündeki bütün vakalarını**
temsil eder. Aynı rehber/brief için workflow bir kez üretilir; train ve validation bu
workflow'u kullanır. `metric_calls` müşteri görüşmesi değil brief-batch değerlendirmesi
sayısıdır. Amaç brief başına geçen vaka oranlarının ortalamasıdır. Her vaka ancak o
değerlendirmenin bütün tekrarları başarılıysa geçer. Varsayılan train 1, validation 3
tekrardır. Final holdout komutu her zaman 3 tekrar kullanır.

Dataseti `datasets/freya-v2/live/{train,validation,holdout}.json` altına koyun.
Bu dizin gitignore kapsamındadır. Her vakada `practice_session_case` ve
`metadata: {split, group_id, ...}` bulunmalıdır. Kimlik, saat ve hesap alanları kayıtlı
PUB seed ile birebir uyuşmalıdır. Gerçek vaka beklentileri ayrıca brief üzerinden
incelenmelidir; JSON kontrolü onların anlamsal doğruluğunu kanıtlamaz.

```bash
uv run --locked freya-optimize validate optimization.example.json
uv run --locked freya-optimize run optimization.example.json --out runs/gepa-study-001
```

Yollar config dosyasına göre çözülür. Validate ve run sadece train/validation dosyalarını
açar; holdout dosyasını okumaz, hash'lemez veya trace içine almaz. Vaka kimliği, scenario
group ve normalize edilmiş aynı scriptin veri bölümleri arasında paylaşılması reddedilir.
Yakın anlamlı kopyalar için ayrıca veri incelemesi gerekir.

İlk sınırlı arama için `optimization.bounded.json`, kaynak dataset manifestindeki
önceden belirlenmiş 16 öncelikli train vakasını ve değişmemiş 8 validation vakasını
kullanır. `diagnostics/prepare_priority_dataset.py` bu bölümü yeni dizine yazar ve
kaynak hash'lerini kaydeder; holdout'a erişmez. Sınırlar 8 brief-batch ölçümü, 4 üretim,
1 reflection ve 2 saattir. Reflection bütün briefleri birlikte görür; kusursuz bir
brief yüzünden atlanmaz. Yeni rehber ancak train skorunu iyileştirirse validation'a
girer. Kabul edilen öneri yolu, validation'ın üç tekrarı dahil 80 planlanmış sentetik
müşteri değerlendirmesi ve 16 simülasyon grubudur; Freya'nın otomatik tekrarları
buna eklenebilir. İki saat tamamlanma garantisi değildir.

```bash
uv run --locked freya-optimize run optimization.bounded.json --out runs/gepa-bounded-001
```

Timeout halinde önce kayıtlı uzak simülasyonun tamamlanıp toplanması gerekir; yeni
bir çalışma onun üstüne başlatılmaz. GEPA çalışma dizininden devam etme desteği yoktur.

Örnek config iki açık geliştirme workflow'unu `workflow_references` ile her iki briefe
birlikte verir. Builder yeni politikasını yalnızca brief/API'den çıkarır, sonra uygun
referansı seçip uyarlar. Referansların içerik hash'leri girdi manifestine ve seçim
kaydına girer; dosyaları değiştirmek dondurulmuş holdout kontrolünü geçersiz kılar.
Referanssız üretim için bu alanı kaldırın veya boş liste verin. Referanslı açık-vaka
skoru, bağımsız genelleme kanıtı değildir.

Varsayılan sınırlar 8 brief-batch değerlendirmesi, 6 farklı ajan üretimi, 2 reflection
ve toplam 2 saattir. Builder ve reflection Codex auth üzerinden Astra kullanır;
örnek config'te builder ve reflection high ayarındadır. Bunlar geliştirme araması sınırlarıdır;
tek final builder koşusunun 30 dakika kuralının yerine geçmez. Çalışma eşzamanlılığı 1'dir
ve ikinci optimizer aynı workspace kilidini alamaz. Altyapı, çözümlenmemiş
sandbox çıktısı, simülatör değerlendirme hatası veya eksik/çelişkili kanıt seçim
sürecini durdurur; bunlar adayın başarısız müşteri vakası olarak puanlanmaz.
Sağlık raporundaki tüm hata listeleri ve açık temiz-karşılaştırma bayrağı
zorunludur; eski veya eksik raporla seçim yapılmaz. Ham simülatör sonucu,
gerekçesi ve run kimliği hem normal koşuda hem `--collect` yolunda korunur.

Kayıtlar seçilen rehberi, bütün üretilmiş workflow'ları, simülasyon tekrarlarını,
başarısız konuşma kanıtlarını ve model mesajlarını korur. Autobench native
`pydantic_gepa`, `pydantic_ai`, `openai` ve `httpx` span'lerini kaydeder. Yerel struktür
doğrulama hataları aday kalitesi için sıfır puandır; altyapı hatasıysa seçim yapılmaz.

`selection.json` ve `selected-guidance.txt` seçim sonucudur; mevcut seçilmiş adayları
veya builder varsayılanını otomatik değiştirmez. Adayı seçtikten sonra ayrı holdout:

```bash
uv run --locked freya-optimize holdout runs/gepa-study-001/selection.json \
  --out runs/gepa-study-001-holdout
```

Bu komut dondurulmuş workflow'ları yeniden üretmeden değerlendirir, GEPA reflection
çalıştırmaz. Rehber, workflow, builder kaynak kodu veya brief/API değişmişse reddeder.
Holdout aynı senaryo gruplarını içeriyorsa çalıştırmaz. Sonuçlar ayrı yeni dizine
yazılır; önceki kayıtlar değiştirilmez. Holdout sonucunu aramaya geri beslemeyin.
Gizli final briefi için seçilen genel rehberle **taze** üretim, yayınlama ve
resmî Freya export'u aynı koşuda yapılır:

```bash
uv run --locked python diagnostics/finalize_submission.py \
  path/to/case-folder --mock-url https://YOUR-MOCK.workers.dev \
  --guidance runs/gepa-study-001/selected-guidance.txt \
  --slot final-brief-name --out runs/fresh-final
```

Önce mock servisini sözleşmesine göre uygulayıp yayımlayın. OpenAPI dosyası klasördeki
sözleşmeden çıkarılır; istenirse ikinci konumsal argümanla açıkça da verilebilir.
Rehber yolunu tamamlanmış ve incelenmiş çalışmanın seçimine bağlayın. Her koşu
için yeni çıktı dizini kullanın; gerektiğinde `--additional-brief` ekleyin.
Komut mevcut ajan/policy onarımını kabul etmez; simülasyon veya telefon çağrısı
başlatmaz. `submission.workflow.json` resmî export, `trace.jsonl` aynı koşunun
izi, `submission.json` ise export/trace hash'leri, platform kimlikleri ve süreyi
eşleştiren manifesttir. `submission_ready` yalnız bu paketin geçerliliğini
belirtir; müşteri vakalarında başarılı olduğu anlamına gelmez. Toplam bütçe
varsayılan olarak 1800 saniyedir; bu, yerel koşu bütçesidir. Süre aşımı veya doğrulama
hatası export paketini hazır saymaz. Resmî submission ayrıca takım/üyeler, GitHub repo
ve commit, yeniden çalıştırma komutu, workflow/trace yolları ve mock_url ister. Bu
komut submission e-postası göndermez.

Şimdiki üretim değerlendirme adaptörü kayıtlı A/B canlı vakaları içindir. Dataset
paketindeki yeni sigorta/bankacılık brieflerinin `local_mock_only` araç sözleşmeleri,
veri geldikten sonra ayrı bir sahte araç adaptörüne bağlanmalıdır; bu komut onları
canlı banka seed'i gibi çalıştırmaz. Gizli brief genellemesi henüz ölçülmüş değildir.

Ağ kullanmadan gerçek GEPA motoru, Pydantic AI builder/reflection ve Autobench zinciri:

```bash
uv run --locked python -m freya_hackathon.optimization_smoke \
  --out runs/gepa-environment-smoke-new
```

Bu test FunctionModel ve HTTPX MockTransport kullanır. Üretilen workflow'daki test
alanını kontrol ederek optimizasyon/veri akışını sınar; gerçek Freya başarısı değildir.
GEPA, Pydantic AI ve HTTPX izlerinin kaydı ve çevrimdışı replay/report da doğrulanır.

## Freya beklerken yerel davranış testi

`offline` incelenmiş workflow kodunu ayrı Python sürecinde, sahte banka yanıtlarıyla
çalıştırır. Ağ çağrısı yapmaz. Freya'nın kalıcı değişken birleştirmesini, deterministik
geçişleri ve API yanıt eşlemelerini uygular; desteklemediği geçiş operatörünü hata sayar.
Kod çalıştırma CPU ve süre sınırına tabidir; güvenilmeyen kod için güvenlik sandbox'ı değildir.

```bash
uv run --locked python -m freya_hackathon.practice_regressions \
  --out runs/offline-a/suite.json
uv run --locked python -m freya_hackathon.offline \
  runs/astra-a-003/workflow.json runs/offline-a/suite.json \
  --out runs/offline-a/results.json
```

Bu senaryolar mevcut Astra A adayının `speak/advance/call_state` yapısını kullanır.
Kabul/ret + soru, yeni tutar/tarih için tekrar onay, tatil düzeltmesi, tutar/tarih sınırları,
kaydedilmiş sözün değişmezliği, banka hatasında tek tekrar ve son işlem olarak tek kapanış
kontrol edilir. Başka workflow yapıları için fixture uyarlamak gerekir. Rapor workflow ve
senaryo SHA256'larını kaydeder; mevcut raporun üzerine yazmaz.

**Çıkarımlar test girdisidir.** Bu testler Freya'nın müşterinin cümlesinden doğru niyeti
çıkaracağını, doğru konuşacağını veya resmi puanı kanıtlamaz. GEPA'ya resmi skor gibi verilmez.

Brief B için `initialize/dialogue/decide/state` yapısını kullanan ayrı set:

```bash
uv run --locked python -m freya_hackathon.practice_b_regressions \
  runs/astra-b-002/workflow.json --out runs/offline-b/suite.json
uv run --locked python -m freya_hackathon.offline \
  runs/astra-b-002/workflow.json runs/offline-b/suite.json \
  --out runs/offline-b/results.json
```

B seti kimliği doğrulanmış müşteri durumundan başlar. Üç ürüne tek onay, ürün başına tarih
sınırı, kısmi kayıt hatası, aktarımda hiç not yazılmaması, kaydedilmemiş anlaşmaların aktarımda
iptali ve ilk ödenmemiş ürünün kapanış önceliğini sınar. B'nin kimlik konuşmasını sınamaz.

Canlıda gözlenen consent-only clauses, ürün adları, yanlış yıl ve reddedilmiş tarih
ifadeleri için `diagnostics/live_shape_regressions.py` kullanılır. `--stale-call` ile
kaydedilmiş PUB-12 görüşmesindeki eski tarih + açık ret durumu da doğrudan yeniden
oynatılır. Yeni extraction şemalarının istediği konuşma kanıtları fixture'lara eklenir;
beklenen banka davranışı değiştirilmez. Bu da deterministik yerel testtir, Freya skoru değildir.

10 Ekim son seçilen adaylar: A `runs/astra-a-015-repair`, B `runs/astra-b-008-repair`.
Tam resmi practice koşularında sırasıyla 10/10 ve 3/3 geçti; altyapı hatası gözlenmedi.
Bu sonuçlar aday başına tek başarılı tam koşudur; gizli brief skoru veya tekrarlanabilirlik
kanıtı değildir. Hash'ler ve kanıt yolları `diagnostics/current-candidates.json` içindedir.

Yerel deterministik testler A için 26/26, B için 13/13 geçti. Raporlar
`runs/offline-a-015-spoken/results.json` ve `runs/offline-b-008-repaired/results.json`
içindedir. Bunlar resmi Freya skorları değildir. A'nın ek konuşma tarihi örnekleri
`diagnostics/date_phrase_regressions.py` ile yeniden oynatılabilir.

## Mevcut politika ile hedefli onarım

Mevcut kod veya prompt içindeki küçük düzeltmelerde `--text-repair` eklenebilir.
Model yalnızca yol + eski/yeni metin üretir; eski metnin hedefte tam bir kez eşleşmesi
zorunludur. Builder değişiklikleri kaynak adayın kopyasına uygular, workflow'un tamamını
derler ve aynı yapısal doğrulamadan geçirir. Model çıktısı, düzenlemeler, tam workflow,
provenance ve Autobench izi saklanır. Yeni node/alan gibi yapısal değişiklikler için
normal tam üretim kullanılır. Bu seçenek de önceki üretimi kullanan geliştirme onarımıdır.

```bash
uv run --locked freya-builder \
  vendor/freya-v3/briefs/brief-a.md vendor/freya-v3/tools/openapi.json \
  --out runs/brief-a-repair \
  --reuse-policy-from runs/astra-a-002 \
  --repair-from runs/astra-a-002 --feedback diagnostics/brief-a-repair.txt
```

Brief, API veya kit referanslarından biri değişirse yeniden kullanım reddedilir. Politika
kaynak alıntıları tekrar doğrulanır. Önceki aday, kaynak trace ve model mesajları provenance
dizinine alınır; geri bildirim ve yeni model çağrıları kaydedilir. `instructions.json` bu koşunun
builder talimatlarını saklar. Bu seçenekler geliştirme içindir: önceki üretimin süresi yeni
süreye dahil değildir ve `execution-scope.json` bunu taze teslim koşusu olarak işaretlemez.
Nihai zamanlı teslimde bu yeniden kullanım seçenekleri verilmeden tam üretim yapılmalıdır.

Aynı kaynak adayın geçmişi bir kez kopyalanır. Autobench kaydında aynı içeriğe sahip
dosyalar tek artifact kullanır; `evidence-manifest` her özgün yolun hangi artifact'a
karşılık geldiğini ve kaynak SHA-256 değerini korur.
`diagnostics/audit_recordings.py RECORD...` kayıtları ağ çağrısı yapmadan yeniden açar,
rapor üretimini ve artifact/manifest referanslarının varlığını doğrular.

`diagnostics/extraction-probes.json` ek müşteri cümlelerini ve korunması gereken bilgileri
içerir. Bunlar henüz Freya'da çalıştırılmamış değerlendirme girdileridir.
