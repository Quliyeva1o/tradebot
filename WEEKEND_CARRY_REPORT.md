# Həftə sonu daşıması — canlı əkiz replay

Hazırlandı: 2026-09-19 07:55 UTC · generasiya edən: `scripts/weekend_carry_report.py`

Sual: `--reverse-on-stop` ayağını həftə sonu saxlamaq nəyə başa gəlir? Mühərrikdə heç nə
dəyişmir — replay onsuz da həftə sonu saxlamağı modelləşdirir (M1 barları zaman limiti
olmadan gəzilir, boşluq stopları `SL_GAP_TICK`/`SL_GAP_PROXY` ilə qiymətlənir, swap hər
server gecəyarısında, indekslərin cümə üçqatı daxil). Bu skript yalnız bitmiş işlemləri
etiketləyir.

## Konfiqurasiyalar

| Bot | Simvol | reverse_on_stop | M1 barları | işlem | ters | daşınan |
|---|---|---|---|---|---|---|
| OrbBreakout_DJI30_Demo | DJI30 | 0.5 | 2008-12..2026-09 | 839 | 367 | 202 |
| OrbBreakout_JP225_Demo | JP225 | 0.5 | 2024-04..2026-09 | 825 | 360 | 57 |
| OrbBreakout_NDX100_Demo | NDX100 | 0.5 | 2013-02..2026-09 | 1005 | 427 | 218 |
| OrbBreakout_SPX500_Demo | SPX500 | 0.5 | 2015-12..2026-09 | 881 | 378 | 202 |
| OrbBreakout_XAUUSD_Demo | XAUUSD | 0.5 | 2009-01..2026-09 | 4496 | 1974 | 381 |
| OrbSweep_GER40_Demo | GER40 | 0.5 | 2021-09..2026-09 | 195 | 63 | 27 |

## Daşınan vs daşınmayan, ayaq üzrə

| Ayaq | Daşınma | n | net R | orta | median | uduş % |
|---|---|---|---|---|---|---|
| reverse | daşınan | 126 | -31.37 | -0.249 | +0.477 | 53.97 |
| reverse | daşınmayan | 3443 | -229.49 | -0.067 | +0.486 | 62.62 |
| reverse | **fərq** | | | **-0.182** | | **t = -1.76** |
| breakout | daşınan | 961 | +717.11 | +0.746 | -1.076 | 42.87 |
| breakout | daşınmayan | 3711 | -867.75 | -0.234 | -1.010 | 18.67 |
| breakout | **fərq** | | | **+0.980** | | **t = +11.66** |

## Boşluq stopu — mexanizm

Həftə sonunu keçmək boşluq stopuna düşmə ehtimalını neçə dəfə artırır:

| Ayaq | Daşınma | n | boşluq stopu | tezlik | onların ortası |
|---|---|---|---|---|---|
| reverse | daşınan | 126 | 14 | 11.11% | -1.92 R |
| reverse | daşınmayan | 3443 | 17 | 0.49% | -1.07 R |
| breakout | daşınan | 961 | 47 | 4.89% | -1.86 R |
| breakout | daşınmayan | 3711 | 33 | 0.89% | -1.63 R |

Daşınan işlemlərin boşluq stopları, qiymətləmə mənbəyinə görə:

- `SL_GAP_TICK` (real tick): n=19, orta **-2.545 R**, median -1.934 R, ən pis -8.422 R
- `SL_GAP_PROXY` (bar-close): n=42, orta **-1.575 R**, median -1.407 R, ən pis -3.679 R
- Real tick ilə qiymətlənənlər proksidən pisdir, yəni köhnə data üzərindəki proksi boşluq riskini **az göstərir**.
- Boşluq stoplarının **60/61**-i (−1R)-dən pisdir.

## `--reverse-on-stop 0.5` qaydasının özü

Həftə sonundan asılı olmayaraq, bütün ters ayaqlar:

- n = **3569**, net **-260.9 R**, orta **-0.0731 R**
- uduş **62.31%**, 1R risk / 0.5R hədəf üçün sıfır nöqtəsi **66.67%** → **4.35 punkt** çatmır
- yalnız uduş nisbətindən çıxan gözlənti **-0.0653 R** — müşahidə olunan -0.0731 R ilə demək olar eyni

Yəni çatışmazlıq giriş keyfiyyətində deyil, **ödəniş həndəsəsindədir**: ters ayaq tam stop məsafəsini riskə atıb onun 0.5 hissəsini hədəfləyir.

## Məhdudiyyətlər

- Breakout sətri (+0.980 R, t +11.66) **səbəbiyyət deyil**. Breakout 4R hədəfləyir: uduzanlar tez stop olur, udanlar günlərlə qaçır, ona görə cümə bağlanışına çatmaq gələcək qalibləri seçir. O rəqəm yaşama müddətini ölçür, həftə sonunu yox.
- Ters ayaq 0.5R hədəfləyib saatlarla həll olunduğu üçün eyni qərəz orada xeyli zəifdir — hesabatın əsas nəticəsi ona görə ters sətridir.
- Breakout üçün təmiz test `--weekend-flat` A/B-dir; mühərrik onu `--reverse-on-stop` ilə birgə modelləşdirməkdən imtina edir (`backtest/live_replay/engine.py`).
- Simvolların tarixçə dərinliyi çox fərqlidir (yuxarıdakı cədvələ bax), ona görə per-simvol nümunələr bir-biri ilə müqayisə oluna bilməz.
- Tick tarixçəsi indekslərdə 2025-03, qızılda 2026-05-dən başlayır; ondan əvvəlki boşluq stopları bar-close proksisi ilə qiymətlənib.
- M1 tarixçəsi MT5-in `Max bars in chart` ayarından asılıdır; default 100000 onu ~3 aya kəsir və nümunəni sükutla kiçildir.
