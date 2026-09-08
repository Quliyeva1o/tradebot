# Strategiya/Simvol/Timeframe/R Sweep Hesabatı — 2026-09-08

Bu sessiyada **168+ backtest case** işlədildi: üç strategiya × 7 simvol × müxtəlif
R-hədəfləri və timeframe kombinasiyaları. Bütün qaçışlar FundingPips-Trial
hesabının öz M1 datası üzərindədir (bu gün 13:47-13:53 UTC-yə qədər yenilənib),
hər simvol **öz 2026 orta spread-i** ilə yüklənib (paylaşılan sabit deyil).

## Metodologiya

**Meyar:** bir konfiqurasiya yalnız **tam tarixçədə VƏ son 1 ildə** PF > 1.0
olanda "müsbət" sayılır. Yalnız tam tarixçəyə baxmaq tələdir — NDX100 bunu
əyani göstərdi: 6.7 illik PF 1.20-1.22 verir, amma son 2 ayda 0.44-0.71-ə düşür.

**Son 2 ay sütunu qərar üçün istifadə edilmir** — nümunə çox vaxt n=1-40
arasındadır, yəni statistik olaraq küydür. Yalnız istiqamət göstəricisi kimi
oxunub.

**Risk faizi sweep ölçüsü DEYİL.** Risk% pozisiya ölçüsünü xətti miqyaslayır,
heç bir trade-in girişini/çıxışını/R-multiple-ını dəyişmir — 0.5%-də və 2%-də
PF/win-rate/netR eynidir. Sizing ayrıca (Monte Carlo / ruin-riski) qərarıdır.

**Sıralama meyarı:** `netR ÷ √n` — ümumi kənarı ölçür, amma kiçik nümunə ilə
şişirdilmiş PF-ləri cəzalandırır.

---

## 1. Breakout (NASDAQ ORB M1 Breakout) — açıq lider

**30 müsbət konfiqurasiya.** Ən yaxşı 5-i (hamısı XAUUSD):

| # | OR / skan | n | PF | netR | Son 1 il PF |
|---|---|---|---|---|---|
| 1 | **60m / M15** | 465 | **1.558** | +188.4R | **1.604** |
| 2 | 30m / M15 | 534 | 1.431 | +171.6R | 1.434 |
| 3 | 60m / M5 | 499 | 1.441 | +163.4R | 1.589 |
| 4 | 15m / M5 | 839 | 1.320 | **+205.0R** | 1.516 |
| 5 | 30m / M5 | 653 | 1.354 | +174.7R | 1.307 |

**Canlı konfiqurasiya (15m OR / M1 skan, 4R) 9-cu yerdədir** — PF 1.241,
netR +172.7R, son 1 il 1.504. Yəni hazırkı qurulum işləyir, amma optimal deyil:
**daha uzun opening range (30-60m) sistematik olaraq üstündür.**

### R-hədəfi (15m/M1-də, simvol üzrə)

| Simvol | 2R | 3R | 4R |
|---|---|---|---|
| XAUUSD | 1.050 | 1.142 | **1.241** |
| GER40 | 1.125 | 1.173 | **1.211** |
| NDX100 | 1.178 | 1.164 | 1.188 |
| DJI30 | 1.186 | 1.178 | 1.090 |
| SPX500 | 1.044 | 1.182 | 1.127 |
| JP225 | 1.087 | 1.076 | **1.181** |
| FTSE100 | 1.018 | 1.104 | 1.063 |

Breakout üçün **R artdıqca nəticə yaxşılaşır** (XAUUSD/GER40/JP225-də 4R qalib).

### Simvol verdikti (son 1 il də daxil)

| Simvol | Status |
|---|---|
| **XAUUSD** | ✅ ən güclü — bütün R və timeframe-lərdə müsbət |
| **GER40** | ✅ müsbət (3R/4R, uzun OR-lar daha yaxşı) |
| **JP225** | ✅ 4R-də müsbət (1.181 / 1.063) |
| **FTSE100** | ✅ 3R/4R-də müsbət (1.104 / 1.129) |
| NDX100 | ⚠️ 30m/M5 və 30m/M15-də müsbət, amma 15m/M1-də son 1 il **0.915** |
| DJI30 | ⚠️ yalnız 5m və 60m/M5-də, digərlərində son 1 il mənfi |
| SPX500 | ❌ son 1 il demək olar hər konfiqurasiyada mənfi (0.73-0.77) |

---

