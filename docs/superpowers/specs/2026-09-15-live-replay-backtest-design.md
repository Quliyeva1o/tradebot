# Canlı Əkiz Backtest (Live Replay) — Dizayn

**Tarix:** 2026-09-15 · **Status:** dizayn təsdiqlənib, spec yoxlamada · **Branch:** `live-replay-backtest`

## 1. Sual

Hər deploy olunmuş ORB konfiqurasiyası üçün: **VPS-dəki bot bu tarixçədə nə edərdi?**
Oxşarı yox, eynisi. Mövcud batch backtest-lər (`scripts/nasdaq_orb_m1_breakout_backtest.py`,
`scripts/xauusd_orb_liquidity_sweep_backtest.py`) canlı koddan fərqli yazılıb və canlı
botla günlərin yalnız 58–76%-ində eyni trade-i açır; üstəlik sessiya açılışı boşluq
stoplarını −1R yazır (real −3R), swap yoxdur, Sweep isə gün sonunda bağlayır (canlı bağlamır).

Nəticə əsasən **R** ilə verilir (valyutadan asılı deyil), ikinci sütun **$50,000 hesabda $**.

## 2. Əhatə

On bir konfiqurasiya (on birincisi 2026-09-16-da əlavə olundu), parametrlər iş vaxtı birbaşa `run_live_orb_*.bat` fayllarından oxunur
(əl ilə köçürülmür — `scripts/live_vs_backtest_report.py`-dakı kimi):

| Task | Runner / sinif | Simvol | Parametrlər | Hesab |
|---|---|---|---|---|
| OrbBreakout_XAUUSD_Demo | nasdaq_orb / `NasdaqOrbM1BreakoutStrategy` | XAUUSD | OR 15m, scan M1, 4R | Demo |
| OrbBreakout_NDX100_Demo | 〃 | NDX100 | OR 30m, scan M5, 4R | Demo |
| OrbBreakout_SPX500_Demo | 〃 | SPX500 | OR 60m, scan M1, 4R | Demo |
| OrbBreakout_DJI30_Demo | 〃 | DJI30 | OR 60m, scan M1, 4R | Demo |
| OrbBreakout_JP225_Demo | 〃 | JP225 | OR 15m, scan M1, 4R | Demo |
| OrbSweep_GER40_Demo | xauusd_orb / `XauusdOrbLiquiditySweepStrategy` | GER40 | M15, sinif defoltları | Demo |
| OrbBreakout_XAUUSD_Paper | nasdaq_orb | XAUUSD | OR 60m, scan M1, 3R | Paper |
| OrbBreakout_GER40_Paper | nasdaq_orb | GER40 | OR 30m, scan M1, 4R | Paper |
| OrbSweep_XAUUSD_Paper | xauusd_orb | XAUUSD | M15, sinif defoltları | Paper |
| OrbSweep_JP225_Paper | xauusd_orb | JP225 | M15, sinif defoltları | Paper |
| OrbBreakoutwf_XAUUSD_Paper | nasdaq_orb | XAUUSD | OR 60m, scan M1, 3R, `--weekend-flat` | Paper |

Hamısı `--risk-per-trade-pct 0.005`, LONG-only (Breakout defoltu). Sweep defoltları
(`XauusdOrbLiquiditySweepConfig`): OR 09:30 M15 şamı, yeni setup 11:00-a qədər, TP 2R,
gündə 1 trade, sweep-dən sonra 4 bar, displacement 1.2 ATR, FVG ≥ 0.05 ATR, SL buferi
0.1 ATR, risk ≤ 3 ATR, Wilder ATR(14).

Paper konfiqurasiyalar da **Demo broker kimi** simulyasiya olunur (broker tərəfli SL/TP):
sual strategiyanın öz nəticəsidir, PaperBroker-in poll qiymətində bağlama artefaktı deyil.

**Əhatədən kənar:** FundingPips hesab qaydaları (5% günlük limit və s.), botlar arası portfel
effekti, xəbər filtri, requote/reject, VPS kəsintiləri, 2026-09-15-də əlavə olunmuş
`FvgWindow_NDX100_Paper` (limit order botudur, öz parity skripti var —
`scripts/first_fvg_paper_parity.py`; lazım olsa sonra əlavə edilir).

## 3. Data

