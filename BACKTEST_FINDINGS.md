# Backtest Tapıntıları — NAS100 (USTEC) NY-Open / Midnight Strategiyaları

Bu sənəd 2026-08-23/24 tarixli sessiyada aparılan bütün backtest işinin
xülasəsidir: infrastruktur qurulması (MT5 qoşulması, data yüklənməsi),
sınanan strategiyalar, hər birinin nəticələri, və yekun tövsiyə.

---

## 1. İnfrastruktur / Data

### MT5 qoşulması
- Köhnə demo hesab (`5052764320`, server `MetaQuotes-Demo`) etibarsız idi
  ("Invalid account" — deaktivasiya olunmuş). Yeni demo hesab yaradıldı:
  `111535061` / server `MetaQuotes-Demo` / $100,000 başlanğıc balans.
- `.env` faylı bu login/password ilə yeniləndi (repo-ya push olunmur,
  `.gitignore`-dadır).
- MT5 terminalının **`MaxBars` ayarı** (`common.ini`, `[Charts]` bölməsi)
  default `100000`-dan `5000000`-a qaldırıldı ki, tam tarixi data endirilə
  bilsin (əvvəl bu limitə görə M1 data yalnız son ~3.5 ay göstərirdi).

### Broker vaxt zonası (kritik tapıntı)
MT5 vaxt damğaları UTC kimi işarələnib, amma əslində **broker server
vaxtıdır** (EET/EEST — Avropa DST təqvimini izləyir, ABŞ DST-ni yox).
Bu, gündəlik texniki fasilənin (~23:55-01:00) düz 2026-03-29 (Avropa DST
tarixi) 1 saat sürüşməsi ilə empirik təsdiqləndi. Bütün skriptlərdə bu
düzəliş tətbiq olunur: broker vaxtı → `Europe/Bucharest` kimi
reinterpretasiya → real UTC → `America/New_York` (NY sessiya vaxtı üçün).

### Yüklənən data (`data/history/`, `.gitignore`-da, repo-ya push olunmur)
- `USTEC_M1.csv` — 2022-07-06 → cari tarix (~4.1 il, MT5-in verdiyi
  maksimum, ~1.4M bar)
- `USTEC_M5.csv` — ~6 ay
- `XAUUSD_M1.csv`, `EURUSD_M1.csv` — 2 il (müqayisə üçün)

Başqa komputerdə davam etmək üçün bu data-nı yenidən yükləmək lazımdır
(`data/download_history.py` və ya bu sənəddəki skriptlərin əvvəlindəki
bənzər bir-dəfəlik yükləmə skripti ilə).

---

## 2. Sınanan strategiyalar

### 2.1 Bias/Liquidity Sweep (`scripts/bias_liquidity_backtest.py`)
İlk strategiya: PD midline bias (əvvəlki günün cash sessiya H+L ortası),
09:30-09:45 pəncərəsində bias istiqamətinə uyğun ilk 5m şam, SL=şamın
əks kənarı+buffer, TP=ən yaxın likvidlik (PDL/PDH+swing), min RR filtri.
**Nəticə: zəif** (PF 0.17-1.22 arası, çox filtrdən sonra çox az trade
qalır). Bu, sonrakı strategiyaların əsasını qoydu, amma özü tövsiyə
olunmur.

### 2.2 First FVG @ NY-Open, 09:30 (`scripts/first_fvg_backtest.py`)
09:30-10:00 pəncərəsində ilk FVG-ni tap, bias filtri ilə/-siz sına.
**Nəticə: qeyri-sabit**, çox vaxt zəif (PF 0.2-2.0 arası, geniş
dəyişkənlik). 09:30 sessiyası midnight-dan (aşağıda) daha az etibarlı
çıxdı.

### 2.3 Midnight FVG, 00:00 NY (`scripts/first_fvg_backtest.py`, `SESSIONS=[midnight]`)
**Ən çox test olunan və ən güclü nəticə verən strategiya.**

Sınanan variasiyalar (xronoloji, hər addımda öyrənilən dərs qeyd olunub):

| Addım | Konfiqurasiya | Nəticə/Dərs |
|---|---|---|
| 1 | Bias+displacement filtri (ATR≥2x) | PF 1.22, amma trade sayı çox azdır (80/2il) |
| 2 | Displacement filtri silindi | Trade sayı 2.4x artdı (195), PF yaxşılaşdı (1.31) — displacement filtri çox sərt imiş |
| 3 | Bias filtri də silindi ("hər gün ilk FVG") | 278-291 trade, PF 1.22-1.52 |
| 4 | SL = FVG-nin öz kənarı + buffer → **SL = yaradıcı (orta) şamın öz wick-i** | PF əhəmiyyətli yaxşılaşdı (məs. TP=2.5R-də 1.16→1.30) |
| 5 | TP sabit R sınaqları: 1R, 2R, 2.5R, 3R | **2.5R ən yaxşı balans** (yüksək PF + yüksək net) |
| 6 | Min-gap sweep (1,2,3,5,8,10 xal) × giriş üsulu (toxunuş vs confirmation-candle) | **min_gap=3, toxunuş girişi = ən yaxşısı** (12 kombinasiyadan) |

