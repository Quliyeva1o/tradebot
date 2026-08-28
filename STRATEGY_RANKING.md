# Ümumi Strategiya Sıralaması — 2026-08-28

Bu sənəd 2026-08-28 sessiyasında spread daxil test olunan BÜTÜN strategiya/
konfiqurasiyaların ümumi sıralamasını cəmləyir: First FVG (bütün variantlar),
SR + Daily Bias (40 kombinasiya), Order Flow (strict + relaxed), PO3
(strict + relaxed), üstəlik bootstrap/Monte Carlo etibarlılıq analizi və
SR canlı sinif fidelity yoxlaması.

Ətraflı metodologiya və tam cədvəllər üçün bax:
[FIRST_FVG_15M_SPREAD_REPORT.md](FIRST_FVG_15M_SPREAD_REPORT.md),
[SR_DAILY_BIAS_SPREAD_REPORT.md](SR_DAILY_BIAS_SPREAD_REPORT.md),
[ORDER_FLOW_SPREAD_REPORT.md](ORDER_FLOW_SPREAD_REPORT.md),
[PO3_SPREAD_REPORT.md](PO3_SPREAD_REPORT.md),
[ROBUSTNESS_VALIDATION_REPORT.md](ROBUSTNESS_VALIDATION_REPORT.md).

---

## 🏆 Tier 1 — Canlıda, ən etibarlı

### 1. SR + Daily Bias — NAS100 / 30m / liquidity-TP
- 5il: n=695, PF **1.116**, +$61.5K | 1il: n=111, PF **1.551**, +$43K
- 40 kombinasiyadan (5 simvol × 4 TF × 2 variant) HƏM 5il HƏM 1il PF≥1.0
  keçən 2 kombinasiyadan ən güclüsü (digəri: NAS100 60m liquidity, kövrək).
- Bootstrap (5000x resample): **82.5% ehtimal ki, real müsbət edge**-dir
  (90% CI [0.92, 1.33]).
- Canlı sinif (`SrDailyBiasStrategy`) fidelity yoxlanıldı: batch skriptə
  çox yaxın nəticə (PF 1.057→1.024, n=811→838), fərq bootstrap CI-nin
  içindədir — statistik əhəmiyyətsiz.
- **Ən güclü namizəd — risk ölçüsü formalaşdırılarkən baza kimi istifadə edilsin.**

---

## 🥈 Tier 2 — Canlıda, amma edge hələ statistik təsdiqlənməyib

### 2. First FVG — NAS100 / 09:30 / 15m / fixed 2R
- 5il: n=1001, PF **1.01** | 1il: n=198, PF **1.16**
- Test edilən 8 kombinasiyadan (00:00/09:30 saatı × 5m/15m TF × 2R/3R
  hədəf) yeganə hər iki böyük-nümunə pəncərəsini (5il VƏ 1il) keçən.
- Bootstrap: yalnız **54.0% ehtimal** real edge-dir (90% CI [0.90, 1.12] —
  1.0-ı ortadan keçir, demək olar sikkə atma ilə eyni).
- Recency split (80/20): 0.95→1.08 (zəif yaxşılaşma, SR-in 0.99→1.36
  qədər inandırıcı deyil).
- **Nəticə: ən yaxşı FVG variantıdır, amma "qazanclı" olması statistik
  cəhətdən sübut olunmayıb.** Paper/kiçik-risk (0.25%) canlıda davam
  etsin, risk ölçüsü SR-dən daha mühafizəkar saxlanılsın.

---

## 🥉 Tier 3 — Maraqlı, canlı DEYİL, paper-də izlənməyə dəyər

### 3. PO3 (relaxed, DAILY_BIAS_VOTE_THRESHOLD=1) — USDJPY 5m / NAS100 60m
- USDJPY 5m: n=44/11, PF 2.84/4.23 (ən böyük nümunə)
- NAS100 60m: n=25/6, PF 6.15/1.70 (ən yüksək PF)
- Gate boşaldılandan sonra (strict-də 0/20 keçirdi) 12/20 kombinasiya
  keçir, cəmi 549 trade (əvvəlki 144-dən 3.8x artım).
- **Xəbərdarlıq:** PF-lər (1.09-8.66) şübhəli yüksəkdir kiçik nümunədə,
  xüsusilə n=2-6 olan 1-illik pəncərələr. "Sübut olunub" demək deyil,
  "izləməyə/paper-test etməyə dəyər" deməkdir.

### 4. Order Flow (relaxed, OF_MIN_CONFIRMATIONS=2) — USDJPY 15m
- n=72/17, PF 1.263/1.024 — 20 kombinasiyadan yeganə HƏM 5il HƏM 1il
  ağlabatan ölçüdə (n>15) nümunədə PF>1.0 saxlayan.
- First FVG/SR-in yüzlərlə-minlərlə trade-inə hələ çox uzaqdır.

---

## ❌ Tier 4 — Rədd edilib (real spreadlə itki, və ya statistik mənasız)

- **First FVG 00:00 anchor** (hər iki TF, hər iki R hədəf) — struktur
  baxımından zərərli; 00:00+5m xüsusilə pis (5il PF 0.26-0.30).
- **First FVG köhnə M1/liquidity-TP variantı** (`scripts/first_fvg_backtest.py`)
  — hələ spreadsiz test olunub, median 7.7pt dar stop səbəbindən şübhəli
  (ayrıca nəzərdən keçirilməli, `SESSION_HANDOFF.md` §3.1).
- **SR: XAUUSD** (bütün 4 TF, canlı XAUUSD botu daxil olmaqla — heç biri
  5il PF≥1.0 keçmir, ən yaxını 60m fixed3r 0.939) — **canlı XAUUSD botunu
  dayandırmaq tövsiyə olunur.**
- **SR: EURUSD, GBPUSD** — bütün TF/variant kombinasiyalarında aydın itki
  (PF 0.39-0.78 aralığı).
- **SR: hər simvolda 5m timeframe** — 10/10 kombinasiya ən pis nəticələr
  sırasında (PF 0.39-0.72). Cəmi: 40 kombinasiyadan 38-i rədd edilib.
- **Order Flow strict** (OF_MIN_CONFIRMATIONS=3) — heç bir kombinasiya
  statistik cəhətdən inandırıcı deyil (20/20 rədd, n=16-196/kombinasiya).
- **PO3 strict** (DAILY_BIAS_VOTE_THRESHOLD=2) — cəmi 144 trade 20
  kombinasiyada (1-22/kombinasiya), statistik nəticə çıxarmaq üçün
  yetərsiz (20/20 rədd).

---

## Əməli nəticə

Canlı bot tərkibi bu sıralamaya uyğun qurulub (bax
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) §1): **SR NAS100 30m liquidity-TP** və
**First FVG 09:30/15m/2R** aktiv saxlanılıb, digər konfiqurasiyalar
(SR XAUUSD daxil) canlıya çıxarılmayıb və ya dayandırılması tövsiyə
olunub. PO3/Order Flow relaxed variantları heç bir halda canlıya
çıxarılmayıb — yalnız gələcək paper-test namizədləri kimi sənədləşdirilib.