- **Bar-lar:** `data/history/fundingpips/<SYMBOL>_M1.csv` — bid OHLC, `spread` sütunu artıq
  qiymət vahidindədir (`mt5/rates.py`: points × point), `time` server saatıdır
  (Europe/Bucharest, `data/download_history.py`). Başlanğıclar: NDX100/SPX500/DJI30/XAUUSD
  2020-01, GER40 2021-09, JP225 2024-04. İşə salmazdan əvvəl 2026-09-15-ə qədər yenilənir
  (indi 2026-09-14 06:xx-də bitir, validasiya həftəsi yarımçıqdır).
- **M5/M15:** M1-dən MT5 qaydası ilə qurulur (bar açılış vaxtı ilə etiketlənir, yalnız
  bağlanandan sonra görünür). Mövcud native `NDX100_M15`, `GER40_M15`, `XAUUSD_M15`
  faylları ilə üst-üstə düşən tarixlərdə müqayisə olunur (§6 G2).
- **Tick-lər:** MT5 `copy_ticks_range`; FundingPips-də indekslər üçün 2025-03-13-dən,
  XAUUSD üçün 2026-05-04-dən var. Yalnız boşluq stoplarında və spread kalibrasiyasında
  işlənir, diskə keşlənir ki, təkrar işləmə MT5 tələb etməsin.
- **FX:** GER40 mənfəəti EUR, JP225 JPY-dir → FundingPips EURUSD və USDJPY D1 bağlanışları
  (yalnız $ sütunu üçün).
- **Simvol parametrləri:** MT5-dən bir dəfə çəkilmiş snapshot
  (`backtest/live_replay/symbol_specs.json`): point, contract size, volume_min/step/max,
  swap_mode/long/short/rollover3days, profit currency, lot başına marja nisbəti. 2026-09-15
  dəyərləri: XAUUSD contract 100, swap mode 1 (points) long −67.986, üçqat çərşənbə;
  NDX100 contract 20, SPX500 50, DJI30 5, GER40 25, JP225 10, hamısı swap mode 5 (illik %),
  üçqat cümə, NDX100/SPX500/DJI30 long −7.33%, GER40 −5.41%, JP225 −3.44%.
- **pandas 3:** `.venv`-dəki pandas 3.0.3 datetime indekslərini `datetime64[us]` edir;
  ns sabitləri ilə `.asi8` hesabı səssizcə sıfır trade verir. Vaxt riyaziyyatı `Timestamp`/
  `timedelta` ilə aparılır.
- Data gitignored-dir və əsas checkout-da qalır; skript `--data-dir` qəbul edir.

## 4. Simulyasiya modeli (hər bot müstəqil)

### 4.1 Saat
- VPS Task Scheduler hər 2 dəqiqədən bir, **cüt dəqiqədə ~+6 s** server saatı ilə işləyir
  (real açılış deal-ları: 16:46:04, 16:48:06, 17:10:06, 17:26:04, 18:30:05, 18:40:06, 18:46:05).
- Poll yalnız poll anından əvvəl **bağlanmış** bar-ları görür (`copy_rates_from_pos(..., 1, n)`).
- Hər poll `run_once`-un sırası ilə: öz açıq mövqeyi varsa idarə et və çıx; yoxdursa siqnal axtar.

### 4.2 Siqnal (runner semantikası)
- Setup yalnız **eyni NY günündəndirsə** və `bars_since_signal ≤ grace` olarsa işlənir:
  grace = `max(1, 4 dəq // bar dəqiqəsi)` → M1: 4, M5: 1, M15: 1.
- `traded_setups` qaydası: bir setup_id yalnız bir dəfə açılır (`execution/traded_setups.py`).
- **Breakout:** strategiya nümunəsinə bar-lar ardıcıl verilir. Bu, runner-in hər poll-da
  `DEFAULT_LOOKBACK_DAYS = 2` günlük bar-ları sıfırdan oynatmasına bərabərdir, çünki sinif
  vəziyyəti hər NY günü sıfırlanır və bugünkü bütün bar-lar həmişə pəncərəyə sığır.
  Bərabərlik testlə sübut olunur (§6 G2).
- **Sweep:** ATR(14) runner-in pəncərəsindən (`DEFAULT_LOOKBACK_DAYS = 3`, 288 M15 bar)
  hər dəfə sıfırdan qurulur, ona görə ardıcıl vermək eyni deyil. Simulyator runner-i
  **dəqiq** təkrarlayır: hər yeni bağlanan M15 bar-da təzə nümunə, son 288 bar-la.
  (Poll-lar arasında yeni bar yoxdursa nəticə dəyişmir, ona görə hər poll-da təkrar lazım deyil.)

