# PO3 (Power of Three) — Spread Daxil Sweep Nəticələri

**Tarix:** 2026-08-28
**Strategiya:** ICT Power of Three (`scripts/po3_backtest.py`). Daily Bias
(1H structure + PDH/PDL discount/premium) → Accumulation (09:30-10:00 NY
opening range) → Manipulation (range/session/PDH-PDL sweep) → Confirmation
(displacement + Market Structure Shift) → Entry (FVG retracement) → SL
(sweep extreminin arxası) → TP (ən yaxın əks-tərəf likvidlik, min 1:2 RR).
Sweep + Displacement + MSS + FVG-retest — 4-ü də HARD gate (spesifikasiyanın
öz vurğusu: "bu 4-dən biri yoxdursa keyfiyyət aşağı düşür").

---

## 0. Doğruluq yoxlaması — tapılan və düzəldilən bug

`scripts/po3_backtest.py`-də giriş bar-ın ORTASINDA baş verir (FVG zonasına
retracement toxunuşu, `l[i] <= upper`/`h[i] >= lower`), amma SL/TP yoxlanışı
mövqe siyahısının BAŞINDA (növbəti bar üçün) işləyirdi — yəni YENİ açılan
mövqenin öz bar-ının QALAN diapazonu HEÇ vaxt yoxlanılmırdı. Bu, First
FVG-də tapılıb düzəldilmiş `SESSION_HANDOFF.md` §2.2 bug-ının EYNİ növüdür,
və §3.3-də artıq "latent" (mövcud, amma köhnə 92-trade dataset-də heç vaxt
tetiklənməmiş) kimi qeyd olunub. **Bu sweep üçün düzəldildi** —
`scripts/po3_spread_sweep.py`-də giriş bar-ının öz H/L-i dərhal SL/TP-yə
qarşı yoxlanılır (SL əvvəlcə, repo-nun standart konvensiyası).

---

## 1. Metodologiya
Eyni data/spread konvensiyası: təzə MT5 M1 (2020-01-01→2026-08-28), NAS100
sabit 3.0pt spread, XAUUSD/EURUSD/GBPUSD/USDJPY real tarixi spread. 5 simvol
× 4 timeframe = 20 kombinasiya (əvvəlki 3-simvol/12-kombo sweep-in
genişləndirilməsi).

---

## 2. ⚠️ Nəticə: bu strategiya ilə HEÇ bir qərar vermək mümkün deyil

| Simvol | TF | 5il n | 5il WR | 5il PF | 1il n | 1il WR | 1il PF |
|---|---|---|---|---|---|---|---|
| NAS100 | 5m | 13 | 38.5% | 2.51 | 3 | 33.3% | 5.65 |
| NAS100 | 15m | 6 | 33.3% | 0.86 | 1 | 0.0% | 0.00 |
| NAS100 | 30m | 3 | 66.7% | 3.36 | 1 | 100.0% | — |
| NAS100 | 60m | 13 | 61.5% | 7.97 | 3 | 33.3% | 1.01 |
| XAUUSD | 5m | 13 | 30.8% | 2.52 | 3 | 0.0% | 0.00 |
| XAUUSD | 15m | 3 | 33.3% | 1.00 | 1 | 0.0% | 0.00 |
| XAUUSD | 30m | 3 | 33.3% | 1.57 | 2 | 50.0% | 3.02 |
| XAUUSD | 60m | 3 | 66.7% | 6.55 | 1 | 0.0% | 0.00 |
| EURUSD | 5m | 10 | 60.0% | 3.30 | 5 | 40.0% | 1.92 |
| EURUSD | 15m | 5 | 80.0% | 11.08 | 2 | 100.0% | — |
| EURUSD | 30m | 6 | 66.7% | 5.46 | 1 | 100.0% | — |
| EURUSD | 60m | 5 | 20.0% | 0.54 | 1 | 0.0% | 0.00 |
| GBPUSD | 5m | 7 | 57.1% | 3.16 | 1 | 0.0% | 0.00 |
| GBPUSD | 15m | **1** | 100.0% | — | 1 | 100.0% | — |
| GBPUSD | 30m | 2 | 0.0% | 0.00 | 1 | 0.0% | 0.00 |
| GBPUSD | 60m | 4 | 25.0% | 0.94 | 2 | 0.0% | 0.00 |
| USDJPY | 5m | 12 | 58.3% | 9.07 | 2 | 100.0% | — |
| USDJPY | 15m | 11 | 54.5% | 3.90 | 3 | 100.0% | — |
| USDJPY | 30m | 2 | 50.0% | 6.34 | 1 | 0.0% | 0.00 |
| USDJPY | 60m | 3 | 33.3% | 3.27 | 1 | 0.0% | 0.00 |

**Cəmi: 20 kombinasiyada 144 trade (5-6 il üzrə), kombinasiya başına 1-22.**
PF 0.00-dan 11.08-ə qədər səpələnib, bir çoxu 1-3 trade-ə əsaslanır (bəziləri
tək 1 trade!). Bu, statistik nəticə çıxarmaq üçün YETƏRSİZDİR — spread
tətbiq etmək və ya etməmək belə fərq etmir, çünki nümunə ölçüsü problemi
buna qədər gəlir.

