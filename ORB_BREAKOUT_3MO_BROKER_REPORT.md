# ORB Breakout — son 3 ay, broker müqayisəsi

Hazırlandı: 2026-09-22 11:14 UTC · pəncərə: **2026-06-21 → 2026-09-18** (90 gün) · hər bot ayrıca $50,000 hesabda · skript: `scripts/orb_breakout_year_backtest.py`

Brokerlər: **FundingPips (hazırkı)** (`data\history\fundingpips`) · **CFI (yeni hesab)** (`data\history\cfi`)

## Əsas cədvəl

| Bot | Simvol | Hal | Parametr | FundingPips (hazırkı): n / PF / netR / nəticə | CFI (yeni hesab): n / PF / netR / nəticə | fərq |
|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo ⚠️ | DJI30 | Demo | 60m OR / M1 / 4R | 7 / 0.425 / -4.2R / -$1,048 | 7 / 0.452 / -3.9R / -$994 | +0.3R |
| OrbBreakout_DJI30_Paper | DJI30 | Paper | 60m OR / M1 / 4R | 7 / 0.425 / -4.2R / -$1,048 | 7 / 0.452 / -3.9R / -$994 | +0.3R |
| OrbBreakoutinv_DJI30_Paper | DJI30 | Paper (inverse) | 60m OR / M1 / 4R | 7 / 1.364 / +0.4R / $107 | 7 / 1.549 / +0.5R / $78 | +0.2R |
| OrbBreakout_GER40_Demo ⚠️ | GER40 | Demo | 30m OR / M1 / 4R | 31 / 1.019 / +0.5R / $141 | 33 / 1.018 / +0.5R / -$57 | +0.0R |
| OrbBreakout_GER40_Paper | GER40 | Paper | 30m OR / M1 / 4R | 31 / 1.019 / +0.5R / $141 | 33 / 1.018 / +0.5R / -$57 | +0.0R |
| OrbBreakout_JP225_Demo ⚠️ | JP225 | Demo | 15m OR / M1 / 4R | 45 / 0.669 / -13.8R / -$1,781 | 45 / 0.561 / -18.9R / -$4,721 | -5.2R |
| OrbBreakout_JP225_Paper | JP225 | Paper | 15m OR / M1 / 4R | 45 / 0.669 / -13.8R / -$1,781 | 45 / 0.561 / -18.9R / -$4,721 | -5.2R |
| OrbBreakoutinv_JP225_Paper | JP225 | Paper (inverse) | 15m OR / M1 / 4R | 44 / 1.268 / +1.9R / $474 | 43 / 1.614 / +3.7R / $932 | +1.8R |
| OrbBreakout_NDX100_Demo ⚠️ | NDX100 | Demo | 30m OR / M5 / 4R | 18 / 0.409 / -11.3R / -$2,613 | 18 / 0.413 / -11.2R / -$2,616 | +0.1R |
| OrbBreakout_NDX100_Paper | NDX100 | Paper | 30m OR / M5 / 4R | 18 / 0.409 / -11.3R / -$2,613 | 18 / 0.413 / -11.2R / -$2,616 | +0.1R |
| OrbBreakoutinv_NDX100_Paper | NDX100 | Paper (inverse) | 30m OR / M5 / 4R | 18 / 2.011 / +2.0R / $405 | 18 / 2.301 / +2.6R / $532 | +0.6R |
| OrbBreakout_SPX500_Demo ⚠️ | SPX500 | Demo | 60m OR / M1 / 4R | 9 / 0.368 / -6.2R / -$1,529 | 8 / 0.420 / -5.0R / -$1,183 | +1.2R |
| OrbBreakout_SPX500_Paper | SPX500 | Paper | 60m OR / M1 / 4R | 9 / 0.368 / -6.2R / -$1,529 | 8 / 0.420 / -5.0R / -$1,183 | +1.2R |
| OrbBreakoutinv_SPX500_Paper | SPX500 | Paper (inverse) | 60m OR / M1 / 4R | 8 / 1.668 / +0.7R / $153 | 8 / 1.835 / +0.8R / $189 | +0.2R |
| OrbBreakout_XAUUSD_Demo ⚠️ | XAUUSD | Demo | 15m OR / M1 / 4R | 43 / 0.762 / -8.7R / -$1,637 | 43 / 0.795 / -7.2R / -$1,336 | +1.4R |
| OrbBreakoutwf_XAUUSD_Demo | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 36 / 1.074 / +1.7R / $228 | 36 / 1.109 / +2.4R / $275 | +0.7R |
| OrbBreakout_XAUUSD_Paper | XAUUSD | Paper | 60m OR / M1 / 3R | 29 / 0.575 / -10.6R / -$2,495 | 29 / 0.592 / -10.0R / -$2,424 | +0.6R |
| OrbBreakoutinv_XAUUSD_Paper | XAUUSD | Paper (inverse) | 15m OR / M1 / 4R | 42 / 1.111 / +0.9R / $191 | 43 / 1.123 / +1.0R / $226 | +0.1R |
| OrbBreakoutwf_XAUUSD_Paper | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 36 / 1.074 / +1.7R / $340 | 36 / 1.109 / +2.4R / $463 | +0.7R |