### 4.3 Giriş
- Market order poll anında: BUY **ask**, SELL **bid**. Tick olan dövrdə (indekslər 2025-03+,
  XAUUSD 2026-05+) fill poll dəqiqəsinin 6-cı saniyəsindən sonrakı ilk tick-in ask/bid-idir.
  Real Demo deal-ları :04–:06 möhürlüdür və MT5 saniyəni kəsir, yəni orta real gecikmə ~5.8 s-dir;
  bu model 2026-09-10..14-ün 10 girişindən 9-unu 2.5 punkt dəqiqliklə verir, bar açılışı isə
  15 punkta qədər yanılırdı (G3 tapıntısı, 2026-09-15). Tick yoxdursa: poll-u ehtiva edən M1
  bar-ın açılışı + həmin bar-ın spread-i.
- SL/TP setup-dan olduğu kimi götürülür: Breakout-da SL = OR low, TP siqnal bar-ının
  close-undan; Sweep-də giriş/TP FVG kənarından. Fill fərqli olduğu üçün real R ≠ nominal R —
  canlıdakı kimi.
- XAUUSD komissiyası: lot başına $5 açılışda (yalnız 2026-09-14 deal-ında görünüb; əvvəlki
  XAUUSD deal-larında yoxdur — həssaslıq kimi qeyd olunur). İndekslərdə komissiya yoxdur.

### 4.4 Lot
- Real `execution/position_sizer.PositionSizer` (0.5%, **balans** üzərindən, balans böyüdükcə
  mürəkkəb artım), `resolve_entry_price(setup)` və setup SL-i ilə — `TradeManager.open_trade`
  kimi. volume_min/step/max yuvarlaması, minimum lota qaldırılma qeydə alınır.
- TradeManager-in 20% marja tavanı snapshot-dakı marja nisbəti ilə.
- Başlanğıc balans $50,000.

### 4.5 Çıxış (broker tərəfli)
- Hər M1 bar-da `execution/level_fill.exit_fill` — PaperBroker-in `level_fills` rejiminin
  işlətdiyi eyni funksiya:
  bar səviyyənin o tayında açılırsa açılış qiymətinə (giriş bar-ında yox); eyni bar-da SL
  və TP → SL; 30 dəqiqədən uzun fasilədən sonrakı ilk bar stopu keçirsə və close daha
  pisdirsə → close.
- `exit_fill` bid bar-ları üzərində işləyir. BUY üçün bu doğrudur; **SELL üçün bar ask-a
  sürüşdürülür** (OHLC + həmin bar-ın spread-i), çünki SELL stopu və TP-si ask ilə tetiklənir.
- Giriş bar-ının fill-dən əvvəlki hissəsi ayrıla bilmədiyi üçün giriş bar-ı `entry_bar=True`
  ilə yoxlanır (açılış qaydası tətbiq olunmur); neçə trade-in giriş bar-ında bağlandığı
  hesabatda sayılır.
- Eyni bar-da SL+TP halları sayılır.

### 4.6 Boşluq stopları
- Tick olan dövrdə (indekslər 2025-03-13+, XAUUSD 2026-05-04+): fasilədən sonra stopu keçən
  ilk real tick-in qiyməti (BUY üçün bid). Köhnə cümə re-quote-u səviyyəni keçmədiyi üçün
  özü seçilmir — 2026-09-14 hadisəsinin mexanizmi.
- Tick olmayan dövrdə: `exit_fill`-in close proksisi. Həssaslıq üçün "səviyyədə dolur"
  variantı (köhnə backtest fərziyyəsi) ayrıca sütunda verilir — boşluğun təsiri iki
  tərəfdən məhdudlaşdırılır.

### 4.7 Swap
- Mövqe açıq qalan hər server gecə keçidində (00:00):
  - mode 5 (indekslər): `lots × contract × əvvəlki bağlanış × illik% / 360`, profit valyutasında;
  - mode 1 (XAUUSD): `lots × contract × point × swap points`;
  - üçqat gün `swap_rollover3days`-dən.
- Tarixi swap dərəcələri saxlanmayıb → bütün tarixçəyə 2026-09-15 dərəcələri (məhdudiyyət).
- Yoxlanıb: 2026-09-14 NDX100 0.01 lot, cümə→bazar ertəsi, 29466 × 20 × 0.01 × 7.33%/360 × 3
  = $3.60; real deal −$3.59.

