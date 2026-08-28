# First FVG (9:30 / 00:00 anchor, 15m / 5m) — Spread Daxil Backtest Nəticələri

**Tarix:** 2026-08-28
**Strategiya:** "First FVG" — hər NY trading günü, seans başlanğıcından (00:00 və
ya 09:30 NY) sonra formalaşan İLK 3-şam Fair Value Gap-ı tapır, zonanın ilk
təmasında girir, stop = displacement şamının body-si, target = fixed R (2R
default, 3R də test edilib).

Bu, `scripts/nas100_first_fvg_15m_backtest.py`-da təsvir olunan strategiyadır
(commit `005b05e`, "first fvg 9:30 15m timeframe") — **`scripts/first_fvg_backtest.py`-dakı
köhnə M1/liquidity-TP variantından fərqlidir** (o, `SESSION_HANDOFF.md` §3.1-də
"CRITICAL, still open" kimi qeyd olunub, median stop 7.7pt; bu sənəddəki
strategiyanın median stop-u xeyli genişdir — aşağıya bax — ona görə iki
nəticəni qarışdırmayın).

---

## 1. Metodologiya

### 1.1 Data
- Mənbə: **`data/history/NAS100_M5.csv`** və **`data/history/NAS100_M15.csv`**
  — broker-in native M5/M15 bar-ları, birbaşa MT5-dən yüklənib:
  ```
  python -m data.download_history --symbols NAS100 --timeframe M15 --start 2020-01-01
  python -m data.download_history --symbols NAS100 --timeframe M5  --start 2020-01-01
  ```
- Bu broker-də simvol adı **`NAS100`**-dır, `USTEC` deyil (`USTEC` bu hesabın
  simvol siyahısında yoxdur — `mt5.symbols_get()` ilə yoxlanılıb).
- Əhatə: 2020-07-22 → 2026-08-27 (~6.1 il).
- `data/history/USTEC_M15.csv` istifadə OLUNMADI — cəmi ~3 ay data saxlayır,
  5il/1il pəncərələri üçün kifayət deyil.

### 1.2 Spread modeli
Hər trade-ə **sabit 3.0-point NAS100 round-trip spread** tətbiq olunub
(`scripts/robustness_analysis.SPREAD_BY_SYMBOL["NAS100"]`, bu repo-nun artıq
mövcud cost-stress konvensiyası), R-ə çevrilərək:
```
cost_r = spread_points / risk_distance
net_r  = gross_r - cost_r
```
Xam CSV-nin öz `spread` sütunu istifadə OLUNMADI — yoxlanılıb ki, 2024-ə
qədər hər bar üçün tam **0.0**-dır (broker canlı spread-i yalnız 2024-dən
qeyd edib), ona birbaşa etibar etmək 2020-2023-ü süni şəkildə pulsuz
göstərərdi.

### 1.3 Script
`scripts/first_fvg_15m_spread_backtest.py` — `find_first_fvg()` /
`simulate_trade()` funksiyalarını `nas100_first_fvg_15m_backtest.py`-dan
olduğu kimi import edir (təkrar yazılmayıb), üstünə əlavə edir:
- `--timeframe-minutes` (5 və ya 15; native fayl varsa onu istifadə edir,
  yoxdursa M1-dən resample edir)
- `--tp-r` (default 2.0)
- fixed-spread cost hesablaması

```
python -m scripts.first_fvg_15m_spread_backtest --timeframe-minutes 15 --tp-r 2
python -m scripts.first_fvg_15m_spread_backtest --timeframe-minutes 5  --tp-r 2
python -m scripts.first_fvg_15m_spread_backtest --timeframe-minutes 15 --tp-r 3
python -m scripts.first_fvg_15m_spread_backtest --timeframe-minutes 5  --tp-r 3
```

---

## 2. Nəticələr — 2R hədəf (default)

| TF | Saat | 5 il PF (n) | 1 il PF (n) | 3 ay PF (n) | 1 ay PF (n) |
|---|---|---|---|---|---|
| 15m | 00:00 | 0.61 (1228) | 0.68 (241) | 1.24 (63) | 1.54 (24) |
| 5m | 00:00 | 0.26 (1253) | 0.56 (249) | 1.01 (64) | 0.51 (24) |
| **15m** | **09:30** | **1.01 (1001)** | **1.16 (198)** | 0.91 (50) | 0.94 (18) |
| 5m | 09:30 | 0.81 (1107) | 1.03 (225) | 1.77 (59) | 2.35 (20) |

## 3. Nəticələr — 3R hədəf

| TF | Saat | 5 il PF (n) | 1 il PF (n) | 3 ay PF (n) | 1 ay PF (n) |
|---|---|---|---|---|---|
| 15m | 00:00 | 0.67 (1228) | 0.74 (241) | 1.40 (63) | 1.71 (24) |
| 5m | 00:00 | 0.30 (1253) | 0.65 (249) | 1.08 (64) | 0.66 (24) |
| 15m | 09:30 | 0.98 (1001) | 1.07 (198) | 0.75 (50) | 0.69 (18) |
| 5m | 09:30 | 0.79 (1107) | 0.91 (225) | 1.45 (59) | 1.25 (20) |

