# ORB Breakout — son 1 il, broker müqayisəsi

Hazırlandı: 2026-09-20 19:39 UTC · pəncərə: **2025-09-19 → 2026-09-18** (365 gün) · hər bot ayrıca $50,000 hesabda · skript: `scripts/orb_breakout_year_backtest.py`

Brokerlər: **FundingPips (hazırkı)** (`data\history\fundingpips`) · **CFI (yeni hesab)** (`data\history\cfi`)

## Əsas cədvəl

| Bot | Simvol | Hal | Parametr | FundingPips (hazırkı): n / PF / netR / nəticə | CFI (yeni hesab): n / PF / netR / nəticə | fərq |
|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | DJI30 | Demo (rev 0.5R) | 60m OR / M1 / 4R | 113 / 0.756 / -18.1R / -$4,020 | 111 / 0.789 / -15.1R / -$2,995 | +2.9R |
| OrbBreakout_DJI30_Paper | DJI30 | Paper | 60m OR / M1 / 4R | 61 / 0.680 / -18.2R / -$4,090 | 61 / 0.698 / -17.0R / -$3,683 | +1.2R |
| OrbBreakoutinv_DJI30_Paper | DJI30 | Paper (inverse) | 60m OR / M1 / 4R | 60 / 1.059 / +0.7R / $189 | 61 / 1.167 / +1.8R / $532 | +1.2R |
| OrbBreakout_GER40_Demo ⚠️ | GER40 | Demo | 30m OR / M1 / 4R | 118 / 0.932 / -7.0R / -$1,761 | 121 / 0.896 / -11.0R / -$3,026 | -4.0R |
| OrbBreakout_GER40_Paper | GER40 | Paper | 30m OR / M1 / 4R | 118 / 0.932 / -7.0R / -$1,761 | 121 / 0.896 / -11.0R / -$3,026 | -4.0R |
| OrbBreakout_JP225_Demo | JP225 | Demo (rev 0.5R) | 15m OR / M1 / 4R | 328 / 0.817 / -40.7R / -$5,227 | 324 / 0.843 / -35.0R / -$7,756 | +5.7R |
| OrbBreakout_JP225_Paper | JP225 | Paper | 15m OR / M1 / 4R | 186 / 0.910 / -13.9R / -$1,793 | 184 / 0.922 / -12.0R / -$2,260 | +1.9R |
| OrbBreakoutinv_JP225_Paper | JP225 | Paper (inverse) | 15m OR / M1 / 4R | 184 / 0.814 / -8.3R / -$1,735 | 182 / 0.839 / -7.0R / -$1,503 | +1.3R |
| OrbBreakout_NDX100_Demo | NDX100 | Demo (rev 0.5R) | 30m OR / M5 / 4R | 127 / 0.825 / -15.4R / -$3,614 | 123 / 0.877 / -10.3R / -$2,340 | +5.1R |
| OrbBreakout_NDX100_Paper | NDX100 | Paper | 30m OR / M5 / 4R | 70 / 0.901 / -6.2R / -$1,590 | 68 / 0.931 / -4.2R / -$1,067 | +2.0R |
| OrbBreakoutinv_NDX100_Paper | NDX100 | Paper (inverse) | 30m OR / M5 / 4R | 68 / 0.879 / -1.8R / -$93 | 68 / 0.952 / -0.7R / $11 | +1.1R |
| OrbBreakout_SPX500_Demo | SPX500 | Demo (rev 0.5R) | 60m OR / M1 / 4R | 128 / 0.593 / -39.0R / -$8,695 | 127 / 0.609 / -36.1R / -$7,873 | +2.9R |
| OrbBreakout_SPX500_Paper | SPX500 | Paper | 60m OR / M1 / 4R | 70 / 0.631 / -24.4R / -$5,646 | 69 / 0.579 / -28.7R / -$6,194 | -4.3R |
| OrbBreakoutinv_SPX500_Paper | SPX500 | Paper (inverse) | 60m OR / M1 / 4R | 67 / 1.115 / +1.4R / $282 | 69 / 1.414 / +4.5R / $979 | +3.1R |
| OrbBreakout_XAUUSD_Demo | XAUUSD | Demo (rev 0.5R) | 15m OR / M1 / 4R | 287 / 1.225 / +39.1R / $8,474 | 284 / 1.234 / +39.2R / $8,642 | +0.1R |
| OrbBreakout_XAUUSD_Paper | XAUUSD | Paper | 60m OR / M1 / 3R | 106 / 1.301 / +22.7R / $5,795 | 105 / 1.335 / +24.8R / $6,211 | +2.1R |
| OrbBreakoutinv_XAUUSD_Paper | XAUUSD | Paper (inverse) | 15m OR / M1 / 4R | 165 / 0.723 / -11.9R / -$2,522 | 165 / 0.745 / -10.7R / -$2,142 | +1.2R |
| OrbBreakoutwf_XAUUSD_Paper | XAUUSD | Paper (həftə sonu bağlı) | 60m OR / M1 / 3R | 125 / 1.447 / +32.2R / $8,096 | 125 / 1.490 / +34.5R / $8,598 | +2.3R |

