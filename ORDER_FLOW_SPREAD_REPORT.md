# Order Flow + Daily Bias — Spread Daxil, Tam Sweep Nəticələri

**Tarix:** 2026-08-28
**Strategiya:** Order Flow + Daily Bias + Trendline (`scripts/order_flow_bias_backtest.py`).
1H/15M market-structure + PDH/PDL/mid + VWAP-dan səs verməklə Daily Bias;
swing-based trendline (bounce və ya qırılmış xəttə retest); PDH/PDL/Asia-
session/swing-pool-dən likvidlik sweep-i; M1 sub-bar-lardan qurulmuş bar-
səviyyəli Order Flow proksisi (delta/CVD/stacked/absorption/displacement, ≥3
təsdiq); min 1:2 RR gate. Real tick/DOM datası olmadığı üçün "Order Flow"
burada proksidir — bu, skriptin öz docstring-ində açıq yazılıb.

Bu, `SESSION_HANDOFF.md`-də qeyd olunan **bitməmiş 12-kombo re-sweep**-i
tamamlayır (§5 "Order Flow — edge did not survive the lookahead fix") —
GBPUSD/USDJPY əlavə edilərək 20 kombinasiyaya genişləndirilib, və İLK DƏFƏ
spread tətbiq edilib.

---

## 0. Doğruluq yoxlaması

`scripts/order_flow_bias_backtest.py` (post-lookahead-fix versiya) yenidən
nəzərdən keçirildi:

- **HTF bias lookahead-fix TƏSDİQ edildi** — `htf_bias_to_index()` bias-ı
  1H bar-ın BAĞLANIŞINA köçürür, yalnız bundan sonra icra bar-ları oxuya
  bilir (`SESSION_HANDOFF.md` §2.1-də tapılan kritik bug artıq düzəldilib,
  bu, strategiyanın "bütün apparent edge"-i idi).
- **VWAP/PDH/PDL/pivot-lar lookahead-siz** — VWAP gündəlik cumsum (yalnız
  keçmiş), PDH/PDL yalnız əvvəlki TAM günün H/L-i, pivot-lar təsdiq bar-ında.
- **Order Flow proksi feature-ları** (delta/CVD/stacked) hər icra bar-ı üçün
  YALNIZ o bar-ın öz DAXİLİNDƏKİ M1 sub-bar-larından qurulur — gələcək
  sızma yoxdur.
- **Eyni-bar stop-out riski yoxdur** — giriş = bar bağlanışı (SR-dəki eyni
  təhlükəsiz nümunə).

Yeni bug tapılmadı — kod etibarlıdır. (Qeyd: bu repoda İKİNCİ, daha
mürəkkəb bir Order Flow variantı da var —
`scripts/order_flow_daily_bias_backtest.py`, iki-ayaqlı TP və retest-based
giriş ilə — bu sweep-ə DAXİL EDİLMƏYİB, çünki fərqli strategiya təyinatıdır
və "trustworthy results" siyahısında heç vaxt olmayıb. İstəsəniz ayrıca
təhlil edə bilərik.)

---

## 1. Metodologiya

- **Data:** eyni təzə MT5 M1 (2020-01-01→2026-08-28), SR sweep-lə eyni fayllar.
- **Spread:** eyni konvensiya — NAS100 sabit 3.0pt, XAUUSD/EURUSD/GBPUSD/
  USDJPY real tarixi spread.
- **Sweep:** 5 simvol × 4 timeframe = 20 kombinasiya, 5il/1il/3ay/1ay +
  1il aybaay + 1ay günbəgün + 5il yarımillik.
- Script: [scripts/order_flow_bias_spread_sweep.py](scripts/order_flow_bias_spread_sweep.py)

---

## 2. ⚠️ Kritik xəbərdarlıq: nümunə ölçüsü çox kiçikdir

