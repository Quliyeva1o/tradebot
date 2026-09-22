# ORB Breakout — son 6 ay, broker müqayisəsi

Hazırlandı: 2026-09-22 11:12 UTC · pəncərə: **2026-03-23 → 2026-09-18** (180 gün) · hər bot ayrıca $50,000 hesabda · skript: `scripts/orb_breakout_year_backtest.py`

Brokerlər: **FundingPips (hazırkı)** (`data\history\fundingpips`) · **CFI (yeni hesab)** (`data\history\cfi`)

## Əsas cədvəl

| Bot | Simvol | Hal | Parametr | FundingPips (hazırkı): n / PF / netR / nəticə | CFI (yeni hesab): n / PF / netR / nəticə | fərq |
|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo ⚠️ | DJI30 | Demo | 60m OR / M1 / 4R | 23 / 0.749 / -5.6R / -$1,253 | 24 / 0.752 / -5.6R / -$1,185 | -0.0R |
| OrbBreakout_DJI30_Paper | DJI30 | Paper | 60m OR / M1 / 4R | 23 / 0.749 / -5.6R / -$1,253 | 24 / 0.752 / -5.6R / -$1,185 | -0.0R |
| OrbBreakoutinv_DJI30_Paper | DJI30 | Paper (inverse) | 60m OR / M1 / 4R | 23 / 0.870 / -0.7R / -$88 | 24 / 1.057 / +0.3R / $147 | +0.9R |
| OrbBreakout_GER40_Demo ⚠️ | GER40 | Demo | 30m OR / M1 / 4R | 53 / 1.035 / +1.6R / -$492 | 55 / 1.044 / +2.0R / -$213 | +0.4R |
| OrbBreakout_GER40_Paper | GER40 | Paper | 30m OR / M1 / 4R | 53 / 1.035 / +1.6R / -$492 | 55 / 1.044 / +2.0R / -$213 | +0.4R |
| OrbBreakout_JP225_Demo ⚠️ | JP225 | Demo | 15m OR / M1 / 4R | 101 / 0.987 / -1.1R / -$129 | 100 / 0.939 / -5.1R / -$1,116 | -4.1R |
| OrbBreakout_JP225_Paper | JP225 | Paper | 15m OR / M1 / 4R | 101 / 0.987 / -1.1R / -$129 | 100 / 0.939 / -5.1R / -$1,116 | -4.1R |
| OrbBreakoutinv_JP225_Paper | JP225 | Paper (inverse) | 15m OR / M1 / 4R | 100 / 0.863 / -3.0R / -$633 | 98 / 0.932 / -1.4R / -$195 | +1.6R |
| OrbBreakout_NDX100_Demo ⚠️ | NDX100 | Demo | 30m OR / M5 / 4R | 36 / 1.089 / +2.8R / $720 | 36 / 1.105 / +3.3R / $733 | +0.5R |
| OrbBreakout_NDX100_Paper | NDX100 | Paper | 30m OR / M5 / 4R | 36 / 1.089 / +2.8R / $720 | 36 / 1.105 / +3.3R / $733 | +0.5R |
| OrbBreakoutinv_NDX100_Paper | NDX100 | Paper (inverse) | 30m OR / M5 / 4R | 36 / 0.735 / -2.4R / -$302 | 36 / 0.821 / -1.6R / -$136 | +0.8R |
| OrbBreakout_SPX500_Demo ⚠️ | SPX500 | Demo | 60m OR / M1 / 4R | 33 / 0.964 / -1.1R / -$171 | 32 / 1.023 / +0.6R / $274 | +1.7R |
| OrbBreakout_SPX500_Paper | SPX500 | Paper | 60m OR / M1 / 4R | 33 / 0.964 / -1.1R / -$171 | 32 / 1.023 / +0.6R / $274 | +1.7R |
| OrbBreakoutinv_SPX500_Paper | SPX500 | Paper (inverse) | 60m OR / M1 / 4R | 31 / 0.692 / -2.5R / -$327 | 32 / 0.807 / -1.5R / -$159 | +1.0R |
| OrbBreakout_XAUUSD_Demo ⚠️ | XAUUSD | Demo | 15m OR / M1 / 4R | 89 / 1.010 / +0.7R / -$1,141 | 89 / 1.050 / +3.6R / -$549 | +2.9R |
| OrbBreakoutwf_XAUUSD_Demo | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 62 / 1.100 / +3.9R / $308 | 63 / 1.102 / +3.9R / $367 | +0.0R |
| OrbBreakout_XAUUSD_Paper | XAUUSD | Paper | 60m OR / M1 / 3R | 49 / 0.822 / -7.0R / -$1,571 | 50 / 0.732 / -11.0R / -$2,502 | -4.0R |
| OrbBreakoutinv_XAUUSD_Paper | XAUUSD | Paper (inverse) | 15m OR / M1 / 4R | 88 / 0.988 / -0.2R / -$196 | 89 / 1.000 / -0.0R / -$171 | +0.2R |
| OrbBreakoutwf_XAUUSD_Paper | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 62 / 1.100 / +3.9R / $726 | 63 / 1.102 / +3.9R / $715 | +0.0R |

