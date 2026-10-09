# ORB Breakout wf, XAUUSD, CFI: ay-ay nəticə

Hazırlandı: 2026-10-08. Pəncərə: 2025-10-08 → 2026-10-07 (365 gün). Replay: `OrbBreakoutwf_XAUUSD_Paper`
(60 dəq OR, M1, yalnız LONG, weekend-flat), CFI öz qiymətləri ilə, hər ay çıxış vaxtına görə.
Dollar sütunu 0.5% riskdədir (~$240/R); canlı bot 0.25% işləyir, R eynidir.

## TP 3R (indiki konfiqurasiya)

| Ay | Əməliyyat | Qazanc | Net R | Kumulyativ R | $ (0.5% risk) |
|---|---|---|---|---|---|
| 2025-10 | 9 | 5 | +6.27 | +6.27 | +1517 |
| 2025-11 | 6 | 3 | +4.71 | +10.98 | +1130 |
| 2025-12 | 9 | 3 | +2.63 | +13.62 | +621 |
| 2026-01 | 12 | 6 | +8.22 | +21.83 | +1995 |
| 2026-02 | 9 | 3 | +1.43 | +23.26 | +419 |
| 2026-03 | 7 | 2 | -4.35 | +18.91 | -1117 |
| 2026-04 | 7 | 4 | +6.50 | +25.42 | +1621 |
| 2026-05 | 12 | 2 | -3.61 | +21.80 | -999 |
| 2026-06 | 8 | 2 | -2.49 | +19.31 | -665 |
| 2026-07 | 15 | 5 | +1.48 | +20.79 | +410 |
| 2026-08 | 11 | 4 | +3.45 | +24.24 | +799 |
| 2026-09 | 13 | 4 | -5.00 | +19.25 | -1312 |
| 2026-10 (7 gün) | 2 | 0 | -2.03 | +17.22 | -536 |
| **Cəmi** | **120** | | **+17.2** | | |

PF 1.24, orta +0.143R/əməliyyat, max drawdown 12.5R. Oktyabr-Fevral +23.3R, Mart-Oktyabr -6.1R.

## TP 2R ilə müqayisə (eyni pəncərə)

| Ay | 2R net R | 3R net R |
|---|---|---|
| 2025-10 | +3.63 | +6.27 |
| 2025-11 | +8.53 | +4.71 |
| 2025-12 | +5.59 | +2.63 |
| 2026-01 | +4.25 | +8.22 |
| 2026-02 | +2.58 | +1.43 |
| 2026-03 | -2.29 | -4.35 |
| 2026-04 | +2.39 | +6.50 |
| 2026-05 | -3.84 | -3.61 |
| 2026-06 | -3.49 | -2.49 |
| 2026-07 | -1.51 | +1.48 |
| 2026-08 | +0.77 | +3.45 |
| 2026-09 | -6.00 | -5.00 |
| 2026-10 | -2.03 | -2.03 |
| **Cəmi** | **+8.6** (n=134, PF 1.12, DD 17.3R) | **+17.2** (n=120, PF 1.24, DD 12.5R) |

Nəticə: 2R daha tez qazanır, amma net R yarıya düşür və drawdown artır. 3R saxlanılır.

## Qeydlər

- Yalnız LONG: aşağı trenddə (Sentyabr 2026: 4408 → 4165) hər yuxarı breakout geri qayıdır.
- Əvvəlki yaddaş rəqəmi (125 əməliyyat, +34.5R) 2026-09-21 pəncərəsinə aiddir; pəncərə sürüşəndə güclü
  Oktyabr 2025 günləri çıxıb, zəif Sentyabr-Oktyabr 2026 günləri daxil olub.
- Kill qaydası: 40 əməliyyatda 17R DD və ya 40-cı əməliyyatda net < -5.8R. Replay-də yaşanan DD 12.5R.
- Swap bugünkü dərəcə ilə bütün tarixə tətbiq olunub; tick datası yoxdur.
