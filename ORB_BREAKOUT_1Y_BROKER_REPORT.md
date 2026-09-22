# ORB Breakout — son 1 il, broker müqayisəsi

Hazırlandı: 2026-09-22 11:10 UTC · pəncərə: **2025-09-19 → 2026-09-18** (365 gün) · hər bot ayrıca $50,000 hesabda · skript: `scripts/orb_breakout_year_backtest.py`

Brokerlər: **FundingPips (hazırkı)** (`data\history\fundingpips`) · **CFI (yeni hesab)** (`data\history\cfi`)

## Əsas cədvəl

| Bot | Simvol | Hal | Parametr | FundingPips (hazırkı): n / PF / netR / nəticə | CFI (yeni hesab): n / PF / netR / nəticə | fərq |
|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo ⚠️ | DJI30 | Demo | 60m OR / M1 / 4R | 52 / 0.701 / -14.7R / -$3,359 | 52 / 0.720 / -13.5R / -$3,037 | +1.2R |
| OrbBreakout_DJI30_Paper | DJI30 | Paper | 60m OR / M1 / 4R | 52 / 0.701 / -14.7R / -$3,359 | 52 / 0.720 / -13.5R / -$3,037 | +1.2R |
| OrbBreakoutinv_DJI30_Paper | DJI30 | Paper (inverse) | 60m OR / M1 / 4R | 51 / 0.979 / -0.2R / -$21 | 52 / 1.120 / +1.2R / $394 | +1.4R |
| OrbBreakout_GER40_Demo ⚠️ | GER40 | Demo | 30m OR / M1 / 4R | 113 / 0.982 / -1.7R / -$728 | 116 / 0.944 / -5.7R / -$2,100 | -4.0R |
| OrbBreakout_GER40_Paper | GER40 | Paper | 30m OR / M1 / 4R | 113 / 0.982 / -1.7R / -$728 | 116 / 0.944 / -5.7R / -$2,100 | -4.0R |
| OrbBreakout_JP225_Demo ⚠️ | JP225 | Demo | 15m OR / M1 / 4R | 185 / 0.947 / -8.1R / -$1,418 | 183 / 0.960 / -6.1R / -$606 | +2.0R |
| OrbBreakout_JP225_Paper | JP225 | Paper | 15m OR / M1 / 4R | 185 / 0.947 / -8.1R / -$1,418 | 183 / 0.960 / -6.1R / -$606 | +2.0R |
| OrbBreakoutinv_JP225_Paper | JP225 | Paper (inverse) | 15m OR / M1 / 4R | 183 / 0.806 / -8.6R / -$1,873 | 181 / 0.827 / -7.5R / -$1,674 | +1.1R |
| OrbBreakout_NDX100_Demo ⚠️ | NDX100 | Demo | 30m OR / M5 / 4R | 65 / 0.829 / -10.2R / -$2,433 | 63 / 0.853 / -8.5R / -$2,081 | +1.7R |
| OrbBreakout_NDX100_Paper | NDX100 | Paper | 30m OR / M5 / 4R | 65 / 0.829 / -10.2R / -$2,433 | 63 / 0.853 / -8.5R / -$2,081 | +1.7R |
| OrbBreakoutinv_NDX100_Paper | NDX100 | Paper (inverse) | 30m OR / M5 / 4R | 63 / 0.949 / -0.7R / -$15 | 63 / 1.039 / +0.5R / $234 | +1.2R |
| OrbBreakout_SPX500_Demo ⚠️ | SPX500 | Demo | 60m OR / M1 / 4R | 64 / 0.693 / -18.5R / -$4,333 | 63 / 0.634 / -22.8R / -$5,076 | -4.3R |
| OrbBreakout_SPX500_Paper | SPX500 | Paper | 60m OR / M1 / 4R | 64 / 0.693 / -18.5R / -$4,333 | 63 / 0.634 / -22.8R / -$5,076 | -4.3R |
| OrbBreakoutinv_SPX500_Paper | SPX500 | Paper (inverse) | 60m OR / M1 / 4R | 61 / 0.994 / -0.1R / -$93 | 63 / 1.276 / +3.0R / $580 | +3.1R |
| OrbBreakout_XAUUSD_Demo ⚠️ | XAUUSD | Demo | 15m OR / M1 / 4R | 165 / 1.377 / +47.4R / $10,853 | 163 / 1.457 / +55.2R / $13,982 | +7.9R |
| OrbBreakoutwf_XAUUSD_Demo | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 118 / 1.463 / +31.1R / $3,612 | 119 / 1.473 / +31.5R / $3,510 | +0.4R |
| OrbBreakout_XAUUSD_Paper | XAUUSD | Paper | 60m OR / M1 / 3R | 99 / 1.292 / +20.6R / $5,307 | 99 / 1.255 / +18.1R / $4,546 | -2.6R |
| OrbBreakoutinv_XAUUSD_Paper | XAUUSD | Paper (inverse) | 15m OR / M1 / 4R | 162 / 0.708 / -12.6R / -$2,670 | 163 / 0.688 / -13.7R / -$2,788 | -1.2R |
| OrbBreakoutwf_XAUUSD_Paper | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 118 / 1.463 / +31.1R / $8,027 | 119 / 1.473 / +31.5R / $7,443 | +0.4R |

