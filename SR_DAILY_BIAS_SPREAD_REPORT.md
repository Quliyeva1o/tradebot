# SR + Daily Bias — Spread Daxil, Tam Sweep Nəticələri

**Tarix:** 2026-08-28
**Strategiya:** Support/Resistance + Daily Bias (`strategy/sr_daily_bias.py`,
Pine mənbəyi `pine scriptlerim/SR_Daily_Bias_Strategy.pine`). Gündəlik EMA-yə
görə bias (bullish/bearish), sonra 3 giriş tipi: **Bounce** (S/R-dən geri
tullanma + rejection şam), **Breakout** (həcm təsdiqli təzə qırılma),
**Retest** (qırılmış səviyyəyə geri test). SL = ATR buffer, TP iki variant:
**fixed3r** (sabit 3R) və **liquidity** (ən yaxın mitigasiya olunmamış
əks-tərəf səviyyəsi — canlı sinif bunu güzgüləyir).

---

## 0. Doğruluq yoxlaması (backtest sweep-dən əvvəl)

Hər iki backtest skripti (`scripts/sr_daily_bias_backtest.py`,
`scripts/sr_daily_bias_backtest_liquidity_tp.py`) və canlı sinif
(`strategy/sr_daily_bias.py`) diqqətlə müqayisə edildi:

- **Daily bias lookahead yoxdur** — bias YALNIZ əvvəlki günün TAM bağlanmış
  şamı/EMA-sına əsaslanır (`First FVG`-də tapılan `htf_bias_known_from`
  pattern-i ilə eyni prinsip).
- **Pivot (S/R) səviyyələri təsdiq bar-ında qeydə alınır** (pivot +
  swing_len bar sonra) — `ta.pivothigh/pivotlow`-un repaint-siz
  semantikasına uyğundur.
- **Eyni-bar stop-out riski YOXDUR** — bu strategiyada giriş = bar-ın
  bağlanış qiyməti, SL/TP yalnız NÖVBƏTİ bardan yoxlanılır. First FVG-də
  tapılan "eyni bar-da giriş VƏ stop" problemi (zonaya bar-ın ORTASINDA
  toxunma + həmin bar-ın qalan diapazonunda stop) bura aid deyil, çünki
  giriş bar-ın SONUNDA baş verir, qalan diapazon yoxdur.
- **Canlı sinifin "KNOWN FIDELITY GAP"** artıq öz sənədində açıq
  yazılıb (broken-level tracking real mövqe olmadıqda fərqli davrana bilər)
  və zərərsiz olaraq təsdiqlənib.

Heç bir yeni correctness bug tapılmadı — kod backtest üçün etibarlıdır.

---

## 1. Metodologiya

### Data
`data/history/{NAS100,XAUUSD,EURUSD,GBPUSD,USDJPY}_M1.csv` — hamısı
2026-08-28-də YENİDƏN yüklənib:
```
python -m data.download_history --symbols NAS100,XAUUSD,EURUSD,GBPUSD,USDJPY \
    --timeframe M1 --start 2020-01-01
```
5m/15m/30m/60m bar-ları bu M1-dən resample edilir (SR skriptlərinin özünün
həmişəki üsulu).

### Spread
- **NAS100**: sabit **3.0-point** (First FVG işində olduğu kimi — real
  sütun 2024-ə qədər 0.0-dır).
- **XAUUSD / EURUSD / GBPUSD / USDJPY**: YENİ tapıntı — bu 4 simvolun
  **real tarixi spread sütunu bütün 2020-2026 aralığında etibarlıdır**
  (sıfır deyil, cari canlı spread-lə üst-üstə düşür). Ona görə sabit
  konstant əvəzinə **hər trade-in öz tarixi spread-i** istifadə olunub —
  NAS100-dan daha dəqiq metodologiya.
- Xərc: `cost_r = spread_price / risk_distance`, R-dən çıxılır (First
  FVG-dəki eyni konvensiya).

### Sweep ölçüsü
**5 simvol × 4 timeframe (5/15/30/60m) × 2 variant (fixed3R/liquidity) = 40
kombinasiya.** Hər biri üçün: 5il/1il/3ay/1ay yekunu, son 1il aybaay, son
1ay günbəgün, **son 5il yarımillik (6-aylıq) breakdown.**

