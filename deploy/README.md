# VPS koçürməsi

Botları öz kompüterinizdən bir Windows VPS-ə köçürmək üçün lazım olan hər şey.
Səbəb sadədir: strategiyaların giriş pəncərəsi Bakı vaxtı ilə **17:30–23:00**
aralığındadır (qış vaxtı bir saat gec), və mövqe açıldıqdan sonra SL/TP broker
tərəfində saxlandığı üçün kompüter yalnız **giriş** üçün lazımdır. VPS bu
pəncərəni 24/7 örtür.

## İki maşın, iki hesab

2026-09-21-dən: **VPS CFI hesabında, iş kompüteri FundingPips-də** işləyir.
Hər iki maşın eyni launcher-ləri qaçırır (onlar git-dədir), ona görə brokerin öz
ticker-i artıq `.bat` faylında yazılmır — hər maşın onu **öz `.env`-indən**
(`MT5_SERVER`) həll edir, `config/brokers.py` vasitəsilə:

```
.env MT5_SERVER  ->  broker profili  ->  həmin brokerin bizim simvola verdiyi ad
CFI11-Demo       ->  cfi             ->  XAUUSD -> XAUUSD_
FundingPips-Trial->  fundingpips     ->  XAUUSD -> XAUUSD
```

Launcher `--symbol XAUUSD` deyir, maşın isə hansı hesabda olduğunu bilir. State
faylları **həll olunmuş ticker-dən** adlanır (CFI-də `..._xauusd__weekendflat`,
FundingPips-də `..._xauusd_weekendflat`), ona görə iki brokerin açıq paper
mövqeyi və risk baseline-ı heç vaxt bir faylı paylaşmır. Bot
işə düşəndə terminalın həqiqətən həmin serverdə olduğunu yoxlayır; olmasa
**işləməkdən imtina edir** (`mt5/connector.ensure_logged_into`).

**Real order icazəsi hesaba görədir.** `deploy/demo_roster.txt` hər Demo botun
yanında hansı brokerdə işləyə biləcəyini yazır. İki canlı bot
(`OrbBreakoutwf_XAUUSD_Demo` və 2026-09-22-dən `FvgWindow_NDX100_Demo`) CFI-dədir:
onların lot ölçüsü, stop qaydası və bootstrap zərfi CFI-nin spread, swap və
0.01-lot minimumu üzərində ölçülüb.
FundingPips-də bütün Demo tasklar **bağlı** qalır, paper botlar işləyir —
`install_tasks.ps1` bunu özü edir.

## Bir VPS, iki broker

2026-09-22-dən FundingPips iş kompüterindən **eyni VPS-ə** köçür: iki checkout,
iki terminal, iki `.env`.