⚠️ = `deploy/demo_roster.txt`-də olmayan Demo konfiqurasiyası: .bat faylı var, amma hazırda real order vermir (o simvolu başqa strategiya tutur).

## Hər broker ayrıca

### FundingPips (hazırkı)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 52 | 19.2% | 0.701 | -14.7 | 15.2 | 10 | 42 | $46,641 | 6.9% | -6.7% | -$2,162 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 52 | 19.2% | 0.701 | -14.7 | 15.2 | 10 | 42 | $46,641 | 6.9% | -6.7% | -$2,162 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 51 | 80.4% | 0.979 | -0.2 | 3.4 | 41 | 10 | $49,979 | 1.5% | -0.0% | -$95 | $0 | 0 |
| OrbBreakout_GER40_Demo | 113 | 23.0% | 0.982 | -1.7 | 18.5 | 26 | 87 | $49,272 | 9.3% | -1.5% | -$2,072 | $0 | 0 |
| OrbBreakout_GER40_Paper | 113 | 23.0% | 0.982 | -1.7 | 18.5 | 26 | 87 | $49,272 | 9.3% | -1.5% | -$2,072 | $0 | 0 |
| OrbBreakout_JP225_Demo | 185 | 22.2% | 0.947 | -8.1 | 19.8 | 41 | 144 | $48,582 | 7.2% | -2.8% | -$506 | $0 | 0 |
| OrbBreakout_JP225_Paper | 185 | 22.2% | 0.947 | -8.1 | 19.8 | 41 | 144 | $48,582 | 7.2% | -2.8% | -$506 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 183 | 76.0% | 0.806 | -8.6 | 13.5 | 139 | 44 | $48,127 | 6.1% | -3.7% | -$163 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 65 | 20.0% | 0.829 | -10.2 | 17.4 | 13 | 52 | $47,567 | 7.8% | -4.9% | -$1,618 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 65 | 20.0% | 0.829 | -10.2 | 17.4 | 13 | 52 | $47,567 | 7.8% | -4.9% | -$1,618 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 63 | 79.4% | 0.949 | -0.7 | 5.5 | 50 | 13 | $49,985 | 2.3% | -0.0% | -$62 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 64 | 18.8% | 0.693 | -18.5 | 19.5 | 12 | 52 | $45,667 | 9.0% | -8.7% | -$2,053 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 64 | 18.8% | 0.693 | -18.5 | 19.5 | 12 | 52 | $45,667 | 9.0% | -8.7% | -$2,053 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 61 | 80.3% | 0.994 | -0.1 | 4.2 | 49 | 12 | $49,907 | 1.6% | -0.2% | -$88 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 165 | 26.1% | 1.377 | +47.4 | 19.9 | 43 | 122 | $60,853 | 11.4% | +21.7% | -$1,384 | -$146 | 1 |
| OrbBreakoutwf_XAUUSD_Demo | 118 | 39.8% | 1.463 | +31.1 | 13.9 | 27 | 62 | $53,612 | 2.9% | +7.2% | -$413 | -$25 | 0 |
| OrbBreakout_XAUUSD_Paper | 99 | 31.3% | 1.292 | +20.6 | 15.6 | 31 | 68 | $55,307 | 7.2% | +10.6% | -$1,011 | -$46 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 162 | 73.5% | 0.708 | -12.6 | 15.4 | 119 | 43 | $47,330 | 6.2% | -5.3% | $90 | -$25 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 118 | 39.8% | 1.463 | +31.1 | 13.9 | 27 | 62 | $58,027 | 6.5% | +16.1% | -$922 | -$57 | 0 |