## 2. Liquidity Sweep (XAUUSD ORB) — konfiqurasiya səhv idi

**5 müsbət konfiqurasiya, hamısı 15m opening range ilə:**

| # | Simvol / OR / entry pəncərəsi | n | PF | netR | Son 1 il PF |
|---|---|---|---|---|---|
| 1 | XAUUSD 15m / 12:00 | 230 | 1.365 | +36.3R | 1.398 |
| 2 | **GER40 15m / 11:00** | 100 | **1.547** | +19.1R | **1.761** |
| 3 | XAUUSD 15m / 11:00 | 163 | 1.246 | +19.1R | 1.713 |
| 4 | JP225 15m / 12:00 | 80 | 1.239 | +10.2R | **1.854** |
| 5 | JP225 15m / 11:00 | 55 | 1.253 | +7.5R | 1.578 |

**Ən vacib tapıntı: canlıdakı konfiqurasiya (5m OR / 10:00 pəncərə) grid-in ən
pis xanalarından biridir.** Onun rəqəmləri: XAUUSD PF 1.173 tam, amma son 1 il
**0.969** və son 2 ay **0.490** — yəni son dövrdə itkili.

15m OR-a keçəndə eyni simvollar canlanır:

| Simvol | 5m/10:00 (canlı) | ən yaxşı 15m | |
|---|---|---|---|
| XAUUSD | 1.173 / 1il 0.969 | 15m/12:00 → 1.365 / **1.398** | ✅ |
| GER40 | 0.761 / 1il 0.748 ❌ | 15m/11:00 → **1.547** / **1.761** | ✅ |
| JP225 | 0.626 / 1il 0.619 ❌ | 15m/12:00 → 1.239 / **1.854** | ✅ |

**Mexaniki qeyd:** `bar_minutes=15` + default 10:00 pəncərəsi **sıfır trade**
verir — entry pəncərəsinə cəmi bir 15-dəqiqəlik bar düşür, sweep+displacement+
FVG+giriş bir barda mümkün deyil. Bu, bug deyil; 15m OR yalnız genişləndirilmiş
pəncərə (11:00/12:00) ilə mənalıdır.

---

## 3. First FVG "silver bullet" (TradingView `JEx4gmZz`) — yalnız NASDAQ-da

Skript closed-source olduğu üçün təsvirindəki qaydalar reallaşdırıldı: hər NY
pəncərəsində (03:00-04:00, **10:00-11:00**, 14:00-15:00) ilk 3-şamlı FVG; giriş
boşluğun yaxın kənarına geri-dönüşdə; stop ortadakı şamın gövdə ekstremumunda;
hədəf R-multiple.

**Cəmi 3 müsbət konfiqurasiya (42 case-dən):**

| # | Konfiqurasiya | n | PF | netR | Son 1 il PF |
|---|---|---|---|---|---|
| 1 | **NDX100 15m / 10:00-11:00** | 549 | 1.150 | +52.0R | **1.588** |
| 2 | DJI30 5m / 10:00-11:00 | 1341 | 1.052 | +46.6R | 1.164 |
| 3 | XAUUSD 15m / 03:00-04:00 | 602 | 1.009 | +3.7R | 1.300 |

### NDX100 detallı (pəncərə 10:00-11:00)

| TF | R | Tam | Son 1 il | Son 3 ay | Son 1 ay |
|---|---|---|---|---|---|
| M1 | 2R | 0.625 ❌ | 0.650 | 0.730 | 0.812 |
| M1 | 3R | 0.633 ❌ | 0.565 | 0.569 | 0.401 |
| M5 | **2R** | 1.000 | 1.216 | **1.505** | **1.629** |
| M5 | 3R | 0.992 | **1.244** | 1.182 | 0.853 |
| M5 | 4R | 0.978 | 1.161 | 0.863 | 0.648 |
| **M15** | **2R** | **1.150** | **1.588** | 1.046 | 0.752 |
| M15 | 3R | 1.127 | 1.330 | 1.063 | 0.818 |
| M15 | 4R | 1.085 | 1.264 | 1.166 | 0.623 |

**Bu strategiyada R artdıqca nəticə PİSLƏŞİR** — Breakout-un tam əksi. FVG-yə
geri-dönüş girişləri dar stop və az davamiyyətlə işlədiyi üçün yaxın hədəf
uyğundur. Ən yaxşı: **M15 + 2R**.