⚠️ = `deploy/demo_roster.txt`-də olmayan Demo konfiqurasiyası: .bat faylı var, amma hazırda real order vermir (o simvolu başqa strategiya tutur).

## Hər broker ayrıca

### FundingPips (hazırkı)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 113 | 39.8% | 0.756 | -18.1 | 20.9 | 45 | 68 | $45,980 | 9.5% | -8.0% | -$1,835 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 61 | 18.0% | 0.680 | -18.2 | 20.1 | 11 | 50 | $45,910 | 8.7% | -8.2% | -$2,065 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 60 | 81.7% | 1.059 | +0.7 | 3.3 | 49 | 11 | $50,189 | 1.5% | +0.4% | -$92 | $0 | 0 |
| OrbBreakout_GER40_Demo | 118 | 22.0% | 0.932 | -7.0 | 23.7 | 26 | 92 | $48,239 | 11.0% | -3.5% | -$2,039 | $0 | 0 |
| OrbBreakout_GER40_Paper | 118 | 22.0% | 0.932 | -7.0 | 23.7 | 26 | 92 | $48,239 | 11.0% | -3.5% | -$2,039 | $0 | 0 |
| OrbBreakout_JP225_Demo | 328 | 37.5% | 0.817 | -40.7 | 41.1 | 122 | 206 | $44,773 | 11.7% | -10.5% | -$550 | $0 | 0 |
| OrbBreakout_JP225_Paper | 186 | 22.0% | 0.910 | -13.9 | 21.5 | 40 | 146 | $48,207 | 7.9% | -3.6% | -$490 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 184 | 75.5% | 0.814 | -8.3 | 13.2 | 140 | 44 | $48,265 | 5.8% | -3.5% | -$156 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 127 | 36.2% | 0.825 | -15.4 | 24.9 | 46 | 81 | $46,386 | 11.4% | -7.2% | -$1,477 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 70 | 21.4% | 0.901 | -6.2 | 15.4 | 15 | 55 | $48,410 | 7.2% | -3.2% | -$1,557 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 68 | 77.9% | 0.879 | -1.8 | 5.5 | 53 | 15 | $49,907 | 2.0% | -0.2% | -$59 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 128 | 32.8% | 0.593 | -39.0 | 39.0 | 42 | 86 | $41,305 | 17.4% | -17.4% | -$1,912 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 70 | 17.1% | 0.631 | -24.4 | 25.4 | 12 | 58 | $44,354 | 11.6% | -11.3% | -$2,015 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 67 | 82.1% | 1.115 | +1.4 | 4.2 | 55 | 12 | $50,282 | 1.6% | +0.6% | -$87 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 287 | 41.1% | 1.225 | +39.1 | 26.6 | 118 | 169 | $58,474 | 12.8% | +16.9% | -$1,294 | -$263 | 1 |
| OrbBreakout_XAUUSD_Paper | 106 | 31.1% | 1.301 | +22.7 | 15.6 | 33 | 73 | $55,795 | 7.0% | +11.6% | -$989 | -$50 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 165 | 73.9% | 0.723 | -11.9 | 14.8 | 122 | 43 | $47,478 | 6.0% | -5.0% | $89 | -$27 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 125 | 39.2% | 1.447 | +32.2 | 13.9 | 29 | 67 | $58,096 | 6.5% | +16.2% | -$891 | -$61 | 0 |

### CFI (yeni hesab)

| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 111 | 40.5% | 0.789 | -15.1 | 20.2 | 45 | 66 | $47,005 | 8.6% | -6.0% | -$1,670 | $0 | 0 |
| OrbBreakout_DJI30_Paper | 61 | 18.0% | 0.698 | -17.0 | 19.9 | 11 | 50 | $46,317 | 8.5% | -7.4% | -$1,827 | $0 | 0 |
| OrbBreakoutinv_DJI30_Paper | 61 | 82.0% | 1.167 | +1.8 | 3.3 | 50 | 11 | $50,532 | 1.2% | +1.1% | $52 | $0 | 0 |
| OrbBreakout_GER40_Demo | 121 | 20.7% | 0.896 | -11.0 | 22.5 | 25 | 96 | $46,974 | 9.9% | -6.1% | -$1,600 | $0 | 0 |
| OrbBreakout_GER40_Paper | 121 | 20.7% | 0.896 | -11.0 | 22.5 | 25 | 96 | $46,974 | 9.9% | -6.1% | -$1,600 | $0 | 0 |
| OrbBreakout_JP225_Demo | 324 | 38.3% | 0.843 | -35.0 | 35.4 | 124 | 200 | $42,244 | 15.7% | -15.5% | -$1,593 | $0 | 0 |
| OrbBreakout_JP225_Paper | 184 | 21.7% | 0.922 | -12.0 | 23.1 | 40 | 144 | $47,740 | 11.4% | -4.5% | -$1,617 | $0 | 0 |
| OrbBreakoutinv_JP225_Paper | 182 | 76.4% | 0.839 | -7.0 | 12.4 | 139 | 43 | $48,497 | 5.6% | -3.0% | -$175 | $0 | 0 |
| OrbBreakout_NDX100_Demo | 123 | 37.4% | 0.877 | -10.3 | 22.6 | 46 | 77 | $47,660 | 10.1% | -4.7% | -$1,458 | $0 | 1 |
| OrbBreakout_NDX100_Paper | 68 | 22.1% | 0.931 | -4.2 | 15.4 | 15 | 53 | $48,933 | 7.0% | -2.1% | -$1,520 | $0 | 1 |
| OrbBreakoutinv_NDX100_Paper | 68 | 77.9% | 0.952 | -0.7 | 5.4 | 53 | 15 | $50,011 | 2.2% | +0.0% | $46 | $0 | 1 |
| OrbBreakout_SPX500_Demo | 127 | 34.6% | 0.609 | -36.1 | 42.8 | 44 | 83 | $42,127 | 18.4% | -15.7% | -$1,897 | $0 | 1 |
| OrbBreakout_SPX500_Paper | 69 | 15.9% | 0.579 | -28.7 | 31.5 | 11 | 58 | $43,806 | 13.9% | -12.4% | -$2,025 | $0 | 1 |
| OrbBreakoutinv_SPX500_Paper | 69 | 84.1% | 1.414 | +4.5 | 3.3 | 58 | 11 | $50,979 | 1.2% | +2.0% | $69 | $0 | 1 |
| OrbBreakout_XAUUSD_Demo | 284 | 41.5% | 1.234 | +39.2 | 23.1 | 118 | 166 | $58,642 | 11.1% | +17.3% | -$1,059 | $0 | 1 |
| OrbBreakout_XAUUSD_Paper | 105 | 31.4% | 1.335 | +24.8 | 14.9 | 33 | 72 | $56,211 | 6.8% | +12.4% | -$861 | $0 | 1 |
| OrbBreakoutinv_XAUUSD_Paper | 165 | 74.5% | 0.745 | -10.7 | 13.8 | 123 | 42 | $47,858 | 5.5% | -4.3% | $115 | $0 | 1 |
| OrbBreakoutwf_XAUUSD_Paper | 125 | 39.2% | 1.490 | +34.5 | 12.5 | 29 | 66 | $58,598 | 5.8% | +17.2% | -$778 | $0 | 0 |

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
  - `OrbBreakout_JP225_Paper` @ fundingpips: trade-lərin 82%-i 10 lot tavanında, median risk $153 (hədəf ~$250)
  - `OrbBreakout_JP225_Demo` @ fundingpips: trade-lərin 78%-i 10 lot tavanında, median risk $153 (hədəf ~$250)
  - `OrbBreakoutinv_JP225_Paper` @ fundingpips: trade-lərin 6%-i 10 lot tavanında, median risk $241 (hədəf ~$250)
- **Swap bugünkü dərəcə ilə.** Hər iki brokerin swap-ı bu günkü dərəcədən bütün ilə tətbiq olunub; brokerlər tarixi dərəcələri yayımlamır.
- **Tick datası istifadə olunmayıb.** CFI-də tick tarixçəsi yoxdur, ona görə FundingPips-inki də söndürülüb — qopma (gap) stopları hər iki tərəfdə eyni proksi ilə qiymətləndirilib. Bu, müqayisəni brokerə görə təmizləyir, amma hər iki tərəfdə stop qiymətini bir az optimist saya bilər.
- **1 il kiçik nümunədir.** Simvoldan asılı olaraq 60–330 trade; bu qədər trade-də PF-in təsadüfi sürüşməsi rahatlıqla ±0.1 ola bilər.
- **Bu portfel nəticəsi deyil.** Hər bot ayrıca $50,000-da işlədilib; bir hesabda altısı birlikdə balansı və marja tavanını bölüşər, drawdown-lar isə üst-üstə düşər.