Script: [scripts/sr_daily_bias_spread_sweep.py](scripts/sr_daily_bias_spread_sweep.py)

---

## 2. Tam nəticə cədvəli (5il/1il PF-ə görə sıralanıb)

**Meyar: PF ≥ 1.0 HƏM 5 illik, HƏM 1 illik pəncərədə** (böyük nümunə,
etibarlı). Yalnız 2/40 kombinasiya bunu keçir.

| Simvol | TF | Variant | 5il n | 5il WR | 5il PF | 5il Net | 1il n | 1il WR | 1il PF | 1il Net | Keçdi? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **NAS100** | **30m** | **liquidity** | **695** | **31.8%** | **1.116** | **+$61,455** | **111** | **34.2%** | **1.551** | **+$42,959** | ✅ |
| NAS100 | 60m | liquidity | 433 | 31.4% | 1.019 | +$6,075 | 75 | 30.7% | 1.049 | +$2,694 | ✅ (kövrək) |
| NAS100 | 60m | fixed3r | 451 | 29.3% | 1.140 | +$47,735 | 91 | 24.2% | 0.902 | -$7,097 | ❌ (1il) |
| NAS100 | 30m | fixed3r | 774 | 27.4% | 0.985 | -$9,390 | 133 | 26.3% | 0.977 | -$2,399 | ❌ |
| USDJPY | 60m | liquidity | 330 | 30.3% | 0.985 | -$3,921 | 54 | 33.3% | 0.806 | -$7,888 | ❌ |
| USDJPY | 60m | fixed3r | 347 | 27.1% | 0.955 | -$12,681 | 49 | 30.6% | 1.129 | +$4,975 | ❌ (5il) |
| NAS100 | 15m | liquidity | 1260 | 26.9% | 0.942 | -$63,517 | 196 | 30.1% | 1.247 | +$38,250 | ❌ (5il) |
| XAUUSD | 60m | fixed3r | 355 | 25.6% | 0.939 | -$17,191 | 55 | 41.8% | 2.064 | +$35,129 | ❌ (5il) |
| XAUUSD | 30m | fixed3r | 752 | 26.5% | 0.921 | -$49,467 | 125 | 27.2% | 1.052 | +$4,933 | ❌ (5il) |
| XAUUSD | 15m | liquidity | 1380 | 25.7% | 0.892 | -$134,119 | 182 | 28.6% | 1.242 | +$33,862 | ❌ (5il) |
| NAS100 | 15m | fixed3r | 1360 | 26.9% | 0.887 | -$131,832 | 225 | 25.8% | 0.897 | -$19,208 | ❌ |
| XAUUSD | 15m | fixed3r | 1458 | 26.8% | 0.865 | -$171,509 | 200 | 32.5% | 1.313 | +$45,537 | ❌ (5il) |
| XAUUSD | 30m | liquidity | 727 | 27.8% | 0.844 | -$93,307 | 103 | 31.1% | 1.095 | +$7,081 | ❌ (5il) |
| USDJPY | 15m | fixed3r | 1351 | 27.7% | 0.815 | -$231,846 | 251 | 23.9% | 0.647 | -$88,531 | ❌ |
| USDJPY | 30m | liquidity | 781 | 26.2% | 0.795 | -$143,040 | 140 | 23.6% | 0.608 | -$50,854 | ❌ |
| XAUUSD | 60m | liquidity | 354 | 30.2% | 0.789 | -$56,397 | 45 | 42.2% | 1.300 | +$8,051 | ❌ (5il) |
| USDJPY | 15m | liquidity | 1328 | 28.8% | 0.778 | -$273,764 | 250 | 28.8% | 0.622 | -$89,384 | ❌ |
| USDJPY | 30m | fixed3r | 785 | 24.8% | 0.778 | -$156,223 | 151 | 23.8% | 0.723 | -$38,489 | ❌ |
| GBPUSD | 60m | fixed3r | 424 | 23.3% | 0.776 | -$82,082 | 63 | 20.6% | 0.630 | -$21,430 | ❌ |
| EURUSD | 30m | fixed3r | 836 | 25.4% | 0.751 | -$194,139 | 139 | 22.3% | 0.620 | -$52,500 | ❌ |
| GBPUSD | 30m | fixed3r | 823 | 23.8% | 0.736 | -$197,938 | 130 | 23.1% | 0.704 | -$35,559 | ❌ |
| EURUSD | 60m | liquidity | 424 | 27.4% | 0.710 | -$106,318 | 69 | 29.0% | 0.589 | -$24,405 | ❌ |
| EURUSD | 60m | fixed3r | 412 | 22.6% | 0.705 | -$110,518 | 71 | 25.4% | 0.800 | -$12,726 | ❌ |
| GBPUSD | 60m | liquidity | 450 | 26.7% | 0.700 | -$112,104 | 67 | 25.4% | 0.771 | -$13,187 | ❌ |
| NAS100 | 5m | fixed3r | 3007 | 25.9% | 0.688 | -$940,058 | 480 | 24.0% | 0.719 | -$125,543 | ❌ |
| NAS100 | 5m | liquidity | 2826 | 27.3% | 0.681 | -$908,719 | 439 | 25.3% | 0.599 | -$162,762 | ❌ |
| GBPUSD | 15m | fixed3r | 1410 | 24.3% | 0.670 | -$457,120 | 223 | 24.2% | 0.651 | -$76,941 | ❌ |
| GBPUSD | 30m | liquidity | 832 | 26.0% | 0.664 | -$249,893 | 138 | 23.9% | 0.555 | -$56,453 | ❌ |
| EURUSD | 15m | fixed3r | 1439 | 25.1% | 0.637 | -$541,817 | 250 | 20.4% | 0.474 | -$148,844 | ❌ |
| GBPUSD | 15m | liquidity | 1407 | 25.2% | 0.632 | -$509,319 | 218 | 22.9% | 0.698 | -$67,119 | ❌ |
| XAUUSD | 5m | fixed3r | 3080 | 25.0% | 0.630 | -$1,185,128 | 377 | 28.9% | 1.020 | +$6,220 | ❌ (5il) |
| EURUSD | 30m | liquidity | 857 | 25.4% | 0.598 | -$327,219 | 134 | 21.6% | 0.388 | -$82,828 | ❌ |
| XAUUSD | 5m | liquidity | 3034 | 24.9% | 0.584 | -$1,342,850 | 358 | 31.6% | 1.197 | +$55,402 | ❌ (5il) |
| EURUSD | 15m | liquidity | 1474 | 24.2% | 0.575 | -$671,034 | 245 | 21.6% | 0.528 | -$129,929 | ❌ |
| USDJPY | 5m | fixed3r | 3224 | 25.7% | 0.536 | -$1,754,229 | 644 | 22.4% | 0.423 | -$460,887 | ❌ |
| USDJPY | 5m | liquidity | 3182 | 25.6% | 0.496 | -$1,918,233 | 637 | 24.0% | 0.394 | -$479,235 | ❌ |
| GBPUSD | 5m | liquidity | 2985 | 24.4% | 0.495 | -$1,873,779 | 510 | 22.5% | 0.278 | -$484,841 | ❌ |
| GBPUSD | 5m | fixed3r | 2967 | 23.7% | 0.465 | -$1,952,391 | 473 | 19.7% | 0.365 | -$402,561 | ❌ |
| EURUSD | 5m | liquidity | 3275 | 22.8% | 0.396 | -$2,771,836 | 558 | 24.2% | 0.359 | -$471,594 | ❌ |
| EURUSD | 5m | fixed3r | 3216 | 23.8% | 0.391 | -$2,672,027 | 577 | 23.4% | 0.392 | -$473,727 | ❌ |

