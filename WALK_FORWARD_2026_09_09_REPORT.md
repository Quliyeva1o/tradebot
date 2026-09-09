# Walk-Forward və Sabitlik Analizi — 2026-09-09

`SWEEP_2026_09_08_REPORT.md` 195 case-dən 48 müsbət konfiqurasiya tapdı. Bu
hesabat növbəti sualı cavablandırır: **o rəqəmlərin nə qədəri realdır və hansı
konfiqurasiya real hesabı daşıya bilər?**

İki müstəqil test aparıldı:
1. **Sabitlik/risk analizi** — hər müsbət konfiqurasiyanın aylıq davranışı
2. **Walk-forward seçim testi** — seçim prosesinin özünün qərəzsiz ölçülməsi

---

## 1. Əsas nəticə: "sabit gəlir" bu dəstdə YOXDUR

İstifadəçinin tələbi "hər ayı profitlə bağlayan, sabit artan strategiya" idi.
46 müsbət konfiqurasiyanın heç biri bunu vermir:

| Metrik | Bütün dəst üzrə |
|---|---|
| Ən yaxşı yaşıl ay faizi | **69.6%** (JP225, cəmi 23 ay data) |
| Tipik yaşıl ay faizi | 50-60% |
| Ən uzun itkili seriya | 2-7 ay |

Yəni ən yaxşı halda hər 10 ayın 3-4-ü mənfidir. Bu, bug və ya pis konfiqurasiya
deyil — ~25% win-rate + 4R hədəfli sistemin **riyazi təbiətidir**. Qazanc az
sayda böyük trade-dən gəlir, aralarda uzun itkili dövrlər olur.

---

## 2. Risk profilləri: iki fərqli ailə

Drawdown baxımından LiqSweep Breakout-dan **4-8 dəfə hamardır**:

| Konfiqurasiya | maxDD | Ən pis ay | Win rate | PF | Yaşıl ay | n |
|---|---|---|---|---|---|---|
| GER40 LiqSweep 15m/11:00 | **6.6R** | −2.2R | **53.0%** | 1.547 | **63.0%** | 100 |
| XAUUSD LiqSweep 15m/12:00 | **8.5R** | −4.1R | 48.3% | 1.365 | 58.1% | 230 |
| JP225 LiqSweep 15m/11:00 | **4.9R** | −3.1R | 41.8% | 1.253 | 52.0% | 55 |
| *Tipik Breakout* | *20-60R* | *−8...−17R* | *22-30%* | *1.1-1.5* | *50-60%* | *300-1200* |

Səbəb strukturaldır: LiqSweep ~50% win-rate ilə 2R alır, Breakout ~25% ilə 4R.
Eyni PF, tamamilə fərqli **yol**. Ancaq LiqSweep-in ümumi gəliri kiçikdir
(5-6 ildə +7...+36R, yəni ildə ~5R).

**Ən yaxşı gəlir/risk balansı: XAUUSD Breakout 60m/M15** — +188.4R gəlir,
cəmi 23.8R drawdown ilə. Canlı konfiqurasiya (15m/M1) eyni ailədə +172.7R
verir, amma **50.1R drawdown** ilə — yəni oxşar gəliri iki qat riskə.

---

## 3. Sezonallıq (Avqust-Noyabr)

| Ay | Nümunə |
|---|---|
| **Avqust** | ✅ Ən güclü — demək olar bütün konfiqurasiyalarda müsbət (PF 1.32-1.51) |
| **Sentyabr** | ⚠️ Ən zəif — NDX100 −18.1R (PF 0.78) və −16.6R (0.72); XAUUSD çətinliklə müsbət |
| Oktyabr | Qarışıq — XAUUSD 5m −34.4R, NDX100 +33.7R |
| **Noyabr** | ✅ Güclü — hamısında müsbət (PF 1.22-1.91) |

Bu hesabat sentyabrda yazılır — tarixən ən zəif ay. Konfiqurasiyaların
əksəriyyətinin "son 1 ay" sütununda mənfi olması bununla üst-üstə düşür və
strategiya nasazlığı kimi oxunmamalıdır.

---

## 4. Walk-forward seçim testi — overfitting-in qiyməti

**Metod:** hər fold-da yalnız train pəncərəsinə (24 ay) baxılır, 63
konfiqurasiyalıq **tam grid-dən** (uduzanlar daxil) ən yaxşısı seçilir, sonra
həmin seçim görmədiyi növbəti 6 aya tətbiq edilir. Seçim hovuzunu artıq müsbət
çıxdığı bilinən konfiqurasiyalarla məhdudlaşdırmaq hər fold-a gələcək məlumatı
sızdırardı, ona görə tam grid işlədilib.

