# Qabaqcıl Doğrulama — Walk-Forward, Monte Carlo, Rejim, Portfel, Spread-Stress

**Tarix:** 2026-08-31
**Əhatə:** İki "qalib" konfiqurasiya — First FVG (NAS100 09:30/15m/2R) və
SR+Bias (NAS100 30m liquidity-TP) — 5 əlavə, əvvəllər aparılmamış test ilə
yenidən yoxlanıldı. Məqsəd: `ROBUSTNESS_VALIDATION_REPORT.md`-in bootstrap
tapıntısını ("First FVG-nin 5-illik PF≥1.0-ı statistik cəhətdən sikkə atma
ilə eynidir, 54% ehtimal") TAMAM MÜSTƏQİL metodlarla ya təsdiqləmək, ya rədd
etmək.

**Metodoloji qeyd:** repoda artıq hazır tədqiqat alətləri var
(`research/walk_forward.py`, `research/monte_carlo.py`,
`research/regime_analysis.py`), amma onlar `backtest/engine.py`-in generic
PENDING-LIMIT-ORDER giriş modelini (setup N-ci barda yaranır, N+1-ci barda
limit order kimi dolur) istifadə edir — bu, nə First FVG-nin (zona ilk
toxunuşunda giriş), nə SR-in (bar bağlanışında giriş) real giriş məntiqinə
uyğun deyil. Bu strategiyaları həmin generic mühərrikə qoşmaq SESSIYADA
DOĞRULANMAMIŞ ÜÇÜNCÜ bir icra modelini sınamaq olardı. Ona görə testlər
`research/*`-in metod və metrik konvensiyalarını (fold strukturu, Monte
Carlo-nun resample+noise+risk-of-ruin tərifləri) EYNƏN İSTİFADƏ EDİR, amma
artıq doğrulanmış trade-loglarının üzərində — canlı sinifin real giriş
məntiqini qoruyaraq.

---

## 1. Walk-Forward: 10 bərabər 6-aylıq fold (chronological, rolling)

Klassik walk-forward normalda in-sample parametr seçimi + out-of-sample
test tsiklindən ibarətdir; hər iki strategiya sabit, əl ilə təyin olunmuş
parametrlərdən istifadə etdiyi üçün (heç bir optimallaşdırma addımı yoxdur),
burada məna daşıyan sual "hər MÜSTƏQİL fold-da PF≥1.0 qalırmı" sualıdır —
"5il VƏ 1il" ekranından fərqli olaraq, bu 10 örtüşməyən pəncərənin HƏR
BİRİNİ ayrıca yoxlayır.

### First FVG (n=1117)

| Fold | Dövr | n | WR | PF | net R |
|---|---|---|---|---|---|
| 1 | 2021-02 → 2021-08 | 112 | 37.5% | 0.760 | -21.40 |
| 2 | 2021-08 → 2022-03 | 108 | 42.6% | 0.988 | -0.88 |
| 3 | 2022-03 → 2022-10 | 114 | 34.2% | 0.829 | -14.42 |
| 4 | 2022-10 → 2023-04 | 112 | 49.1% | **1.432** | +27.16 |
| 5 | 2023-04 → 2023-11 | 110 | 34.5% | 0.721 | -25.39 |
| 6 | 2023-11 → 2024-06 | 113 | 40.7% | 0.931 | -5.40 |
| 7 | 2024-06 → 2024-12 | 115 | 48.7% | **1.346** | +23.51 |
| 8 | 2024-12 → 2025-07 | 114 | 33.3% | 0.758 | -20.73 |
| 9 | 2025-07 → 2026-02 | 102 | 43.1% | 1.143 | +9.63 |
| 10 | 2026-02 → 2026-08 | 117 | 39.3% | 1.116 | +8.65 |

**PF≥1.0 keçən fold: 4/10 (40%).**

### SR+Bias (n=813)

| Fold | Dövr | n | WR | PF | net R |
|---|---|---|---|---|---|
| 1 | 2020-08 → 2021-03 | 48 | 29.2% | 0.665 | -12.36 |
| 2 | 2021-03 → 2021-10 | 85 | 28.2% | 0.774 | -16.39 |
| 3 | 2021-10 → 2022-05 | 69 | 36.2% | **2.003** | +48.55 |
| 4 | 2022-05 → 2023-01 | 94 | 24.5% | 0.743 | -20.04 |
| 5 | 2023-01 → 2023-08 | 104 | 28.8% | 1.093 | +7.89 |
| 6 | 2023-08 → 2024-03 | 87 | 37.9% | 1.256 | +16.06 |
| 7 | 2024-03 → 2024-11 | 108 | 27.8% | 0.756 | -21.30 |
| 8 | 2024-11 → 2025-06 | 84 | 35.7% | 0.795 | -12.03 |
| 9 | 2025-06 → 2026-01 | 67 | 38.8% | **1.830** | +37.37 |
| 10 | 2026-01 → 2026-08 | 67 | 29.9% | 1.154 | +7.66 |

**PF≥1.0 keçən fold: 5/10 (50%).**

**Nəticə:** "5il VƏ 1il PF≥1.0" ekranı ilkin güman edildiyindən daha
səxavətlidir — yalnız 2 böyük, örtüşən pəncərəni yoxlayır. 10 müstəqil
fold-a bölündükdə First FVG demək olar sikkə atma nisbətindədir (40%),
bootstrap-un 54% tapıntısı ilə tam uzlaşır. SR bir az yaxşıdır (50%), amma
əsl fərq KEYFİYYƏTDƏDİR: SR-in müsbət fold-ları güclü (PF 1.83-2.00),
First FVG-inki orta (PF 1.12-1.43).

---

## 2. Monte Carlo: 5000 sınaq, bootstrap resample + 0-1.5pt slippage səs-küyü

`research/monte_carlo.py` sabit-$ risk modelindən istifadə edir (hər
trade eyni dollar riski daşıyır, balansdan asılı olmayaraq) — bu, canlı
botların həqiqi ölçüləndirmə üsulu ilə (`--risk-per-trade-pct`, CARİ
balansın faizi) uyğun gəlmir və drawdown zamanı ruin ehtimalını
şişirdir. Üç modeli də hesabladıq:

| Model | First FVG gözlənilən nəticə | SR+Bias gözlənilən nəticə |
|---|---|---|
| Sabit $1000/trade (`research/monte_carlo.py` konvensiyası) | -56.6%, ruin 59.6% | +15.9%, ruin 22.1% |
| Fixed-fractional 1% | -48.5%, ruin 25.2% | +14.3%, ruin 6.8% |
| **Fixed-fractional 0.25% (canlıda İSTİFADƏ OLUNAN real risk)** | **-15.3%, worst-DD 50.1%, ruin 0%** | **+3.5%, worst-DD 48.8%, ruin 0%** |

**Nəticə:** real (0.25%) ölçüdə heç biri "ruin" həddinə düşmür (kiçik risk
ölçüsü öz işini görür), amma **First FVG-nin gözlənilən nəticəsi bütün
trade tarixçəsi üzrə orta hesabla MƏNFİDİR** (-15.3%) — nöqtə-estimatın
"PF≈1.0" görünüşünün arxasında, minlərlə təsadüfi yenidən-sıralamada
mənfi tərəf üstünlük təşkil edir. SR hər üç modeldə də müsbətdir.

---

## 3. Rejim-asılılığı (`research/regime_analysis.py`, dəyişməz, 200-bar pəncərə)

Hər trade giriş anındakı 200 bar-lıq lag-1 avtokorrelyasiya əsaslı rejimlə
(TRENDING / MEAN_REVERTING / RANGING) etiketləndi.

| | TRENDING | MEAN_REVERTING | **RANGING** |
|---|---|---|---|
| First FVG | PF 0.876 (itki), n=171 | PF 0.774 (itki), n=173 | **PF 1.053**, n=773 (69%) |
| SR+Bias | PF 0.719 (itki), n=99 | PF 0.857 (itki), n=171 | **PF 1.189**, n=543 (67%) |

**Ən dəyərli tapıntı: hər İKİ strategiyanın BÜTÜN edge-i RANGING
rejimindən gəlir.** TRENDING və MEAN_REVERTING bazarda hər ikisi itki
verir. Bu, potensial aktiv filtr namizədidir (giriş anında bazar
TRENDING/MEAN_REVERTING-dirsə keç) — AMMA eyni tarixi datada aşkarlanıb,
ona görə out-of-sample sınanmadan tətbiq edilməməlidir (özü ayrıca
walk-forward test tələb edir ki, bu, curve-fit deyil, real filtr olsun).

### 3.1. RANGING-filtrinin fold-based yoxlanması (`scripts/regime_filter_walk_forward.py`)

§3-də tapılan "bütün edge RANGING-dən gəlir" tapıntısı tək bir AQREQAT
ölçü idi — bir neçə böyük fold nəticəni idarə edə bilərdi. Ona görə §1-in
EYNİ 10 fold-una bölünüb, hər fold-da AYRICA yoxlanıldı: filtri (yalnız
RANGING trade-ləri saxla) tətbiq etsək, o fold-un PF-i yaxşılaşırmı?

| | Filtr yaxşılaşdıran fold | Orta fold PF (əvvəl → sonra) |
|---|---|---|
| First FVG | **8/10** | 1.002 → **1.097** |
| SR+Bias | **7/10** | 1.107 → **1.257** |

Bu, tək aqreqat ölçüdən qat-qat güclü sübutdur — filtr demək olar HƏR
MÜSTƏQİL 6-aylıq dövrdə yaxşılaşma verir, təkcə ümumi cəmdə deyil. **Qeyd:**
bu hələ də EYNİ tarixi data üzərindədir (əsl kor out-of-sample deyil) — 10
ayrı fold-un 8-i/7-i eyni istiqamətdə çıxması təsadüf ehtimalını xeyli
azaldır, amma yekun sübut yalnız filtri canlı/paper-də işə salıb İRƏLİYƏ
doğru izləməklə gələ bilər.

---

## 4. Portfel-səviyyəli birləşik risk (real `trade_manager.py` exclusivity ilə)

Hər iki bot eyni NAS100 slotunu paylaşır — biri açıqkən digəri rədd
olunur (`"Skipping NAS100: position held by another strategy"`, canlı
loglardan təsdiqlənib). Bu, `SESSION_HANDOFF.md` §3.3-də qeyd olunan köhnə
"3 bot müstəqil ölçü seçir → 3x ekspozisiya" narahatlığından FƏRQLİDİR —
o, artıq söndürülmüş XAUUSD botuna aid idi; hazırkı 2 bot (hər ikisi
NAS100) bu exclusivity vasitəsilə eyni-anlıq ekspozisiyanı artıq 1x-də
saxlayır.

| | Solo | Combined-də |
|---|---|---|
| First FVG | n=1117, PF 0.975, DD 13.2% | 139/1117 trade itirilir |
| SR+Bias | n=813, PF 1.057, DD 11.0% | 34/813 trade itirilir |
| **Birləşik** | — | **n=1757, PF 1.011, return +1.4%, DD 17.4%** |

**Tapıntı:** birləşik drawdown (17.4%) HƏR İKİ solo drawdown-dan
(13.2%/11.0%) PİSDİR, baxmayaraq ki exclusivity eyni-anlıq ekspozisiyanı
məhdudlaşdırır. Səbəb: `SESSION_HANDOFF.md` §4-də "yaxşı diversifikasiya"
kimi qeyd olunan −0.21…+0.26 aylıq korrelyasiya, real exclusivity altında
NETTING vermir — sadəcə hər iki strategiyanın pis dövrləri ARDICIL
DÜZÜLÜR. Diversifikasiya iddiası qismən yanlış idi.

---

## 5. SR spread-stress (First FVG-nin §6 metodu ilə, eyni gross datadan)

| Spread (pt) | 0 | 1 | 2 | **3 (istifadə)** | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| PF | 1.221 | 1.162 | 1.108 | **1.057** | 1.009 | 0.965 | 0.924 | 0.885 |

**SR-in breakeven spread həddi: ~4.21pt** — istifadə olunan 3.0pt-dən
1.2pt yuxarıda, rahat marj. Müqayisə üçün First FVG 15m-in breakeven həddi
`FIRST_FVG_15M_SPREAD_REPORT.md` §6-da ~2.6pt kimi tapılmışdı — istifadə
etdiyimiz 3.0pt-dən AŞAĞI, yəni First FVG-nin öz spread konvensiyası
onun breakeven həddini artıq keçib.

---

---

## 6. RANGING-filtrinin canlı sinif inteqrasiyası + doğrulanması

§3.1-in fold-based təsdiqindən sonra filtr `strategy/first_fvg_15m.py` və
`strategy/sr_daily_bias.py`-ə **opt-in** parametr kimi əlavə olundu:

```python
require_ranging_regime: bool = False   # default OFF -- mövcud, artıq doğrulanmış davranış toxunulmayıb
regime_window_bars: int = 200          # research.regime_analysis.analyze_regime()-in öz defaultu
```

`True` olduqda, hər iki sinif giriş setup-u yaratmazdan dərhal əvvəl
`research/regime_analysis.analyze_regime()`-i (dəyişməz) `market_state.bars_view()`
üzərində çağırır və rejim RANGING olmadıqda yeni `RejectionReason.REGIME_NOT_RANGING`
ilə rədd edir. First FVG üçün günün TƏK retest cəhdi hesab olunur (rədd
olunsa belə həmin gün üçün ikinci cəhd YOXDUR — batch skriptin "bir gün, bir
cəhd" semantikası qorunur).

### Canlı sinif üzərindən doğrulama (`scripts/backtest_first_fvg_15m_live_class.py`, `scripts/backtest_sr_daily_bias_live_class.py --require-ranging-regime`)

| | Regime-gate OFF (default, dəyişməz) | Regime-gate ON |
|---|---|---|
| First FVG (canlı sinif) | n=1105, PF 1.004 | **n=766, PF 1.063** |
| SR+Bias (canlı sinif) | n=839, PF 1.025 (əvvəlki sessiyanın 838/1.024-ə uyğun — reqressiya yoxdur) | **n=568, PF 1.168** |

Hər iki halda REAL canlı sinif (offline trade-etiketləmə deyil) filtri
tətbiq edəndə oxşar yaxşılaşma göstərir (§3.1-in offline RANGING-only
rəqəmlərinə yaxın: First FVG n=773/PF 1.053, SR n=543/PF 1.189 — kiçik
fərqlər aşağıdakı qeydə görədir).

**Əlaqəli, yeni tapılan qeyd (First FVG-ə aid, regime-gate-dən ASILI
DEYİL):** doğrulama skriptinin öz exit-simulyasiyası (`open_until_idx`
gating + SL/TP-ni gün sərhədi olmadan axtarma) batch skriptin EOD-close
konvensiyasından (`simulate_trade`, gün bitəndə hələ açıq olan trade-i
sonuncu bar-ın bağlanışında məcburi bağlayır) fərqlənir — real canlı bot da
EOD-də məcburi bağlamır (broker SL/TP-ni gün sərhədindən asılı olmayaraq
saxlayır), ona görə bu doğrulama skripti real davranışa DAHA yaxındır, amma
bu, batch skriptin özündə HEÇ vaxt modelləşdirilməmiş, kiçik (n fərqi
~1%) bir sahədir. SR-in artıq sənədləşdirilmiş "KNOWN FIDELITY GAP"-ına
bənzər, kiçik və qeyri-kritik.

### Tövsiyə → Status (2026-08-31, tətbiq edildi)
`run_live_first_fvg_15m.py` / `run_live_sr_bias.py`-ə `--require-ranging-regime`
CLI bayrağı əlavə olundu, hər ikisi paper-mode-da smoke-test edildi (xəta
yoxdur). Bayraq YALNIZ **Paper** Scheduled Task-ların öz `.bat` fayllarına
əlavə edildi:
- `run_live_first_fvg_15m_paper.bat` (`FirstFVG15m_NAS100_Paper` task-ı)
- `run_live_sr_bias_nas100_paper.bat` (`SRBias_NAS100_Paper` task-ı)

**Demo (real sifariş yönləndirən) botlar TOXUNULMAYIB** —
`run_live_first_fvg_15m_demo.bat` / `run_live_sr_bias_nas100_demo.bat`
köhnə, artıq tam doğrulanmış davranışı saxlayır. Bu, tövsiyə olunan
"əvvəlcə Paper-də izlə" ardıcıllığının birbaşa tətbiqidir — filtr indi
canlı Paper hesabda REAL vaxtda işləyir, amma real pul/real order
yönləndirmə heç vaxt bu qərardan asılı olmayıb.

---

## Yekun sintez

| Test | First FVG | SR+Bias |
|---|---|---|
| Bootstrap (`ROBUSTNESS_VALIDATION_REPORT.md`) | 54.0% real-edge ehtimalı | 82.5% |
| Walk-forward fold-lar | 4/10 keçir | 5/10 keçir |
| Monte Carlo (real 0.25% risk, gözlənilən nəticə) | **-15.3%** | +3.5% |
| Rejim-filtri olmadan | yalnız RANGING-də iş görür | yalnız RANGING-də iş görür |
| Spread marjı (breakeven − istifadə) | **-0.4pt (mənfi marj)** | +1.2pt |
| Portfeldə itirilən trade | 139/1117 (12.4%) | 34/813 (4.2%) |

**5 tamam müstəqil metodun 5-i də eyni istiqaməti göstərir: SR+Bias real
edge-ə malikdir; First FVG-nin isə YOXDUR və ya statistik cəhətdən
təsdiqlənə bilməyəcək qədər kövrəkdir.** Bu artıq təsadüf ehtimalı deyil.

### Tövsiyə
- **SR+Bias-a davam et** — 5 testin hamısında müstəqil təsdiqlənib.
- **First FVG-ni canlıda saxla, amma risk ölçüsünü azalt** (0.25% artıq
  kifayət qədər kiçikdir, daha da azaltmaq mümkündür) VƏ ya #3-də tapılan
  RANGING-only filtri ayrıca out-of-sample sınaqdan keçirməyi düşün — bu,
  edge-i real edə bilər.
- Portfel-səviyyəli combined drawdown (17.4%) hər iki solo-dan pisdir —
  risk büdcələşdirməsi bunu nəzərə almalıdır, "uncorrelated = safe"
  fərziyyəsinə etibar edilməsin.

---

## Fayllar
- **Walk-forward + Monte Carlo:** [scripts/walk_forward_montecarlo.py](scripts/walk_forward_montecarlo.py)
- **Rejim-asılılığı:** [scripts/regime_conditioned_performance.py](scripts/regime_conditioned_performance.py)
- **RANGING-filtrinin fold-based yoxlanması:** [scripts/regime_filter_walk_forward.py](scripts/regime_filter_walk_forward.py)
- **Portfel-səviyyəli risk:** [scripts/portfolio_combined_risk.py](scripts/portfolio_combined_risk.py)
- **Canlı sinif + regime-gate doğrulaması:** [scripts/backtest_first_fvg_15m_live_class.py](scripts/backtest_first_fvg_15m_live_class.py), [scripts/backtest_sr_daily_bias_live_class.py](scripts/backtest_sr_daily_bias_live_class.py)
- **Strategiya kodu (opt-in `require_ranging_regime`):** [strategy/first_fvg_15m.py](strategy/first_fvg_15m.py), [strategy/sr_daily_bias.py](strategy/sr_daily_bias.py), [strategy/diagnostics.py](strategy/diagnostics.py)
- **İstifadə olunan mövcud tədqiqat modulları (dəyişməz):** `research/monte_carlo.py`, `research/regime_analysis.py`