#### Yekun konfiqurasiya (tövsiyə olunan)
```
Sessiya:      Midnight (00:00-00:30 NY)
Bias filtri:  YOX
Displacement: YOX (hər real 3-şamlıq boşluq FVG sayılır)
Min gap:      3 xal
Giriş:        FVG-nin yaxın kənarına birbaşa toxunuşda
              (bullish→üst kənar, bearish→alt kənar)
SL:           FVG-ni yaradan (orta) şamın öz wick-i
              (bullish→şamın low-u, bearish→şamın high-ı; buffer YOX)
TP:           sabit 2.5R
Risk:         1% balans/trade
Retest pəncərəsi: 5 şam (breakout-dan sonra)
```

#### Yekun nəticələr (tam ~4.1 il, 2022-07-06 → 2026-08-24, 409 trade)
| Göstərici | Dəyər |
|---|---|
| Win rate | 34.5% |
| **Profit Factor** | **1.30** |
| **Net P&L** (1% risk, $100k baza) | **+$80,846** |

**Dövr üzrə sabitlik (9 yarımillik dövr):**

| Dövr | WR | PF | Net |
|---|---|---|---|
| 2022 H2 | 37.8% | 1.52 | +$14,500 |
| 2023 H1 | 28.6% | 1.00 | $0 |
| 2023 H2 | 27.8% | 0.96 | -$500 |
| 2024 H1 | 29.4% | 1.04 | +$500 |
| 2024 H2 | 31.0% | 0.99 | -$154 |
| 2025 H1 | 35.5% | 1.38 | +$18,500 |
| 2025 H2 | 35.2% | 1.36 | +$12,500 |
| 2026 H1 | 41.7% | 1.79 | +$47,500 |
| 2026 H2 (natamam) | 18.2% | 0.56 | -$12,000 |

9 dövrdən 6-sı aydın müsbət, 2-si demək olar tam sıfır (±$500), yalnız
sonuncu (natamam) dövr ciddi mənfidir. Bu, sessiyada test olunan bütün
konfiqurasiyalar arasında ən sabit profildir.

**$1,000 hesab, 1% risk simulyasiyası (tam 4.1 il):** $1,000 → $1,743.46
(+74.3%), min balans $820 (2022-10), maks $1,993.

### 2.4 NY-Open Accumulation Breakout + Retest (`scripts/accumulation_breakout_backtest.py`)
SMC-üslubunda: HTF struktur (BOS/CHoCH state machine) bias, 09:30-dan
2-8 şamlıq sıxılma (akkumulyasiya), engulfing breakout, iki səviyyəli
retest (A=diapazon sərhədi, B=FVG/50%), SL=akkumulyasiyanın əks
sərhədi, TP=likvidlik-hədəfli (PDH/PDL+Asia/London+swing) və ya sabit R.

**Əsas dərs**: Premium/Discount və likvidlik-sweep-i sərt filtr (hard
gate) etmək demək olar sıfır trade verdi (2 il ərzində cəmi 2 trade) —
çünki struktur (trend-davam siqnalı) təbii olaraq price-ı öz "genişlənmiş"
tərəfinə aparır, bu da klassik mean-reversion tipli Premium/Discount
məntiqi ilə ziddiyyət təşkil edir. Bu iki amil **informativ** edildi
(sərt filtr yox), yalnız HTF struktur bias-ı təyin edir.

**Nəticə (tam 2 il, TP=3R sabit): 91 trade, WR 33.0%, PF 1.22, net
+$13,632.** Bias+likvidlik-hədəfli TP (3R tavanla) versiyası son 1 ildə
daha güclü idi: 20 trade, WR 45.0%, **PF 2.39**, net +$15,288.

Midnight FVG-dən daha az trade verir (aylıq ~2-4 vs ~8-9), amma bəzi
pəncərələrdə daha yüksək PF göstərir. İkisi paralel işlədilsə (2 il,
USTEC): 171 trade, WR 27.5%, PF 1.22, net +$27,316 — 23 gündə hər iki
sistem eyni gün trade açıb (o günlərdə cəmi risk 2% olur).

---

## 3. Canlı (live) bot infrastrukturu

`run_live_demo.py`-nin (mövcud, əvvəldən repo-da olan) eyni təhlükəsizlik
relsləri təkrarlanaraq Accumulation Breakout strategiyası üçün canlı
sinif quruldu:

