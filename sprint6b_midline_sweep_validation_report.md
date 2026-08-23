# Sprint 6b — Midline Sweep Tam Kəmiyyət Doğrulama Hesabatı (2026-07-22)

**Əhatə:** `NasdaqMidlineSweepStrategy`, USTEC M5, `data/history/USTEC_M5.csv` (99859 bar, 2024-12-10 →
2026-07-09). Alətlər: `research/walk_forward.py::WalkForwardRunner`, `research/robustness.py::RobustnessTester`,
`research/monte_carlo.py::MonteCarloSimulator` (ilk ikisi Sprint 6a-da factory-based ediləndən sonra ilk dəfə
Midline Sweep üzərində real işə salınır).

**Qayda:** Bu YALNIZ doğrulama sprintidir — heç bir strategiya parametri, pozisiya-ölçüsü və ya konfiq
dəyişdirilməyib. Sprint boyu 1 real bug aşkarlandı (`research/monte_carlo.py`) — AYRICA bildirilib, kod
DƏYİŞDİRİLMƏYİB (bax Hissə 4).

**Əsas etalon (əvvəlki, sənədləşdirilmiş nəticə, dəyişməz):** Tək train/test OOS bölgüsü (70/30,
`--split out_of_sample --split-ratio 0.7`) — **106 ticarət, Profit Factor 1.0510, Net Profit +$379.24**,
Max Drawdown 10.08%, Win Rate 35.85% (OOS pəncərəsi: 2026-02-05 → 2026-07-09, 29958 bar).

**Ümumi qiymətləndirmə:** Sübut bazası bu sprintdən sonra xeyli genişlənib, amma nəticə TƏK-mənalı "yaşıl işıq"
DEYİL. Walk-forward 3 pəncərədən 2-sini təsdiqləyir, amma ƏN SON (ən "canlıya yaxın") pəncərə itki göstərir.
Robustness göstərir ki, sadə, real spread-lə müqayisə edilə bilən 1 nöqtəlik slippage artımı PF-i 1.0-ın
ALTINA salır. Monte Carlo (bug düzəldildikdən/təcrid edildikdən sonra) ruin riskini 0%-də göstərir, amma
worst-case drawdown tail-i (47.44%) tarixi müşahidə olunan 10.08%-dən xeyli genişdir. **Tövsiyə: Sprint 7-yə
(demo kapital) İNDİ KEÇMƏ — bax Hissə 5.**

---

## Hissə 1 — Walk-Forward Doğrulama

