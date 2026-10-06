# ICT strategiyaları - test nəticəsi (2026-10-01)

Mənbə: `ict_spec.py` (sənəd). Mühərrik: `data.py`, `core.py`, `strategies.py`, `run.py`, `analyze.py`.
Nəticələr: `ict.db` -> `results` cədvəli (strategiya x dərəcə x simvol x variant). Trade-lər: `out/trades_all.csv`.

**Data/qaydalar.** CFI M1 bid, 2021-01..2026-09, XAUUSD, US100, EURUSD, GBPUSD (SMT partnyorları: XAGUSD, US500, GBPUSD/EURUSD).
Vaxtlar NY (broker saatı NY+7-dən çevrilib). Bid/ask doldurma, spread = max(bar spread, canlı spread: XAU 0.15, US100 0.8,
EURUSD 0.0001, GBPUSD 0.00012), komissiya 0 (CFI). Hər killzone-da gündə 1 trade, hamısı 16:00 NY-də bağlanır.
Stop-a məsafə 5 spread-dən azdırsa trade yoxdur. Eyni barda SL və TP olarsa SL sayılır. RR < 1:2 olarsa trade yoxdur.
Variantlar: `doc` = sənədin TP qaydası, `r2` = sabit 2R. Turtle: `opp` = diapazonun əks tərəfi; PDH/PDL: `tp1` = günün 50%-i.
Qismən bağlama və breakeven-ə çəkmə yoxdur (tək çıxış).

**Dərəcə (bütün modellər üçün eyni, 5 təsdiq, hər biri 1 bal):** bias uyğun, displacement (gövdə >= 1.5x orta gövdə və >= 0.6 range),
giriş düzgün premium/discount tərəfində (son 24s diapazonunun 50%-nə görə), SMT divergence (NY Open-da CBDR SD uyğunluğu da sayılır),
sweep olunan səviyyə HTF-dir (PDH/PDL, PWH/PWL, 20g, Asia, London). A+ = 4-5 bal, A = 3, B+ = 2, B = 0-1.
Dərəcələr P&L-ə baxmadan əvvəl təyin olunub və sonra dəyişdirilməyib. Turtle Soup-da displacement yoxdur (max 4 bal).

## Yekun (doc variantı, 4 simvol birlikdə)

| # | Strategiya | n | orta R | PF | t | hökm |
|---|---|---|---|---|---|---|
| 1 | Silver Bullet | 354 | -0.22 | 0.70 | -2.8 | mənfi |
| 2 | London Asia sweep (OTE 0.62) | 262 | -0.05 | 0.94 | -0.4 | edge yox |
| 3 | Power of 3 | 468 | -0.09 | 0.88 | -1.0 | mənfi |
| 4 | OTE 0.705 | 5542 | -0.05 | 0.93 | -2.3 | mənfi (r2: t -4.2) |
| 5 | 2022 Model | 5324 | -0.03 | 0.96 | -1.1 | edge yox |
| 6 | Unicorn | 1807 | +0.04 | 1.06 | 0.8 | sıfıra yaxın, əhəmiyyətsiz |
| 7 | Turtle Soup | 1510 | -0.08 | 0.91 | -1.2 | mənfi (r2: t -2.7) |
| 8 | Order Block | 2730 | -0.04 | 0.95 | -1.1 | ümumi mənfi; A+ xanası təsdiqlənmədi |
| 9 | Breaker Block | 3483 | 0.00 | 1.00 | 0.0 | edge yox |
| 10 | NY Open + CBDR | 794 | -0.06 | 0.91 | -1.0 | edge yox (r2 +0.01) |
| 11 | PDH/PDL raid | 246 | -0.01 | 0.99 | 0.0 | edge yox (r2 +0.08, t 1.0) |

Hər dərəcə xanası üçün (n, qazanma %, net R, PF, t, max DD, 2021-23 və 2024-26 yarım dövrlər) bax `ict.db`.

**Meyar:** xana "keçdi" sayılır ancaq n >= 50, t >= 2 və hər iki yarım dövr müsbətdirsə. Yalnız bir xana keçdi: Order Block A+
(n=168, +105.9R, PF 1.92, t=2.75). 11 strategiya x 4 dərəcə x 2-3 variant = ~100 xana sınanıb, təsadüfən 2-3 xananın t>2 olması gözlənilir.
Qazanc US100-dəndir (+90R; XAUUSD +10R, EURUSD -2R). Ən yaxşı 5 trade çıxanda orta R 0.63 -> 0.28 olur (qalan +46R).
Dərəcələr monoton deyil: Order Block A -0.14R, B+ -0.03R. Nəticə: tək xana, təsdiq tələb edir.
A+ praktik olaraq "SMT var və bias uyğun" deməkdir (SMT 85%, bias 74%).

Əvvəlki sessiyaların nəticələri ilə uyğundur: London Asia-sweep+OTE, AMD və intraday sweep+FVG ailəsi bu datada edge göstərmir.