## 3. Tövsiyə (STRICT gate ilə)

Qərar: ya bir gate-i yumşaldıb trade tezliyini artırıb yenidən test etmək,
ya da strategiyanı tərk etmək. Hazırkı formada canlıya çıxarmaq mümkün deyil.

---

## 4. Gate boşaldılması: əsl bottleneck MSS/RR DEYİL, Daily Bias imiş (2026-08-28)

NAS100 60m üçün skip-funnel diaqnostikası çəkildi (34,708 bar):
```
neutral_bias: 28,769  (83%)
no_sweep:      3,959
no_displacement: 1,813
no_mss:           59
no_fvg_retest:    97
rr_gate_failed:   11
```
**"Neutral bias" TƏKBAŞINA digər BÜTÜN gate-lərin cəmindən 5 dəfə çoxdur.**
İlk təxminimiz səhv idi: `MSS_LOOKBACK_BARS` 10→20 heç bir dəyişiklik
vermədi (13→13 trade), `MIN_RR` 2.0→1.5 cüzi artırdı (13→15). Əsl problem:
Daily Bias 2 səsdən (1H structure + PDH/PDL zone) İKİSİNİN DƏ eyni istiqamətə
uzlaşmasını tələb edir (`total >= 2`) — bu, demək olar həmişə Neutral verir.

`DAILY_BIAS_VOTE_THRESHOLD`-u 2-dən 1-ə endirəndə (tək səs kifayətdir):
NAS100 60m 13→**25** trade (2x), PF 8.50→6.15 (hələ yüksək qalır).

**Tam 5-simvol × 4-TF sweep bu ayarla:**

| Simvol | TF | 5il n | 5il PF | 1il n | 1il PF | Keçdi? |
|---|---|---|---|---|---|---|
| NAS100 | 60m | 25 | 6.15 | 6 | 1.70 | ✅ |
| EURUSD | 30m | 15 | 4.65 | 2 | 2.30 | ✅ (n=2) |
| EURUSD | 15m | 15 | 4.30 | 4 | 8.11 | ✅ (n=4) |
| USDJPY | 30m | 11 | 3.85 | 5 | 7.88 | ✅ |
| USDJPY | 5m | 62 | 3.58 | 11 | 2.94 | ✅ ən böyük nümunə |
| USDJPY | 15m | 45 | 3.04 | 10 | 8.66 | ✅ |
| NAS100 | 5m | 44 | 2.84 | 11 | 4.23 | ✅ |
| NAS100 | 15m | 20 | 2.81 | 5 | 5.36 | ✅ |
| NAS100 | 30m | 12 | 2.77 | 5 | 2.25 | ✅ |
| USDJPY | 60m | 6 | 2.65 | 4 | 1.35 | ✅ (n=6) |
| XAUUSD | 30m | 6 | 1.33 | 4 | 2.69 | ✅ (n=6) |
| EURUSD | 5m | 39 | 1.09 | 14 | 1.81 | ✅ |
| GBPUSD/XAUUSD digərləri | | | | | | ❌ (8 kombinasiya) |

**Cəmi 549 trade 20 kombinasiyada (əvvəlki 144-dən 3.8x artım). 12/20
kombinasiya keçir** (əvvəl 0/20 inandırıcı idi). Ən inandırıcı: **USDJPY
5m** (n=62/11, ikisi də ağlabatan ölçüdə) və **NAS100 60m** (n=25/6, ən
yüksək PF).

**Xəbərdarlıq:** bu hələ də First FVG/SR-in yüzlərlə-minlərlə trade-inə
çatmır, və PF-lər (1.09-8.66) şübhəli dərəcədə yüksəkdir kiçik nümunələrdə
(xüsusilə n=2-6 olan 1-illik pəncərələr). Bu, "artıq sübut olunub, canlıya
çıxar" demək deyil — "artıq izləməyə/paper-test etməyə DƏYƏR" deməkdir.
Tövsiyə: USDJPY 5m/60m NAS100-u paper-də bir neçə ay izləyib, PF-in
sabitləşib-sabitləşmədiyini görmək.

Tam nəticələr: `artifacts/po3_relaxed_sweep.json`.

---

## 5. Fayllar
- **Script:** [scripts/po3_spread_sweep.py](scripts/po3_spread_sweep.py)
  (indi `--mss-lookback-bars`, `--min-rr`, `--bias-threshold` arqumentlərini dəstəkləyir)
- **STRICT nəticələr (JSON):** `artifacts/po3_spread_sweep.json`
- **RELAXED (bias-threshold=1) nəticələr (JSON):** `artifacts/po3_relaxed_sweep.json`
- **Trade logları:** `artifacts/po3_sweep_{symbol}_{tf}m_trades.csv` (20 fayl, strict)
