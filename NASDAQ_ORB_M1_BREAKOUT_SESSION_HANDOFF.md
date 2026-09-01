# NASDAQ 09:30 NY 15M Opening Range + M1 Breakout — Sessiya Handoff (2026-09-01)

Bu strategiya istifadəçinin öz yazılı spesifikasiyası əsasında sıfırdan
quruldu və backtest edildi. `scripts/nasdaq_orb_m1_breakout_backtest.py`
tam kodu saxlayır. Aşağıda NƏ TAPILDI və NƏ HƏLƏ EDİLMƏYİB var.

**Data qeydi:** real NQ/MNQ fyuçers datası yoxdur — NAS100 (FXTM-Demo02
CFD indeksi, `data/history/NAS100_M1.csv`, 2020-07-22 → 2026-09-01) və
XAUUSD (`data/history/XAUUSD_M1.csv`, 2020-01-02 → 2026-09-01) istifadə
olunub.

---

## 1. Spesifikasiya (istifadəçinin öz sözləri ilə, dəyişməz tətbiq edilib)

1. Opening Range = 09:30-09:45 NY, tək M15 şamı (High/Low).
2. 09:45-dən sonra M1 timeframe-də analiz: M1 şam OR-un çölünə bağlanırsa
   (yuxarı = bullish, aşağı = bearish) → breakout.
3. Gündə maksimum 1 trade (LONG YA DA SHORT) — hansı istiqamət ƏVVƏL
   breakout versə, elə O trade-dir.
4. TP = 2R (sonra 3R də sınandı, aşağıya bax).
5. Look-ahead YOX — OR-un H/L-i yalnız bar bağlandıqdan sonra istifadə
   olunur; giriş REAL bazar sifarişi kimi NÖVBƏTİ bar-ın açılışında
   dolur (breakout-u təsdiqləyən bar-ın öz bağlanışında YOX).

**İki açıq qeyri-müəyyənlik, HƏR İKİSİ ayrıca test edilib (susqun seçim
edilməyib):**
- Stop-loss: "Entry → OR Low" (`--stop-mode full`) VƏ "OR-un 0.5
  səviyyəsi" = `OR High - 0.5*(OR High-OR Low)` (`--stop-mode half`).
- Entry-window son saatı VERİLMƏYİB — skan 09:45-dən günün sonuna qədər
  məhdudiyyətsiz aparılır (əlavə edilməyib, spesifikasiyaya sadiq qalınıb).

Sonra istifadəçi İKİ ƏLAVƏ variant istədi:
- **`break_retest`** ("Variant A"): breakout yalnız İSTİQAMƏT təyin edir,
  giriş YOX; sonra OR səviyyəsinin retest olunub TƏSDİQLƏNMƏSİNİ
  (bar OR-a toxunub geri breakout tərəfində bağlanması) gözləyir, giriş
  növbəti bar-ın açılışında, stop = retest-in "swing low/high"-ı (breakout
  bar-dan retest bar-ına qədər ən aşağı/yuxarı nöqtə).
- **`fvg_retest`**: breakout yalnız istiqamət təyin edir, sonra OR-un
  ÇÖLÜNDƏ yaranan İLK 3-şamlıq FVG-ni tapır, o FVG-yə İLK TOXUNUŞ giriş
  olur, stop = FVG-ni yaradan (ortadakı) şamın aşağısı/yuxarısı (buferzsiz).

---

## 2. Nəticələr — 4 variant, hər ikisi (LONG+SHORT), tam tarixçə (6.1 il), spread net

| Variant | NAS100 PF (2R) | XAUUSD PF (2R) | NAS100 PF (3R) | XAUUSD PF (3R) |
|---|---|---|---|---|
| **full** (sadə breakout) | **1.18** ✅ | 0.959 | **1.183** ✅ | **1.00** |
| half (OR ortası stop) | 0.947 | 0.860 | 1.002 | 0.941 |
| break_retest (Variant A) | 0.828 | 0.696 | 0.843 | 0.736 |
| fvg_retest | 0.669 | 0.507 | 0.743 | 0.575 |

**`full` hər zaman ən güclüsüdür.** "Gözlə, retest et" variantları (Variant
A, FVG-retest) mexanika məntiqli görünsə də statistik olaraq DAHA PİS
çıxır — gözləmə breakout-un davam etmə ehtimalını əldən qaçırır.

3R hədəf ÜMUMİLİKDƏ kömək edir (8 kombinasiyadan 7-si yaxşılaşıb).

