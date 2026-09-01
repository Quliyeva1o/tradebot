# XAUUSD 09:30 ORB + Liquidity-Sweep — Sessiya Handoff (2026-08-31 → 09-01)

Bu sənəd yeni kompüterdə davam etmək üçün yazılıb. Bu sessiyada tam yeni bir
strategiya (XAUUSD Opening-Range-Breakout + Liquidity-Sweep) sıfırdan
quruldu, backtest edildi, canlı sinifə köçürüldü və Paper-də smoke-test
edildi. Aşağıda NƏ EDİLDİ, NƏ TAPILDI, VƏ NƏ HƏLƏ BİTMƏYİB var.

---

## 0. TƏCİLİ: yeni kompüterdə əvvəlcə bunu et

`data/history/XAUUSD.ifx_M1.csv` (3.75 illik, 2022-12 → 2026-08, 1.33M bar)
**`.gitignore`-dadır (`data/**/*.csv`), commit-ə düşməyib.** Bu repo-nu başqa
kompüterdə klonlayanda bu fayl OLMAYACAQ. Bütün bu sessiyanın son
tapıntıları həmin fayla əsaslanır (köhnə `data/history/XAUUSD_M1.csv`
yalnız 2 illikdir, 2024-08-dən başlayır — KİFAYƏT DEYİL).

Yenidən yükləmək üçün (MT5 terminalı qoşulu olmalıdır, hesab məlumatları
`.env`-də):

```bash
python -c "
from datetime import datetime, UTC
from pathlib import Path
from data.download_history import download_symbol
from mt5.connector import MT5Connector
c = MT5Connector()
c.connect()
download_symbol('XAUUSD.ifx', 'M1', datetime(2022,12,1,tzinfo=UTC), datetime.now(UTC), Path('data/history'))
c.disconnect()
"
```

**Qeyd — simvol adı:** bu MT5 hesabında (IFXBrokers-Real, login 2129582)
qızıl `"XAUUSD.ifx"` adı ilədir, sadə `"XAUUSD"` DEYİL (`symbol_select`
sadə adla False qaytarır). Başqa kompüterdə fərqli hesab/broker qoşulubsa,
əvvəlcə `mt5.symbols_get()` ilə real simvol adını yoxla.

**Digər vacib qeyd:** bu hesabda NAS100/USTEC simvolu HEÇ YOXDUR (yalnız
Aus200, DAX, SPX, WS30 kimi indekslər var). Repo-dakı `data/history/USTEC_M1.csv`
başqa bir mənbədən/hesabdan gəlib — əgər onu da yeniləmək lazım olsa, başqa
bir MT5 hesabı/broker lazımdır.

---

## 1. Bu sessiyada yaradılan fayllar

| Fayl | Nə edir |
|---|---|
| `scripts/xauusd_orb_liquidity_sweep_backtest.py` | Əsas batch backtest (Setup A breakout + Setup B reversal), çoxlu CLI parametri (`--bar-minutes`, `--entry-window-end`, `--reversal-tp-mode`, `--enable-breakout`/`--no-enable-breakout`, `--spread-points`) |
| `strategy/xauusd_orb_liquidity_sweep.py` | Canlı sinif `XauusdOrbLiquiditySweepStrategy` — YALNIZ Setup B (reversal). **DİQQƏT: hələ M5-ə köklənib, aşağıdakı M15 tapıntısı ilə YENİLƏNMƏYİB — bax §5.** |
| `scripts/backtest_xauusd_orb_live_class.py` | Canlı sinifin batch skriptlə fidelity yoxlaması |
| `scripts/xauusd_orb_validation.py` | Bootstrap/walk-forward/MC/regime konveyeri (M5 üçün yazılıb, M15 üçün əl ilə python -c ilə işlədildi, skript yenilənməyib) |
| `run_live_xauusd_orb.py` | Paper/Demo runner (SR/First FVG runner-lərinin klonu) |

Bütün `artifacts/xauusd_orb_*.csv` və `artifacts/nas100_orb_reversal_trades.csv`
faylları müxtəlif təcrübələrin trade log-larıdır (aşağıda izah olunur hansı
hansıdır).

---

## 2. Əsas tapıntılar (xronoloji, ən vacibi əvvəldə)