### CFI (yeni hesab)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 52 | 19.2% | 0.720 | -13.5 | 15.0 | 10 | 42 | $46,963 | 6.6% | -6.1% | -$1,913 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 52 | 19.2% | 0.720 | -13.5 | 15.0 | 10 | 42 | $46,963 | 6.6% | -6.1% | -$1,913 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 52 | 80.8% | 1.120 | +1.2 | 2.9 | 42 | 10 | $50,394 | 1.0% | +0.8% | $54 | $0 | 0 |
| OrbBreakout_GER40_Demo | 116 | 21.6% | 0.944 | -5.7 | 17.2 | 25 | 91 | $47,900 | 8.3% | -4.2% | -$1,622 | $0 | 0 |
| OrbBreakout_GER40_Paper | 116 | 21.6% | 0.944 | -5.7 | 17.2 | 25 | 91 | $47,900 | 8.3% | -4.2% | -$1,622 | $0 | 0 |
| OrbBreakout_JP225_Demo | 183 | 22.4% | 0.960 | -6.1 | 23.1 | 41 | 142 | $49,394 | 11.3% | -1.2% | -$1,762 | $0 | 0 |
| OrbBreakout_JP225_Paper | 183 | 22.4% | 0.960 | -6.1 | 23.1 | 41 | 142 | $49,394 | 11.3% | -1.2% | -$1,762 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 181 | 76.2% | 0.827 | -7.5 | 12.9 | 138 | 43 | $48,326 | 5.9% | -3.3% | -$180 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 63 | 20.6% | 0.853 | -8.5 | 16.4 | 13 | 50 | $47,919 | 7.4% | -4.2% | -$1,589 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 63 | 20.6% | 0.853 | -8.5 | 16.4 | 13 | 50 | $47,919 | 7.4% | -4.2% | -$1,589 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 63 | 79.4% | 1.039 | +0.5 | 5.4 | 50 | 13 | $50,234 | 2.2% | +0.5% | $48 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 63 | 17.5% | 0.634 | -22.8 | 25.5 | 11 | 52 | $44,924 | 11.2% | -10.2% | -$2,066 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 63 | 17.5% | 0.634 | -22.8 | 25.5 | 11 | 52 | $44,924 | 11.2% | -10.2% | -$2,066 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 63 | 82.5% | 1.276 | +3.0 | 3.3 | 52 | 11 | $50,580 | 1.2% | +1.2% | $69 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 163 | 27.0% | 1.457 | +55.2 | 18.8 | 44 | 119 | $63,982 | 11.0% | +28.0% | -$1,215 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Demo | 119 | 39.5% | 1.473 | +31.5 | 12.5 | 27 | 62 | $53,510 | 2.6% | +7.0% | -$359 | $0 | 0 |
| OrbBreakout_XAUUSD_Paper | 99 | 30.3% | 1.255 | +18.1 | 15.1 | 30 | 69 | $54,546 | 7.1% | +9.1% | -$863 | $0 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 163 | 73.0% | 0.688 | -13.7 | 16.8 | 119 | 44 | $47,212 | 6.6% | -5.6% | $115 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 119 | 39.5% | 1.473 | +31.5 | 12.5 | 27 | 62 | $57,443 | 6.0% | +14.9% | -$799 | $0 | 0 |

