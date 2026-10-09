# Qalan strategiyaların tam yoxlanışı — 2026-10-09

**Data:** CFI və FundingPips M1 tarixçəsi 2026-10-09 10:38-ə qədər MT5-dən yeniləndi (ehtiyat nüsxə: `C:\tradebot_data_backup_2026-10-09`).
**Metod:** hər botun *öz canlı konfiqurasiyası* ilə, hər brokerin öz qiymət/spred/swap-ı ilə tam tarixçə replay-i. Bütün ölçülər R ilə.
FundingPips qızıl tarixçəsi 2025-03-10-dan əvvəl saatla uyğunsuzdur, ona görə həmin hissə atıldı.
Tam kəsimlər (il, yarımil, fəsil, ay, həftənin günü, gün-bəgün, sürüşən 12 ay) `sections.md`-dədir; xam trade-lər `trades.csv`-dədir.

## Qərar cədvəli

| Strategiya | Broker | Tarixçə | n | PF | Net R | Son 12 ay PF | Son 6 ay PF | Müsbət illər | Hökm |
|---|---|---|---|---|---|---|---|---|---|
| **AMD gold** | CFI | 2024-01 → | 66 | 2.25 | +26.4 | 2.16 | 3.93 | 3/3 | **Güvən (paper-də qalsın)** |
| **AMD gold** | FP | 2025-03 → | 31 | 2.40 | +14.3 | 2.06 | 3.62 | 2/2 | **Güvən (paper-də qalsın)** |
| **ICT A+ 2R US100** | CFI | 2021-02 → | 502 | 1.28 | +84.6 | 1.13 | 1.30 | 5/6 | **Şərti güvən** |
| ICT A+ 2R XAUUSD | CFI | 2021-02 → | 741 | 1.05 | +21.9 | 1.61 | 1.12 | 3/6 | Zəif, qeyri-sabit |
| ICT A+ 2R GBPUSD / EURUSD | CFI | 2021-02 → | 523 / 409 | 1.14 / 0.84 | +45 / −43 | 1.19 / 1.43 | 1.73 / 3.89 | 5/6 / 4/6 | Güvənmə (EURUSD 2023: −38R) |
| **ORB breakout wf XAUUSD** | CFI | 2016-05 → | 1258 | 1.06 | +50.3 | 1.30 | 0.95 | 7/11 | **Şərti, yalnız kill qaydası ilə** |
| ORB breakout wf XAUUSD | FP | 2025-03 → | 195 | 1.27 | +31.9 | 1.30 | 0.91 | 2/2 | Eyni |
| FVG window NDX100 | CFI | 2019-03 → | 735 | 1.13 | +74.3 | 1.05 | 0.88 | 6/8 | Marjinal, edge azalıb |
| FVG window NDX100 | FP | 2019-03 → | 733 | 1.06 | +31.7 | 1.04 | 0.86 | 5/8 | Güvənmə |
| ORB breakout NDX100 | CFI / FP | 2019-03 → | 566 / 566 | 1.09 / 1.21 | +43 / +94 | 0.89 / 0.89 | 1.17 / 1.23 | 5/8 / 6/8 | Güvənmə (2025 −7R, 2026 ≈ 0) |
| ORB breakout GER40 | FP | 2021-09 → | 655 | 1.28 | +145 | 0.82 | 0.77 | 4/6 | Güvənmə **indi** (2026: −18.6R) |
| ORB breakout GER40 | CFI | 2024-09 → | 254 | 1.24 | +48.7 | 0.80 | 0.78 | 2/3 | Güvənmə indi |
| ORB breakout DJI30 | CFI / FP | 2024-09 / 2018-12 → | 106 / 472 | 0.95 / 1.03 | −5 / +10 | 0.75 / 0.74 | 0.54 / 0.53 | 1/3 / 5/9 | **Yox** |
| ORB breakout SPX500 | CFI / FP | 2024-09 / 2018-12 → | 120 / 491 | 0.94 / 1.10 | −6 / +41 | 0.75 / 0.83 | 1.07 / 1.04 | 2/3 / 6/9 | **Yox** |
| ORB breakout JP225 | CFI / FP | 2024 → | 390 / 484 | 0.93 / 0.97 | −23 / −11 | 0.94 / 0.94 | 0.85 / 0.91 | 1/3 / 1/3 | **Yox** |
| ORB sweep GER40 | CFI / FP | 2025-01 / 2022-01 → | 39 / 123 | 1.50 / 1.02 | +8.9 / +1.1 | 1.00 / 0.78 | 0.93 / 0.69 | 1/2 / 2/5 | **Yox** |
| ORB sweep XAUUSD | CFI | 2017-05 → | 270 | 0.89 | −17.7 | 1.87 | 1.28 | 3/10 | **Yox** (12 aylıq PF təsadüfdür) |
| ORB sweep XAUUSD | FP | 2025-03 → | 38 | 1.22 | +4.4 | 1.35 | 0.89 | 2/2 | Nümunə çox kiçik |

PF 1.15-dən aşağı, bootstrap p > 0.10 və ya iki yarımdan biri mənfi olan strategiya "güvənmə" sayıldı. Bu qaydaları qiymətlərə baxmazdan əvvəl yazdım (`analyze.py`).

## Əsas nəticələr