**3R heç bir kombinasiyada 2R-dan yaxşı deyil** — hədəfi genişləndirmək
winrate-i R-qazancından daha çox aşağı salır.

## 4. Yekun tövsiyə: **09:30 + 15m + 2R**

Test edilən 8 kombinasiyadan (2 saat × 2 TF × 2 R) yeganəsi ki, HƏM 5 illik
(n=1001), HƏM 1 illik (n=198) böyük nümunədə PF > 1.0 saxlayır. Digərlərinin
hamısı bu iki böyük-nümunə pəncərəsinin ən azı birində itki verir. Kiçik
pəncərələr (3ay/1ay, n=18-64) həm yaxşı, həm pis nəticə göstərə bilir —
bunlara etibar edərək qərar vermək səhv olardı (bax §6).

### 00:00 saatı niyə tərk edilib
Hər iki timeframe-də, hər iki R-də struktur baxımdan zərərlidir; 00:00+5m
xüsusilə pisdir (5 il PF 0.26-0.30, demək olar tam səmərəsiz).

---

## 5. Risk profili — ardıcıl stop seriyaları (tam tarix, 09:30)

| Konfiqurasiya | Ən uzun ardıcıl stop | Tarix aralığı |
|---|---|---|
| 15m + 2R | 13 trade (hamısı SL) | 2022-09-22 → 2022-10-17 |
| 15m + 3R | 18 trade (hamısı SL) | 2022-09-15 → 2022-10-17 |

15m+2R üçün: ~13 ardıcıl 1% risk ≈ hesabın ~13%-i ardıcıl düşür
(compounding olmadan). Pozisiya ölçüsü seçərkən nəzərə alınmalıdır.

---

## 6. Spread həssaslığı — broker asılılığı

Fərqli broker fərqli spread verə bilər. Amma nəticə TƏKCƏ spread rəqəmindən
asılı deyil — **stop məsafəsi** də önəmlidir (`cost_r = spread / risk`):

| TF | Orta stop (pt) | Median stop (pt) |
|---|---|---|
| 15m | 39.25 | 31.2 |
| 5m | 27.55 | 22.0 |

5m-in stop-u daha dar olduğu üçün EYNİ spread onun R-ini 15m-dən daha çox
yeyir. PF-in spread-ə görə dəyişməsi (tam tarix, 09:30, 2R):

| Spread (pt) | 15m PF | 5m PF |
|---|---|---|
| 0 (spreadsiz) | 1.25 | 1.12 |
| 1.0 | 1.15 | **1.00** ← 5m-in breakeven həddi |
| 2.0 | 1.06 | 0.89 |
| 2.6 | ~1.00 ← 15m-in breakeven həddi | 0.86 |
| 3.0 (istifadə etdiyimiz) | 0.98* | 0.80 |
| 5.0 | 0.84 | 0.65 |

\* Bu tam-tarix (n=1116) rəqəmdir; son-5-il pəncərəsində (n=1001) 15m+2R+3.0pt
PF 1.01-dir (§2) — fərq 2020-2021-in daha zəif hissəsinin daxil/xaric
olmasından qaynaqlanır.

**Nəticə:** 5m yalnız çox dar spread-li (ECN/raw, ≲1.0pt) broker-də mənalı
ola bilər — bu, retail səviyyədə nadirdir. 15m-in breakeven həddi (~2.6pt)
əksər normal broker-lərin NAS100 spread-inə uyğundur. Üstəlik, spread=0
olsa belə 15m (PF 1.25) 5m-dən (PF 1.12) öndədir — yəni 15m-in üstünlüyü
təkcə spread artefaktı deyil, xam edge də daha yaxşıdır.

---

## 7. Fayllar

- **Script:** [scripts/first_fvg_15m_spread_backtest.py](scripts/first_fvg_15m_spread_backtest.py)
- **Trade logları:** `artifacts/first_fvg_{5m,15m}_{2R,3R}_spread_{0000,0930}_all.csv`
- **JSON xülasələr:** `artifacts/first_fvg_{5m,15m}_{2R,3R}_spread_summary.json`
  (2R faylları üçün tag yoxdur — `first_fvg_{tf}_spread_summary.json`, 2R
  default olduğundan skriptin ilk versiyasında yaradılıb)
- **HTML hesabatın data mənbəyi:** `artifacts/first_fvg_0930_5m_vs_15m.json`
  (yalnız 09:30/2R, iki TF birləşdirilib)
- **HTML hesabat (canlı artifact):** https://claude.ai/code/artifact/a7acefed-126c-46c6-9817-b8ee66b0685a
  (09:30 seansı, 5m vs 15m toggle, windows/aylıq/günlük cədvəllər — 2R üçün)
- **Xam data:** `data/history/NAS100_M5.csv`, `data/history/NAS100_M15.csv`

## 8. Əlaqəli açıq məsələlər

`SESSION_HANDOFF.md` §3.1-də qeyd olunan "Spread is not in the strategy
math — CRITICAL" maddəsi **`scripts/first_fvg_backtest.py`-dakı köhnə
M1/liquidity-TP variantına aiddir**, bu sənəddəki 9:30/00:00+15m/5m
variantına yox. Həmin köhnə variant hələ də spread-siz test olunub və
ayrıca nəzərdən keçirilməlidir.