**Son 1 il (2025-09→2026-08) hər 4 variant, hər iki alətdə MƏNFİ və ya
breakeven-ə yaxındır** (`full` NAS100: 0.94, XAUUSD: 0.96 — SR/First
FVG/Midline Sweep-də gördüyümüz "son aylarda ümumi zəifləmə" nümunəsinin
YENƏ bir təzahürü) — TƏK BAŞINA "hər iki istiqamət" versiyası canlıya
çıxarılacaq qədər güclü DEYİL.

---

## 3. ƏSAS TAPINTI — LONG/SHORT asimmetriyası

Hər 4 variantda, hər iki alətdə, LONG SHORT-dan sistematik olaraq
güclüdür (məs. XAUUSD full 2R tam tarixçə: LONG PF 1.189, SHORT PF 0.824).
Bu, TƏK BİR konfiqurasiyaya xas deyil — 8/8 ölçmədə eyni istiqamətdədir.

**LONG-only test edildi (`--direction long`) — bu, "hər iki istiqamətdən
LONG alt-çoxluğunu SÜZMƏK" DEYİL:** günün ƏVVƏLKİ breakout-u SHORT olsaydı
belə, indi HƏMİN GÜN sonrakı bir LONG breakout üçün açıq qalır (əvvəllər
SHORT günün "trade slot"-unu bloklayırdı). Bu, ƏHƏMİYYƏTLİ DƏRƏCƏDƏ daha
çox trade tapır (məs. NAS100 full 2R: hər-iki-istiqamətdəki LONG alt-
çoxluğu n=390 idi, əsl LONG-only n=923).

### LONG-only `full` — tam tarixçə (6.1 il)

| | n | WR | PF | netR |
|---|---|---|---|---|
| NAS100 2R | 923 | 38.5% | **1.182** | +107.46 |
| NAS100 3R | 719 | 28.1% | 1.12 | +64.15 |
| XAUUSD 2R | 1272 | 35.9% | 1.015 | +12.98 |
| XAUUSD 3R | 1147 | 28.4% | 1.092 | +80.88 |

### LONG-only `full` — son 2 il vs hər iki istiqamət, son 2 il

| | Hər iki istiqamət PF | LONG-only PF |
|---|---|---|
| NAS100 2R | 1.07 | **1.19** |
| NAS100 3R | 0.93 ❌ | **1.13** ✅ |
| XAUUSD 2R | 1.00 | **1.14** |
| XAUUSD 3R | 1.13 | **1.31** |

### LONG-only `full` — son 1 il

| | PF | netR |
|---|---|---|
| NAS100 2R | 1.08 | +8.92 |
| NAS100 3R | 1.16 | +14.98 |
| XAUUSD 2R | 1.09 | +11.39 |
| **XAUUSD 3R** | **1.42** | **+50.16** |

**8/8 ölçmə (2 alət × 2 R-hədəf × {tam tarixçə, son 2 il}) müsbətdir, o
cümlədən SON 1 İL də (bu, bugünə qədər sınadığımız BÜTÜN strategiyalar
arasında YEGANƏ haldır ki, son 1 il aydın müsbətdir — SR, First FVG,
Midline Sweep, XAUUSD ORB-un hamısında son 1 il zəif/mənfi idi).**

**Diqqət — istisna:** NAS100-un son 1 ilində "hər iki istiqamət" rejimində
LONG əslində SHORT-dan PİS idi (2R: -10.47 vs +2.01). Bu ziddiyyət
DÜZGÜN LONG-only məntiqi ilə (SHORT-un günü bloklamaması ilə) HƏLL OLUR —
düzgün LONG-only NAS100-da da müsbətdir (+8.92). Yəni "hər iki
istiqamətdən LONG-u süzmək" ilə "əsl LONG-only" arasındakı fərq praktiki
əhəmiyyətlidir, sadəcə nəzəri deyil.

**Ən yaxşı tək nəticə: XAUUSD, LONG-only, `full`, 3R — son 1 ildə PF
1.42, +50.16R; son 2 ildə PF 1.31, +78.58R.**

---

## 4. Real, tapılıb-düzəldilmiş kod bugı (VACIB)

`run_backtest()` funksiyasında `direction` parametrini (LONG/SHORT/BOTH
filtri üçün, "long"/"short"/"both" dəyərləri ilə) EYNİ adla trade-in öz
istiqamətini saxlamaq üçün YENİDƏN İSTİFADƏ ETMİŞDİM
(`direction = "LONG" if bullish else "SHORT"`). Bu, parametri
PƏRDƏLƏYİRDİ (shadowing) — BİRİNCİ trade tapılandan sonra `direction`
dəyəri "LONG"/"SHORT" (böyük hərflə) olur, sonrakı bütün
`direction in ("both","long")` yoxlamaları HƏMİŞƏ False olur (böyük/kiçik
hərf uyğunsuzluğu) → BÜTÜN sonrakı breakout aşkarlanması dayanır.