### 2.1 Setup A (breakout) rədd edilib
OR-un qırılıb geri test edilməsinə əsaslanan "sadə breakout" setup-u HƏM
XAUUSD-də, HƏM NAS100-də spread-lə itki verir (PF 0.75-0.81). Canlı sinifə
HEÇ vaxt daxil edilməyib — yalnız Setup B (liquidity sweep + FVG reversal)
işlədilir.

### 2.2 Canlı sinif fidelity bug-u tapıldı və düzəldildi
İlk fidelity yoxlamasında canlı sinif batch-dan 14 trade artıq göstərdi
(115 vs 101). Səbəb: istinad faylı A+B BİRGƏ run-dan filtr edilmişdi — Setup
A-nın (artıq rədd edilmiş) trade-ləri eyni pozisiya slotunu tutub bəzi real
Setup B imkanlarını gizlədirdi. Düzəliş: batch skriptə `--no-enable-breakout`
(`enable_breakout=False`) əlavə edildi ki, Setup B TƏCRİD OLUNMUŞ yoxlanıla
bilsin. Bundan sonra **115/115 trade, eyni tarix/istiqamətlə tam üst-üstə
düşdü.**

`max_trades_per_day` default-u spec-in "2"-si əvəzinə **1**-ə endirildi —
canlı sinif memarlıqda pozisiya vəziyyətini bilmir (yalnız TradeSetup təklif
edir), və həm 101, həm 115 trade-lik yoxlamalarda HEÇ VAXT eyni gün 2-ci
trade baş verməyib — yəni bu, heç bir validasiya edilmiş edge-i itirmir.

### 2.3 Paper smoke-test keçdi
`run_live_xauusd_orb.py --symbol XAUUSD.ifx --timeframe M5 --paper` xətasız
işlədi (MT5-ə qoşuldu, PaperBroker $10,000 balans, 864 bar çəkdi, düzgün
"NO SIGNAL" nəticəsi verdi, çünki o an 09:30-10:00 NY pəncərəsindən kənar
idi). Risk-state faylı düzgün yaradıldı: `risk/daily_risk_state_xauusd_orb_xauusd_ifx_paper.json`.

### 2.4 KRİTİK TAPINTI — idealized giriş qiyməti real deyil
Backtest "giriş qiyməti" kimi FVG zonasının DƏQIQ kənarını (şamın low/high-ı)
götürür — sanki orada hazır limit order var idi. AMMA `execution/trade_manager.py`
və `execution/paper_broker.py` yoxlanıldı: **real sistem MARKET order
göndərir**, `execution/fill_simulator.py`-da `simulate_market_fill()`
NÖVBƏTİ ŞAMIN AÇILIŞINDA (+ spread/slippage) doldurur, zona kənarında YOX.

**Bu, bütün repo-ya aiddir (First FVG, SR də daxil) — yeni strategiyaya
məxsus deyil, sistemin ümumi konvensiyasıdır.**

Ölçülmüş təsir (M5, pəncərəli, spread daxil):

| | idealized (zone edge) | real (next_bar_open) |
|---|---|---|
| n | 115 | 86 |
| PF | 1.576 | **1.176** |
| PnL | $18,168 | **$4,657** |

`entry_fill_mode` parametri (`"zone_edge"` default / `"next_open"`) batch
skriptə əlavə edildi bu fərqi test etmək üçün.

### 2.5 M15 range, M5-dən DAHA GÜCLÜ çıxdı
OR-u M5 əvəzinə M15 şamı kimi götürəndə (`--bar-minutes 15 --entry-window-end 11:00`,
6 M15 şamı ≈ 6 M5 şamının vaxt nisbətini saxlamaq üçün) nəticə hər iki
fill-fərziyyəsində yaxşılaşdı:

| | M5 real fill | **M15 real fill** |
|---|---|---|
| n | 86 | 97 |
| Winrate | 41.9% | 49.5% |
| PF | 1.176 | **1.471** |
| Bootstrap (real edge ehtimalı) | (ayrıca yoxlanmayıb) | **98.0%** |

