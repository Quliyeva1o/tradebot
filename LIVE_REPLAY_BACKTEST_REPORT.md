# Canlı Əkiz Backtest — nəticə

Hazırlandı: 2026-09-15 12:50 UTC · data sonu: 2026-09-15 · spec: `docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md`

## Yoxlamalar

G1–G3 yoxlamaları (`pytest tests/live_replay`, 2026-09-15): **85 test keçdi, 0 yıxıldı** — 65 vahid test (G1), 13 bərabərlik testi (G2), 7 real trade testi (G3).

- G2: 10 konfiqurasiyanın hamısında replay hər 2 dəqiqəlik poll-da real runner-in `_evaluate_for_new_trade` ilə eyni setup-ı verir; M1-dən qurulan M15 brokerin öz M15-i ilə uyğundur.
- G3: 2026-09-10..14-ün real Demo deal-ları (6 simvol) və VPS paper trade-ləri təkrarlanır — eyni setup, eyni SL/TP, eyni çıxış növü, boşluq stopları real −3.1R-dən ≤0.3R, swap lot başına ≤5%, giriş həmin dəqiqənin :04–:07 kotirovka aralığında.
- Baza: `tests/test_nasdaq_midline_sweep_regression.py::test_midline_sweep_ustec_oos_regression` bu işdən əvvəl də sınıq idi.


## Əsas cədvəl (köhnə backtest vs canlı əkiz)

| Bot | köhnə n | köhnə PF | köhnə netR | əkiz n | əkiz PF | əkiz netR | əkiz maxDD R | son 1 il PF (köhnə → əkiz) | 6 aylıq blok yaşıl % | filtrlər |
|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 383 | 1.201 | +59.5 | 437 | 1.093 | +33.5 | 22.0 | 1.055 → 0.664 | 64% (14) | ✅❌✅ |
| OrbBreakout_JP225_Demo | 341 | 1.155 | +43.1 | 467 | 0.975 | -9.2 | 38.2 | 1.059 → 0.898 | 50% (6) | ❌❌❌ ⚠️az tarixçə |
| OrbBreakout_NDX100_Demo | 492 | 1.318 | +119.0 | 528 | 1.223 | +93.3 | 21.9 | 1.083 → 0.942 | 79% (14) | ✅❌✅ |
| OrbBreakout_SPX500_Demo | 388 | 1.325 | +97.3 | 443 | 1.086 | +31.5 | 28.9 | 1.032 → 0.659 | 64% (14) | ✅❌✅ |
| OrbBreakout_XAUUSD_Demo | 926 | 1.234 | +168.6 | 991 | 1.090 | +72.4 | 56.0 | 1.443 → 1.308 | 79% (14) | ✅✅✅ |
| OrbSweep_GER40_Demo | 100 | 1.548 | +19.1 | 131 | 1.229 | +15.5 | 5.7 | 1.761 → 1.007 | 55% (11) | ✅✅❌ |
| OrbBreakout_GER40_Paper | 633 | 1.256 | +128.9 | 660 | 1.262 | +137.5 | 32.4 | 1.043 → 0.961 | 55% (11) | ✅❌❌ |
| OrbBreakout_XAUUSD_Paper | 678 | 1.382 | +179.1 | 676 | 1.176 | +88.9 | 31.1 | 1.384 → 1.283 | 64% (14) | ✅✅✅ |
| OrbSweep_JP225_Paper | 55 | 1.253 | +7.5 | 80 | 0.863 | -6.7 | 12.8 | 1.578 → 0.781 | 17% (6) | ❌❌❌ ⚠️az tarixçə |
| OrbSweep_XAUUSD_Paper | 163 | 1.246 | +19.1 | 210 | 1.004 | +0.4 | 17.4 | 1.399 → 1.478 | 50% (14) | ✅✅❌ |

Filtrlər sırası: tam tarixçə PF > 1, son 1 il PF > 1, 6 aylıq blokların ≥60%-i müsbət.

## Fərqin parçalanması (netR, xüsusiyyət söndürüləndə)

| Bot | tam əkiz | poll_clock | spread | entry_ticks | gap_ticks | gap_proxy | swap | commission |
|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | +33.5 | +34.1 | +35.0 | +35.4 | +33.1 | +34.0 | +88.1 | +33.5 |
| OrbBreakout_JP225_Demo | -9.2 | -0.1 | +30.2 | -7.4 | -9.7 | -7.5 | +0.0 | -9.2 |
| OrbBreakout_NDX100_Demo | +93.3 | +101.4 | +96.1 | +93.5 | +94.1 | +94.3 | +133.4 | +93.3 |
| OrbBreakout_SPX500_Demo | +31.5 | +34.1 | +35.1 | +32.6 | +31.2 | +32.3 | +85.2 | +31.5 |
| OrbBreakout_XAUUSD_Demo | +72.4 | +80.1 | +91.9 | +76.0 | +72.4 | +73.3 | +152.2 | +81.1 |
| OrbSweep_GER40_Demo | +15.5 | +16.0 | +20.2 | +15.9 | +15.9 | +15.6 | +18.8 | +15.5 |
| OrbBreakout_GER40_Paper | +137.5 | +118.3 | +143.2 | +139.7 | +137.4 | +139.7 | +181.3 | +137.5 |
| OrbBreakout_XAUUSD_Paper | +88.9 | +88.5 | +97.3 | +88.6 | +88.9 | +91.1 | +163.9 | +92.4 |
| OrbSweep_JP225_Paper | -6.7 | -6.4 | -2.1 | -8.9 | -7.3 | -6.7 | -5.8 | -6.7 |
| OrbSweep_XAUUSD_Paper | +0.4 | +1.7 | +1.4 | +0.6 | +0.4 | +1.1 | +2.8 | +1.7 |