| | CFI | FundingPips |
|---|---|---|
| Checkout | `C:\tradebot` | `C:\tradebot_fp` |
| Terminal (`MT5_PATH`) | `C:\Program Files\MetaTrader 5\terminal64.exe` | ikinci MT5, **ayrı qovluğa** qurulur |
| `MT5_SERVER` | `CFI11-Demo` | `FundingPips-Trial` |
| Task Scheduler qovluğu | `\tradebot\cfi\` | `\tradebot\fundingpips\` |

İkisinin də launcher-ləri eyni adlıdır, ona görə:

- **Tasklar broker qovluğuna yazılır.** `install_tasks.ps1` qovluğu checkout-un
  `.env`-indən çıxarır və yalnız **öz qovluğundakı** taskları açıb-bağlayır.
  Əvvəl Demo-ları bütün maşın üzrə söndürürdü; FundingPips checkout-undan
  işə salınsaydı CFI-nin canlı botunu da söndürərdi. Kökdə (`\`) qalmış köhnə
  tasklar — yalnız **bu** checkout-un launcher-ini işlədənlər — yenidən
  qurulanda qovluğa köçür.
- **`MT5_PATH` iki `.env`-də də mütləqdir.** Onsuz `mt5.initialize()` tapdığı
  ilk terminala qoşulur, bot isə `mt5.login()` ilə **o terminalın hesabını
  dəyişərdi** — o terminaldakı o biri brokerin botlarının altından.
  `mt5/connector.initialize_terminal()` indi səhv terminala və başqa serverdə
  olan terminala qoşulmaqdan imtina edir; hesabat və kill-rule da ondan keçir.
  `preflight.py` maşında iki terminal görüb `MT5_PATH` boş olanda dayanır.
- **Resurs:** ölçülüb — ən yüklü anda 27 python prosesi ~0.5 GB tutur, 8 GB-lıq
  VPS-də 3.2 GB boş qalır. İkinci dəst sığır.

FundingPips checkout-unu qurduqdan sonra **iş kompüterindəki taskları
söndürün** — yoxsa eyni hesabın paper nəticələri iki yerə bölünər.

## Botu söndürəndə brokerdə qalanlar

Demo bot yalnız öz açdığını izləyir. Onu rosterdən çıxaranda və ya maşını başqa
brokerə keçirəndə, brokerdə hələ açıq mövqeyi və ya pending order-i varsa, o
sahibsiz qalır. 2026-09-21-də belə oldu: FundingPips-də açıq SPX500 mövqeyi və
müddəti bitməyən iki reverse order bir gün heç kimin xəbəri olmadan qaldı.

- **Hər checkout-un öz həftəlik hesabatı var** (`WeeklyReport`, şənbə 09:00,
  broker qovluğunda). `install_tasks.ps1` onu qurur. Hesabatın sonunda, eləcə də
  `preflight.py`-ın 7-ci bölməsində, hesabda **rosterdəki heç bir botun idarə
  etmədiyi** mövqe və order-lər sadalanır (`scripts/account_orphans.py`). Əllə
  yoxlamaq üçün: `.venv\Scripts\python.exe -m scripts.account_orphans`.
- **Heç nə avtomatik silinmir və ya bağlanmır.** Real mövqe ilə nə etmək insanın
  qərarıdır; yoxlama yalnız göstərir.
- **Reverse order-lər:** ORB botu `--reverse-on-stop` olmadan işləyəndə də öz
  simvolunda qalmış `_sar` order-lərini silir. Əvvəl flag götürüləndə köhnə
  order-lərə heç kim toxunmurdu.

## Nə köçür, nə köçmür

Repo daşınabiləndir — `.py`, `.bat` və `.vbs` fayllarının heç birində mütləq yol
yoxdur, launcher-lər `cd /d "%~dp0"` ilə başlayır. Maşına bağlı olan üç şey var:

| | Necə |
|---|---|
| Scheduled Task-lar | `install_tasks.ps1` avtomatik qurur |
| `.env` | əl ilə köçürün — git-də saxlanmır, **hansı broker olduğunu da bu deyir** |
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

`preflight.py` 0 qaytarmayınca 6-cı addıma keçməyin. 3-cü addımda `.env`-dəki
`MT5_SERVER` **bu maşının hesabını** göstərməlidir (VPS-də `CFI11-Demo`, iş
kompüterində `FundingPips-Trial`) — botlar ticker-i ondan çıxarır.

Broker dəyişdirmək üçün: MT5-i yeni hesaba salın, `.env`-i yeniləyin,
`.venv\Scripts\python.exe scripts/capture_symbol_specs.py` ilə həmin brokerin
spec-lərini götürün, `preflight.py` qaçırın, sonra `install_tasks.ps1`. Launcher
faylına **toxunmaq lazım deyil**.

## `preflight.py` nəyi yoxlayır

Hər bənd bu layihədə faktiki baş vermiş və real ticarət günü itirmiş bir
nasazlığa uyğundur:

- **AutoTrading bağlı** — real order `retcode 10027` ilə səssizcə rədd olunur.
  Bir dəfə bütün Demo sifarişləri günlərlə boşa çıxıb.
- **Simvol adı fərqli** — broker dəyişəndən sonra `NAS100` yox olub (burada
  `NDX100`-dür), botlar bir həftə mövcud olmayan simvolu yoxlayıb. İndi preflight
  əvvəlcə `.env`-dən brokeri tapır, sonra launcher-lərin adlandırdığı hər simvolu
  **həmin brokerin ticker-i ilə** MT5-dən soruşur, və terminalın `.env`-dəki
  serverdə olduğunu təsdiqləyir.
- **Köhnə `risk/` faylları** — başqa hesabın equity baseline-i 99% zərər kimi
  oxunub və bütün real ticarəti dayandırıb.
- **`tzdata` yoxdur** — Windows-da IANA bazası yoxdur, strategiyalar
  `America/New_York`-a bağlıdır və paket olmadan işləmir, xəta atır.
- **Saat yalan danışır** — 2026-09-21 07:24 UTC-də bu VPS-in saati 2 saat irəli
  atıldı (host agenti qonağın UTC-sinə öz **lokal** saatını yazdı, `dllhost.exe`),
  Windows Time 2 dəq 04 san sonra geri qaytardı. İki polling gələcək tarixli
  yazıldı və **heç bir yoxlama dinmədi**. NY 03:24 olduğu üçün heç nəyə başa
  gəlmədi; 09:30 NY-də olsaydı opening range səhv barlardan yığılardı.

## Saat: iki ayrı təhlükə, bir ölçü

`mt5/clock.py` brokerin öz divar saatını (MT5 hər tick-də xam göndərir)
`BROKER_TZ`-in həmin an üçün proqnozu ilə müqayisə edir. Uyğunluq **həm** OS
saatının, **həm** timezone-un düz olduğunu bildirir. Tam saatlıq fərq isə
birinin yanlış olduğunu — hansının, buradan bilinmir, amma ikisi də ticarəti
dayandırmalıdır.

| Nə tutur | Necə görünür |
|---|---|
| OS saatının sıçraması (yuxarıdakı hadisə) | broker saatı proqnozdan tam saat geridə/irəlidə |
| `BROKER_TZ` köhnəlməsi | eyni əlamət, amma davamlı |

**25 oktyabr 2026 riski:** `Europe/Bucharest` UTC+2-yə keçir, New York isə
**1 noyabrda**. CFI ABŞ qrafikinə baxırsa, o bir həftə bar vaxtları 1 saat
sürüşəcək. `mt5/rates.py`-dakı şərh açıq deyir: bu sabit FXTM-dən başqa heç bir
broker üçün təsdiqlənməyib. Yoxlama indi bunu özü tutacaq — 25 oktyabrdan sonra
ilk iş günü `preflight.py`-ı qaçırın. Fərq çıxsa, `.env`-ə `MT5_BROKER_TZ=...`
yazın (kod dəyişikliyi lazım deyil).

**Botlar nə edir:** fərq tam saatdırsa, **yeni giriş açılmır** (log-da
`entry_blocked_clock_drift`), açıq mövqe isə idarə olunmağa davam edir — onun
SL/TP-si onsuz da brokerdədir. Bazar bağlı olanda ölçmək mümkün olmur və bu
**dayandırma səbəbi deyil**, yoxsa botlar hər bazar günü dayanardı.

**Host saatını qonağa yazma yolunu bağlayın** (VPS-də, administrator kimi):

```powershell
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\VMICTimeProvider' -Name Enabled -Value 0
Set-Service vmictimesync -StartupType Disabled
w32tm /config /syncfromflags:manual /manualpeerlist:"time.windows.com,0x8 pool.ntp.org,0x8" /update
Restart-Service w32time
w32tm /resync /force
```

`MaxPosPhaseCorrection` / `MaxNegPhaseCorrection`-u **kiçiltməyin**. İndi 54000
saniyədir və məhz buna görə w32time 2 saatlıq səhvi geri qaytara bildi. Kiçiltsəniz,
növbəti sıçrayışdan sonra düzəliş rədd olunar və saat səhv qalar.

## Sonra

VPS-də MT5 terminalı **daim açıq və hesaba girmiş** qalmalıdır. RDP-dən
çıxarkən:

- **Disconnect** (pəncərəni bağlayın) → sessiya işləməyə davam edir
- **Sign out / Log off** → MT5 bağlanır, botlar dayanır

MT5-i "Windows ilə başlasın" rejiminə qurun ki, server yenidən qalxanda özü
işə düşsün.
