# VPS koçürməsi

Botları öz kompüterinizdən bir Windows VPS-ə köçürmək üçün lazım olan hər şey.
Səbəb sadədir: strategiyaların giriş pəncərəsi Bakı vaxtı ilə **17:30–23:00**
aralığındadır (qış vaxtı bir saat gec), və mövqe açıldıqdan sonra SL/TP broker
tərəfində saxlandığı üçün kompüter yalnız **giriş** üçün lazımdır. VPS bu
pəncərəni 24/7 örtür.

## Nə köçür, nə köçmür

Repo daşınabiləndir — `.py`, `.bat` və `.vbs` fayllarının heç birində mütləq yol
yoxdur, launcher-lər `cd /d "%~dp0"` ilə başlayır. Maşına bağlı olan üç şey var:

| | Necə |
|---|---|
| Scheduled Task-lar | `install_tasks.ps1` avtomatik qurur |
| `.env` | əl ilə köçürün — git-də saxlanmır |
| `.venv` | yenidən qurulur |

**`data/history/*.csv` (~1GB) köçürməyin.** Canlı botlar barları MT5-dən çəkir;
o fayllar yalnız backtest və analiz skriptləri üçündür, onları isə iş
kompüterinizdə işlətməyə davam edəcəksiniz.

**`risk/` qovluğunu köçürməyin.** Köhnə maşının `day_start_equity` dəyəri yeni
maşında yalançı kill-switch tətikləyir — bu layihədə real ticarəti bir dəfə
günlərlə dayandırıb. Boş başlasın; fayllar ilk işləmədə özləri yaranır.

## Sıra

```powershell
# 1. Repo
git clone <repo-url> C:\tradebot
cd C:\tradebot

# 2. Python muhiti (yalniz ishleme paketleri, dev aletleri yox)
python -m venv .venv
.venv\Scripts\pip install -r deploy\requirements-live.txt

# 3. .env fayilini is kompyuterinizden kopyalayin

# 4. MT5 qurun, hesaba girin, Ctrl+E ile Alqoritmik Ticareti ACIN

# 5. Hazir olub-olmadigini yoxlayin -- hec ne deyismir, yalniz oxuyur
.venv\Scripts\python.exe deploy\preflight.py

# 6. Yalniz paper tasklari (temiz Windows .ps1-i bloklayir, ona gore -ExecutionPolicy)
powershell -ExecutionPolicy Bypass -File .\deploy\install_tasks.ps1 -PaperOnly

# 7. Bir sessiya izleyin, sonra qalanini qurun
powershell -ExecutionPolicy Bypass -File .\deploy\install_tasks.ps1
```

`preflight.py` 0 qaytarmayınca 6-cı addıma keçməyin.

## `preflight.py` nəyi yoxlayır

Hər bənd bu layihədə faktiki baş vermiş və real ticarət günü itirmiş bir
nasazlığa uyğundur:

- **AutoTrading bağlı** — real order `retcode 10027` ilə səssizcə rədd olunur.
  Bir dəfə bütün Demo sifarişləri günlərlə boşa çıxıb.
- **Simvol adı fərqli** — broker dəyişəndən sonra `NAS100` yox olub (burada
  `NDX100`-dür), botlar bir həftə mövcud olmayan simvolu yoxlayıb.
- **Köhnə `risk/` faylları** — başqa hesabın equity baseline-i 99% zərər kimi
  oxunub və bütün real ticarəti dayandırıb.
- **`tzdata` yoxdur** — Windows-da IANA bazası yoxdur, strategiyalar
  `America/New_York`-a bağlıdır və paket olmadan işləmir, xəta atır.

## Sonra

VPS-də MT5 terminalı **daim açıq və hesaba girmiş** qalmalıdır. RDP-dən
çıxarkən:

- **Disconnect** (pəncərəni bağlayın) → sessiya işləməyə davam edir
- **Sign out / Log off** → MT5 bağlanır, botlar dayanır

MT5-i "Windows ilə başlasın" rejiminə qurun ki, server yenidən qalxanda özü
işə düşsün.