**Swap sütununu necə oxumaq lazımdır.** Ən böyük fərq swap-dandır, çünki bu strategiya 4R
hədəflə günlərlə mövqe saxlayır və indekslərdə illik 7.33% maliyyələşdirmə tutulur. Amma bütün
tarixçəyə **bugünkü** dərəcə tətbiq olunub; 2020–2021-də faizlər sıfıra yaxın idi, deməli o
illərin real swap xərci xeyli az olub. Ona görə düzgün oxunuş budur: **həqiqi nəticə "tam əkiz"
ilə "swap" sütununun arasındadır** — birincisi bugünkü dərəcə ilə, ikincisi sıfır faiz sərhədi.
Dəqiqləşdirmə üçün brokerin tarixi swap dərəcələri lazımdır, onlar isə heç yerdə saxlanmır.

## $50,000 hesabda (hər bot ayrıca)

| Bot | son balans | max drawdown % | trade | swap $ | komissiya $ |
|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | $57,645 | 11.4% | 437 | $-14,859 | $0 |
| OrbBreakout_JP225_Demo | $52,050 | 7.9% | 467 | $-1,061 | $0 |
| OrbBreakout_NDX100_Demo | $79,297 | 9.7% | 528 | $-13,768 | $0 |
| OrbBreakout_SPX500_Demo | $57,247 | 14.0% | 443 | $-14,732 | $0 |
| OrbBreakout_XAUUSD_Demo | $70,303 | 25.2% | 991 | $-19,683 | $-2,158 |
| OrbSweep_GER40_Demo | $54,302 | 4.1% | 131 | $-1,062 | $0 |
| OrbBreakout_GER40_Paper | $86,273 | 14.9% | 660 | $-14,108 | $0 |
| OrbBreakout_XAUUSD_Paper | $74,961 | 15.1% | 676 | $-21,588 | $-1,010 |
| OrbSweep_JP225_Paper | $47,544 | 7.1% | 80 | $-167 | $0 |
| OrbSweep_XAUUSD_Paper | $50,148 | 10.9% | 210 | $-756 | $-437 |

## Çıxış növləri və köhnə sütunun yoxlanması

| Bot | TP | SL | boşluq (tick) | boşluq (proksi) | açıq | eyni bar SL+TP | giriş barında bağlanan | spread nisbəti | qeydə alınmış köhnə PF | təkrar |
|---|---|---|---|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | 106 | 323 | 6 | 2 | 0 | 1 | 2 | 1.00x | 1.205 | 1.205 |
| OrbBreakout_JP225_Demo | 106 | 354 | 5 | 2 | 0 | 0 | 1 | 1.00x | 1.177 | 1.172 |
| OrbBreakout_NDX100_Demo | 136 | 386 | 3 | 3 | 1 | 0 | 1 | 1.00x | 1.329 | 1.325 |
| OrbBreakout_SPX500_Demo | 108 | 325 | 6 | 4 | 0 | 0 | 1 | 1.00x | 1.326 | 1.325 |
| OrbBreakout_XAUUSD_Demo | 236 | 749 | 1 | 5 | 0 | 0 | 0 | 1.13x | 1.235 | 1.239 |
| OrbSweep_GER40_Demo | 69 | 58 | 2 | 2 | 0 | 0 | 1 | 1.00x | 1.547 | 1.548 |
| OrbBreakout_GER40_Paper | 168 | 481 | 7 | 4 | 0 | 0 | 0 | 1.00x | — | — |
| OrbBreakout_XAUUSD_Paper | 213 | 457 | 2 | 4 | 0 | 0 | 0 | 1.13x | 1.389 | 1.385 |
| OrbSweep_JP225_Paper | 37 | 39 | 4 | 0 | 0 | 0 | 1 | 1.00x | 1.253 | 1.253 |
| OrbSweep_XAUUSD_Paper | 104 | 105 | 0 | 1 | 0 | 0 | 6 | 1.13x | — | — |

**Spread yoxlaması (G4).** Bar-ların spread sütunu indekslərdə real tick spread-i ilə eynidir
(1.00x). Yalnız XAUUSD-də 13% aşağı göstərir. 1.13x ilə yenidən hesabladım, təsiri cüzidir:
XAUUSD Demo PF 1.090 → 1.087 (991 trade-də −2.4R), XAUUSD Paper 1.176 → 1.174,
XAUUSD Sweep 1.004 → 1.003. Cədvəldəki rəqəmlər 1.00x ilədir.

## Məhdudiyyətlər

- Swap dərəcələri tarixi deyil: bütün tarixçəyə 2026-09-15 dərəcələri tətbiq olunub.
- Spread hər M1 barın öz spread sütunundandır; tick müqayisəsi yuxarıdakı nisbətdədir.
- Tick tarixçəsi indekslərdə 2025-03, qızılda 2026-05-dən başlayır; ondan əvvəlki boşluq stopları bar close proksisi ilə qiymətləndirilib (`gap_proxy` sütunu bunun qiymətidir).
- Poll saniyəsi sabit götürülüb; real jitter 4–6 saniyədir.
- Requote, reject, AutoTrading kəsintiləri və FundingPips-in məcburi bağlanışları modelləşdirilmir.
- Botlarda heç nə dəyişmir; 2026-10-12 dondurma planı qüvvədədir.