**Metodologiya:** `WalkForwardRunner(bars, "USTEC", Timeframe.M5)` — alətin ÖZ default parametrləri ilə (heç nə
override edilməyib): `train_size_pct=0.6, val_size_pct=0.2, step_size_pct=0.1, expanding=False` (rolling).
Tam 99859-bar tarixçə üzərində: `train_size=59915, val_size=19971, step_size=9985`. Strategiya:
`NasdaqMidlineSweepStrategy(body_multiplier=1.5)` (sənədləşdirilmiş validasiya default-u), `strategy_factory`
vasitəsilə (Sprint 6a-nın Bug #48 həlli). BacktestConfig əsl OOS doğrulaması ilə eynidir:
`initial_balance=10000, risk_per_trade=0.01, spread=0.0002 (CSV real spread üstünlük təşkil edir), commission=0,
slippage=0`.

**Nəticə: 3 fold.** (PF `WalkForwardRunner.run()`-un qaytardığı sxemdə YOXDUR — yalnız `net_profit`/`win_rate`/
`total_trades` var; aşağıdakı PF sütunları eyni fold sərhədləri ilə `BacktestEngine`+`BacktestReportGenerator`-ı
birbaşa çağıraraq MÜSTƏQİL hesablanıb, heç bir production kodu dəyişdirilmədən.)

| Fold | Train Pəncərə | Val Pəncərə | Train Trades | Train PF | Train Net | Val Trades | Val PF | Val Net |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 2024-12-10 → 2025-12-12 | 2025-12-12 → 2026-03-30 | 192 | 1.093 | +$1034.97 | 71 | **1.149** | **+$682.99** |
| 2 | 2025-01-31 → 2026-02-05 | 2026-02-05 → 2026-05-19 | 208 | 1.138 | +$1816.43 | 70 | **1.176** | **+$829.05** |
| 3 | 2025-03-25 → 2026-03-30 | 2026-03-30 → 2026-07-09 | 208 | 1.191 | +$2521.76 | 70 | **0.927** | **-$349.29** |

**Tapıntı — Fold 3 (ən son, ən "canlıya yaxın" pəncərə) İTKİ göstərir.** Fold 1 və 2-nin validation PF-i
(1.149, 1.176) əslində orijinal tək-bölgü OOS nəticəsindən (1.051) DAHA GÜCLÜDÜR — xoş sürpriz, konsistentliyi
dəstəkləyir. AMMA Fold 3-ün validation pəncərəsi (2026-03-30 → 2026-07-09, ən son ~3.5 ay) PF 0.927 ilə
BREAKEVEN-in altına düşür və -$349.29 itki verir — həm ən aşağı Win Rate (32.86% — digər fold-larda 38%
ətrafında), həm də TƏK itkili pəncərə. Bu, "performans BİR pəncərəyə cəmlənib" narahatlığının əksi deyil (əksinə,
2/3 fold güclüdür), amma "performans SON zamanlarda zəifləyib" narahatlığını dəstəkləyir — məhz demo/canlıya
keçəndə qarşılaşılacaq REJIM bu ola bilər.

**Diaqqnostika qeydi:** Bütün fold-larda `trade_already_taken` (gündə-bir-ticarət gate-i) ən çox rədd səbəbidir
(məs. Fold 1 val: 29155 evaluasiyadan yalnız 108 setup — strategiyanın öz dizaynı görə gözlənilir, bug deyil).

---

## Hissə 2 — Robustness Stress-Test

**Metodologiya:** `RobustnessTester(oos_bars, "USTEC", Timeframe.M5)` — ƏSL OOS pəncərəsi ilə EYNİ 29958 bar
(2026-02-05 → 2026-07-09), ki baseline ssenarisi məlum 106-ticarət/PF-1.0510 nəticəsini DƏQİQ təkrarlasın (aşağıda
təsdiqlənib). `pip_size=1.0`: USTEC bir FX cütü deyil (nöqtə ölçüsü 0.01, bu dataset-də ölçülmüş orta real
spread ~0.98 qiymət-vahidi) — "1 pip" konsepti FX-ə məxsusdur, ona görə `pip_size=1.0` ("1 nöqtə") bu
instrumentin öz orta spread miqyasına uyğun seçilib (bu, ölçmə parametri seçimidir, kod dəyişikliyi deyil).

| Ssenari | Trades | PF | Net Profit | Baseline-a nisbətən |
|---|---:|---:|---:|---:|
| **Baseline** (=OOS-un dəqiq təkrarı) | 106 | 1.0510 | +$379.24 | — |
| 3x Spread | 106 | ~1.0510 | +$379.23 | ~dəyişməz* |
| 2x Commission | 106 | 1.0510 | +$379.24 | dəyişməz** |
| **3x Slippage + 1 nöqtə** | 106 | **0.9648** | **-$259.88** | **PF 1.0 ALTINA düşür, itkiyə çevrilir** |
| 10% Təsadüfi Buraxılmış Ticarət | — | n/a*** | +$241.02 | -36.4% |
| 25% Təsadüfi Buraxılmış Ticarət | — | n/a*** | +$182.99 | -51.8% |

\* CSV-nin real per-bar spread sütunu (`candle.spread > 0`) `BacktestConfig.spread`-dən HƏMİŞƏ üstündür
(`BacktestEngine._effective_spread`), ona görə konfiqin spread-ini 3x etmək demək olar HEÇ NƏYƏ təsir etmir —
bu "robustluq" DEYİL, sadəcə bu ssenarinin USTEC-in real-spread datası üzərində praktiki olaraq heç nəyi
sınamadığını göstərir.
\*\* Orijinal OOS doğrulaması `commission=0.0` istifadə edib; 2×0=0, ona görə bu ssenari də heç nəyi sınamır.
\*\*\* `_simulate_skips()` 100 təsadüfi-buraxma simulyasiyasının ORTALAMASINI qaytarır (tək ticarət siyahısı
deyil), ona görə tək PF ədədi mənasız olardı — yalnız orta net profit/drawdown var.

**Tapıntı — yeganə MƏNALI stress ssenarisi (slippage) PF-i 1.0-ın altına salır.** Round-trip cəmi 1 əlavə
qiymət-nöqtəsi (giriş+çıxışa hərəsinə 1.0, `backtest/engine.py`-in "hər ayağa tam slippage" konvensiyasına görə)
— bu, USTEC-in öz real orta spreadi (~0.98) ilə TƏXMİNƏN eyni miqyasda, yəni "real spreadin təxminən 2 qat
ağırlaşması" səviyyəsində REALIST bir stress — artıq PF-i 1.051-dən 0.965-ə salır və net-i mənfiyə çevirir.
Bu, strategiyanın "kənar"ının (edge) nə qədər NAZİK olduğunu göstərir: PF 1.05 onsuz da zəif bir kənardır,
icra sürtünməsinə qarşı praktiki olaraq TAMPON YOXDUR.

**Tapıntı — 10%/25% təsadüfi buraxılmış ticarət** net profit-i müvafiq olaraq 36%/52% azaldır, amma mənfiyə
ÇEVİRMİR (hələ də müsbət qalır). Bu, gündə-bir-ticarət, aşağı-tezlikli strategiya olduğu üçün gözlənilən bir
həssaslıqdır (az sayda ticarətin hər biri ümumi nəticəyə nisbətən böyük çəki daşıyır).

---

## Hissə 3 — Monte Carlo Simulyasiyası

**Metodologiya:** Orijinal 106-ticarətlik OOS `BacktestResult`-u (yenidən işə salınıb, DƏQİQ eyni PF
1.0509977052755126 / net $379.23679407752854 təsdiqlənib) → `MonteCarloSimulator(n=2000).run(result, pip_size=...)`.

### Bug Tapıntısı (Hissə 4-də ətraflı) — İLK NƏTİCƏ İSTİFADƏYƏ YARARSIZ İDİ

`pip_size=1.0` (Robustness-də istifadə edilən EYNİ, USTEC-ə uyğun dəyər) ilə ilk çağırış:

| Parametr | Dəyər |
|---|---:|
| Expected Return | **-$10,000.00** |
| Worst Case Drawdown | **100%** |
| 95% CI | [$0, $0] |
| **Risk of Ruin** | **100%** |

Bu, real risk DEYİL — `MonteCarloSimulator.run()`-un `noise_cost` düsturunda hardcode edilmiş FX-standart-lot
(`* 100000.0`) fərziyyəsinin USTEC-in pozisiya-ölçüsü miqyası ilə TOQQUŞMASIdır (bax Hissə 4). Kök-səbəb
müstəqil təsdiqləndikdən sonra bu nəticə RƏDD EDİLİB, aşağıdakı TƏMİZ nəticə istifadə olunub.

### Təmiz Nəticə (`pip_size=0.0` — xalis sequence/ruin riskini təcrid edir, bax Hissə 4)

| Parametr | Dəyər |
|---|---:|
| Expected Return | **+$404.20** (+4.04%) |
| Worst Case Max Drawdown | **47.44%** |
| 95% Confidence Interval (final balans) | **$7316.15 → $13544.19** |
| **Risk of Ruin** (<30% hesab ölçüsü) | **0.00%** |
| Yeni-maksimum ehtimalı | 6.90% |

**Tapıntı — Risk of Ruin 0%, amma worst-case drawdown tail əsl tarixi nəticədən (10.08%) 4.7x GENİŞDİR.**
2000 sınaqdan HEÇ BİRİ hesabı 30%-in altına salmayıb — cari 1% risk-per-trade pozisiya-ölçüsündə "tam iflas"
narahatlığı DƏSTƏKLƏNMİR. Amma eyni 106 ticarətin sırasını təsadüfi qarışdırmaq (heç bir əlavə icra xərci
ƏLAVƏ EDİLMƏDƏN) worst-case ssenaridə 47.44% drawdown istehsal edir — bu, tək tarixi ardıcıllıqda müşahidə
olunan 10.08%-dən DƏFƏLƏRLƏ pisdir. Bu o deməkdir ki, əsl tarixi nəticə NİSBƏTƏN XOŞBƏXT ardıcıllıq ola bilər;
eyni ticarətlərin fərqli sırası ilə qat-qat pis bir yol da mümkün idi.

---

## Hissə 4 — Aşkarlanan Bug (Kod DƏYİŞDİRİLMƏYİB, Yalnız Bildirilir)

### Bug #75 — Orta-Yüksək — `research/monte_carlo.py:74` (`MonteCarloSimulator.run`)

**Problem:** `noise_cost = noise_pips * pip_size * pos_size * 100000.0` sətri (`# 1 lot = 100k standard contract
size` şərhi ilə) `pip_size`-in FX-üslublu kəsr miqyasda (~0.0001) VƏ `pos_size`-in FX-standart-lot miqyasında
olduğunu FƏRZ EDİR — heç bir instrument-fərqləndirməsi, heç bir sağlamlıq-yoxlaması yoxdur.

**Empirik təsdiq (bu sprintdə əldə edilib):** USTEC üçün `BacktestEngine`-in `SimplePositionSizer`-i
`position_size` dəyərlərini ~1.2-5.2 aralığında istehsal edir (contract-multiplier vahidləri, FX currency-unit
vahidləri DEYİL). `pip_size=1.0` (USTEC-in öz nöqtə-ölçüsünə uyğun, məqsədəuyğun seçim) ilə worst-case
`noise_cost` TƏK ticarətə **$420,000–$650,000** çıxır — $10,000 hesaba qarşı — HƏR simulyasiya sınağında ANİ,
QARANTİYALI iflasa səbəb olur (bax Hissə 3-ün "İlk Nəticə" cədvəli: Risk of Ruin 100%). Funksiyanın ÖZ
default-u (`pip_size=0.0001`) partlamanı yumşaldır (~$24/ticarət orta), amma bu ədəd HEÇ BİR real qiymət
hərəkətinə uyğun gəlmir (0.75×0.0001 = 0.000075 "nöqtə" — USTEC-in orta real spread-inin (~0.98) 13,000 dəfə
kiçiyi) — sadəcə düsturun öz konstantası ilə TƏSADÜFƏN mötəbər miqyasa düşür, konseptual olaraq düzgün deyil.

**Təsir:** Bu funksiya HAZIRDA yalnız FX cütləri üçün etibarlı nəticə verir. İndeks/metal/kripto kimi başqa bir
instrument üzərində, xüsusi diaqnoz aparılmadan, istifadəçi ya saxta "100% ruin risk" xəbərdarlığı görəcək (əgər
instrumentin öz miqyasında pip_size seçsə), ya da funksiyanın FX default-unu kor-koranə istifadə edərək
konseptual mənası olmayan, amma "ağlabatan görünən" ədədlər alacaq (bu sprintdə məhz bu baş verdi: -$2170.89
expected return, əsl bootstrap nəticəsi isə +$404.20-dır).

**Bu sprintdə tətbiq edilən iş-ətrafı (KOD DƏYİŞDİRİLMƏDƏN):** `pip_size=0.0` çağırışı `noise_cost`-u riyazi
olaraq DƏQİQ sıfıra endirir (istənilən `pip_size` üçün `noise_pips * 0.0 * pos_size * 100000.0 == 0.0`), bununla
funksiyanın ikinci sənədləşdirilmiş məqsədini (sequence risk) birincidən (execution noise) TƏMİZ TƏCRİD edir.
Bu, "sehrli ləğvedici dəyər axtarmaq" DEYİL — `pip_size=0.0` "heç bir əlavə icra küyü yoxdur" mənasında
literal, standart bir sensitivlik-analiz bazasıdır.

**Təklif olunan həll (BU SPRINTDƏ TƏTBİQ EDİLMƏYİB):** `noise_cost` düsturunu instrument-nötr et — məsələn,
`pip_size`-i birbaşa "hər ticarətə maksimum əlavə qiymət-sürüşməsi" kimi qəbul et (`* 100000.0`-sız), ya da
funksiyaya explicit `contract_size`/vahid parametri əlavə et ki, çağıran öz instrumentinin miqyasını düzgün
ötürə bilsin. Bu, AYRI bir bug-fix sprinti üçün namizəddir, Sprint 6b-nin əhatəsi XARİCİNDƏDİR.

---

## Hissə 5 — Yekun Verdikt

| Sübut | Nəticə | Qiymətləndirmə |
|---|---|---|
| Orijinal tək-bölgü OOS | PF 1.0510, 106 ticarət, +$379.24 | ✅ Müsbət, amma tək-nöqtəli sübut (zəif) |
| Walk-Forward (3 fold) | 2/3 fold güclü (val PF 1.149, 1.176); 1/3 (ƏN SON) itkili (val PF 0.927) | ⚠️ QARIŞIQ — son pəncərə narahatedicidir |
| Robustness — spread/commission | Praktiki təsir yoxdur | ➖ Nəticəsiz (real CSV spread/sıfır komissiya üstündür) |
| Robustness — slippage | PF 1.051 → 0.965, itkiyə çevrilir | 🔴 Real, əhəmiyyətli zəiflik |
| Robustness — buraxılmış ticarət | -36% / -52% net profit, amma müsbət qalır | ⚠️ Gözlənilən həssaslıq |
| Monte Carlo — Risk of Ruin | 0.00% (2000 sınaq, təmiz oxuma) | ✅ Cari pozisiya-ölçüsündə iflas narahatlığı yoxdur |
| Monte Carlo — Worst-Case Drawdown | 47.44% (tarixi 10.08%-dən 4.7x) | 🔴 Tail-risk əhəmiyyətli dərəcədə tarixi müşahidədən pisdir |
| Tooling — Monte Carlo bug | Bug #75, ayrıca bildirilib | 🔴 Alət etibarlılığı problemi (kod düzəldilməyib) |

**Verdikt: Sprint 7-yə (demo kapital) İNDİ KEÇMƏ.** Toplanmış sübut TƏK-mənalı yaşıl işıq DEYİL:

1. **Kənar (edge) nazikdir və icra sürtünməsinə qarşı tamponsuzdur** — ən sadə, realist stress (real spread
   miqyasında 1 əlavə nöqtə slippage) PF-i 1.0-ın altına salıb strategiyani mənfiyə çevirir. Demo/canlı ticarətdə
   sürüşmə, requote, latency kimi real amillər bu backtestdə istifadə olunan 0-slippage/0-commission fərziyyəsindən
   PİS olacaq — demək bu tam olaraq elə bir ssenaridir ki, bu robustness testi artıq PF<1 göstərib.
2. **Ən son walk-forward pəncərəsi itkilidir.** 2 fold-un güclü olması ürəkaçandır, amma "son 3.5 ay"ın
   itkili olması, məhz demo başlayanda qarşılaşılacaq REJIM ola biləcəyi üçün diqqətəlayiqdir.
3. **Monte Carlo-nun worst-case tail-i (47%) tarixi müşahidədən (10%) DƏFƏLƏRLƏ genişdir** — hətta heç bir
   əlavə icra xərci olmadan, sırf ticarət ardıcıllığının təsadüfi variasiyası ilə. Risk-of-ruin 0% olsa da,
   "rahat" nəticə tək-tarixi ardıcıllığın nisbətən xoşbəxt olma ehtimalını əks etdirə bilər.
4. **Doğrulama alətinin özündə (Monte Carlo) etibarlılıq problemi tapıldı** (Bug #75) — gələcək təkrar
   doğrulamalar bu düzəldilmədən manual diaqnozsuz etibarlı olmayacaq.

**Növbəti addım tövsiyəsi (bu sprintin əhatəsindən KƏNAR, YALNIZ tövsiyə):**
- Sprint 7 (demo kapital) ilə davam ETMƏZDƏN ƏVVƏL: (a) Bug #75-i düzəlt (ayrıca bug-fix sprinti), (b) ya
  pozisiya-ölçüsünü/risk-per-trade-i icra-sürtünməsi tamponu buraxacaq şəkildə YENİDƏN qiymətləndir (bu sprintin
  ƏHATƏSİNDƏ DEYİL — TUNING deyil, MEASUREMENT idi), ya da bu riski açıq gözlə QƏBUL ET.
- Sprint 6c (canlı etibarlılıq infrastrukturu — order execution, reconnect, s.) kapital riski daşımadığı üçün
  PARALEL davam edə bilər, əgər komanda infrastrukturu əvvəlcədən hazır etmək istəyirsə — amma DEMO KAPİTALIN
  ÖZÜ yuxarıdakı tapıntılar həll olunana/qəbul edilənə qədər GÖZLƏMƏLİDİR.

---

*Hesabat tarixi: 2026-07-22 · Yalnız ölçmə/doğrulama, heç bir strategiya/konfiq/production kodu dəyişdirilməyib
(Bug #75 istisna olmaqla — o da yalnız BİLDİRİLİB, düzəldilməyib) · Xam nəticələr: `artifacts/walk_forward_summary.csv`,
`artifacts/walk_forward_report.md`, `artifacts/robustness_metrics.json`, `artifacts/robustness_report.md`,
`artifacts/monte_carlo_report.md` (bu sonuncu `pip_size=0.0` təmiz oxumasını əks etdirir).*

---

## Əlavə (2026-07-22, Bug #75 düzəldildikdən sonra) — Korrigə Edilmiş Monte Carlo + Slippage Kövrəkliyi Araşdırması

**Əhatə:** Bug #75 ayrıca bir sprintdə DÜZƏLDİLİB (`research/monte_carlo.py`-dən `* 100000.0` konstantası
tamamilə çıxarılıb — instrument-spesifik parametr ƏLAVƏ EDİLMƏYİB, çünki empirik yoxlama göstərdi ki, bu
konstanta HEÇ BİR instrument üçün — FX daxil olmaqla — düzgün olmayıb, bax bug-fix hesabatı). Bu əlavə iki
şeyi əhatə edir: (1) düzəldilmiş düsturla YENİDƏN işə salınmış Monte Carlo, (2) niyə strategiya slippage-ə bu
qədər həssasdır sualının kök-səbəb araşdırması. **Walk-forward TƏKRAR işə salınmayıb** (Hissə 1-in nəticələri
Bug #75-dən təsirlənməyib və dəyişməz qalır).

### Ə.1 — Korrigə Edilmiş Monte Carlo Nəticəsi

**Metodologiya:** EYNİ 106-ticarətlik `BacktestResult` (Hissə 3-dəki ilə eyni OOS backtest — YENİDƏN
generasiya edilməyib, mövcud nəticə təkrar istifadə olunub) → `MonteCarloSimulator(n=2000).run(result,
pip_size=1.0)`, düzəldilmiş düsturla (`noise_cost = noise_pips * pip_size * pos_size`, `* 100000.0`-sız).

| Parametr | Sprint 6b — Bug-lu (`pip_size=1.0`) | **İNDİ — Düzəldilmiş (`pip_size=1.0`)** |
|---|---:|---:|
| Expected Return | -$10,000.00 (-100%) | **+$161.60** (+1.62%) |
| Worst Case Max Drawdown | 100% | **52.54%** |
| 95% Confidence Interval | [$0, $0] | **[$7239.24, $13248.23]** |
| **Risk of Ruin** | **100%** | **0.00%** |
| Yeni-maksimum ehtimalı | 0% | 4.45% |

**Nə dəyişdi və niyə (sadə dillə):** Köhnə nəticə real risk DEYİLDİ — düsturun özündəki `* 100000.0`
konstantası hər ticarətə $420,000–$650,000 "saxta" itki əlavə edirdi (bax əvvəlki bug hesabatı), bu da $10,000
hesabı HƏR simulyasiya sınağında ANİNDƏN sıfırlayırdı, strategiyanın əsl ticarətlərindən TAMAMİLƏ ASILI
OLMAYARAQ. Düzəliş bu saxta konstantanı çıxarıb, `noise_cost`-u REAL P&L-in hesablandığı EYNİ qaydaya
(`pos_size`-ın özü artıq "qiymət-hərəkəti başına dollar" vahidində olduğuna) uyğunlaşdırıb. Nəticədə indi
`pip_size=1.0` (USTEC-in öz nöqtə-ölçüsünə uyğun) HƏQİQƏTƏN kiçik, mötəbər əlavə icra-küyü (~$1-5/ticarət)
təmsil edir — 100,000x SUNİ şişirdilmə deyil.

**Qeyd (metodoloji):** `MonteCarloSimulator` toxunulmamış təsadüfi ədədlər (`random`/`np.random`, sabit seed
YOXDUR) istifadə edir, ona görə n=2000 sınaqlı hər müstəqil çağırış bir qədər FƏRQLİ ədədlər verəcək (bu
sprintin öz yoxlama mərhələsində eyni parametrlərlə edilmiş əlavə çağırış +$78.69 Expected Return, 55.13%
Worst Drawdown vermişdi — yuxarıdakı cədvəldəki ilə YAXIN, amma eyni deyil). KEYFİYYƏT nəticəsi (Risk of Ruin
0%, Expected Return müsbət, worst-case drawdown-un tarixi 10.08%-dən xeyli geniş olması) BÜTÜN müstəqil
çağırışlarda TUTARLIDIR.

### Ə.2 — Slippage Kövrəkliyi: Ticarət-Səviyyəli Araşdırma

**Metodologiya:** `RobustnessTester`-in `high_slippage` ssenarisini (`slippage=1.0`) EYNİ OOS data üzərində
baseline (`slippage=0.0`) ilə YAN-YANA işə saldım, hər ticarəti (giriş-vaxtı + istiqamət açarı ilə, çünki
`setup_id` hər çağırışda təsadüfi UUID daşıyır) uyğunlaşdırdım və PnL fərqini (`delta`) hesabladım.

**Tapıntı 1 — Bu strategiyada "marginal/sərhəd" ticarət demək olar YOXDUR.** Sabit `risk_reward=2.0` və 1%
risk-ölçüləndirməsi ilə, HƏR ticarət ya təmiz ~2R qazanc, ya da təmiz ~1R itkidir:

| | n | Min | Maks | Orta | Std Sapma |
|---|---:|---:|---:|---:|---:|
| Qazanclar | 38 | $188.91 | $217.97 | **$205.67** | $6.98 |
| İtkilər | 68 | -$116.21 | -$100.14 | **-$109.36** | $3.40 |

"Marginal" ticarətlər (\|baseline pnl\| < $20): **0 / 106**. Bu, sualı ("konsentrasiya olunmuş, yoxsa
sistemik?") artıq qismən CAVABLANDIRIR — konsentrasiya olacaq AYRICA "sərhəd" ticarət qrupu strukturca YOXDUR.

**Tapıntı 2 — Deqradasiya SİSTEMİKDİR, konkret bir neçə ticarətə KONSENTRASİYA OLUNMAYIB.** Slippage stress-i
HƏR 38 qazancın hər birini ORTA HESABLA $18.45 azaldır (ən pisi -$27.82, ən az təsirlisi hələ də mənfi) —
BÜTÜN qazanclar bu təsirə məruz qalır, 1-2 "bədbəxt" ticarətə deyil:

| Delta aralığı | Ticarət sayı |
|---|---:|
| ≥ $0 (itkilər, çox az yaxşılaşıb) | 49 |
| $0 → -$3 | 15 |
| -$3 → -$6 | 4 |
| -$10 → -$20 | 25 |
| < -$20 | 13 |

38 qazancın 38-i də -$10-dan pis bir delta alıb (25+13=38) — YƏNİ HAMISI. Maraqlı yan-effekt: itki
ticarətləri slippage altında ORTA HESABLA $0.91 YAXŞILAŞIR (ikinci dərəcəli pozisiya-ölçüləndirmə effekti:
giriş qiyməti slippage ilə pisləşdikdə giriş-SL məsafəsi artır, bu da `SimplePositionSizer`-i həmin ticarət
üçün bir qədər KİÇİK pozisiya seçməyə məcbur edir, itkini qismən yumşaldır — bu, bug deyil, sırf riyazi
əlaqədir). **Heç bir tək ticarət fərdi olaraq qazancdan itkiyə ÇEVRİLMİR** (38 qazancın hamısı ~$180-220
aralığında qalır, slippage-in maksimum ~$28-lik təsiri bunları fərdi olaraq mənfiyə salmaq üçün KİFAYƏT
DEYİL) — portfel-səviyyəli PF/net-profit "çevrilməsi" TOPLU effektdir: 38 qazancın CƏMİ marjı -$701.14
azalır, 68 itkinin CƏMİ +$62.03 yumşalır, xalis -$639.11 — bu, orijinal +$379.24 kənarını RAHATLIQLA aşaraq
onu -$259.88-ə çevirir.

**Tapıntı 3 — Orta ticarət kənarı slippage miqyası ilə necə müqayisə olunur.** Orta baseline pnl/ticarət =
**$3.58** (379.24/106) — bu, strategiyanın XALIS, portfel-üzrə orta "kənarı"dır. Portfel-üzrə orta slippage
xərci/ticarət = **$6.03** — artıq TƏKBAŞINA orta kənardan 1.7 dəfə BÖYÜKDÜR. Daha dəqiq desək: strategiyanın
BÜTÜN kənarı yalnız 38 qazancdan gəlir (itkilər sabit ~1R-dir, təbiətən "kənar" daşımır) — bu 38 qazancın
FƏRDI ölçüsünə nisbətən slippage xərci (~$18.45/qazanc) kiçikdir (~9%), AMMA strategiyanın ÜMUMI xalis
mənfəəti ($379.24) yalnız 38 qazancın CƏM slippage-xərcinin ($701.14) YARISINDAN AZ-dır. Başqa sözlə:
**strategiyanın kənarı öz BRUTTO mənfəət marjına nisbətdə DEYİL, öz XALIS (netto) marjına nisbətdə nazikdir**
— brutto qazanclar özləri kifayət qədər böyükdür (~$206 hər biri), amma aşağı Win Rate (35.85%) səbəbindən
onların SAYI az olduğundan, kiçik-görünən per-trade icra xərci CƏM halda bütün xalis kənarı udur.

**Yekun xarakteristika:** Bu, "bir neçə şanssız/sərhəd ticarətin fraciyalliyi" DEYİL — bu, STRUKTURAL,
SİSTEMİK bir zəiflikdir: strategiyanın sabit-R dizaynı səbəbindən GƏLƏCƏK İSTƏNİLƏN ticarət də EYNİ proporsional
slippage-vergisinə məruz qalacaq (çünki hər ticarət təbiətən eyni ~2R/1R formatındadır). Filtrasiya, seçim və
ya "pis ticarətləri kənarlaşdırma" ilə HƏLL OLUNA BİLMƏZ — kənarın özü icra-xərclərinə nisbətdə strukturca
nazikdir.

### Ə.3 — Yenilənmiş Yekun Verdikt

Bug #75 düzəldikdən sonra Monte Carlo artıq saxta 100% ruin siqnalı vermir (bu ürəkaçandır), AMMA bu, əvvəlki
narahatlığı LƏĞV ETMİR — əksinə, slippage-kövrəkliyi araşdırması əvvəlki tapıntını DAHA da DƏQİQLƏŞDİRİB VƏ
GÜCLƏNDİRİB:

| Yeni/Yenilənmiş Sübut | Nəticə | Qiymətləndirmə |
|---|---|---|
| Monte Carlo (düzəldilmiş, `pip_size=1.0`) | Risk of Ruin 0.00%, Expected Return +$161.60, worst-case DD 52.54% | ✅ Saxta 100%-ruin ARADAN QALXDI; real profil əvvəlki "təmiz" oxumaya (Hissə 3) keyfiyyətcə UYĞUNDUR |
| Slippage — marginal ticarət varlığı | 0/106 "sərhəd" ticarət — hamısı təmiz ~2R/~1R | 🔴 Konsentrasiya YOXDUR, çünki konsentrasiya olacaq HEDƏF strukturca yoxdur |
| Slippage — deqradasiyanın paylanması | 38 qazancın 38-i də sistemik təsirlənib (-$10-dan pis) | 🔴 SİSTEMİK, gələcək istənilən ticarətə tətbiq olunacaq |
| Kənar vs slippage miqyası | Orta kənar $3.58 < orta slippage-xərci $6.03; 38 qazancın CƏM slippage-xərci ($701) ÖZÜ xalis mənfəətdən (∼$379) 2x böyükdür | 🔴 Kənar öz icra-xərclərinə nisbətdə STRUKTURCA nazikdir |

**Yenilənmiş Verdikt: Sprint 7-yə (demo kapital) HƏLƏ DƏ KEÇMƏ — indi DAHA GÜCLÜ əsasla.** Bug #75-in
düzəldilməsi Monte Carlo-nun ALƏT kimi etibarlılığını bərpa etdi (bu özü müsbətdir), amma DÜZGÜN ölçülmüş
nəticələr strategiyanın əsl profilini DƏYİŞMİR — sadəcə onu SAXTA alarmdan AYIRIR. Slippage-kövrəkliyi
araşdırması göstərir ki, bu, TƏSADÜFİ və ya "bir neçə pis ticarətdən qaynaqlanan" bir problem deyil — bu,
strategiyanın öz DİZAYNINDAN (aşağı Win Rate + sabit-R format + hər qazanca bərabər proporsional icra-xərci)
irəli gələn STRUKTURAL bir zəiflikdir. **Bu, "tuning" TƏLƏB EDƏN bir tapıntıdır (bu sprintin əhatəsindən
KƏNAR): strategiyanın kənarını real icra-xərclərinə nisbətdə YENİDƏN qiymətləndirmək** (məs. daha az tezlikli,
daha böyük-kənarlı setup-lara doğru filtr sərtləşdirmək, ya da broker/icra keyfiyyətini USTEC-in öz spread
miqyasından DAHA YAXŞI əldə etmək) **Sprint 7-dən ƏVVƏL həll edilməli məsələdir, sadəcə "riski qəbul et" ilə
keçilə bilməz** — çünki risk BURADA "bəzən pis olar" deyil, "hər dəfə eyni proporsional xərci daşıyar"
formatındadır.

Sprint 6c (canlı etibarlılıq infrastrukturu) hələ də kapital riski daşımadığı üçün paralel davam edə bilər.
Demo kapitalın (Sprint 7) özü isə yuxarıdakı struktural kənar-vs-icra-xərci məsələsi HƏLL OLUNANA qədər
GÖZLƏMƏLİDİR.

---

*Əlavə tarixi: 2026-07-22 · Yalnız ölçmə/araşdırma, heç bir production kodu (strategy/*, backtest/*,
execution/*, research/*) dəyişdirilməyib · Walk-forward TƏKRAR işə salınmayıb (Hissə 1 dəyişməz qalır) ·
Mövcud 106-ticarətlik `BacktestResult` YENİDƏN generasiya edilmədən təkrar istifadə olunub.*