### 4.8 Həftə sonu qaydası (`--weekend-flat`)
- Cümə 23:40 server vaxtından həftə açılana qədər: açıq mövqe həmin yoxlamanın qiymətinə
  bağlanır (giriş kimi qiymətləndirilir: uzun bid, qısa ask, tick olan yerdə 6-cı saniyənin
  kotirovkası), yeni giriş açılmır. Həftə sonu swap-ı tutulmur.
- Kəsmə runner-in `weekend_flat_due` funksiyası ilə dəqiqə-dəqiqə eynidir, saat dəyişən həftələr
  daxil (`tests/live_replay/test_weekend_flat_parity.py`).
- Batch backtest bu qaydanı bilmir; hesabatda bu botun "köhnə" sütunu mövqe saxlayan versiyadır.

## 5. Arxitektura

Yeni paket `backtest/live_replay/`, hər modul bir iş görür:

| Modul | Nə edir | Nədən asılıdır |
|---|---|---|
| `configs.py` | `.bat` fayllarından `BotConfig` (task, runner növü, simvol, OR, scan, R, risk, paper) | yalnız fayl sistemi |
| `market.py` | M1 yükləmə (UTC, bid, spread), MT5-stil M5/M15, tick keşi, FX seriyası | pandas, MT5 (yalnız keş dolanda) |
| `specs.py` + `symbol_specs.json` | `SymbolSpec`: kontrakt, lot qaydaları, swap, marja, valyuta | — |
| `signals.py` | poll anına qədər bağlanmış bar-lardan işlənə bilən setup (runner qaydaları ilə) | real strategiya sinifləri |
| `broker.py` | giriş fill-i, lot, `exit_fill` ilə çıxış, boşluq tick-i, swap, komissiya → `TradeRecord` | `PositionSizer`, `level_fill` |
| `engine.py` | poll saatı, mövqe qapısı, traded-setups; `run(config, data, specs, flags)` | yuxarıdakılar |
| `metrics.py` | PF, net/orta R, win%, max DD, seriyalar, pəncərələr, 6 aylıq bloklar, filtrlər | — |

- `scripts/live_replay_backtest.py` — CLI (`--configs`, `--data-dir`, `--start/--end`,
  `--ablate`), CSV və hesabat yazır.
- `scripts/capture_symbol_specs.py` — MT5-dən snapshot (yalnız oxuyur).
- **Realizm bayraqları** (`flags`): poll/grace saatı, ask/bid spread, tick ilə giriş, boşluq fill-i, swap,
  komissiya — hər biri ayrıca söndürülə bilər. Hamısı sönük olanda model köhnə batch
  fərziyyələrinə yaxınlaşır; fərqin parçalanması (§7) bununla ölçülür.
- **Performans hədəfi:** konfiqurasiya başına < 5 dəqiqə. Mövqe yoxdursa və NY sessiya
  pəncərəsindən kənardırsa poll-lar atlanır; açıq mövqe poll-la yox, M1 bar-la idarə olunur.

## 6. Nəticəyə güvənmə şərtləri (hər biri keçməlidir)

- **G1 — vahid testlər (TDD):** saat/poll görünməsi, grace, traded-setups, BUY/SELL fill,
  SELL ask sürüşdürməsi, `exit_fill` inteqrasiyası, boşluq tick-i və proksi, swap (üçqat gün
  daxil), lot yuvarlaması və marja tavanı, `.bat` parse.
- **G2 — bərabərlik:** (a) Breakout: ardıcıl vermə == runner-in 2 günlük tam təkrarı,
  hər Breakout konfiqurasiyası üçün ən az 30 təsadüfi gündə eyni setup-lar; (b) Sweep:
  hər Sweep konfiqurasiyası üçün ən az 30 təsadüfi gündə, sessiya pəncərəsindəki hər poll-da
  simulyatorun işlənə bilən setup-ı (id, giriş, SL, TP) runner-in öz təkrar döngüsünün
  (`run_live_xauusd_orb._evaluate_for_new_trade`-dəki kimi təzə nümunə + son 288 M15 bar)
  həmin poll üçün verdiyi setup ilə eynidir; (c) M1→M15 qurulması native M15 fayllarla üst-üstə düşən günlərdə eyni OHLC.