⚠️ = `deploy/demo_roster.txt`-də olmayan Demo konfiqurasiyası: .bat faylı var, amma hazırda real order vermir (o simvolu başqa strategiya tutur).

## Hər broker ayrıca

### FundingPips (hazırkı)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 23 | 21.7% | 0.749 | -5.6 | 9.1 | 5 | 18 | $48,747 | 4.1% | -2.5% | -$1,123 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 23 | 21.7% | 0.749 | -5.6 | 9.1 | 5 | 18 | $48,747 | 4.1% | -2.5% | -$1,123 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 23 | 78.3% | 0.870 | -0.7 | 2.6 | 18 | 5 | $49,912 | 1.0% | -0.2% | -$47 | $0 | 0 |
| OrbBreakout_GER40_Demo | 53 | 22.6% | 1.035 | +1.6 | 8.5 | 12 | 41 | $49,508 | 4.4% | -1.0% | -$948 | $0 | 0 |
| OrbBreakout_GER40_Paper | 53 | 22.6% | 1.035 | +1.6 | 8.5 | 12 | 41 | $49,508 | 4.4% | -1.0% | -$948 | $0 | 0 |
| OrbBreakout_JP225_Demo | 101 | 21.8% | 0.987 | -1.1 | 19.8 | 22 | 79 | $49,871 | 6.1% | -0.3% | -$230 | $0 | 0 |
| OrbBreakout_JP225_Paper | 101 | 21.8% | 0.987 | -1.1 | 19.8 | 22 | 79 | $49,871 | 6.1% | -0.3% | -$230 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 100 | 78.0% | 0.863 | -3.0 | 7.5 | 78 | 22 | $49,367 | 3.5% | -1.3% | -$67 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 36 | 25.0% | 1.089 | +2.8 | 15.4 | 9 | 27 | $50,720 | 6.7% | +1.4% | -$817 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 36 | 25.0% | 1.089 | +2.8 | 15.4 | 9 | 27 | $50,720 | 6.7% | +1.4% | -$817 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 36 | 75.0% | 0.735 | -2.4 | 5.5 | 27 | 9 | $49,698 | 2.0% | -0.6% | -$32 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 33 | 24.2% | 0.964 | -1.1 | 7.8 | 8 | 25 | $49,829 | 3.9% | -0.3% | -$1,108 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 33 | 24.2% | 0.964 | -1.1 | 7.8 | 8 | 25 | $49,829 | 3.9% | -0.3% | -$1,108 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 31 | 74.2% | 0.692 | -2.5 | 4.2 | 23 | 8 | $49,673 | 1.5% | -0.7% | -$42 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 89 | 20.2% | 1.010 | +0.7 | 19.9 | 18 | 71 | $48,859 | 11.3% | -2.3% | -$511 | -$66 | 1 |
| OrbBreakoutwf_XAUUSD_Demo | 62 | 33.9% | 1.100 | +3.9 | 13.9 | 12 | 36 | $50,308 | 2.8% | +0.6% | -$151 | -$12 | 0 |
| OrbBreakout_XAUUSD_Paper | 49 | 22.4% | 0.822 | -7.0 | 15.6 | 11 | 38 | $48,429 | 6.9% | -3.1% | -$356 | -$19 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 88 | 79.5% | 0.988 | -0.2 | 5.2 | 70 | 18 | $49,804 | 2.2% | -0.4% | $45 | -$15 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 62 | 33.9% | 1.100 | +3.9 | 13.9 | 12 | 36 | $50,726 | 6.1% | +1.5% | -$326 | -$27 | 0 |

