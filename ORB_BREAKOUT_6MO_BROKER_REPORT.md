# ORB Breakout — son 6 ay, broker müqayisəsi

Hazırlandı: 2026-09-20 19:56 UTC · pəncərə: **2026-03-23 → 2026-09-18** (180 gün) · hər bot ayrıca $50,000 hesabda · skript: `scripts/orb_breakout_year_backtest.py`

Brokerlər: **FundingPips (hazırkı)** (`data\history\fundingpips`) · **CFI (yeni hesab)** (`data\history\cfi`)

## Əsas cədvəl

| Bot | Simvol | Hal | Parametr | FundingPips (hazırkı): n / PF / netR / nəticə | CFI (yeni hesab): n / PF / netR / nəticə | fərq |
|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | DJI30 | Demo (rev 0.5R) | 60m OR / M1 / 4R | 45 / 0.892 / -3.0R / -$565 | 45 / 0.917 / -2.3R / -$354 | +0.7R |
| OrbBreakout_DJI30_Paper | DJI30 | Paper | 60m OR / M1 / 4R | 23 / 0.748 / -5.6R / -$1,272 | 24 / 0.752 / -5.6R / -$1,170 | -0.0R |
| OrbBreakoutinv_DJI30_Paper | DJI30 | Paper (inverse) | 60m OR / M1 / 4R | 23 / 0.874 / -0.6R / -$89 | 24 / 1.061 / +0.3R / $130 | +0.9R |
| OrbBreakout_GER40_Demo ⚠️ | GER40 | Demo | 30m OR / M1 / 4R | 52 / 0.945 / -2.5R / -$1,476 | 54 / 0.954 / -2.1R / -$1,243 | +0.4R |
| OrbBreakout_GER40_Paper | GER40 | Paper | 30m OR / M1 / 4R | 52 / 0.945 / -2.5R / -$1,476 | 54 / 0.954 / -2.1R / -$1,243 | +0.4R |
| OrbBreakout_JP225_Demo | JP225 | Demo (rev 0.5R) | 15m OR / M1 / 4R | 176 / 0.896 / -11.8R / -$779 | 175 / 0.921 / -9.0R / -$1,949 | +2.9R |
| OrbBreakout_JP225_Paper | JP225 | Paper | 15m OR / M1 / 4R | 101 / 0.970 / -2.5R / $119 | 100 / 0.922 / -6.5R / -$1,314 | -4.0R |
| OrbBreakoutinv_JP225_Paper | JP225 | Paper (inverse) | 15m OR / M1 / 4R | 100 / 0.867 / -2.9R / -$598 | 98 / 0.936 / -1.3R / -$187 | +1.6R |
| OrbBreakout_NDX100_Demo | NDX100 | Demo (rev 0.5R) | 30m OR / M5 / 4R | 67 / 1.008 / +0.3R / -$132 | 67 / 1.020 / +0.9R / -$27 | +0.5R |
| OrbBreakout_NDX100_Paper | NDX100 | Paper | 30m OR / M5 / 4R | 38 / 1.026 / +0.9R / $88 | 38 / 1.041 / +1.4R / $380 | +0.5R |
| OrbBreakoutinv_NDX100_Paper | NDX100 | Paper (inverse) | 30m OR / M5 / 4R | 38 / 0.800 / -1.8R / -$166 | 38 / 0.882 / -1.1R / -$7 | +0.8R |
| OrbBreakout_SPX500_Demo | SPX500 | Demo (rev 0.5R) | 60m OR / M1 / 4R | 56 / 0.919 / -3.1R / -$711 | 54 / 1.127 / +4.2R / $1,094 | +7.4R |
| OrbBreakout_SPX500_Paper | SPX500 | Paper | 60m OR / M1 / 4R | 34 / 0.932 / -2.0R / -$525 | 33 / 0.988 / -0.3R / -$17 | +1.7R |
| OrbBreakoutinv_SPX500_Paper | SPX500 | Paper (inverse) | 60m OR / M1 / 4R | 32 / 0.728 / -2.2R / -$387 | 33 / 0.850 / -1.2R / -$60 | +1.0R |
| OrbBreakout_XAUUSD_Demo | XAUUSD | Demo (rev 0.5R) | 15m OR / M1 / 4R | 161 / 0.933 / -6.8R / -$2,869 | 161 / 0.976 / -2.4R / -$1,805 | +4.5R |
| OrbBreakout_XAUUSD_Paper | XAUUSD | Paper | 60m OR / M1 / 3R | 53 / 0.852 / -6.3R / -$1,616 | 53 / 0.866 / -5.6R / -$1,491 | +0.6R |
| OrbBreakoutinv_XAUUSD_Paper | XAUUSD | Paper (inverse) | 15m OR / M1 / 4R | 88 / 1.063 / +1.1R / $176 | 89 / 1.076 / +1.3R / $239 | +0.2R |
| OrbBreakoutwf_XAUUSD_Paper | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 66 / 1.098 / +4.1R / $692 | 66 / 1.147 / +6.0R / $930 | +1.8R |