- **G3 — real trade-ləri təkrarlama** (`tests/fixtures/live_replay/`):
  - `live_deals_2026_09.csv` — köhnə hesab 40000281947-in 13 Demo deal-ı (MT5 tarixçəsindən,
    2026-09-15). Yoxlanan: `use_in_validation = yes` (7) və `entry_only` (3, FundingPips
    məcburi bağlanışları). 2026-09-07..09 deal-ları başqa konfiqurasiya/əl ilə TP/tick_value
    xətası dövrünə aiddir, yoxlanmır.
  - `paper_trades_2026_09.csv` — VPS-in NDX100 və XAUUSD Breakout paper trade-ləri (6):
    yalnız giriş, SL/TP, setup_id (paper çıxışı poll qiymətindədir).
  - Pəncərə 2026-09-10..14 (VPS, sabit roster). Konfiqurasiya tarixə görə: XAUUSD Demo
    09-13-ə qədər 60m/3R, 09-14-dən 15m/4R; XAUUSD Paper əksinə (commit 190b319).
  - Keçmə meyarı: həmin günlərdə hər bot üçün eyni setup-lar (artıq/əskik yox); giriş
    realdan həmin dəqiqənin :04–:07 kotirovka aralığı + ~0.3 spread sürüşmədən çox fərqlənmir
    (sabit punkt dözümlülüyü yanlışdır: JP225/NDX100 bu 3 saniyədə 15 punkta qədər hərəkət edib); SL/TP 0.01 dəqiqliklə eyni; çıxış növü eyni; SL fill-i
    ≤ 1 spread; boşluq stopları (12480723, 12494940) tick modeli ilə real R-dən ≤ 0.3R;
    swap (12377152 −1.19, 12494940 −3.59, 12480723 −1.97) ≤ 5% fərq.
- **G4 — spread kalibrasiyası:** tick olan dövrdə simulyasiya giriş anlarında bar spread-i
  ilə tick spread-inin medianı müqayisə olunur; bar spread-i > 10% aşağıdırsa simvol üzrə
  əmsal tətbiq edilir və hər iki nəticə göstərilir.
- **G5 — "köhnə" sütunun düzgünlüyü:** köhnə batch skriptləri eyni parametrlərlə yenidən
  işlədilir və qeydə alınmış rəqəmləri (məs. GER40 Sweep n=100 PF 1.548; roster
  şərhlərindəki PF-lər) eyni son tarixdə ±0.01 PF daxilində verməlidir, yoxsa fərq izah olunur.

Şərtlərdən biri keçməsə, rəqəm verilmir — əvvəl səbəb tapılır.

## 7. Nəticə

- **Hesabat** `LIVE_REPLAY_BACKTEST_REPORT.md` (Azərbaycanca): metod, G1–G5 nəticələri,
  hər konfiqurasiya üçün köhnə vs əkiz — n, win%, PF, net R, orta R, max DD (R),
  ən uzun itki seriyası — tam tarixçə / son 2 il / son 1 il; 6 aylıq blokların müsbət faizi.
- **Üç filtr** hər sətirdə açıq: tam PF > 1, son 1 il PF > 1, blokların ≥ 60%-i müsbət.
  Qeyd: orijinal filtr seçim walk-forward-unun fold-larıdır; sabit konfiqurasiya üçün
  ölçülə bilən ekvivalent sabit 6 aylıq bloklardır — bu fərq hesabatda yazılır. JP225
  (2.4 il data) "az tarixçə" kimi işarələnir, "keçmədi" kimi yox.
- **Fərqin parçalanması:** realizm bayraqlarını bir-bir söndürərək R fərqi — poll/grace
  (qaçırılan və gecikən siqnallar), spread, boşluq stopları, swap, komissiya, Sweep-in
  gün sonu/TP bağlanması.
- **$50K:** hər konfiqurasiya ayrıca hesab kimi — son balans, max DD %.
- **Trade-trade CSV:** `artifacts/live_replay/<task>_trades.csv`.
- Botlarda heç nə dəyişmir; 2026-10-12 dondurma planı qüvvədədir.

## 8. Məhdudiyyətlər (hesabatda da yazılır)

- Swap dərəcələri və spread tarixi dəyərləri deyil, M1 bar spread-i və indiki swap-dır.
- Tick-dən əvvəlki boşluq stopları proksidir (iki variantla məhdudlaşdırılıb).
- Poll saniyəsi sabit +6 s götürülür; real jitter 4–6 s-dir.
- Requote, reject, AutoTrading/kill-switch kəsintiləri və FundingPips məcburi bağlanışları
  modelləşdirilmir.
- MT5 native M5/M15 ilə M1-dən qurulan bar-lar arasında nadir fərq ola bilər (G2c ölçür).