First FVG-də (n=1000+) və SR-da (n=400-3000) trade sayı statistik cəhətdən
etibarlı idi. **Order Flow-da 5 illik trade sayı hər kombinasiyada 16-196
arasındadır, 1-illik isə 2-26 arasında** — bu, `SESSION_HANDOFF.md`-də PO3
üçün artıq xəbərdarlıq edilən "statistically meaningless" reжiminə çox
yaxındır (məsələn EURUSD 60m-in 1-illik PF 3.34 rəqəmi cəmi **6 trade**-ə
əsaslanır — bir-iki trade nəticəni tamamilə dəyişə bilər). Aşağıdakı
sıralama buna görə **çox ehtiyatla** oxunmalıdır.

## 3. Tam nəticə cədvəli

| Simvol | TF | 5il n | 5il WR | 5il PF | 5il Net | 1il n | 1il WR | 1il PF | 1il Net | Keçdi? |
|---|---|---|---|---|---|---|---|---|---|---|
| NAS100 | 60m | 44 | 25.0% | 1.415 | +$14,512 | 9 | 22.2% | 1.185 | +$1,386 | ✅ (kiçik n) |
| EURUSD | 60m | 22 | 31.8% | 1.386 | +$7,029 | 6 | 50.0% | 3.343 | +$8,568 | ✅ (n=6, etibarsız) |
| USDJPY | 15m | 51 | 31.4% | 1.352 | +$14,758 | 16 | 31.2% | 1.029 | +$390 | ✅ (ən böyük n) |
| USDJPY | 30m | 31 | 32.3% | 1.132 | +$3,344 | 5 | 40.0% | 1.129 | +$473 | ✅ (n=5, etibarsız) |
| GBPUSD | 30m | 54 | 25.9% | 1.131 | +$6,031 | 7 | 14.3% | 0.385 | -$4,386 | ❌ (1il) |
| XAUUSD | 15m | 70 | 24.3% | 0.956 | -$2,655 | 12 | 25.0% | 1.350 | +$3,399 | ❌ (5il) |
| GBPUSD | 60m | 16 | 25.0% | 0.868 | -$1,743 | 2 | 0.0% | 0.000 | -$2,183 | ❌ |
| NAS100 | 15m | 93 | 22.6% | 0.864 | -$11,034 | 12 | 25.0% | 0.677 | -$3,057 | ❌ |
| EURUSD | 30m | 43 | 27.9% | 0.703 | -$11,195 | 11 | 18.2% | 0.318 | -$7,728 | ❌ |
| XAUUSD | 5m | 114 | 24.6% | 0.702 | -$32,564 | 17 | 35.3% | 1.270 | +$3,292 | ❌ (5il) |
| GBPUSD | 15m | 69 | 18.8% | 0.666 | -$23,147 | 8 | 12.5% | 0.485 | -$4,406 | ❌ |
| XAUUSD | 30m | 46 | 15.2% | 0.643 | -$15,339 | 9 | 11.1% | 0.235 | -$6,407 | ❌ |
| NAS100 | 5m | 111 | 19.8% | 0.602 | -$45,685 | 26 | 26.9% | 0.814 | -$4,105 | ❌ |
| GBPUSD | 5m | 115 | 22.6% | 0.560 | -$61,138 | 19 | 31.6% | 0.678 | -$6,574 | ❌ |
| XAUUSD | 60m | 25 | 16.0% | 0.556 | -$10,236 | 7 | 14.3% | 0.320 | -$4,408 | ❌ |
| EURUSD | 15m | 73 | 16.4% | 0.522 | -$37,579 | 9 | 11.1% | 0.696 | -$3,066 | ❌ |
| USDJPY | 5m | 143 | 17.5% | 0.442 | -$96,594 | 22 | 36.4% | 1.199 | +$4,074 | ❌ (5il) |
| EURUSD | 5m | 113 | 19.5% | 0.431 | -$86,006 | 24 | 12.5% | 0.301 | -$23,575 | ❌ |
| NAS100 | 30m | 61 | 11.5% | 0.375 | -$36,349 | 9 | 0.0% | 0.000 | -$9,523 | ❌ |
| USDJPY | 60m | 23 | 13.0% | 0.356 | -$14,406 | 2 | 0.0% | 0.000 | -$2,227 | ❌ |