## Xərc müqayisəsi (eyni ekspozisiyaya görə)

| Simvol | FundingPips (hazırkı): spread / illik faiz / komissiya | CFI (yeni hesab): spread / illik faiz / komissiya |
|---|---|---|
| DJI30 | 1.000 / -7.33% / yox | 2.000 / -6.47% / yox |
| GER40 | 2.000 / -5.41% / yox | 3.700 / -4.37% / yox |
| JP225 | 10.000 / -3.44% / yox | 7.100 / -5.48% / yox |
| NDX100 | 1.600 / -7.33% / yox | 0.800 / -6.47% / yox |
| SPX500 | 0.800 / -7.33% / yox | 0.480 / -6.56% / yox |
| XAUUSD | 0.150 / -5.59% / $5/lot | 0.060 / -4.86% / yox |

Spread = bu pəncərədəki barların medianı, qiymət vahidində (R-ə təsiri birbaşa buradan gəlir). İllik faiz = bir lotun gecəlik swap-ı, həmin lotun dəyərinin faizi kimi — iki broker swap-ı fərqli vahidlərdə (illik % vs gecəlik $) yazdığı və kontrakt ölçüləri fərqli olduğu üçün xam dərəcələr müqayisə oluna bilməz, bu isə olunur.

## Oxunuşu məhdudlaşdıran şeylər

- **Lot tavanı.** Aşağıdakı botlar trade-lərinin bir hissəsində brokerin `volume_max` həddinə dirənir, yəni nəzərdə tutulan 0.5% riski ala bilmir — orada dollar sütunu zərəri olduğundan kiçik göstərir, **R sütunu isə düzgündür**:
  - `OrbBreakout_JP225_Demo` @ fundingpips: trade-lərin 81%-i 10 lot tavanında, median risk $157 (hədəf ~$250)
  - `OrbBreakout_JP225_Paper` @ fundingpips: trade-lərin 81%-i 10 lot tavanında, median risk $157 (hədəf ~$250)
  - `OrbBreakoutinv_JP225_Paper` @ fundingpips: trade-lərin 5%-i 10 lot tavanında, median risk $241 (hədəf ~$250)
- **Swap bugünkü dərəcə ilə.** Hər iki brokerin swap-ı bu günkü dərəcədən bütün ilə tətbiq olunub; brokerlər tarixi dərəcələri yayımlamır.
- **Tick datası istifadə olunmayıb.** CFI-də tick tarixçəsi yoxdur, ona görə FundingPips-inki də söndürülüb — qopma (gap) stopları hər iki tərəfdə eyni proksi ilə qiymətləndirilib. Bu, müqayisəni brokerə görə təmizləyir, amma hər iki tərəfdə stop qiymətini bir az optimist saya bilər.
- **Nümunə kiçikdir.** Bu pəncərədə bot başına 51–185 bağlanmış trade var. Bu qədər trade-də PF-in təsadüfi sürüşməsi böyük olur — pəncərə nə qədər qısadırsa, nəticəni bir o qədər az ciddiyə almaq lazımdır.
- **Açıq qalanlar sayılmır.** Pəncərənin sonunda hələ açıq olan trade-lər statistikaya girmir (18 ədəd); 4R hədəflə mövqe günlərlə saxlanıldığı üçün qısa pəncərədə bu pay böyüyür.
- **Bu portfel nəticəsi deyil.** Hər bot ayrıca $50,000-da işlədilib; bir hesabda altısı birlikdə balansı və marja tavanını bölüşər, drawdown-lar isə üst-üstə düşər.