---

## 3. Yekun tövsiyə: **NAS100 + 30m + liquidity-TP**

40 kombinasiyadan cəmi 2-si böyük-nümunə testini keçir, və **NAS100 30m
liquidity** aralarında AÇIQ liderdir (ən yüksək PF hər iki pəncərədə, həm
də ən böyük 5-illik nümunə, n=695). Bu, artıq **canlı işləyən** konfiqurasiya
ilə üst-üstə düşür (`run_live_sr_bias.py`, `SRBias_NAS100_*` task-ı,
`SESSION_HANDOFF.md`) — real spreadlə təzə sweep bu seçimi TƏSDİQLƏYİR.

### Kritik tapıntı: XAUUSD 15m (canlı işləyən DİGƏR strategiya) real spreadlə İTİRİR
`SRBias_XAUUSD_*` canlı task-ı XAUUSD M15-də işləyir. Bu sweep-də
**XAUUSD_15m_fixed3r: 5il PF 0.865, XAUUSD_15m_liquidity: 5il PF 0.892** —
hər ikisi 1.0-dan aşağı. Bu, `SESSION_HANDOFF.md` §3.1-də artıq qeyd olunan
"SR+Bias XAUUSD is net-negative once real costs are applied" tapıntısını
TƏSDİQ EDİR (indi tək 15m deyil, XAUUSD-un 4 timeframe-in HEÇ BİRİ 5 illik
PF≥1.0 keçmir — 60m fixed3r ən yaxını, 0.939). **Tövsiyə: XAUUSD canlı
botunu dayandırmaq və ya yenidən qurmaq nəzərdən keçirilməlidir.**