- **`strategy/ny_open_accumulation_breakout.py`** — `TradeSetupStrategy`
  interfeysinə uyğun, gündəlik state-machine, `DailyContext` +
  `compute_daily_context()` ilə cross-timeframe (bias/PDH-PDL/likvidlik)
  girişləri ayrıca hesablanır.
- **`run_live_accumulation_breakout.py`** — `run_live_demo.py`-nin eyni
  demo-hesab təhlükəsizlik yoxlamaları (`.env`-də `MT5_ACCOUNT_TYPE=demo`
  + MT5-in öz `trade_mode` təsdiqi + kill-switch), `--paper` bayrağı ilə
  `PaperBroker` (real order yox) dəstəyi əlavə olundu.
- **`scripts/replay_live_strategy_check.py`** — canlı sinifi tarixi data
  üzərindən şam-şam keçirib batch backtest ilə tutuşdurma aləti
  (regression check).

⚠️ **Bu bot HEÇ VAXT işə salınmayıb** (nə demo, nə real hesabda) — yalnız
kod qurulub və backtest ilə struktur baxımından tutuşdurulub. Canlıya
keçmədən əvvəl uzun müddət `--paper` rejimində sınanması tövsiyə olunur.

**Qeyd**: Midnight FVG strategiyası (yekun tövsiyə olunan, bax bölmə 2.3)
üçün HƏLƏ canlı sinif qurulmayıb — yalnız backtest skripti var.

---

## 4. Fayl bələdçisi

| Fayl | Təyinatı |
|---|---|
| `scripts/bias_liquidity_backtest.py` | Strategiya 2.1 (Bias/Liquidity) |
| `scripts/first_fvg_backtest.py` | Strategiya 2.2/2.3 (First FVG, NY-open və Midnight, konfiqurasiya edilə bilən) |
| `scripts/accumulation_breakout_backtest.py` | Strategiya 2.4 (Accumulation Breakout) |
| `scripts/ny_open_accumulation_analysis.py` | Tək-günlük SMC analiz formatı (əl ilə yoxlama üçün) |
| `scripts/replay_live_strategy_check.py` | Canlı sinif vs batch backtest tutuşdurma |
| `strategy/ny_open_accumulation_breakout.py` | Canlı strategiya sinifi (yalnız Accumulation Breakout üçün) |
| `run_live_accumulation_breakout.py` | Canlı/paper trading runner (yalnız Accumulation Breakout üçün) |
| `USTEC_*.csv`, `EURUSD_*.csv`, `XAUUSD_*.csv` (repo kökündə) | Müxtəlif test pəncərələrinin trade cədvəlləri (istifadəçiyə göndərilənlərin surəti) |

`first_fvg_backtest.py`-in yuxarı hissəsindəki konstantlar
(`INPUT_CSV`, `SESSIONS`, `USE_BIAS_FILTER`, `REQUIRE_DISPLACEMENT`,
`MIN_GAP_POINTS`, `ENTRY_MODE`, `FIXED_TP_R`, `TEST_START_DATE`/
`TEST_END_DATE`) dəyişdirilərək istənilən variasiya təkrar test oluna
bilər — fayl hazırda bölmə 2.3-dəki yekun tövsiyə olunan konfiqurasiyaya
qurulub (`MIN_GAP_POINTS=3.0`, `ENTRY_MODE="touch"`, `FIXED_TP_R=2.5`,
`USE_BIAS_FILTER=False`, `REQUIRE_DISPLACEMENT=False`).

---

## 5. Əsas risklər / xatırlatmalar

1. **Heç bir strategiya real/demo hesabda işə salınmayıb** — hamısı
   backtest-dir.
2. **İlk 2-2.5 il (2022-2024) əksər konfiqurasiyalarda zəif/başabaş** —
   real edge yalnız 2025-dən sonra aydın görünür. Bu, ya bazar rejimi
   dəyişikliyi, ya təsadüfdür — əmin olmaq mümkün deyil.
3. **Son ay (2026-08) demək olar bütün variantlarda ən pis aydır** —
   canlıya keçmədən əvvəl bunun davam edib-etmədiyini izləmək lazımdır.
4. Nümunə ölçüləri (185-409 trade) statistik cəhətdən "kifayət qədər"
   sayıla bilər, amma "sübut olunmuş edge" demək üçün hələ tezdir —
   xüsusən kiçik alt-pəncərələrdə (aylıq 2-20 trade) təsadüf effekti
   böyükdür.
5. `.env` və `data/history/*.csv` repo-ya push olunmur (`.gitignore`) —
   başqa komputerdə bunları yenidən qurmaq/yükləmək lazımdır.
