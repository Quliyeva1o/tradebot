# Bootstrap / Monte Carlo Doğrulama — İki Qalib Strategiya

**Tarix:** 2026-08-28
**Metod:** hər iki strategiyanın SPREAD-Lİ (net) R-multiple trade tarixçəsi
5000 dəfə bootstrap resample edilib (`scripts/robustness_winners.py`,
`scripts.robustness_analysis`-in mövcud `bootstrap()`/`recency_split()`
funksiyalarını dəyişmədən istifadə edir). Bu, tək bir PF rəqəminin (məs.
"PF 1.01") NƏ QƏDƏR etibarlı olduğunu göstərir — eyni tarixi datadan minlərlə
təsadüfi yenidən-nümunə çəkərək PF-in NECƏ dağıldığını ölçür.

---

## ⚠️ Əsas tapıntı: First FVG-in "PF 1.01" göründüyündən qat-qat az etibarlıdır

| Strategiya | Pəncərə | n | PF (nöqtə) | Bootstrap median | 90% CI | **P(real PF>1)** |
|---|---|---|---|---|---|---|
| **First FVG** | 5 il | 1000 | 1.00 | 1.01 | [0.90, 1.12] | **54.0%** |
| First FVG | 1 il | 197 | 1.14 | 1.14 | [0.89, 1.45] | 80.9% |
| **SR+Bias** | 5 il | 696 | 1.11 | 1.11 | [0.92, 1.33] | **82.5%** |
| SR+Bias | 1 il | 112 | 1.53 | 1.50 | [0.90, 2.37] | 91.1% |

**First FVG-in 5-illik PF≥1.0 nəticəsi statistik cəhətdən demək olar sikkə
atmaqla eynidir (54% — 46%).** 90%-lik etibar intervalı [0.90, 1.12]
1.0-ı ORTADA keçir — yəni bu 1000 trade-lik nümunədən əldə edilə bilən
məlumatla, real edge-in müsbət OLDUĞUNU inamla demək olmaz.

**SR+Bias xeyli daha etibarlıdır** (82.5% ehtimal ki, real PF>1.0-dır) —
eyni "5il/1il pəncərəsində PF≥1.0" testini keçsələr də, ikisi arasında
böyük etibarlılıq fərqi var.

## Recency split (xronoloji 80/20)

| Strategiya | İlk 80% | Son 20% |
|---|---|---|
| First FVG | n=892, PF **0.95** (itki) | n=224, PF 1.08 |
| SR+Bias | n=648, PF **0.99** (demək olar breakeven) | n=163, PF **1.36** |