**Bu, hazırkı ƏN GÜCLÜ, TAM DOĞRULANMIŞ namizəddir: M15 range, pəncərəli
(09:45-11:00), real market-order fill, spread daxil PF≈1.47, n=97, bootstrap
98%.** Fayl: `artifacts/xauusd_orb_M15_nextopen_trades.csv` (gross) /
əlaqəli spread-adjusted hesablama koddadır.

### 2.6 10:00 kəsimini götürmək (bütün gün axtarmaq) İŞİ PİSLƏŞDİRDİ
"Niyə 10:00-dan sonra dayanırıq, bütün gün axtaraq" sualı test edildi —
nəticə əksinə oldu:

| | Pəncərəli (09:45-11:00) | Bütün gün |
|---|---|---|
| M15, real fill, n | 97 | 136 |
| M15, real fill, PF | **1.47** | 1.20 |
| Bootstrap | 98.0% | 92.3% (p5 CI artıq 1.0-ın altında: 0.95) |

**Nəticə: səhər pəncərəsi TƏSADÜFİ QAYDA DEYİL — real, faydalı filtrdir.**
Əlavə trade-lər (pəncərədən kənar) keyfiyyətcə zəifdir, ümumi PF-i aşağı
salır. Pəncərəni SAXLA.

### 2.7 FVG minimum-gap filtri demək olar əhəmiyyətsizdir
`fvg_min_gap_atr`-i 0.05-dən 0-a endirəndə (First FVG-nin öz batch
skriptindəki kimi minimum yoxdur) cəmi 97→101 trade fərqi (4%), PF demək
olar dəyişmir (1.632→1.590). Bu filtr darboğaz DEYİL.

### 2.8 Displacement filtri TAMAMİLƏ əhəmiyyətsiz çıxdı (GÖZLƏNİLMƏZ)
`displacement_atr_mult`-i 1.2-dən 0-a qədər (1.2/0.8/0.5/0.0) dəyişdirəndə
NƏTİCƏ HƏRFİ EYNİ QALDI: n=101, PF=1.59, hər dörd dəyərdə də bayt-bayta
eyni rəqəmlər. Bu, ya kодun bu filtri effektiv şəkildə heç vaxt
məhdudlaşdırmadığını göstərir (yəni FVG-i formalaşdıran şam onsuz da həmişə
"güclü" olur), ya da başqa bir gate (məs. `REVERSAL_LOOKBACK_BARS=4`, ya da
sweep-in özünün nadirliyi) artıq daha ciddi məhdudlaşdırıcıdır və
displacement heç vaxt reallıqda sınanmır. **BU, GEcə YARIMÇIQ QALAN
TƏDQIQATDIR — səbəb tapılmayıb, davam etdirilməlidir (bax §6).**

### 2.9 Niyə cəmi ~1.5-2 trade/ay?
964 gündən yalnız 129-97-i (fərqli fill/TF kombinasiyalarında) real trade-ə
çevrilir (~10-13%). Səbəb: OR sweep-i (tələ) baş verməli, SONRA 4 şamlıq
lookback pəncərəsində (`REVERSAL_LOOKBACK_BARS`) düzgün FVG+retest
tamamlanmalıdır, VƏ bu, dar (25-90 dəqiqəlik) səhər pəncərəsi daxilində
olmalıdır. Bu, dizaynın nəticəsidir (nadir, keyfiyyətli siqnal), bug deyil.

---

## 3. Ən son (2026-08-31, ~4/4 dəyər sınandı, DAVAM ETMƏYİB)

`displacement_atr_mult` sıfır təsirinin səbəbini tapmaq üçün növbəti addım
`REVERSAL_LOOKBACK_BARS` (hazırda 4, kod daxilində sabit, CLI-dan
dəyişdirilmir) həssaslığını yoxlamaq idi — BU BAŞLANMAYIB. Sual açıq qalır:
əsl darboğaz sweep-in nadirliyimidir, yoxsa 4-şamlıq lookback-dır?

---

## 4. Commit olunmamış, ƏLAQƏSİZ iş (bu sessiyanın ƏVVƏLİNDƏN)