### CFI (yeni hesab)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 24 | 20.8% | 0.752 | -5.6 | 9.2 | 5 | 19 | $48,815 | 4.1% | -2.4% | -$918 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 24 | 20.8% | 0.752 | -5.6 | 9.2 | 5 | 19 | $48,815 | 4.1% | -2.4% | -$918 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 24 | 79.2% | 1.057 | +0.3 | 1.7 | 19 | 5 | $50,147 | 0.5% | +0.3% | $26 | $0 | 0 |
| OrbBreakout_GER40_Demo | 55 | 21.8% | 1.044 | +2.0 | 9.9 | 12 | 43 | $49,787 | 5.0% | -0.4% | -$768 | $0 | 0 |
| OrbBreakout_GER40_Paper | 55 | 21.8% | 1.044 | +2.0 | 9.9 | 12 | 43 | $49,787 | 5.0% | -0.4% | -$768 | $0 | 0 |
| OrbBreakout_JP225_Demo | 100 | 21.0% | 0.939 | -5.1 | 23.1 | 21 | 79 | $48,884 | 11.3% | -2.2% | -$611 | $0 | 0 |
| OrbBreakout_JP225_Paper | 100 | 21.0% | 0.939 | -5.1 | 23.1 | 21 | 79 | $48,884 | 11.3% | -2.2% | -$611 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 98 | 78.6% | 0.932 | -1.4 | 6.7 | 77 | 21 | $49,805 | 3.0% | -0.4% | -$64 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 36 | 25.0% | 1.105 | +3.3 | 15.1 | 9 | 27 | $50,733 | 6.7% | +1.5% | -$760 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 36 | 25.0% | 1.105 | +3.3 | 15.1 | 9 | 27 | $50,733 | 6.7% | +1.5% | -$760 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 36 | 75.0% | 0.821 | -1.6 | 5.4 | 27 | 9 | $49,864 | 2.0% | -0.3% | $24 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 32 | 25.0% | 1.023 | +0.6 | 6.7 | 8 | 24 | $50,274 | 3.3% | +0.5% | -$1,076 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 32 | 25.0% | 1.023 | +0.6 | 6.7 | 8 | 24 | $50,274 | 3.3% | +0.5% | -$1,076 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 32 | 75.0% | 0.807 | -1.5 | 3.3 | 24 | 8 | $49,841 | 1.1% | -0.3% | $31 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 89 | 20.2% | 1.050 | +3.6 | 18.8 | 18 | 71 | $49,451 | 10.9% | -1.1% | -$441 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Demo | 63 | 33.3% | 1.102 | +3.9 | 12.5 | 12 | 36 | $50,367 | 2.5% | +0.7% | -$131 | $0 | 0 |
| OrbBreakout_XAUUSD_Paper | 50 | 20.0% | 0.732 | -11.0 | 14.9 | 10 | 40 | $47,498 | 6.9% | -5.0% | -$313 | $0 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 89 | 79.8% | 1.000 | -0.0 | 5.2 | 71 | 18 | $49,829 | 2.2% | -0.3% | $55 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 63 | 33.3% | 1.102 | +3.9 | 12.5 | 12 | 36 | $50,715 | 5.6% | +1.4% | -$283 | $0 | 0 |

## Xərc müqayisəsi (eyni ekspozisiyaya görə)

| Simvol | FundingPips (hazırkı): spread / illik faiz / komissiya | CFI (yeni hesab): spread / illik faiz / komissiya |
|---|---|---|
| DJI30 | 1.000 / -7.33% / yox | 2.000 / -6.47% / yox |
| GER40 | 2.000 / -5.41% / yox | 3.700 / -4.37% / yox |
| JP225 | 10.000 / -3.44% / yox | 7.100 / -5.48% / yox |
| NDX100 | 1.600 / -7.33% / yox | 0.800 / -6.47% / yox |
| SPX500 | 0.800 / -7.33% / yox | 0.480 / -6.56% / yox |
| XAUUSD | 0.150 / -5.59% / $5/lot | 0.040 / -4.86% / yox |

Spread = bu pəncərədəki barların medianı, qiymət vahidində (R-ə təsiri birbaşa buradan gəlir). İllik faiz = bir lotun gecəlik swap-ı, həmin lotun dəyərinin faizi kimi — iki broker swap-ı fərqli vahidlərdə (illik % vs gecəlik $) yazdığı və kontrakt ölçüləri fərqli olduğu üçün xam dərəcələr müqayisə oluna bilməz, bu isə olunur.

## Oxunuşu məhdudlaşdıran şeylər

- **Lot tavanı.** Aşağıdakı botlar trade-lərinin bir hissəsində brokerin `volume_max` həddinə dirənir, yəni nəzərdə tutulan 0.5% riski ala bilmir — orada dollar sütunu zərəri olduğundan kiçik göstərir, **R sütunu isə düzgündür**:
  - `OrbBreakout_JP225_Demo` @ fundingpips: trade-lərin 78%-i 10 lot tavanında, median risk $167 (hədəf ~$250)
  - `OrbBreakout_JP225_Paper` @ fundingpips: trade-lərin 78%-i 10 lot tavanında, median risk $167 (hədəf ~$250)
- **Swap bugünkü dərəcə ilə.** Hər iki brokerin swap-ı bu günkü dərəcədən bütün ilə tətbiq olunub; brokerlər tarixi dərəcələri yayımlamır.
- **Tick datası istifadə olunmayıb.** CFI-də tick tarixçəsi yoxdur, ona görə FundingPips-inki də söndürülüb — qopma (gap) stopları hər iki tərəfdə eyni proksi ilə qiymətləndirilib. Bu, müqayisəni brokerə görə təmizləyir, amma hər iki tərəfdə stop qiymətini bir az optimist saya bilər.
- **Nümunə kiçikdir.** Bu pəncərədə bot başına 23–101 bağlanmış trade var. Bu qədər trade-də PF-in təsadüfi sürüşməsi böyük olur — pəncərə nə qədər qısadırsa, nəticəni bir o qədər az ciddiyə almaq lazımdır.
- **Açıq qalanlar sayılmır.** Pəncərənin sonunda hələ açıq olan trade-lər statistikaya girmir (18 ədəd); 4R hədəflə mövqe günlərlə saxlanıldığı üçün qısa pəncərədə bu pay böyüyür.
- **Bu portfel nəticəsi deyil.** Hər bot ayrıca $50,000-da işlədilib; bir hesabda altısı birlikdə balansı və marja tavanını bölüşər, drawdown-lar isə üst-üstə düşər.