⚠️ = `deploy/demo_roster.txt`-də olmayan Demo konfiqurasiyası: .bat faylı var, amma hazırda real order vermir (o simvolu başqa strategiya tutur).

## Hər broker ayrıca

### FundingPips (hazırkı)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 7 | 14.3% | 0.425 | -4.2 | 7.3 | 1 | 6 | $48,952 | 3.5% | -2.1% | -$525 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 7 | 14.3% | 0.425 | -4.2 | 7.3 | 1 | 6 | $48,952 | 3.5% | -2.1% | -$525 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 7 | 85.7% | 1.364 | +0.4 | 1.0 | 6 | 1 | $50,107 | 0.4% | +0.2% | -$22 | $0 | 0 |
| OrbBreakout_GER40_Demo | 31 | 22.6% | 1.019 | +0.5 | 8.5 | 7 | 24 | $50,141 | 4.2% | +0.3% | -$641 | $0 | 0 |
| OrbBreakout_GER40_Paper | 31 | 22.6% | 1.019 | +0.5 | 8.5 | 7 | 24 | $50,141 | 4.2% | +0.3% | -$641 | $0 | 0 |
| OrbBreakout_JP225_Demo | 45 | 15.6% | 0.669 | -13.8 | 19.8 | 7 | 38 | $48,219 | 6.3% | -3.6% | -$123 | $0 | 0 |
| OrbBreakout_JP225_Paper | 45 | 15.6% | 0.669 | -13.8 | 19.8 | 7 | 38 | $48,219 | 6.3% | -3.6% | -$123 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 44 | 84.1% | 1.268 | +1.9 | 4.1 | 37 | 7 | $50,474 | 2.0% | +0.9% | -$33 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 18 | 11.1% | 0.409 | -11.3 | 11.3 | 2 | 16 | $47,387 | 5.2% | -5.2% | -$341 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 18 | 11.1% | 0.409 | -11.3 | 11.3 | 2 | 16 | $47,387 | 5.2% | -5.2% | -$341 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 18 | 88.9% | 2.011 | +2.0 | 1.3 | 16 | 2 | $50,405 | 0.6% | +0.8% | -$15 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 9 | 11.1% | 0.368 | -6.2 | 7.2 | 1 | 8 | $48,471 | 3.6% | -3.1% | -$483 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 9 | 11.1% | 0.368 | -6.2 | 7.2 | 1 | 8 | $48,471 | 3.6% | -3.1% | -$483 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 8 | 87.5% | 1.668 | +0.7 | 1.0 | 7 | 1 | $50,153 | 0.4% | +0.3% | -$17 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 43 | 18.6% | 0.762 | -8.7 | 17.2 | 8 | 35 | $48,363 | 8.0% | -3.3% | -$218 | -$33 | 1 |
| OrbBreakoutwf_XAUUSD_Demo | 36 | 33.3% | 1.074 | +1.7 | 5.9 | 7 | 21 | $50,228 | 1.2% | +0.5% | -$80 | -$8 | 0 |
| OrbBreakout_XAUUSD_Paper | 29 | 17.2% | 0.575 | -10.6 | 10.6 | 5 | 24 | $47,505 | 5.0% | -5.0% | -$177 | -$12 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 42 | 81.0% | 1.111 | +0.9 | 5.2 | 34 | 8 | $50,191 | 2.2% | +0.4% | $18 | -$7 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 36 | 33.3% | 1.074 | +1.7 | 5.9 | 7 | 21 | $50,340 | 2.7% | +0.7% | -$172 | -$16 | 0 |