`scripts/nas100_first_fvg_15m_backtest.py`-ə `--bias-filter` (HTF Daily
Bias filtri) əlavə edilmişdi (First FVG strategiyası üçün, ORB-dan ASILI
DEYİL). Nəticə: 5 illik, TP=2.5R, bias-filter aktiv → 73 trade, winrate
35.6%, +4.11R/+$4,105.68. Bu da hələ commit edilməyib, bu sessiyada ORB işi
ilə birlikdə commit olunacaq (istəyirsənsə ayrıca commit et deyə xəbərdarlıq
edilib, amma indi "hər şeyi commitlə" tapşırığı gəldi).

---

## 5. NÖVBƏTİ SESSİYA ÜÇÜN TODO (prioritet sırası ilə)

1. ~~`strategy/xauusd_orb_liquidity_sweep.py`-i YENİLƏ~~ **BİTDİ 2026-09-01**
   — `entry_window_end` default 10:00→11:00 dəyişdirildi (OR indi 09:30-09:45
   tək M15 şamıdır), docstring tam yeniləndi. **DİQQƏT — bu, FƏRQLİ
   maşında/hesabda edildi**: bu sessiya FXTM-Demo02 hesabında (login
   67660753) işlədi, orada qızıl sadəcə **"XAUUSD"**-dır, "XAUUSD.ifx" DEYİL
   (o, əvvəlki sessiyanın İFXBrokers-Real tədqiqat hesabına məxsus idi).
   Bu hesabın öz `data/history/XAUUSD_M1.csv` faylı artıq 2020-01-dən
   2026-08-27-dək (6.7 illik) mövcuddur — §0-dakı "yenidən yüklə" addımı
   BU HESAB ÜÇÜN artıq lazım deyildi. `run_live_xauusd_orb.py`-nin
   `--symbol`/`--timeframe` default-ları "XAUUSD"/"M15"-ə yeniləndi.
2. ~~§2.8-i həll et~~ **CAVABLANDI 2026-09-01** — həm batch, həm canlı sinif
   6.7 illik data üzərində alətləndirildi (instrumented): `REVERSAL_LOOKBACK_BARS`
   (=4)-in "expire" budağı BİR DƏFƏ BELƏ işə düşmür, nə M5-də, nə M15-də.
   Səbəb: entry pəncərəsi HƏMİŞƏ tam 5 şam enindədir (lookback-un öz
   4-şamlıq büdcəsi ilə eyni), ona görə pəncərənin özü sweep-i lookback-dan
   ƏVVƏL bağlayır. Deməli `reversal_lookback_bars` hazırkı pəncərə eni ilə
   TAM ARTIQDIR (redundant), real məhdudlaşdırıcı DEYİL — əsl darboğaz sweep-in
   öz nadirliyidir. Bunu dəyişmək heç nəyi dəyişməyəcək; pəncərəni DARALTMAQ
   real təsir edərdi (sınanmayıb).
3. ~~M15 real-fill üçün TAM battery~~ **BİTDİ 2026-09-01** —
   `scripts/xauusd_orb_validation.py` özü M5-dəymiş VƏ öz köhnə A+B-birgə
   filtrasiya səhvini daşıyırmış (heç vaxt düzəldilməmişdi) — hər ikisi
   düzəldildi (M15/next-open + `enable_breakout=False`), sonra tam battery
   işlədildi (n=137, bu hesabın 6.7 illik datası, spread net):
   bootstrap 97.7% (median PF 1.50), recency split PF 1.46→1.63
   (yaxşılaşan), walk-forward 5/7 fold PF≥1.0 (İKİ İTKİN FOLD 2020-2021-dir
   — bu konfiqurasiya öz ilk ~2 ilində qazanclı OLMAYIB, 2021-sonundan bəri
   ardıcıl müsbətdir), Monte Carlo REAL risklə (0.5%) gözlənilən qazanc
   CƏMİ +1.7% (6.7 il ərzində) — bu, "PF 1.33, +$17.8k" başlığından daha
   zəif, real başlıq rəqəmi budur. Regime: bütün 3 rejimdə müsbətdir amma
   BƏRABƏR DEYİL — trade-lərin əksəriyyəti (84/137) məhz ən zəif marjinal
   RANGING rejimindədir (PF 1.10). Tam yazı: strategy modulunun öz
   docstring-i, "Full battery run 2026-09-01" bəndi.