### EURUSD / GBPUSD tamamilə tərk edilməli
Hər bir TF/variant kombinasiyasında hər ikisi aydın itki verir (PF 0.39-0.78
aralığında, 5il/1il fərq etmir). Bu, canlı sinifin öz docstring-indəki
xəbərdarlığı ilə tam üst-üstə düşür ("USDJPY/EURUSD/GBPUSD did not [perform
well] and are NOT recommended").

### 5 dəqiqəlik timeframe hər simvolda fəlakətlidir
**Bütün** 5m kombinasiyaları (10/10) ən pis nəticələr sırasındadır (PF
0.39-0.72). First FVG-dəki tapıntıya bənzər: daha qısa TF = daha dar
stop = spread nisbətən daha çox R yeyir + daha çox səs-küy sinyalı.

---

## 4. Qalib kombinasiyanın (NAS100+30m+liquidity) detalları

### Yarımillik breakdown (son 5 il)
| Dövr | Trades | WR | PF | Net |
|---|---|---|---|---|
| 2021-08→2022-02 | 57 | 35.1% | 2.218 | +$51,656 |
| 2022-02→2022-08 | 66 | 22.7% | 0.465 | -$29,763 |
| 2022-08→2023-02 | 78 | 32.1% | 1.135 | +$7,888 |
| 2023-02→2023-08 | 84 | 27.4% | 1.034 | +$2,395 |
| 2023-08→2024-02 | 76 | 38.2% | 1.313 | +$17,174 |
| 2024-02→2024-08 | 85 | 29.4% | 0.766 | -$15,918 |
| 2024-08→2025-02 | 71 | 33.8% | 0.800 | -$10,233 |
| 2025-02→2025-08 | 67 | 32.8% | 0.905 | -$4,703 |
| 2025-08→2026-02 | 64 | 35.9% | 1.639 | +$28,268 |
| 2026-02→2026-08 | 47 | 31.9% | 1.436 | +$14,691 |

10 yarımillikdən 6-sı müsbət, 4-ü mənfi — dalğalı, amma orta hesabla
müsbətdə (5il yekunu PF 1.116). Son 4 yarımillikdən 2-si güclü müsbət
(2025-08→2026-02: PF 1.64; 2026-02→2026-08: PF 1.44).

### Son 1 ay (n=8) — xəbərdarlıq
`{'trades': 8, 'win_rate': 0.0, 'profit_factor': 0.0, 'net_pnl': -8363.02}`
— son 8 trade-in HAMISI itki! Bu, kiçik nümunənin (First FVG-də görüldüyü
kimi) tək başına narahatlıq mənbəyi olmamalıdır, amma izlənməlidir —
əgər bu trend davam etsə, strategiyanın rejimi dəyişmiş ola bilər.

---

## 5. Fayllar
- **Script:** [scripts/sr_daily_bias_spread_sweep.py](scripts/sr_daily_bias_spread_sweep.py)
- **Tam nəticələr (JSON):** `artifacts/sr_daily_bias_spread_sweep.json`
- **Trade logları:** `artifacts/sr_sweep_{symbol}_{tf}m_{variant}_trades.csv` (40 fayl)
- **Xam data:** `data/history/{NAS100,XAUUSD,EURUSD,GBPUSD,USDJPY}_M1.csv`
  (2026-08-28 tarixli təzə yükləmə)