| Fold | Train | Validation | Seçilən | train PF | val PF | val netR |
|---|---|---|---|---|---|---|
| 1 | 2020-01→2022-01 | 2022-01→2022-07 | NDX100 60m/M15 | 1.820 | 0.626 | −7.2 |
| 2 | 2020-07→2022-07 | 2022-07→2023-01 | NDX100 30m/M5 | 1.354 | 1.010 | +0.3 |
| 3 | 2021-01→2023-01 | 2023-01→2023-07 | SPX500 30m/M1 | 1.398 | 1.574 | +18.7 |
| 4 | 2021-07→2023-07 | 2023-07→2024-01 | XAUUSD 30m/M5 | 1.429 | 1.377 | +12.0 |
| 5 | 2022-01→2024-01 | 2024-01→2024-07 | XAUUSD 30m/M15 | 1.481 | 0.976 | −0.8 |
| 6 | 2022-07→2024-07 | 2024-07→2025-01 | NDX100 30m/M1 | 1.735 | 1.159 | +4.4 |
| 7 | 2023-01→2025-01 | 2025-01→2025-07 | SPX500 60m/M15 | 1.758 | 0.780 | −3.4 |
| 8 | 2023-07→2025-07 | 2025-07→2026-01 | GER40 60m/M5 | 1.713 | 1.161 | +6.0 |
| 9 | 2024-01→2026-01 | 2026-01→2026-07 | XAUUSD 60m/M15 | 1.924 | 1.205 | +6.8 |

**Nəticə:**

| | Dəyər |
|---|---|
| Müsbət validation fold | **6/9 (67%)** |
| Orta train PF (vəd) | **1.623** |
| Orta validation PF (real) | **1.096** |
| Orta val netR | +4.1R / 6 ay |
| Top-3 diversifikasiya | +4.6R / 6 ay |

**Edge-in ~84%-i buxarlanır.** 1.0-dan yuxarı hissə 0.62 → 0.10 düşür. Seçim
metodu işləyir, amma zəif: ildə ~8R. Top-3 birlikdə işlətmək cüzi yaxşıdır,
yəni tək konfiqurasiya riski fəlakətli deyil.

---

## 5. Fold-be-fold sabitlik

| Konfiqurasiya | F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | F9 | Müsbət | Cəmi |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **XAUUSD 60m/M15** | +2 | +13 | +4 | +11 | +8 | +21 | +20 | +29 | +7 | **9/9** | +114.2R |
| XAUUSD 15m/M5 | +23 | +4 | −0 | +11 | +10 | +32 | +46 | +53 | +2 | 8/9 | **+182.1R** |
| XAUUSD 15m/M1 *(canlı)* | +15 | −1 | +0 | +6 | +11 | +32 | +33 | +47 | +8 | 8/9 | +151.3R |
| NDX100 5m/M1 | −21 | +21 | +31 | +10 | +31 | +3 | +2 | +28 | −2 | 7/9 | +103.0R |
| SPX500 60m/M5 | −14 | −8 | +15 | +16 | +18 | −3 | +10 | +7 | +6 | 6/9 | +48.3R |
| **GER40 30m/M1** | −3 | −7 | +27 | +17 | −9 | +34 | +53 | +4 | −5 | **5/9** | +110.7R |

**XAUUSD 60m/M15 heç bir 6 aylıq pəncərədə itirməyib** (2020-dən bəri), və son
fold-lar daha güclüdür. Sweep-in birinci seçimi ilə 9-cu fold-un müstəqil seçimi
üst-üstə düşür.

**GER40 30m/M1 xəbərdarlıqdır:** tam tarixçə PF 1.264 yaxşı görünür, amma yalnız
5/9 fold müsbət — demək olar sikkə atmaq. Sweep-in bəyəndiyi, walk-forward-un
bəyənmədiyi konfiqurasiya.

---

## 6. Real gözlənti — dürüst rəqəmlər

Bölmə 5-dəki 9/9 **tam təmiz deyil**: o konfiqurasiyalar tam tarixçəyə baxılaraq
seçilib, ona görə fold-lar seçim datası ilə üst-üstə düşür. Qərəzsiz qiymət
bölmə 4-dür.

| | XAUUSD 60m/M15 |
|---|---|
| Sweep-in dediyi | PF 1.558 |
| Walk-forward-un qərəzsiz qiyməti | PF ~1.10-1.30 |
| **Real hesab üçün gözlənti** | **PF 1.1-1.3, 1.5 DEYİL** |

Buraya real icra sürtünməsi (slippage, requote, gecikmə) hələ daxil deyil.

**Yekun xarakteristika:** XAUUSD Breakout 60m/M15-in real edge-i var, amma
kapital əyrisi **artan və dalğalıdır** — "sabit" deyil. Ayların 43%-i qırmızı,
5 ayadək itkili seriya mümkündür, maksimum drawdown 23.8R.

---

## 7. Metodoloji qeydlər

- Walk-forward yalnız **Breakout ailəsinə** tətbiq olunub (63 konfiqurasiya).
  LiqSweep və First FVG üçün eyni test **aparılmayıb** — onların nümunə ölçüsü
  (n=55-230) fold-lara bölünəndə statistik mənasız olur.
- Bütün rəqəmlər FundingPips-Trial feed-inə və hər simvolun öz 2026 spread-inə
  əsaslanır. Başqa broker-də nəticə fərqlənə bilər — layihənin öz təcrübəsi
  bunu dəfələrlə göstərib (`project-hfm-broker-comparison`).
- Risk faizi heç bir yerdə sweep ölçüsü kimi işlədilməyib: o, pozisiya ölçüsünü
  miqyaslayır, R-multiple-ı dəyişmir.

## 8. Təkrar istehsal

```
python -m scripts.consistency_analysis                 # bölmə 1-3
python -m scripts.consistency_analysis --detail "XAUUSD 60m"
python -m scripts.walk_forward_selection               # bölmə 4-5
```