4. ~~Fidelity check TƏKRAR işlət~~ **BİTDİ 2026-09-01, VƏ dərinləşdirildi** —
   n=181 (live) vs n=187 (batch) tapıldı; 10 faizlik WR fərqi "məlum EOD
   gap"-a sadəcə istinad edilmədi, HƏR 6 fərqli trade AYRI-AYRI
   kökləndirildi (bax strategy modulunun öz docstring-i, "M15 port" bölməsi):
   4/6 — məlum, qəbul edilmiş harness fərqi (EOD force-close yoxdur, uzun
   müddət açıq qalan mövqe sonrakı setup-u bloklayır — real live sistem də
   eyni şeyi edərdi); 1/6 — datanın son günü, backtest sadəcə bitdiyi üçün
   trade heç vaxt "resolve" olmadı (real canlıda problem deyil); 1/6 —
   BATCH skriptin ÖZÜNDƏ kiçik bir korrektlik boşluğu tapıldı: `open_trade()`
   `risk = abs(entry_price - sl)` işlədir (işarəsiz), buna görə entry-nin
   sweep wick-dən "səhv tərəfdə" olduğu nadir hal (SL invert olub) səhvən
   keçərli trade kimi qəbul olunur; canlı sinif `risk = zone_top - sl`
   (işarəli) işlədir və bunu düzgün rədd edir. ~187 trade-dən 1-i təsirlənib
   — kiçik, aşağı prioritetli, BU SESSİYADA DÜZƏLDİLMƏYİB (batch skriptin
   öz tarixi rəqəmlərinə toxunardı).
5. ~~Docstring-i real-fill rəqəmlərinə uyğunlaşdır~~ **BİTDİ 2026-09-01** —
   modul docstring-i indi HƏM idealized zone-edge (n=187, PF 2.218), HƏM
   realistic next-open (n=137, PF 1.326) rəqəmlərini ehtiva edir, aydın
   şəkildə "next-open etibarlıdır, zone-edge tavandır" qeydi ilə.
6. ~~Paper runner-i M15 ilə smoke-test et~~ **BİTDİ 2026-09-01** —
   `run_live_xauusd_orb.py --symbol XAUUSD --timeframe M15 --paper` FXTM-
   Demo02-yə qoşuldu, 288 M15 bar çəkdi (3 gün), düzgün "NO SIGNAL"
   (not_in_session — o an NY 03:00 idi, pəncərədən kənar) qaytardı, xəta
   yox. Hələ AÇIQ: Scheduled Task kimi qurmaq (SR/First FVG Paper
   botlarının nümunəsi ilə) — bu, canlı/avtomatlaşdırılmış konfiqurasiya
   dəyişikliyi olduğu üçün İSTİFADƏÇİNİN AÇIQ TƏSDİQİ olmadan edilməyib;
   əvvəlcə bir neçə əl ilə smoke-test/paper-run dövrü, sonra istifadəçi ilə
   razılaşma tövsiyə olunur (bax [[project-live-bot-composition]] memory-də
   necə əvvəlki strategiyalar üçün edilib).

---

## Fayl xəritəsi (artifacts/)

- `xauusd_orb_liquidity_sweep_trades.csv` — son run-un ümumi (A+B) nəticəsi (üzərinə yazılır, referans üçün etibarsız) — hazırda 2026-09-01-in M15/zone-edge/bu-hesab run-unun nəticəsini daşıyır
- `xauusd_orb_M15_nextopen_thisaccount_trades.csv` — 2026-09-01, M15, pəncərəli, REAL fill, bu hesabın (FXTM-Demo02, "XAUUSD") 6.7 illik datası üzərində — n=137, PF 1.326, canlı sinifin gözlədiyi rəqəmə ən yaxın müstəqil təsdiq
- `xauusd_orb_reversal_trades.csv`, `_4yr.csv`, `_4yr_spread.csv` — ERKƏN, SƏHV (A+B birgə run-dan filtr edilmiş) Setup B nəticələri — İSTİFADƏ ETMƏ, §2.2-yə bax
- `xauusd_orb_reversal_ONLY_trades_4yr.csv` — DÜZƏLDİLMİŞ, təcrid olunmuş Setup B (M5, pəncərəli, idealized fill, n=115) — canlı sinif fidelity-nin əsaslandığı fayl
- `xauusd_orb_live_class_trades.csv` — canlı sinifdən (M5) çıxan nəticə, fidelity check
- `xauusd_orb_reversal_M15_trades.csv` — M15, pəncərəli, idealized fill
- `xauusd_orb_M15_nextopen_trades.csv` — **M15, pəncərəli, REAL fill — hazırkı ən yaxşı namizəd**
- `xauusd_orb_M5_windowed_nextopen_gross.csv` — M5, pəncərəli, real fill
- `xauusd_orb_M*_allday_*` — bütün-gün təcrübələri (§2.6, rədd edilib)
- `nas100_orb_reversal_trades.csv` — NAS100 üzərində eyni strategiyanın (A+B birgə filtr, YƏNİ SƏHV METODLA) yoxlanması — təkrar edilməlidir düzgün təcrid ilə