⚠️ = `deploy/demo_roster.txt`-də olmayan Demo konfiqurasiyası: .bat faylı var, amma hazırda real order vermir (o simvolu başqa strategiya tutur).

## Hər broker ayrıca

### FundingPips (hazırkı)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 45 | 46.7% | 0.892 | -3.0 | 9.3 | 21 | 24 | $49,435 | 4.3% | -1.1% | -$995 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 23 | 21.7% | 0.748 | -5.6 | 9.1 | 5 | 18 | $48,728 | 4.1% | -2.5% | -$1,128 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 23 | 78.3% | 0.874 | -0.6 | 2.6 | 18 | 5 | $49,911 | 1.0% | -0.2% | -$47 | $0 | 0 |
| OrbBreakout_GER40_Demo | 52 | 21.2% | 0.945 | -2.5 | 8.5 | 11 | 41 | $48,524 | 4.6% | -3.0% | -$936 | $0 | 0 |
| OrbBreakout_GER40_Paper | 52 | 21.2% | 0.945 | -2.5 | 8.5 | 11 | 41 | $48,524 | 4.6% | -3.0% | -$936 | $0 | 0 |
| OrbBreakout_JP225_Demo | 176 | 37.5% | 0.896 | -11.8 | 19.4 | 66 | 110 | $49,221 | 6.2% | -1.6% | -$275 | $0 | 0 |
| OrbBreakout_JP225_Paper | 101 | 21.8% | 0.970 | -2.5 | 19.8 | 22 | 79 | $50,119 | 6.1% | +0.2% | -$230 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 100 | 78.0% | 0.867 | -2.9 | 7.5 | 78 | 22 | $49,402 | 3.5% | -1.2% | -$66 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 67 | 41.8% | 1.008 | +0.3 | 14.9 | 28 | 39 | $49,868 | 6.8% | -0.3% | -$812 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 38 | 23.7% | 1.026 | +0.9 | 15.4 | 9 | 29 | $50,088 | 6.8% | +0.2% | -$804 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 38 | 76.3% | 0.800 | -1.8 | 5.5 | 29 | 9 | $49,834 | 2.0% | -0.3% | -$32 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 56 | 41.1% | 0.919 | -3.1 | 9.9 | 23 | 33 | $49,289 | 5.0% | -1.4% | -$1,121 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 34 | 23.5% | 0.932 | -2.0 | 7.8 | 8 | 26 | $49,475 | 3.9% | -1.1% | -$1,101 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 32 | 75.0% | 0.728 | -2.2 | 4.2 | 24 | 8 | $49,613 | 1.7% | -0.8% | -$47 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 161 | 38.5% | 0.933 | -6.8 | 26.6 | 62 | 99 | $47,131 | 12.3% | -5.7% | -$479 | -$116 | 1 |
| OrbBreakout_XAUUSD_Paper | 53 | 22.6% | 0.852 | -6.3 | 15.6 | 12 | 41 | $48,384 | 6.9% | -3.2% | -$340 | -$20 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 88 | 80.7% | 1.063 | +1.1 | 5.2 | 71 | 17 | $50,176 | 2.2% | +0.4% | $45 | -$15 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 66 | 33.3% | 1.098 | +4.1 | 13.9 | 13 | 39 | $50,692 | 6.1% | +1.4% | -$317 | -$28 | 0 |

