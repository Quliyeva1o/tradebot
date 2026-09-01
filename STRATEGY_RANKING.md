# Ümumi Strategiya Sıralaması — 2026-09-01 (yenilənib)

Bu sənəd 2026-08-28-in ilkin sıralamasını 2026-08-31-in qabaqcıl doğrulaması
(walk-forward/Monte Carlo/rejim/portfel, First FVG-ni ƏHƏMİYYƏTLİ DƏRƏCƏDƏ
aşağı endirən) və 2026-09-01-in yeni XAUUSD ORB strategiyası ilə YENİLƏYİR.
**Diqqət:** 2026-08-28 versiyası First FVG-ni Tier 2-də ("canlıda, amma
statistik təsdiqlənməyib") saxlayırdı — bu artıq düzgün deyil, bax aşağıda.

Ətraflı metodologiya və tam cədvəllər üçün bax:
[ADVANCED_VALIDATION_REPORT.md](ADVANCED_VALIDATION_REPORT.md) (2026-08-31,
First FVG-nin aşağı düşməsinin əsl mənbəyi),
[FIRST_FVG_15M_SPREAD_REPORT.md](FIRST_FVG_15M_SPREAD_REPORT.md),
[SR_DAILY_BIAS_SPREAD_REPORT.md](SR_DAILY_BIAS_SPREAD_REPORT.md),
[ORDER_FLOW_SPREAD_REPORT.md](ORDER_FLOW_SPREAD_REPORT.md),
[PO3_SPREAD_REPORT.md](PO3_SPREAD_REPORT.md),
[ROBUSTNESS_VALIDATION_REPORT.md](ROBUSTNESS_VALIDATION_REPORT.md),
[XAUUSD_ORB_SESSION_HANDOFF.md](XAUUSD_ORB_SESSION_HANDOFF.md) (2026-08-31/09-01),
`strategy/xauusd_orb_liquidity_sweep.py`-in öz docstring-i (tam ORB rəqəmləri).

---

## 🏆 Tier 1 — ən güclü sübut əsası, canlıda

### 1. SR + Daily Bias — NAS100 / 30m / liquidity-TP
- 5il: n=695, PF **1.116**, +$61.5K | 1il: n=111, PF **1.551**, +$43K
- Bootstrap: **82.5%** ehtimal real edge (90% CI [0.92, 1.33]).
- Walk-forward (10 fold, 2026-08-31): **5/10** keçir, VƏ qazanan fold-lar
  GÜCLÜDÜR (PF 1.09-2.00), zəif deyil.
- Monte Carlo, REAL risk (0.25-0.5%): gözlənilən **+3.5%**, ruin 0%.
- Spread marjı: breakeven ~4.2pt, istifadə olunan 3.0pt-dən **+1.2pt yuxarı**.
- RANGING-gate (opt-in, hazırda yalnız Paper-də AÇIQ): PF 1.025→**1.168**
  (n=568), 7/10 fold-da ayrıca yaxşılaşma.
- Portfel: solo PF 1.057, DD 11.0% (First FVG-dən daha az itirir birgə
  slot-da: 34/813 trade, First FVG-nin 139/1117-i ilə müqayisədə).
- **Ən güclü namizəd — 5 MÜSTƏQİL testin 5-i də eyni istiqamətdə: real
  edge.**

---

## 🥈 Tier 2 — real edge ehtimalı var, amma kiçik/gənc, hələ canlı DEYİL

### 2. XAUUSD 09:30 ORB + Liquidity-Sweep (M15, real next-open fill) — YENİ, 2026-09-01
- n=137 (6.7 il, bu hesabın datası), net spread: WR 51.1%, PF **1.33**, +$8.9K
- Bootstrap: **97.7%** ehtimal real edge (median PF 1.50, 90% CI [1.08, 2.05])
  — First FVG-nin 54%-dən QAT-QAT güclü, SR-in 82.5%-dən də yuxarı.
- Recency split: PF 1.46→1.63 (yaxşılaşan).
- Walk-forward (7 fold): 5/7 keçir, AMMA 2 uğursuz fold məhz **2020-2021**-dir
  (bu konfiqurasiya öz ilk ~2 ilində zərərli olub); 2021-sonundan bəri
  ardıcıl müsbətdir.
- Monte Carlo, REAL risk (0.5%): gözlənilən **+1.7%** (6.7 il ərzində) —
  müsbətdir, amma NAZİKDİR, SR-in +3.5%-dən (daha qısa 6y pəncərədə) zəif.
- Rejim: bütün 3 rejimdə müsbət (First FVG/SR-dən fərqli, RANGING-only
  DEYİL), amma trade-lərin əksəriyyəti (84/137) məhz ən zəif marjinal
  RANGING-dədir (PF 1.10).
- **Statistik profil SR-ə bənzəyir (HƏR test müsbətdir, mənfi işarə yoxdur)
  — amma HƏLƏ canlı/paper track record-u yoxdur (yalnız bir dəfə smoke-test
  edilib) VƏ real-risk gözlənilən qazanc kiçikdir.** SR-dən sonra ən
  inandırıcı namizəd, First FVG-dən daha çox güvən doğurur, amma hələ
  sınanmamış icra riski daşıyır.

### 3. First FVG — NAS100 / 09:30 / 15m / fixed 2R — **AŞAĞI DÜŞDÜ (2026-08-31)**
- 5il: n=1001, PF 1.01 | 1il: n=198, PF 1.16 — TƏK BAŞINA yaxşı görünür.
- AMMA 2026-08-31-in 5 ƏLAVƏ müstəqil testi HAMISI mənfi işarə verir:
  - Bootstrap: yalnız **54.0%** (demək olar sikkə atma).
  - Walk-forward (10 fold): yalnız **4/10** keçir.
  - Monte Carlo, REAL risk (0.25%): gözlənilən **-15.3%** (MƏNFİ!).
  - Spread marjı: breakeven ~2.6pt, istifadə olunan 3.0pt-dən **-0.4pt
    AŞAĞI** (mənfi marj).
  - Portfeldə ən çox itirən (139/1117 trade, SR-in 34/813-ü ilə müqayisədə).
- RANGING-gate kömək edir (PF 1.004→1.063, n=766, hazırda Paper-də AÇIQ),
  amma bu filtr özü hələ out-of-sample sınanmayıb.
- **Nəticə: 5 müstəqil metodun 5-i də eyni istiqaməti göstərir — bu
  konfiqurasiyanın real edge-i YOXDUR və ya statistik cəhətdən sübut
  edilə bilməyəcək qədər kövrəkdir.** Canlıda qalır (kiçik 0.25% risklə),
  amma bu sırada ƏN ZƏİF namizəddir, "uğurlu davam edəcək" gözləntisi
  ilə YOX.

---

## 🥉 Tier 3 — maraqlı, canlı DEYİL, paper-də izlənməyə dəyər

### 4. PO3 (relaxed, DAILY_BIAS_VOTE_THRESHOLD=1) — USDJPY 5m / NAS100 60m
- USDJPY 5m: n=44/11, PF 2.84/4.23 | NAS100 60m: n=25/6, PF 6.15/1.70
- 12/20 kombinasiya keçir (549 trade), amma PF-lər (1.09-8.66) kiçik
  nümunədə şübhəli yüksəkdir.

### 5. Order Flow (relaxed, OF_MIN_CONFIRMATIONS=2) — USDJPY 15m
- n=72/17, PF 1.263/1.024 — First FVG/SR-in yüzlərlə-minlərlə trade-inə
  hələ çox uzaqdır.

### 6. Nasdaq Midline Sweep (2026-07-22, köhnə metodologiya, YENİLƏNMƏYİB)
- OOS baseline: n=106, PF 1.051, +$379. Walk-forward 3 pəncərədən 2-si
  keçir, AMMA **ən son (ən "canlıya yaxın") pəncərə itki göstərir** —
  First FVG-ni öldürən "recency degradation" siqnalının eynisi.
  Robustness: 1pt əlavə slippage PF-i 1.0-ın altına salır. Monte Carlo
  worst-case DD 47.4% (tarixi 10.08%-dən 4.7x geniş). Öz hesabatının
  tövsiyəsi: **demo kapitala keçmə**. Bu sənəd yeni spread/bootstrap
  metodologiyası ilə YENİDƏN yoxlanılmayıb — status "dayandırılıb", "rədd
  edilib" deyil.

---

## ❌ Tier 4 — Rədd edilib (real spreadlə itki, və ya statistik mənasız)

- **First FVG 00:00 anchor** (hər iki TF, hər iki R hədəf) — struktur
  baxımından zərərli; 00:00+5m xüsusilə pis (5il PF 0.26-0.30).
- **First FVG köhnə M1/liquidity-TP variantı** (`scripts/first_fvg_backtest.py`)
  — median 7.7pt dar stop, spread-lə PF 0.73 (bax `SESSION_HANDOFF.md` §3.1).
- **Midnight FVG** (00:00 sessiya, M1, fixed 2.5R) — First FVG 15m-in sələfi,
  canlı task-lardan SÖNDÜRÜLÜB.
- **SR: XAUUSD** (bütün 4 TF, canlı XAUUSD botu daxil) — heç biri 5il
  PF≥1.0 keçmir (ən yaxını 0.939) — **canlı task SÖNDÜRÜLÜB.**
- **SR: EURUSD, GBPUSD** — bütün kombinasiyalarda aydın itki (PF 0.39-0.78).
- **SR: hər simvolda 5m timeframe** — 10/10 ən pis nəticələr arasında.
- **XAUUSD ORB Setup A (breakout+retest)** — həm XAUUSD (PF 0.81/2y), həm
  NAS100-də (PF 0.75/4y) itki, canlı sinifə heç vaxt daxil edilməyib.
- **Order Flow strict** (OF_MIN_CONFIRMATIONS=3) — 20/20 kombinasiya rədd.
- **PO3 strict** (DAILY_BIAS_VOTE_THRESHOLD=2) — cəmi 144 trade, 20/20 rədd.

---

## Əməli nəticə

Canlı bot tərkibi hələ bu yenilənmiş sıralamanı TAM ƏKS ETDİRMİR — bax
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) §1: **SR NAS100 30m** və
**First FVG 09:30/15m/2R** hələ də ikisi də aktiv (First FVG kiçik risklə
davam edir, dayandırılmayıb — istifadəçi qərarı gözlənilir). XAUUSD ORB
hələ HEÇ bir Scheduled Task-a bağlanmayıb
(yalnız bir dəfə paper smoke-test edilib) — istifadəçinin açıq təsdiqi
olmadan bağlanmayacaq. PO3/Order Flow/Midline Sweep heç bir halda canlıya
çıxarılmayıb — yalnız gələcək paper-test namizədləri kimi sənədləşdirilib.