---

## 6. Portfel testi: SR + Bias ilə birgə (2026-09-01)

`scripts/portfolio_sr_orb_risk.py` (yeni skript) — SR+Bias (NAS100/30m) və
XAUUSD ORB-un (M15/next-open) EYNİ hesabda EYNİ VAXTDA işləməsini
simulyasiya edir. **Diqqət: bu, `scripts/portfolio_combined_risk.py`-dən
(First FVG+SR) FƏRQLİ modeldir** — o ikisi eyni simvolda (NAS100) idi və
real `trade_manager.py` bir pozisiya slotunu paylaşır; SR (NAS100) və ORB
(XAUUSD) FƏRQLİ simvoldur, real sistemdə heç bir slot paylaşmır (bax
`run_live_*.py`-in `_partition_positions()`-u, yalnız EYNİ simvollu
bot-ları bloklayır) — ikisi HƏQİQƏTƏN eyni anda açıq ola bilər. Ona görə
skript "hansı trade slot-u itirir" deyil, "iki bot EYNİ balansdan
müstəqil ölçüləndiyində birgə drawdown nə olur" sualına baxır (event-driven
balans modeli, hər trade-in giriş anındakı balansdan öz risk%-i qədər
risk götürməsi).

**Nəticə (2020-2026, hər ikisi öz sənədləşdirilmiş default riski ilə —
SR 0.25%, ORB 0.5%):**

| | Solo | |
|---|---|---|
| SR+Bias | n=813, PF 1.057, return +7.7%, max DD **11.0%** |
| XAUUSD ORB | n=137, PF 1.326, return +9.1%, max DD **5.8%** |
| **Birgə (1 hesab)** | return **+17.4%** (təxminən solo cəmi qədər), max DD **13.3%** |

- Birgə DD (13.3%) hər İKİ solo DD-dən (11.0%/5.8%) PİSDİR — First
  FVG+SR-in eyni-simvol nəticəsinə bənzər siqnal: "korrelyasiyasız = tam
  təhlükəsiz" fərziyyəsi burada da tam doğru deyil.
- AMMA fərq: qazanc da təxminən solo cəminə uyğun artıb (+17.4% ≈
  +7.7%+9.1%), First FVG+SR-dəki kimi "DD pisləşir, qazanc PROPORSIONAL
  artmır" effekti YOXDUR — DD-nin pisləşməsi ölçülü (2.3 faiz bəndi əlavə),
  əldə edilən əlavə qazancla təxminən mütənasibdir.
- Aylıq R-multiple cəmi korrelyasiyası: **+0.15** (zəif müsbət, demək olar
  sıfır) — orijinal 3-bot iddiasının (-0.21…+0.26) aralığında.
- `max_concurrent_open=2` — hər iki bot HƏQİQƏTƏN eyni anda açıq olub
  (gözlənildiyi kimi, fərqli simvol olduğu üçün).

**Nəticə:** SR+ORB cütlüyü First FVG+SR cütlüyündən DAHA sağlam
diversifikasiya nümunəsidir (DD artımı qazanc artımı ilə mütənasibdir),
amma "risk artmır" demək YALANDIR — birgə işlədəndə hər bot öz solo
DD-sindən daha böyük DD-yə məruz qala bilər. Risk büdcəsi qurarkən bunu
nəzərə al.