### CFI (yeni hesab)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 45 | 46.7% | 0.917 | -2.3 | 9.2 | 21 | 24 | $49,646 | 4.0% | -0.7% | -$834 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 24 | 20.8% | 0.752 | -5.6 | 9.2 | 5 | 19 | $48,830 | 4.1% | -2.3% | -$921 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 24 | 79.2% | 1.061 | +0.3 | 1.7 | 19 | 5 | $50,130 | 0.5% | +0.3% | $26 | $0 | 0 |
| OrbBreakout_GER40_Demo | 54 | 20.4% | 0.954 | -2.1 | 9.9 | 11 | 43 | $48,757 | 5.0% | -2.5% | -$742 | $0 | 0 |
| OrbBreakout_GER40_Paper | 54 | 20.4% | 0.954 | -2.1 | 9.9 | 11 | 43 | $48,757 | 5.0% | -2.5% | -$742 | $0 | 0 |
| OrbBreakout_JP225_Demo | 175 | 38.3% | 0.921 | -9.0 | 24.2 | 67 | 108 | $48,051 | 11.6% | -3.9% | -$641 | $0 | 0 |
| OrbBreakout_JP225_Paper | 100 | 21.0% | 0.922 | -6.5 | 23.1 | 21 | 79 | $48,686 | 11.3% | -2.6% | -$606 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 98 | 78.6% | 0.936 | -1.3 | 6.7 | 77 | 21 | $49,813 | 3.0% | -0.4% | -$64 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 67 | 41.8% | 1.020 | +0.9 | 14.5 | 28 | 39 | $49,973 | 6.7% | -0.1% | -$730 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 38 | 23.7% | 1.041 | +1.4 | 15.1 | 9 | 29 | $50,380 | 6.5% | +0.8% | -$741 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 38 | 76.3% | 0.882 | -1.1 | 5.4 | 29 | 9 | $49,993 | 2.0% | -0.0% | $23 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 54 | 46.3% | 1.127 | +4.2 | 6.7 | 25 | 29 | $51,094 | 3.2% | +2.2% | -$1,066 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 33 | 24.2% | 0.988 | -0.3 | 6.7 | 8 | 25 | $49,983 | 3.3% | -0.0% | -$1,071 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 33 | 75.8% | 0.850 | -1.2 | 3.3 | 25 | 8 | $49,940 | 1.1% | -0.1% | $31 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 161 | 39.8% | 0.976 | -2.4 | 23.1 | 64 | 97 | $48,195 | 10.8% | -3.6% | -$406 | $0 | 1 |
| OrbBreakout_XAUUSD_Paper | 53 | 22.6% | 0.866 | -5.6 | 14.9 | 12 | 41 | $48,509 | 6.6% | -3.0% | -$299 | $0 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 89 | 80.9% | 1.076 | +1.3 | 5.2 | 72 | 17 | $50,239 | 2.1% | +0.5% | $56 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 66 | 33.3% | 1.147 | +6.0 | 12.5 | 13 | 38 | $50,930 | 5.6% | +1.9% | -$279 | $0 | 0 |

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
  - `OrbBreakout_JP225_Paper` @ fundingpips: trade-lərin 78%-i 10 lot tavanında, median risk $167 (hədəf ~$250)
  - `OrbBreakout_JP225_Demo` @ fundingpips: trade-lərin 78%-i 10 lot tavanında, median risk $170 (hədəf ~$250)
- **Swap bugünkü dərəcə ilə.** Hər iki brokerin swap-ı bu günkü dərəcədən bütün ilə tətbiq olunub; brokerlər tarixi dərəcələri yayımlamır.
- **Tick datası istifadə olunmayıb.** CFI-də tick tarixçəsi yoxdur, ona görə FundingPips-inki də söndürülüb — qopma (gap) stopları hər iki tərəfdə eyni proksi ilə qiymətləndirilib. Bu, müqayisəni brokerə görə təmizləyir, amma hər iki tərəfdə stop qiymətini bir az optimist saya bilər.
- **Nümunə kiçikdir.** Bu pəncərədə bot başına 23–176 bağlanmış trade var. Bu qədər trade-də PF-in təsadüfi sürüşməsi böyük olur — pəncərə nə qədər qısadırsa, nəticəni bir o qədər az ciddiyə almaq lazımdır.
- **Açıq qalanlar sayılmır.** Pəncərənin sonunda hələ açıq olan trade-lər statistikaya girmir (18 ədəd); 4R hədəflə mövqe günlərlə saxlanıldığı üçün qısa pəncərədə bu pay böyüyür.
- **Bu portfel nəticəsi deyil.** Hər bot ayrıca $50,000-da işlədilib; bir hesabda altısı birlikdə balansı və marja tavanını bölüşər, drawdown-lar isə üst-üstə düşər.

