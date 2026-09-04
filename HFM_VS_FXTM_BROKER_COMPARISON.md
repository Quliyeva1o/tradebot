# HFM vs FXTM — Broker Comparison, 3 Active Strategies

**Tarix:** 2026-09-02
**Məqsəd:** yeni açılan HFM Demo Premium hesabında (login 49843976,
server `HFMarketsGlobal-Demo`, balans $1000) son aktiv qalan 3 strategiyanı
öz real qiymət tarixçəsi + öz real spread-i ilə yenidən backtest edib,
FXTM-Demo02-dəki mövcud nəticələrlə müqayisə etmək.

---

## 0. Metodologiya

**Bu, sadəcə "eyni qiymətə fərqli spread calamaq" DEYİL** — istifadəçinin
özünün qeyd etdiyi kimi, fərqli broker fərqli likvidlik provayderindən qiymət
alır, ona görə HFM üçün MT5-dən **tam öz OHLC tarixçəsi** yenidən yükləndi
(sadəcə spread konstantı dəyişdirilmədi).

- **Qoşulma:** `.env`-dəki HFM login/password/server ilə, terminal restart
  edilib AlgoTrading aktivləşdirildikdən sonra uğurla qoşuldu (əvvəlki "IPC
  timeout" xətası terminal restartından sonra həll oldu).
- **Simvol adları HFM-də FXTM-dən fərqlidir:**
  - NAS100 ekvivalenti: HFM-də **"USA100"** ("NASDAQ 100 / CFD", davamlı) —
    DİQQƏT, "US100.F" adlı OXŞAR simvol var amma o, **bitiş tarixli fyuçers**
    kontraktdır ("US Tech 100 Index, Exp: March 2026"), 2026-dan əvvəl
    tarixçəsi YOXDUR — səhv seçim olardı, istifadə EDİLMƏYİB.
  - XAUUSD: hər iki brokerdə eyni ad (`XAUUSD`).
- **Data:** `data/history/hfm/{USA100,XAUUSD}_M1.csv`, 2020-01-02 →
  2026-09-01 (FXTM-dəki eyni pəncərə), `data/download_history.py` ilə
  yüklənib (2.35M / 2.35M bar, cəmi 53-54 dublikat silinib, 0 OHLC pozuntusu).
- **Spread:** HFM-in **real, bar-bar tarixi spread sütunu bütün dövr üzrə
  etibarlıdır** (FXTM-dəki NAS100 sütunu kimi 2024-ə qədər sıfır DEYİL):
  - USA100 orta spread: **1.90 pt** (erkən dövr 2.21, son aylar 2.15)
  - XAUUSD orta spread: **0.285** (erkən dövr 0.355, son aylar 0.347)
  - Müqayisə üçün FXTM-in bu sessiyalarda işlətdiyi FIKSƏ konstantlar: NAS100
    **3.0 pt**, XAUUSD **0.39** — yəni **HFM-in real spread-i FXTM-in
    fərziyyəsindən DAHA DAR-dır hər iki simvolda**. Bu vacibdir: aşağıda
    HFM-in bəzi strategiyalarda daha zəif çıxması UCUZ OLMAYAN spread-dən
    DEYİL, sadəcə qiymət hərəkətinin özündəki fərqdən irəli gəlir.
  - SR+Bias üçün real bar-bar spread sütunu birbaşa istifadə olunub;
    NASDAQ ORB M1 Breakout skripti yalnız TƏK konstant qəbul etdiyi üçün
    orada HFM-in müşahidə olunan orta qiyməti sabit olaraq keçirilib
    (USA100: 2.0, XAUUSD: 0.35).

---

## 1. SR + Daily Bias (NAS100/USA100, 30m, liquidity-TP)

| Pəncərə | Broker | n | WR | PF | Net |
|---|---|---|---|---|---|
| 5 il | **FXTM** | 695 | 31.8% | **1.116** | +$61,455 |
| 5 il | **HFM** | 852 | 27.9% | **1.027** | +$17,619 |
| 1 il | **FXTM** | 111 | 34.2% | **1.551** | +$42,959 |
| 1 il | **HFM** | 126 | 24.6% | **0.718** ❌ | **-$27,928** |
| 3 ay | HFM | 22 | 18.2% | 0.616 ❌ | -$7,123 |

**KRİTİK TAPINTI:** 5 illik pəncərədə hər iki broker PF≥1.0 saxlayır (HFM
zəifdir amma hələ müsbətdir), AMMA **son 1 ildə tam ƏKS istiqamətə
keçir** — FXTM-də ən güclü nəticə məhz son 1 il idi (+$42,959), HFM-də isə
son 1 il **NET İTKİDİR** (-$27,928). Bu, son 3 ayda daha da pisləşir
(-$7,123). Spread HFM-də DAHA UCUZ olduğu üçün bu fərq spread-dən deyil,
broker-in qiymət feed-inin (fərqli likvidlik provayderi) son dövrdə fərqli
davranmasından irəli gəlir.

**Nəticə:** SR+Bias-ın [[project-chosen-strategy-configs]] memory-də "ən
etibarlı strategiya" statusu **HFM üçün TƏSDİQLƏNMİR** — bu hesabda canlıya
çıxarmadan əvvəl bir neçə həftə Paper monitorinqi ŞİDDƏTLƏ tövsiyə olunur.

---

## 2. XAUUSD ORB + Liquidity-Sweep (M15, next-open, pəncərəli 09:45-11:00, Setup B)

| Broker | n | WR | PF | netR | net_pnl |
|---|---|---|---|---|---|
| **FXTM** (6.7 il, bu hesab) | 137 | — | **1.326** | — | — |
| **HFM** (6.6 il) | 104 | 47.1% | **1.145** | +6.18 | +$3,090.88 |

**Tapıntı:** HFM-də 24% daha az siqnal (104 vs 137) və PF nəzərəçarpacaq
dərəcədə aşağıdır (1.145 vs 1.326), amma **hələ 1.0-dan yuxarıdır** —
istiqamət eynidir, sadəcə daha zəifdir. Bu strategiya likvidlik-sweep +
FVG-retest kimi dəqiq wick-səviyyəli quruluşlara əsaslandığı üçün başqa
strategiyalardan DAHA HƏSSAS görünür broker feed fərqinə.

**Nəticə:** müsbət qalır, amma FXTM-dəki güc təkrarlanmır — HFM-də scale
etməzdən əvvəl əlavə Paper dövrü tövsiyə olunur.

---

## 3. NASDAQ ORB + M1 Breakout, LONG-only, `full` variant

| Konfiqurasiya | Broker | n (tam) | PF (tam) | n (1il) | PF (1il) | n (2il) | PF (2il) |
|---|---|---|---|---|---|---|---|
| NAS100 2R | FXTM | 923 | 1.182 | — | 1.08 | — | 1.19 |
| NAS100 2R | HFM | 1120 | **1.184** | 163 | 0.966 | 326 | 1.087 |
| NAS100 3R | FXTM | 719 | 1.12 | — | 1.16 | — | 1.13 |
| NAS100 3R | HFM | 873 | **1.127** | 133 | 1.091 | 262 | 1.09 |
| XAUUSD 3R | FXTM | 1147 | 1.092 | — | **1.42** | — | 1.31 |
| XAUUSD 3R | HFM | 1165 | **1.115** | 185 | **1.367** | 364 | 1.36 |

**Bu, üç strategiya arasında ƏN BROKER-DAYANIQLI olanıdır.** Tam tarixçə PF-i
demək olar İDENTİKDİR hər 3 kombinasiyada (fərq ≤0.03), trade sayı da çox
yaxındır (1120 vs 923, 873 vs 719, 1165 vs 1147). Son 1 ildə XAUUSD 3R HFM-də
də güclü qalır (PF 1.367, FXTM-in 1.42-nə çox yaxın) — bu strategiyanın FXTM
raportundakı "ən yaxşı tək nəticə" başlığı HFM-də də TƏSDİQLƏNİR.

Yeganə kiçik fərq: NAS100 2R-in son 1 ili HFM-də cüzi mənfi (-3.72R, n=163)
FXTM-in müsbət +8.92R-inə qarşı — kiçik nümunə ölçüsündə (163 trade) bu fərq
gözlənilən dalğalanma daxilindədir, xüsusi narahatlıq siqnalı deyil (3R
variant hər iki brokerdə müsbət qalır).

**Nəticə:** bu strategiya (xüsusən **XAUUSD LONG-only 3R**) HFM-ə keçiddə
edge-in itiriləcəyindən EN AZ narahat olmalı olan namizəddir.

---

## 4. Ümumi Xülasə

| Strategiya | Broker-dayanıqlılıq | Tövsiyə |
|---|---|---|
| **NASDAQ ORB M1 Breakout (LONG-only)** | ✅ Güclü — nəticələr demək olar identik | HFM-ə keçiddə ən etibarlısı |
| **XAUUSD ORB Liquidity-Sweep** | ⚠️ Orta — müsbət qalır, amma zəifləyib (PF 1.33→1.15) | Paper ilə əlavə doğrulama tövsiyə olunur |
| **SR + Daily Bias** | ❌ Zəif — son 1 il/3 ay HFM-də NET İTKİ | Bu hesabda canlıya keçirmədən əvvəl mütləq Paper monitorinqi |

**Ən vacib qeyd:** HFM-in real spread-i FXTM-in fərziyyəsindən DAHA UCUZ idi
hər iki simvolda (NAS100 ~1.9-2.15 vs FXTM 3.0 fiksə; XAUUSD ~0.29-0.35 vs
FXTM 0.39 fiksə) — deməli yuxarıdakı zəifləmələr XƏRC fərqindən yox, MƏHZ
broker-in qiymət feed-inin (fərqli likvidlik provayderi) fərqli davranışından
irəli gəlir. Bu, "eyni strategiya = eyni nəticə" fərziyyəsinin YANLIŞ
olduğunu göstərir — hər yeni broker/hesab üçün ən azı SR+Bias kimi
həssas strategiyaları canlıya keçirmədən əvvəl bir doğrulama dövrü keçirmək
lazımdır.

## Fayl xəritəsi

- `data/history/hfm/USA100_M1.csv`, `data/history/hfm/XAUUSD_M1.csv` — HFM-in
  xam M1 tarixçəsi (real spread sütunu daxil).
- `artifacts/nasdaq_orb_m1_breakout_usa100_full_long_trades.csv` / `_3R_` —
  HFM NAS100(USA100) LONG-only trade logları.
- `artifacts/nasdaq_orb_m1_breakout_xauusd_full_3R_long_trades.csv` — HFM
  XAUUSD LONG-only 3R trade logu.
- SR+Bias və XAUUSD ORB nəticələri ad-hoc python çağırışları ilə hesablanıb
  (fayla yazılmayıb, bu sənəddəki rəqəmlər bunların çıxışıdır) — təkrarlamaq
  üçün: `scripts/sr_daily_bias_spread_sweep.py`-dəki `load_m1_with_spread`/
  `resample_tf`/`run_backtest("USA100", 30, "liquidity", ...)` funksiyalarını
  `data/history/hfm/USA100_M1.csv` üzərində çağır; XAUUSD ORB üçün
  `scripts/xauusd_orb_liquidity_sweep_backtest.py`-dəki `run_backtest(...)`-i
  `entry_fill_mode="next_open"`, `bar_minutes=15`, `entry_window_end=11:00`,
  `enable_breakout=False`, `spread_points=0.35` ilə çağır.