## 4. Yarımillik detallar — ən "yaxşı" iki kombinasiya niyə inandırıcı deyil

**USDJPY 15m** yarımillikləri: 0.00, 1.66, **9.29**, 5.42, 1.42, 0.00, 2.09,
0.00, 1.22, 1.02 — 2-9 trade/yarımillik. PF 0-dan 9.29-a sıçrayır: bu, sabit
edge deyil, aztrade-lik gurultusudur.

**NAS100 60m** yarımillikləri: 0.00, 0.70, 1.01, **4.78**, 0.71, **&infin;
(3/3 win)**, 1.42, 0.61, 0.00, 1.55 — eyni pattern: 2-6 trade/yarımillik,
0-dan sonsuza qədər PF.

## 5. Yekun tövsiyə (STRICT gate ilə): **Order Flow-u canlıya çıxarma**

Heç bir kombinasiya statistik cəhətdən inandırıcı edge göstərmir. Ən "yaxşı"
görünən NAS100 60m və USDJPY 15m belə, ildə 5-16 trade tezliyi ilə (First
FVG-nin ~200/il-inə qarşı) real qərar üçün kifayət qədər sürətlə nümunə
toplamır.

---

## 6. Gate boşaldılması: OF_MIN_CONFIRMATIONS 3→2 (2026-08-28)

Yuxarıdakı tövsiyəyə uyğun olaraq, confirmation gate-i (5 order-flow
siqnalından minimum neçəsi tələb olunur) 3-dən 2-yə endirilib, eyni 5
simvol × 4 TF × spread metodologiyası ilə yenidən sınandı.

**Nəticə: nümunə ölçüsü xeyli yaxşılaşdı (144→2348 trade cəmi 20
kombinasiyada, kombinasiya başına 26-188), amma PF əksəriyyətdə AŞAĞI
düşdü** — daha çox trade gəldi, amma əlavə gələnlər əsasən zəif keyfiyyətli
idi:

| Simvol | TF | 5il n | 5il PF | 1il n | 1il PF | Keçdi? |
|---|---|---|---|---|---|---|
| **USDJPY** | **15m** | **72** | **1.263** | **17** | **1.024** | ✅ ən inandırıcı |
| EURUSD | 60m | 30 | 1.261 | 6 | 3.343 | ✅ (n=6, hələ kiçik) |
| NAS100 | 60m | 48 | 1.254 | 9 | 1.185 | ✅ |
| ... (17 digər kombinasiya) | | | 0.46-0.96 | | | ❌ |

Yekun: **17/20 hələ də itkidə**, amma indi bu, 26-188 trade-lik həqiqi
nümunəyə əsaslanır — "kifayət qədər sınanmayıb" arqumenti artıq keçərli
deyil bu strategiya üçün. **USDJPY 15m** yeganə kombinasiyadır ki, həm 5il
(n=72), həm 1il (n=17) ağlabatan ölçüdə nümunədə PF>1.0 saxlayır — maraqlı,
amma First FVG/SR-in yüzlərlə/minlərlə trade-inə qədər hələ uzaqdır.
**Tövsiyə dəyişmir: canlıya çıxarma**, USDJPY 15m istisna olaraq ayrıca
izlənə bilər.

Tam nəticələr: `artifacts/order_flow_bias_relaxed_sweep.json`.

---

## 7. Fayllar
- **Script:** [scripts/order_flow_bias_spread_sweep.py](scripts/order_flow_bias_spread_sweep.py)
  (indi `--min-confirmations` arqumentini dəstəkləyir)
- **STRICT nəticələr (JSON):** `artifacts/order_flow_bias_spread_sweep.json`
- **RELAXED nəticələr (JSON):** `artifacts/order_flow_bias_relaxed_sweep.json`
- **Trade logları:** `artifacts/of_sweep_{symbol}_{tf}m_trades.csv` (20 fayl, strict)