Hər ikisində son 20% ilk 80%-dən yaxşıdır (təşviqedici — "getdikcə
pisləşən" strategiya deyil), amma SR+Bias-ın yaxşılaşması (0.99→1.36) daha
kəskin və inandırıcıdır, First FVG-inki (0.95→1.08) daha zəifdir.

## Şərh: niyə eyni "PF≥1.0" iki fərqli inam səviyyəsi verir?

PF-in bootstrap-dakı dağılma genişliyi əsasən **winrate + R-multiple
paylanmasının forması**ndan asılıdır, təkcə n-dən yox. First FVG-də 2R fixed
hədəf + ~40% winrate kombinasiyası nəticəni hər tək trade-ə görə daha
"kövrək" edir (bir neçə əlavə uduş/itki nəticəni asanlıqla 1.0-ın hər iki
tərəfinə keçirə bilər). SR+Bias-ın liquidity-based (dəyişkən R) hədəfi və
bir qədər fərqli R-paylanması daha "sabit" bir orta nəticə verir.

## Tövsiyə

- **SR+Bias-a (NAS100 30m liquidity-TP) daha çox etibar edilə bilər** —
  82.5% real-edge ehtimalı, canlıda davam etməyə dəyər.
- **First FVG (09:30+15m+2R) hələ də ən yaxşı FVG variantıdır** (digər 7
  kombinasiyanın hamısından üstündür), amma onun "qazanclı" olması
  statistik cəhətdən TƏSDİQ OLUNMAYIB — 54% ehtimal ilə sadəcə şansdan
  fərqlənmir. Paper/kiçik-risk canlı işləməyə davam edilsin, amma risk
  ölçüsü SR-dən DAHA MÜHAFİZƏKAR saxlanılmalıdır (artıq 0.25% seçilib —
  bu münasibdir, artırılmasın).
- Hər iki strategiya üçün 6-12 ay sonra bu bootstrap analizini təkrarlamaq
  faydalı olar — nümunə böyüdükcə confidence interval daralacaq.

---

## Fayllar
- **Script:** [scripts/robustness_winners.py](scripts/robustness_winners.py)
- **Data mənbəyi:** `artifacts/first_fvg_15m_spread_0930_all.csv`,
  `artifacts/sr_sweep_NAS100_30m_liquidity_trades.csv` (hər ikisi artıq
  spread-net R-multiple daşıyır)

---

## SR canlı sinif (SrDailyBiasStrategy) fidelity yoxlaması

`strategy/sr_daily_bias.py`-in öz docstring-i "KNOWN FIDELITY GAP" adlandırıb
qeyd edib: batch skript mövqe açıqkən HEÇ bir yeni setup axtarmır
(`if in_position: continue`), amma canlı sinif real mövqedən xəbərsizdir və
hər bar broken-level/retest izləməsini davam etdirir — nəzəri cəhətdən batch
skriptin heç görməyəcəyi bir Retest setup-u canlıda yarada bilər. Bu, sadəcə
keyfiyyətcə "zərərsiz" kimi qeyd olunmuşdu, heç vaxt ölçülməmişdi.

**Metod:** `scripts/backtest_sr_daily_bias_live_class.py` real
`SrDailyBiasStrategy`-ni bar-be-bar (M30, tam 6 il) işlədir, eyni
"bir mövqe = bir zaman" qapısını xaricdən tətbiq edir (`run_live_sr_bias.py`
kimi), nəticələri batch skriptlə müqayisə edir. (Qeyd: ilk versiyada
`in_position` bayrağını təmizləyən öz kodum bug idi — dərhal, eyni
iterasiyada təmizlənirdi, "bir mövqe" qapısını əslində SÖNDÜRÜRDÜ, 1017
trade verdi. `open_until_idx` pattern-inə keçəndə (backtest_midnight_fvg
_live_class.py-dəki eyni üsul) düzgün nəticə alındı.)

| | n | WR | PF (net) | Cəmi R (net) |
|---|---|---|---|---|
| **Batch skript** | 811 | 31.3% | 1.057 | +35.8 |
| **Canlı sinif** | 838 | 30.9% | 1.024 | +15.3 |
| Fərq | +27 (+3.3%) | -0.4pp | -0.033 | -20.5 |

**Nəticə: fidelity gap REAL-dır, amma kiçikdir.** Canlı sinif batch-dan
27 (3.3%) daha çox trade tapır (docstring-in dediyi kimi — bloklanmış
Retest-lər əlavə görünür), winrate demək olar eynidir, PF cüzi aşağıdır
(1.057→1.024, hələ də >1.0). Docstring-in "zərərsiz" iddiası əsasən
təsdiqlənir — canlı nəticə batch-dan qat-qat pis DEYİL, sadəcə bir az
zəifdir. Bootstrap kontekstində (yuxarı bax, SR-in 5il CI-si [0.92, 1.33]),
bu 0.033-lük fərq həmin intervalın içindədir — statistik cəhətdən əhəmiyyətli
deyil.

### Fayllar
- **Script:** [scripts/backtest_sr_daily_bias_live_class.py](scripts/backtest_sr_daily_bias_live_class.py)
- **Trade log:** `artifacts/sr_daily_bias_live_class_trades.csv`