**Simptom:** ilk LONG-only sweep cəmi **n=1** trade tapdı (6.1 il üçün,
gözlənilən ~600-1200 əvəzinə) — hər zaman 2021-02-01-dəki İLK trade,
sonra HEÇ NƏ. **Kök səbəb tapıldı** (dəyişən adı toqquşması,
`systematic-debugging` metodologiyası ilə), **düzəldildi** (`direction`
→ `trade_dir` yenidən adlandırıldı `run_backtest()`-də), **yenidən
işlədilib təsdiqləndi** (n=923/719/1272/1147, yuxarıdakı cədvələ bax).
Digər iki funksiya (`run_backtest_fvg_retest`, `run_backtest_break_retest`)
fərqli dəyişən adı (`breakout_dir`) işlətdiyi üçün bu bugdan TƏSİRLƏNMƏYİB.

**Dərs:** eyni funksiyada bir parametrin adını YENİDƏN İSTİFADƏ ETMƏ,
hətta "məntiqi cəhətdən fərqli mərhələdə" olsa belə.

---

## 5. Fayl xəritəsi (artifacts/)

Bütün fayllar `nasdaq_orb_m1_breakout_{symbol}_{variant}[_{R}R][_long]_trades.csv`
formatındadır (simvol adı YAZILMALIDIR — əvvəlcə yox idi, bu, iki dəfə
fərqli alətlərin bir-birinin üzərinə yazmasına səbəb oldu, düzəldilib).

- `nas100_full_trades.csv` / `_3R_` — NAS100, hər iki istiqamət, 2R/3R
- `nas100_full_long_trades.csv` / `_3R_` — NAS100, LONG-only, 2R/3R (bug DÜZƏLDİLMİŞ versiya)
- `xauusd_full_trades.csv` / `_3R_` — XAUUSD, hər iki istiqamət, 2R/3R
- `xauusd_full_long_trades.csv` / `_3R_` — XAUUSD, LONG-only, 2R/3R (bug DÜZƏLDİLMİŞ versiya)
- `xauusd_half_trades.csv` / `_3R_` — XAUUSD, hər iki istiqamət, `half` stop
- `xauusd_break_retest_trades.csv` / `_3R_` — XAUUSD, hər iki istiqamət, Variant A
- `xauusd_fvg_retest_trades.csv` / `_3R_` — XAUUSD, hər iki istiqamət, FVG-retest

**Qeyd:** NAS100-un `half`/`break_retest`/`fvg_retest` (hər iki istiqamət)
xam trade log-ları simvol-taglı adlandırma düzəlişindən ƏVVƏL yazılıb və
sonra XAUUSD run-ları tərəfindən üzərinə yazılıb — YALNIZ aqreqat
statistika (n/WR/PF/netR, §2-də) qalıb, xam CSV-lər YOXDUR. Lazım olsa
`python -m scripts.nasdaq_orb_m1_breakout_backtest --input-csv
data/history/NAS100_M1.csv --stop-mode {half,break_retest,fvg_retest}`
ilə asanlıqla təkrar yaradıla bilər (bir neçə dəqiqə çəkir).

---

## 6. Növbəti sessiya üçün TODO

1. **XAUUSD LONG-only `full` 3R-i tam validasiya batareyasından keçir**
   (bootstrap, walk-forward, Monte Carlo) — SR/XAUUSD ORB üçün etdiyimiz
   kimi. Bu, indiyədək ən güclü namizəddir, amma hələ TƏK-run nöqtə
   qiymətidir, tam battery yoxdur.
2. `half`/`break_retest`/`fvg_retest` üçün LONG-only sınanmayıb (yalnız
   `full` üçün edilib, çünki o, əsas namizəddir).
3. Canlı/Paper botuna qoşulmayıb — hələ heç bir Scheduled Task yaradılmayıb.
4. `data/update_history.py` (yeni, bu sessiyada yazılıb) — mövcud
   `data/history/*.csv` fayllarını INKREMENTAL (tam yenidən yükləmədən)
   günə qədər yeniləyir. `python -m data.update_history --symbols
   XAUUSD,NAS100 --timeframe M1`.