1. **Güvənə biləcəyimiz tək şey AMD gold-dur, amma yalnız paper-də.** İki brokerdə PF 2.2–2.4, bootstrap p = 0.002 / 0.01, maks. DD 3.4R / 2.8R, sürüşən 12 aylıq PF həmişə > 1.1 (2025-01-də 1.15, sonra 1.7–3.9). Canlı paper 6 oktyabrda replay ilə eyni trade-i və +2R verdi. Şübhələr: 2024-də PF cəmi 1.10 (+0.8R, 20 trade), qazancın əsası 2025-dən gəlir (qızıl yüksəlişi), long-lar short-lardan güclüdür, ayda ~2 trade. Demo üçün canlı 20–30 trade lazımdır.
2. **ICT A+ yalnız US100-də ayaqda qalır.** PF 1.28, 5/6 il müsbət, ən pis il −17.8R (2021). Digər simvollar qeyri-sabitdir (XAUUSD 3/6 il, EURUSD 2023-də −38R). "Bütün modellər" birləşmiş sətri (PF 1.08, +108R, 2024-26-da +145R) yanıltıcıdır: eyni setup bir neçə modeldə təkrarlanır, p-dəyəri şişirdilib. Laboratoriya özü də bunu ~100 xanadan ən yaxşısı kimi qeyd edib. Bot 8 oktyabrdan işləyir, canlı nəticə hələ yoxdur.
3. **ORB wf XAUUSD pul qazandırıb, amma 10 ilə PF cəmi 1.06.** 11 ilin 4-ü mənfidir (2017 −12.5R, 2018 −48.1R, 2021 −16.2R, 2022 −16.6R). 2023–2026 hər il müsbətdir, amma 2026-da yalnız +6.7R (PF 1.11), son 6 ay PF 0.95. Tarixdə maks. DD **113.6R** (0.25% risklə hesabın ~28%-i) olub; 17R kill qaydası yalnız son ilin bootstrap-ına əsaslanır. Qayda 2018 kimi rejimdə işə düşüb botu dayandırar, bu onun məqsədidir. Risk artırılmamalıdır.
4. **Beş ORB indeks botu təxminən 1–2 bahisdir.** Aylıq R-lərin korrelyasiyası DJI30–SPX500 0.53, DJI30–GER40 0.50. 2026-da hamısı birlikdə itirir: DJI30 −15.6R, GER40 −16.9R, JP225 −21.4R, SPX500 −12.2R (CFI). CFI-də bu beşinin cəmi 2026 yanvardan sentyabra −53R (mart −26.9R, sentyabr −27.1R). GER40 FP-də 2023–25 +170R verib, 2026-da çevrilib. Bu rejim dəyişikliyidir, tək bota aid səhv deyil. DJI30, SPX500, JP225 üçün 2025–26-nın heç birində davamlı edge yoxdur.
5. **FVG window NDX100 edge-ini itirib.** 7 ildə PF 1.13 / 1.06, amma 2025 (−7R / −16R) mənfi, 2026 ≈ 0, son 6 ay PF 0.88 / 0.86. Canlı paper 0/7. Backtest, iki broker və canlı paper eyni istiqamətdədir.
6. **Sweep botları (GER40, XAUUSD) ya nümunəsi kiçikdir, ya mənfidir.** CFI GER40 PF 1.50 yalnız 39 trade-dir və son 12 ayda 1.00. FP-də 123 trade, PF 1.02. XAUUSD CFI 270 trade, PF 0.89, yalnız 3/10 il müsbət.

## Fəsil, ay, həftənin günü barədə

`sections.md`-də hər strategiya üçün var. Nəticəm: **bunlardan tradeable pattern çıxarmaq olmaz.** 12 ay × 6–10 il = hər xanada 5–15 trade, və xanalar bir-birinə uyğun gəlmir (wf XAUUSD-da dekabr PF 1.65, avqust 0.68, amma n ≈ 90–120 və ardıcıl deyil). Yalnız AMD üçün "Bazar ertəsi" PF 38 (n=8) qeyd edilib, bu da təsadüfdür. Bu kəsimlər hər strategiyanın necə dəyişdiyini izləmək üçündür, giriş süzgəci kimi istifadə edilməməlidir.

## Tövsiyə

| Hərəkət | Bot |
|---|---|
| Saxla, dəyişmə | OrbBreakoutwf_XAUUSD_Demo (kill qaydası ilə, 0.25%), AmdGold_XAUUSD_Paper, ICT A+ (US100) |
| Paper-də saxla, demo-ya çıxarma | AmdGold (20–30 canlı trade-ə qədər) |
| Paper-də qalsın, qərar ver | FvgWindow_NDX100, OrbBreakout_NDX100, OrbBreakout_GER40 (2026 çevrilməsi davam edirsə sonrakı rübdə bağla) |
| Bağlamağa dəyər | OrbBreakout DJI30 / SPX500 / JP225 paper, OrbSweep GER40 / XAUUSD paper (onsuz da demo yoxdur; paper yalnız ölçmə üçün idi, cavab alınıb) |

## Məhdudiyyətlər

- ORB indekslərində CFI M1 tarixçəsi yalnız 2024-09-dan başlayır (JPN225, DJI30, SPX500, GER40), NDX100 isə 2019-dan. Müqayisələrdə n fərqli olur.
- FVG və AMD trade vaxtları gün səviyyəsindədir (saat təxminidir), ICT trade-ləri `ict_lab/out/trades_all.csv`-dən götürülüb (4 simvol, 2026-09-25-ə qədər; botun 24 setup-ı daha genişdir).
- Replay-də swap bugünkü dərəcə ilə bütün tarixə tətbiq olunur və tick yoxdur; mütləq rəqəmlər yaxınlaşmadır.
- Mənim AMD replay-im bot kodunun docstring-indəki 74 trade / PF 1.99 rəqəmlərindən fərqlənir (66 / 2.25), çünki exit simulyasiyam bir az fərqlidir. Sıralama dəyişmir.
- 25 strategiya × broker xanasından seçim aparıldığı üçün ən yaxşı nəticələr (AMD, ICT US100) optimistik ola bilər.