**M1 hər iki R-də də struktural olaraq işləmir:** M1 FVG-ləri kiçikdir, stop
məsafəsi 3-4 punt olur, NDX100-ün 1.71 puntluq spread-i hər trade-ə ~0.5R xərc
gətirir. Timeframe böyüdükcə FVG genişlənir və spread-in nisbi çəkisi azalır.

**İndikatorun vaxt iddiası qismən təsdiqlənir:** 10:00-11:00 pəncərəsi demək olar
hər simvolda digər iki pəncərədən yaxşıdır (məs. NDX100: 10:00 → 1.150, amma
03:00 → 0.744, 14:00 → 0.698). Amma mənfəətə yalnız NASDAQ-da çatır.

**Struktural uğursuzluqlar:** JP225 (spread 10.0), GER40 (3.5), FTSE100 (1.4)
simvollarında M5-də PF 0.08-0.47 kimi fəlakətli rəqəmlər çıxır. Bu real "itki"
deyil — dar FVG stopu + geniş spread uyğunsuzluğudur (`cost_R = spread ÷ risk`
bəzən 1R-i aşır). Bu simvollarda strategiya struktural olaraq mümkünsüzdür.

---

## 4. Bu sessiyada edilən kod dəyişiklikleri

| Fayl | Dəyişiklik |
|---|---|
| `scripts/backtest_common.py` | `resample()`-ə `offset_minutes` parametri (default 0 = pandas-ın öz davranışı, mövcud çağıranlara təsirsiz) |
| `scripts/nasdaq_orb_m1_breakout_backtest.py` | `run_backtest()`-ə `or_minutes` / `scan_minutes` parametrləri; hər iki şəbəkə öz anchor-una bağlanır |
| `run_live_nasdaq_orb_xauusd_{demo,paper}.bat` | `--tp-r` 3.0 → 4.0 (sweep nəticəsinə əsasən geri qaytarıldı) |

**60m offset düzəlişi:** 60 dəqiqəlik resample şəbəkəsi gecə yarısından anchorlanır
(09:00, 10:00...), ona görə 09:30 opening range şamı **heç vaxt mövcud olmurdu** və
bütün 60m sətirləri `n=0` verirdi. İndi şəbəkə 09:30-a bağlanır.

**Regressiya təsdiqi:** default parametrlərlə (15m OR / M1 skan) nəticə düzəlişdən
əvvəlki ilə eynidir — n=922, PF 1.241, netR +172.8.

---

## 5. Nəticə və növbəti addımlar

1. **Breakout hər üç ölçüdə (say, PF, sübut möhkəmliyi) liderdir** — 30 müsbət
   konfiqurasiya, ən yaxşıları n=465-839 nümunə ilə.
2. **Canlı konfiqurasiyaların heç biri optimal deyil:**
   - Breakout XAUUSD: 15m/M1 (PF 1.241) yerinə **60m/M15** (PF 1.558) və ya
     **15m/M5** (netR +205R)
   - LiqSweep XAUUSD: 5m/10:00 (son 1 il **0.969**) yerinə **15m/12:00**
     (son 1 il 1.398)
3. **Yeni namizəd simvollar:** JP225 4R və FTSE100 4R Breakout üçün hər iki
   pəncərədə müsbətdir; NDX100 15m/2R First FVG üçün.
4. **SPX500 rədd edilir** — son 1 ildə demək olar bütün konfiqurasiyalarda mənfi.

### Vacib xəbərdarlıq — çoxlu müqayisə

38 müsbət nəticə **168+ case**-dən seçilib. Ən yuxarıdakı PF-lər bu səbəbdən
bir qədər şişikdir (grid-in ən yaxşı xanasını seçmək edge-i sistematik
şişirdir). **Heç bir konfiqurasiya bu hesabata əsasən birbaşa canlıya
çıxarılmamalıdır** — əvvəlcə walk-forward / bootstrap / out-of-sample təsdiqi
lazımdır, bu layihənin öz metodologiyasına (`ADVANCED_VALIDATION_REPORT.md`,
`ROBUSTNESS_VALIDATION_REPORT.md`) uyğun olaraq.

### Tamamlanmamış hissə

Breakout timeframe sweep 7 simvoldan **4.5-ində** tamamlandı (XAUUSD, GER40,
NDX100, DJI30 tam; SPX500 6/9). **JP225 və FTSE100 üçün timeframe grid
işlədilməyib** — onlar yalnız R-grid-də (15m/M1) test olunub. Təkrar işə salmaq
üçün: `python -m scripts.breakout_timeframe_sweep --symbols JP225,FTSE100`.