### CFI (yeni hesab)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 7 | 14.3% | 0.452 | -3.9 | 7.1 | 1 | 6 | $49,006 | 3.3% | -2.0% | -$423 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 7 | 14.3% | 0.452 | -3.9 | 7.1 | 1 | 6 | $49,006 | 3.3% | -2.0% | -$423 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 7 | 85.7% | 1.549 | +0.5 | 1.0 | 6 | 1 | $50,078 | 0.4% | +0.2% | $12 | $0 | 0 |
| OrbBreakout_GER40_Demo | 33 | 21.2% | 1.018 | +0.5 | 9.9 | 7 | 26 | $49,943 | 5.0% | -0.1% | -$483 | $0 | 0 |
| OrbBreakout_GER40_Paper | 33 | 21.2% | 1.018 | +0.5 | 9.9 | 7 | 26 | $49,943 | 5.0% | -0.1% | -$483 | $0 | 0 |
| OrbBreakout_JP225_Demo | 45 | 13.3% | 0.561 | -18.9 | 22.0 | 6 | 39 | $45,279 | 10.7% | -9.4% | -$269 | $0 | 0 |
| OrbBreakout_JP225_Paper | 45 | 13.3% | 0.561 | -18.9 | 22.0 | 6 | 39 | $45,279 | 10.7% | -9.4% | -$269 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 43 | 86.0% | 1.614 | +3.7 | 3.0 | 37 | 6 | $50,932 | 1.4% | +1.9% | -$30 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 18 | 11.1% | 0.413 | -11.2 | 11.2 | 2 | 16 | $47,384 | 5.2% | -5.2% | -$303 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 18 | 11.1% | 0.413 | -11.2 | 11.2 | 2 | 16 | $47,384 | 5.2% | -5.2% | -$303 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 18 | 88.9% | 2.301 | +2.6 | 1.2 | 16 | 2 | $50,532 | 0.6% | +1.1% | $11 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 8 | 12.5% | 0.420 | -5.0 | 6.0 | 1 | 7 | $48,817 | 2.9% | -2.4% | -$415 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 8 | 12.5% | 0.420 | -5.0 | 6.0 | 1 | 7 | $48,817 | 2.9% | -2.4% | -$415 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 8 | 87.5% | 1.835 | +0.8 | 1.0 | 7 | 1 | $50,189 | 0.4% | +0.4% | $12 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 43 | 18.6% | 0.795 | -7.2 | 16.2 | 8 | 35 | $48,664 | 7.5% | -2.7% | -$190 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Demo | 36 | 33.3% | 1.109 | +2.4 | 5.6 | 7 | 21 | $50,275 | 1.2% | +0.5% | -$70 | $0 | 0 |
| OrbBreakout_XAUUSD_Paper | 29 | 17.2% | 0.592 | -10.0 | 10.0 | 5 | 24 | $47,576 | 4.8% | -4.8% | -$154 | $0 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 43 | 81.4% | 1.123 | +1.0 | 5.2 | 35 | 8 | $50,226 | 2.1% | +0.5% | $24 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 36 | 33.3% | 1.109 | +2.4 | 5.6 | 7 | 21 | $50,463 | 2.6% | +0.9% | -$150 | $0 | 0 |

## Xərc müqayisəsi (eyni ekspozisiyaya görə)

| Simvol | FundingPips (hazırkı): spread / illik faiz / komissiya | CFI (yeni hesab): spread / illik faiz / komissiya |
|---|---|---|
| DJI30 | 1.000 / -7.33% / yox | 2.000 / -6.47% / yox |
| GER40 | 2.000 / -5.41% / yox | 3.700 / -4.37% / yox |
| JP225 | 10.000 / -3.44% / yox | 7.100 / -5.48% / yox |
| NDX100 | 1.600 / -7.33% / yox | 0.800 / -6.47% / yox |
| SPX500 | 0.800 / -7.33% / yox | 0.480 / -6.56% / yox |
| XAUUSD | 0.160 / -5.59% / $5/lot | 0.040 / -4.86% / yox |

Spread = bu pəncərədəki barların medianı, qiymət vahidində (R-ə təsiri birbaşa buradan gəlir). İllik faiz = bir lotun gecəlik swap-ı, həmin lotun dəyərinin faizi kimi — iki broker swap-ı fərqli vahidlərdə (illik % vs gecəlik $) yazdığı və kontrakt ölçüləri fərqli olduğu üçün xam dərəcələr müqayisə oluna bilməz, bu isə olunur.

## Oxunuşu məhdudlaşdıran şeylər

- **Lot tavanı.** Aşağıdakı botlar trade-lərinin bir hissəsində brokerin `volume_max` həddinə dirənir, yəni nəzərdə tutulan 0.5% riski ala bilmir — orada dollar sütunu zərəri olduğundan kiçik göstərir, **R sütunu isə düzgündür**:
  - `OrbBreakout_JP225_Demo` @ fundingpips: trade-lərin 62%-i 10 lot tavanında, median risk $223 (hədəf ~$250)
  - `OrbBreakout_JP225_Paper` @ fundingpips: trade-lərin 62%-i 10 lot tavanında, median risk $223 (hədəf ~$250)
- **Swap bugünkü dərəcə ilə.** Hər iki brokerin swap-ı bu günkü dərəcədən bütün ilə tətbiq olunub; brokerlər tarixi dərəcələri yayımlamır.
- **Tick datası istifadə olunmayıb.** CFI-də tick tarixçəsi yoxdur, ona görə FundingPips-inki də söndürülüb — qopma (gap) stopları hər iki tərəfdə eyni proksi ilə qiymətləndirilib. Bu, müqayisəni brokerə görə təmizləyir, amma hər iki tərəfdə stop qiymətini bir az optimist saya bilər.
- **Nümunə kiçikdir.** Bu pəncərədə bot başına 7–45 bağlanmış trade var. Bu qədər trade-də PF-in təsadüfi sürüşməsi böyük olur — pəncərə nə qədər qısadırsa, nəticəni bir o qədər az ciddiyə almaq lazımdır.
- **Açıq qalanlar sayılmır.** Pəncərənin sonunda hələ açıq olan trade-lər statistikaya girmir (18 ədəd); 4R hədəflə mövqe günlərlə saxlanıldığı üçün qısa pəncərədə bu pay böyüyür.
- **Bu portfel nəticəsi deyil.** Hər bot ayrıca $50,000-da işlədilib; bir hesabda altısı birlikdə balansı və marja tavanını bölüşər, drawdown-lar isə üst-üstə düşər.

