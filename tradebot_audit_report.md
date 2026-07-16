# tradebot — Tam Repo Auditı (2026-07-16)

**Əhatə:** `core/`, `market_structure/`, `smc/`, `strategy/`, `backtest/`, `data/`, `config/`, `application/`,
`research/`, `utils/`, `mt5/`, `notifications/`, `risk/`, `dashboard/`, `tests/` — ~90 mənbə faylı, ~470 test.

**Qayda:** Bu, YALNIZ audit-dir. Heç bir kod dəyişikliyi edilməyib. Əvvəlki tapıntılar (Bug #1–#48,
`walkthrough.md`/`task.md`) təsdiqlənmə nöqtəsi kimi istifadə olunub, təkrarlanmayıb.

**Metodologiya:** 5 paralel alt-araşdırma (strategy/; market_structure+smc/; backtest+data+research/;
core+config+infra; tests/) və mənim özümün apardığım repo-geniş konsistentlik yoxlamaları əsasında qurulub. Hər
tapıntı mövcud koda qarşı YENİDƏN yoxlanılıb (yalnız `walkthrough.md`-a etibar edilməyib, çünki bəzi qeydlər artıq
köhnəlmişdi — bax son bölmə). İki tapıntı (`smc/pipeline.py` duplikat OrderBlock, `structure_engine.py::handle_upgrade`
recency reqressiyası) ayrıca repro skripti ilə empirik təsdiqlənib.

**Statistika:** 1 Kritik · 7 Yüksək · 9 Orta · 14 Aşağı · 469/470 test keçdi (1 xfail, qəsdən — Bug #29).

**Ümumi qiymətləndirmə:** əvvəlki auditdən (52/100) bəri sistem həqiqətən irəliləyib — Bug #1/#2/#3/#7/#5/#8/#13
kimi fundamental korrektlik/performans problemləri düzəldilib, arxitektura təmizlənib (ölü `application/ports` qatı
silinib), 6 strategiya və construction-time konfiq validasiyası əlavə olunub. Amma bu audit YENİ bir kateqoriya
problem aşkarladı: **tədqiqat/hesabat qatının (research/) özü indi ən zəif nöqtədir** — xüsusilə
`run_research_campaign.py`-in "Executive Summary"-i saxta ədədlər göstərir (Bug #49, Kritik). Bu, canlı pulla
bağlı qərar vermək üçün istifadə oluna biləcək bir hesabatdır.

---

## Hissə 1 — Tam Fayl-Fayl Kod Auditi

Bütün 6 strategiyada (`continuation.py` = Bullish+Bearish, `accumulation_breakout.py`, `manipulation_reversal.py`,
`nasdaq_midline_sweep.py`, `opening_range_breakout.py`, `order_block_retest.py`) `strategy_name` doldurulması,
`RejectionReason` enum istifadəsi və construction-time konfiq validasiyası TAM və konsistent tapıldı (bax
Hissə 2). Aşağıdakılar konkret YENİ problemlərdir.

### strategy/ — 6 Strategiya, Mühərrik, Diaqnostika

#### Bug #50 — Yüksək — `accumulation_breakout.py:253-257`, `nasdaq_midline_sweep.py:223-229`, `manipulation_reversal.py:202-215`

**Problem:** Gündəlik/sessiya state reset-i yalnız DAR bir vaxt pəncərəsindən "sessiyaya YENİ giriş" keçid bayrağı
ilə işə düşür (məs. `new_session = in_session and not self._was_in_session`). Bu, artıq düzəldilmiş Bug #23-ün
("stale state sükutla istifadə olunur") YENİ yerdə təkrarlanmasıdır — məhz kopyala-yapışdır zamanı köhnə bug-ın
təkrarlanması nümunəsidir.

`opening_range_breakout.py` bu problemi artıq həll edib: dar pəncərə yerinə təqvim-tarixi müqayisəsi +
"sessiya başlanğıcından bəri" (birtərəfli, məhdudiyyətsiz) şərtdən istifadə edir (sətir 28-37-də bunun MƏHZ bu
səbəbdən seçildiyi izah olunur). Bu düzəliş digər 3 strategiyaya ötürülməyib.

**Konkret ssenari:** `walkthrough.md`-in özü (Bug #18) EURUSD tarixində 888 real "izahsız gap/broker kəsintisi"
hadisəsi sənədləşdirir. Əgər belə bir gap məhz reset pəncərəsinə düşən barı "yeyərsə" (məs.
ManipulationReversalStrategy-də 1 dəqiqəlik pəncərə, NasdaqMidlineSweepStrategy-də 20 dəqiqəlik), reset heç vaxt
işə düşmür: (a) əvvəlki gün `_trade_taken=True` idisə, strategiya sükutla gələcək günlərə qədər bloklanır; (b)
əvvəlki gün ticarət olmayıbsa, ONUN dondurulmuş `mid`/`zone`/reference səviyyəsi YENİ günün əlaqəsiz qiymət
hərəkətinə qarşı sükutla istifadə olunur — xəyali siqnal riski.

#### Bug #59 — Orta — `opening_range_breakout.py:224`, `diagnostics.py:39,51`

**Problem:** `OpeningRangeBreakoutStrategy` "günə bir ticarət" gate-i üçün eyni konseptə görə ayrıca
`TRADE_ALREADY_TAKEN_TODAY` yaradıb, halbuki `AccumulationBreakoutStrategy` və `ManipulationReversalStrategy` eyni
konsept üçün artıq mövcud olan `TRADE_ALREADY_TAKEN`-dan istifadə edir. Diaqnostika şərhi digər reuse-ları qeyd
edir ("Reuses NO_BREAKOUT, NO_VOLUME_SPIKE"), amma bunu qeyd etmir — deməli qəsdən deyil, gözdən qaçıb.

**Təsir:** `top_rejection_reasons()` cross-strategiya aqreqasiyasında bu iki sayğac BİRLƏŞMİR, "gündə-bir-ticarət"
gate-inin sistem-geniş nə qədər tez-tez tətikləndiyi faktiki olaraq az göstərilir.

#### Bug #60 — Orta — `accumulation_breakout.py`/`nasdaq_midline_sweep.py` `Config.__post_init__`, `session_utils.py::is_in_session`

**Problem:** Construction-time validasiya (`require_positive`/`require_non_negative`) hər sahəni AYRI-AYRI
yoxlayır, amma `session_start < session_end` (və ya `build_session_start < build_session_end`) əlaqəsini heç vaxt
yoxlamır. `is_in_session` `start <= t < end` istifadə edir — `start >= end` olduqda sükutla HƏMİŞƏ `False`
qaytarır, nə xəta, nə xəbərdarlıq.

**Konkret ssenari:** `.env`/konfiqurasiya faylında sadə bir yazı xətası (saatlar səhv sıralanıb) strategiyanı
UĞURLA qurar, amma ƏBƏDİ olaraq heç bir siqnal istehsal etməz — səssiz "0 trade" halı, heç bir log/istisna
olmadan.

#### Aşağı Şiddətli (Ölü Kod / Kosmetik)

- **Bug #64** — `accumulation_breakout.py:330`, `nasdaq_midline_sweep.py:290`, `opening_range_breakout.py:260`,
  `order_block_retest.py:160` — `RR_GATE_FAILED` runtime yoxlaması bu 4 faylda ÇATILA BİLMƏZ, çünki konfiqin
  `__post_init__`-i artıq construction zamanı `risk_reward <= 0` üçün `ValueError` atır. Test faylları bunu
  təsdiqləyir (bu branch heç bir testdə işə düşmür).
- (nömrəsiz, Aşağı) `accumulation_breakout.py:328`, `opening_range_breakout.py:258`, `nasdaq_midline_sweep.py:288`
  — `NON_POSITIVE_RISK` bu 3 faylda riyazi olaraq ÇATILA BİLMƏZ (entry/SL eyni range sərhədlərindən törəyir).
  `order_block_retest.py` və `manipulation_reversal.py`-dan fərqli olaraq (orada düzgün əlçatandır və test edilib).
- **Bug #65** — `strategy/continuation.py:317-318, 541-542` — Artıq düzəldilmiş Bug #5-in kölgəsi:
  `timestamp = latest_bar.timestamp if latest_bar is not None else datetime.now()` — bu gün ölü branch, amma
  gələcək refaktorda təsadüfən əlçatan olub Bug #5-i geri gətirə bilər.

**Təsdiqləndi — problem yoxdur:** Bug #10 naxışı (nearest/most-recent seçim, `order_block_retest.py` düzgün);
Bug #22 naxışı (mitigation semantikası — yalnız `order_block_retest.py` SMC zone-larına toxunur və qəsdən
`is_mitigated`-dən müstəqildir); Bug #23 naxışı (yalnız `continuation.py` `trend`/`breaks_history` oxuyur, digər
5 strategiya bu sahələrə toxunmur). Duplicate-setup mühafizəsi və R:R gate sıralaması bütün fayllarda düzgündür.

### market_structure/ + smc/ — Struktur Mühərriki və SMC Detektorları

#### Bug #57 — Orta — `smc/pipeline.py:86-95 (SMCPipeline.update)`

**Problem (empirik təsdiqlənib, repro skripti ilə):** hər yeni `StructureBreak`-da OB detektoru yalnız BİR-break-lik
müvəqqəti `StructureState` ötürülür (Bug #30-dan sonra), bu isə `OrderBlockDetector`-in öz daxili dublikat-yoxlaması
(`order_block.py:157`) üçün heç vaxt 1-dən çox namizəd görünmür — yəni bu yoxlama artıq REAL dublikatların qarşısını
ala bilmir, çünki `market_state.smc_state.order_blocks.extend(new_obs)` mövcud siyahıya qarşı ID yoxlaması etmədən
əlavə edir.

**Konkret ssenari (real skriptlə təsdiqlənib):** davamlı bullish trend + bir neçə ardıcıl BOS, aralarında
əks-istiqamətli şam olmadan → hər yeni BOS eyni əvvəlki bearish şamı özünə "anchor" edir və eyni ID-li
(`ob_1_bullish`) İKİ `OrderBlock` obyekti siyahıya düşür. Aşağı axında `MitigationMonitor._last_checked` ID-ə görə
keşlənir (yanlış nəticə vermir) və `continuation.py`-in `_proposed_keys` dublikat-qorumasi ticarətin
təkrarlanmasının qarşısını alır — deməli bu, HAZIRDA yanlış ticarət qərarına səbəb olmur, amma "hər ID-yə bir
qeyd" invariantını pozan bir data-bütövlüyü xətasıdır.

#### Bug #61 — Orta (yatan) — `market_structure/structure_engine.py::handle_upgrade (~sətir 473-481)`

**Problem (empirik təsdiqlənib):** `handle_upgrade()` — Bug #1-in düzəlişinin əsas mexanizmi — ötürülən swing-in
köhnə saxlanılan `last_major_high`/`last_major_low`-dan xronoloji olaraq DAHA YENİ olduğunu yoxlamadan onu sükutla
ÜSTÜNƏ YAZIR (və `last_broken_*_id`-i `None`-a sıfırlayır). Repro skripti: MINOR high@10 → MAJOR high@20 (düzgün,
`last_major_high`=@20) → sonra @10 MAJOR-a yüksəldikdə `handle_upgrade(swing@10)` çağırılsa, `last_major_high`
KÖHNƏ @10-a REQRESSİYA edir.

**Əlçatanlıq qeydi:** saf bar-be-bar emalda bu sıra-inversiyası birbaşa əlçatan deyil. Amma `is_replacement` yolu
ilə (artıq açıq olan Bug #29 ilə eyni kövrək sahə) əlçatan ola bilər: `swing_detector.py:320-338`-dəki
duplikat/alternasiya filtri `graph.last_node()`-un artıq MAJOR olub-olmadığını yoxlamadan `replace_last_node()`-a
icazə verir. `handle_upgrade()`-in birbaşa unit testi YOXDUR.

#### Bug #66 — Aşağı — `smc/breaker.py`, `market_structure/bos.py`, `choch.py`, `trend.py`

**Ölü/orfan kod, təsdiqləndi (əvvəlki auditdəki tapıntı hələ də doğrudur):** `smc/breaker.py` (Breaker Block
detektoru) tam qurulub və test edilib (2 test — bearish/multi-OB/sərhəd halları əhatə olunmayıb), amma
`SMCPipeline` heç vaxt `BreakerBlockDetector` instansiyası yaratmır və heç bir strategiya onu istehlak etmir.
Eynilə `bos.py`/`choch.py`-in `get_bos_events`/`get_choch_events` utilitləri öz testlərindən başqa heç yerdə
çağırılmır. `market_structure/trend.py::TrendDetector.identify_trend()` tam stub-dur (`NotImplementedError`), sıfır
çağıran, sıfır test — üstəlik `pandas` import edir, bu da "domain pandas-free olmalıdır" prinsipini pozur.

#### Bug #72 — Aşağı (yatan) — `smc/mitigation.py`, `smc/liquidity.py`, `application/services/market_state_builder.py:35-44`

`MitigationMonitor._last_checked` və `LiquidityDetector`-in keşləri `MarketStateBuilder.initialize()` tərəfindən
sıfırlanmır (yalnız `structure_engine` sıfırlanır). Hazırda zərərsizdir (heç bir çağıran instansiyanı fərqli bar
tarixçəsi ilə yenidən istifadə etmir), amma Bug #48-in həlli (WalkForwardRunner-in generic edilməsi) məhz
instansiya-yenidən-istifadəsini gətirə biləcək növ dəyişiklikdir.

**Təsdiqləndi — problem yoxdur:** `smc/mitigation.py` (Bug #22 kök-səbəb düzəlişi real kodda yoxlanıldı — düzgün).
`smc/liquidity.py` bu auditdəki ƏN YAXŞI test edilmiş fayldır (3000-bar diferensial test + 15k-bar performans
testi). `smc/fvg.py`, `smc/order_block.py`, `smc/premium_discount.py` — simmetrik, korrekt, yaxşı test edilib.
`swing_detector.py`-də Bug #29-dan artıq irəli yeni divergensiya tapılmadı.

**Fragillik qeydləri (bug deyil, diqqət tələb edir):**
- `smc/pipeline.py:69-83` — FVG slice-i (`bars[-3:]`) və offset (`len(bars)-3`) hər ikisi `3` literalını hardcode
  edir; hazırda tutarlıdır, amma biri dəyişib digəri unudularsa, bütün FVG index dəyərləri sükutla sürüşər.
- `smc/pipeline.py:102-123` — Displacement pəncərəsi (`atr_period + 100`) hər ~100 barda Wilder ATR-i sıfırdan
  yenidən "seed" edir — ədədi fərq cüzidir, amma test edilmir.

### backtest/ + data/ + research/ — Ən Çox Yeni Tapıntının Olduğu Sahə

> Bu bölmədə ən mühüm kəşf: `walkthrough.md`-da "hələ açıqdır" kimi qeyd olunan Bug #19/#20/#21 əslində **artıq
> düzəldilib** (test-lərlə), amma sənəd yenilənməyib — VƏ eyni zamanda bu düzəliş `research/stability.py`-ə tətbiq
> OLUNMAYIB, yəni problem qismən geri qayıdıb.

#### Bug #49 — KRİTİK — `run_research_campaign.py:537-564 (campaign_summary)`

**Problem:** bütün 800-sətirlik, 6-fazalı araşdırma kampaniyasının YEKUN məhsulu olan "Executive Summary" —
istifadəçinin "forward test-ə hazırdır?" qərarı üçün oxuyacağı hesabat — HƏQİQİ nəticələrdən (`wf_results`,
`opt_results`, `mc_results`, `rob_results`, `stability_results`) demək olar ki, HEÇ NƏ HESABLAMIR:

- `"overall_score": 65.0`, `"robustness_score": 75.0`, `"walk_forward_score": 60.0`, `"monte_carlo_score": 85.0`,
  `"optimization_score": 70.0` — sabit literallar.
- `"profit_factor": 1.45`, `"sharpe": 1.25`, `"risk_of_ruin": 0.0` — real `mc_results`/`rob_results`-dan törənmir.
- `"best_params": "RR=1.5, Buffer=5.0"` — hardcode edilmiş STRING, halbuki bir sətir yuxarıda (sətir 410) real
  `opt_results` həqiqi ən-yaxşı parametrləri saxlayır.
- `"strengths"`/`"weaknesses"`/`"next_action"` — heç bir hesablanmış nəticədən asılı olmayan ümumi, hazır mətn.

**Konkret ssenari:** kampaniyanı istənilən data üzərində işə salın — qazandırsın, ya da tam iflas etsin — PDF/MD
Executive Summary HƏMİŞƏ Sharpe 1.25, PF 1.45, risk of ruin 0.0%, "Best Parameter Set: RR=1.5, Buffer=5.0"
göstərəcək. Bu, real canlı-ticarət qərarını dəstəkləyə biləcək saxta-inam yaradan bir hesabatdır. Bu skript üçün
HEÇ BİR test faylı yoxdur — indiki test suite-i bunu heç vaxt tuta bilməz.

#### Bug #51 — Yüksək — `research/stability.py`

**Problem:** `research_optimizer.py`, `walk_forward.py`, `robustness.py` hər üçü artıq Bug #19-un
(`max_grid_combinations` ölçü-qoruması) və Bug #21-in (`get_diagnostics()` + `top_rejection_reasons()` 0-trade
şəffaflığı) düzəlişlərini daşıyır — VƏ testlərlə təsdiqlənib. Amma `ParameterStabilityAnalyzer` bu iki düzəlişin
HEÇ BİRİNİ almayıb: `run()` `lookback_grid × buffer_grid` tam kartezyen hasilini HEÇ BİR ölçü-qoruması olmadan
iterasiya edir və `_simulate()` diaqnostika inteqrasiyası etmir.

**Konkret ssenari:** istifadəçi stability heatmap üçün geniş grid seçsə (məs. 20×20), sistem sükutla 400 tam
backtest işə salar — Bug #19-un məhz qarşısını almaq istədiyi eyni risk, unudulmuş 4-cü modulda.

#### Bug #52 — Yüksək — `run_backtest.py:147-148` (Bug #48-in əhatəsi genişlənir)

Bug #48 sənədləşdirilərkən yalnız `walk_forward.py` qeyd olunub (bu auditdə `research_optimizer.py`,
`robustness.py`, `stability.py` də eyni naxışı daşıyır). Amma kök-səviyyəli `run_backtest.py`-in özü də
(`execute_backtest()`) `BullishContinuationStrategy`/`BearishContinuationStrategy`-ni birbaşa qeydiyyatdan
keçirir — 5-ci nümunə. Yeganə həqiqətən generic olan runner `research/run_strategy_backtest.py`-dir
(`STRATEGY_REGISTRY` ilə bütün 7 strategiyanı dəstəkləyir).

#### Bug #53 — Yüksək — `data/csv_provider.py::validate()` çağırış nöqtələri; `core/data_engine.py`

**Problem:** `CSVDataProvider.validate()` qəsdən "opt-in" dizayn edilib, amma tutarsız tətbiq olunur:
`run_backtest.py:101` və `research/run_strategy_backtest.py:215` çağırır; `run_diagnostics.py:63`,
`run_research.py:88-89`, və `run_research_campaign.py`-in BÜTÜN fazaları çağırMIR. Bundan əlavə: `core/data_engine.py`
daha sərt yoxlama edir (boş bar siyahısını `EmptyDataError` ilə tutur), amma bu qat istehsalatda İSTİFADƏ OLUNMUR.

**Konkret ssenari:** mənfi/sıfır qiymətli və ya high<low olan bir CSV `run_backtest.py`-i dərhal dayandırar, amma
eyni fayl `run_diagnostics.py`/`run_research_campaign.py`-in istənilən fazasına SÜKUTLA axar. Boş CSV isə sıfır-barlı
backtest sükutla işə salar.

#### Bug #54 — Yüksək — `backtest/engine.py::run()`

Data seriyası bitəndə hələ açıq olan mövqe (heç bir `max_holding_bars` yoxdursa) heç vaxt `closed_trades`-ə
yazılmır, `final_balance` onu əks etdirmir və heç bir metrika onu görmür. Heç bir sayğac yoxdur, heç bir test bu
ssenarini yoxlamır.

#### Bug #55 — Orta-Yüksək — `mt5/history_downloader.py:93 (MT5HistoryDownloader.download)`

`data/download_history.py`-dən fərqli olaraq (chunking tətbiq edir), `MT5HistoryDownloader.download()` eyni
funksiyanı BİR DƏFƏYƏ, chunk-sız çağırır. Bu ÖLÜ kod DEYİL — `run_backtest.py`, `run_research.py`,
`run_research_campaign.py`-də aktiv istifadə olunur. Sıfır test əhatəsi.

**Konkret ssenari:** geniş tarix aralığı MT5-in bar-limitini keçər, `download()` `None` qaytarar →
`run_backtest.py` köhnə/fərqli fallback CSV-ə keçər və ya `FileNotFoundError` atar.

#### Bug #56 — Orta — `backtest/report.py:39-40, 220-221, 342`

`average_win`/`average_loss` = `gross_profit / winning_trades`, amma `gross_profit` `result`-dan asılı olmadan
`pnl > 0` olan BÜTÜN ticarətləri cəmləyir — EXPIRED nəticəli müsbət ticarətlər daxil. **Konkret ssenari:** 5 WIN
ticarət cəmi $500 + 1 EXPIRED +$50 → `average_win` $110 göstərir, əsl orta isə $100-dür. (`expectancy` təsirlənmir.)

**Aşağı əhatə boşluğu:** Sharpe/Sortino/Calmar əmsallarının HEÇ BİR birbaşa unit testi yoxdur.

#### Bug #58 — Orta — `config/settings.py:58-59`; `data/csv_provider.py:124, 180-195`

`DUPLICATE_POLICY`/`MISSING_VALUE_POLICY` sərbəst-mətn stringlərdir, icazəli dəyərlərə qarşı yoxlanılmır. Yazı
xətalı dəyər (`Drop` səhv registr, ya da `ignore`) heç bir budaqda tutulmadan sükutla keçir.

**Digər aşağı-şiddətli:**
- **Bug #62** — `research/research_optimizer.py:106-123` — Naməlum `search_space` açarı sükutla nəzərə alınmır.
- **Bug #71** — `research/run_strategy_backtest.py::split_bars` — `split_ratio` [0,1] daxilində yoxlanılmır.

**Təsdiqləndi — problem yoxdur:** `research/run_strategy_backtest.py` — 7 strategiyanın hamısı üçün DÜZGÜN generic
runner. Spread double-charge (Bug #7), margin/leverage yoxlaması, conflict policy (Bug #25), circuit breaker-lər —
hamısı yenidən yoxlanıldı, düzgün və yaxşı test edilib. `data/download_history.py` — problemsiz.

### core/ + config/ + application/ + utils/ + mt5/ + notifications/ + risk/ + dashboard/

#### Bug #67 — Aşağı — `README.md:18,20`; `risk/__init__.py`

`README.md` hələ də silinmiş `indicators/` qovluğunu və `risk/`-i sanki funksional imiş kimi təsvir edir.
Reallıqda `risk/__init__.py` yalnız 4-sətirlik docstringdir — heç bir kod yoxdur.

#### Bug #68 — Aşağı — `.gitignore`, `artifacts/`

`.gitignore` yalnız `artifacts/diagnostics_results.json`-u istisna edir; qalan 25+ generasiya olunmuş fayl
(json/csv/png/pdf/md) git-ə TAM commit olunub — niyyət ilə faktiki vəziyyət arasında uyğunsuzluq.

#### Bug #73 — Aşağı — `notifications/telegram.py`, `dashboard/app.py`, `mt5/connector.py`

`TelegramNotifier.send_message()` və `run_dashboard()` hər ikisi `NotImplementedError` atır və heç yerdə
çağırılmır — vicdanla işarələnmiş, amma docstring-ləri elə yazılıb ki, koda baxmadan işlək olduqları düşünülə
bilər. `mt5/connector.py` hələ də yalnız login/logout — order yerləşdirmə, mövqe idarəetməsi, reconnect məntiqi
TAM YOXDUR (FAZA 6 hələ başlamayıb, reqressiya deyil).

**Təsdiqləndi — problem yoxdur:** `core/validation.py` bütün 6 strategiya konfiqində düzgün çağırılır.
`utils/validators.py` ilə `core/validation.py` arasında duplikasiya YOXDUR (fərqli məqsəd, fərqli çağıranlar).
Bug #26 düzəlişi təsdiqləndi. Margin/leverage-aware sizing artıq `backtest/engine.py::_margin_ok`-da
REALLAŞDIRILIB — köhnə auditin tövsiyəsi artıq yerinə yetirilib.

### tests/ — Test Suite Keyfiyyəti

**469 keçdi, 1 xfail (Bug #29, qəsdən), 0 sabit uğursuz.**

#### Bug #63 — Aşağı (etibarlılıq) — `tests/test_swing_detector.py::test_large_dataset_performance`

3 dəfə işə salınan test suite-dən 1-də bu test UĞURSUZ oldu: sərt `elapsed < 1.0` divar-saatı şərti maşın yükünə
görə "flaky"-dir (1.09s–1.52s aralığında). "469 passed, 0 failed" iddiası yalnız bu test təsadüfən 1s-dən az
olduqda doğrudur.

#### Bug #69 — Orta (əhatə boşluğu) — `tests/test_research.py`

Walk-forward/optimizer/robustness/stability üçün BÜTÜN testlər eyni "düz, hərəkətsiz 30-bar" fixture-dan istifadə
edir. Nəticədə bu 4 modulun ƏSAS simulyasiya məntiqi HEÇ VAXT real ticarət istehsal edən ssenari ilə test edilmir.

#### Bug #70 — Aşağı — `mt5/connector.py`

Sıfır test əhatəsi, o cümlədən `connect()`-dəki `int(login_str)` `try/except` naxışı, MƏHZ artıq düzəldilmiş
Bug #26 ilə eyni bug sinfidir (orada test edilib, burada yox).

#### Bug #74 — Aşağı — `tests/conftest.py` (28 sətir)

Paylaşılan fixture YOXDUR — 40 test faylının hər biri öz bar-qurma helper-ini müstəqil təkrarlayır. DST keçidləri
HEÇ YERDƏ test olunmur; həftəsonu-gap yalnız bir faylda test olunur.

**Digər aşağı-şiddətli:** `tests/test_audit_requirements.py`-də iki tavtoloji test (dataclass konstruktorunu test
edir, real BOS/CHoCH aşkarlamasını yox).

---

## Hissə 2 — Konsistentlik və Sağlamlıq Yoxlaması

| Yoxlama | Nəticə | Qeyd |
|---|---|---|
| `RejectionReason` enum-un təkrarsız istifadəsi | ✅ TƏSDİQLƏNDİ | 7 strategiya instansiyası yalnız mərkəzi enum-dan istifadə edir, ad-hoc string yoxdur. Yeganə nöqsan: Bug #59. |
| `strategy_name` doldurulması (Bug #27) | ✅ TƏSDİQLƏNDİ | Bütün 7 `TradeSetup` konstruksiya nöqtəsində `strategy_name=self.__class__.__name__` var. |
| Construction-time konfiq validasiyası | ✅ TƏSDİQLƏNDİ | Bütün 6 konfiq sinfi `isinstance` + `require_positive`/`require_non_negative` istifadə edir. Nöqsan: cross-field yoxlama yoxdur (Bug #60). |
| `.gitignore`/`requirements.txt`/`pyproject.toml` güncəlliyi | ⚠️ QİSMƏN | Asılılıqlar tutarlıdır, silinmiş modullara istinad yoxdur. Amma: `artifacts/` demək olar tamamı commit olunub (Bug #68), README köhnə istinad saxlayır (Bug #67). |

---

## Hissə 3 — "Peşəkar Treyder" Perspektivindən Təkliflər

Yalnız SİYAHI — kod yazılmayıb.

### Risk İdarəetməsi

- **Canlı circuit breaker yoxdur.** `max_daily_loss_pct`/`max_equity_drawdown_pct` yalnız `backtest/engine.py`-də
  mövcuddur. Canlı icra qatı hələ olmadığı üçün bunların ekvivalenti canlıda YOXDUR.
- **Ardıcıl-itki sayı YALNIZ hesabatda var, aktiv əyləc DEYİL.** `max_consecutive_losses` post-hoc metrikdir.
- **Strategiyalar/simvollar arası aqreqasiya-səviyyəli risk yoxdur.** Fərqli simvollarda korrelyasiyalı mövqelər
  üçün ümumi expozisiya limiti yoxdur.
- **Kill-switch / əl ilə dayandırma mexanizmi yoxdur.**
- **Leverage sərt sayılmadıqda mövqe-ölçüsü tavanı yoxdur.** `_margin_ok` yalnız `leverage` konfiqi verilibsə
  işləyir (default `None` = yoxlama SÖNÜK).
- **`Settings.ENVIRONMENT` təyin olunur, amma HEÇ YERDƏ oxunmur.** development/production arasında davranış
  fərqləndirən heç bir keçid yoxdur.

### Monitorinq / Xəbərdarlıq

- **Bildiriş kanalı sıfırdır.** `notifications/telegram.py` tam stub-dur və heç yerdə çağırılmır.
- **Heartbeat/watchdog yoxdur.** MT5 bağlantısı səssizcə kəsiləndə bunu aşkarlayan mexanizm yoxdur.
- **Circuit breaker hadisələri heç kimə "page" etmir.**

### Loglama

- **Log rotasiyası yoxdur.** `utils/logging.py::setup_logger` sadə `FileHandler` istifadə edir (`RotatingFileHandler` deyil).
- **Struktursuz (JSON olmayan) log formatı.** Modul "Structured logging" adlanır, amma çıxış sadə mətndir.
- **Trade/signal-səviyyəli korrelyasiya ID-si yoxdur.**
- **Diaqnostika sayğacları yaddaşda, prosess-ömürlüdür.** Restart-da tarixçə itir.

### Data Keyfiyyəti Monitorinqi

- **Gap aşkarlanması yalnız offline/tarixi endirmə üçündür.** Real-vaxt data-keyfiyyəti monitorinqi konseptual
  olaraq mövcud deyil.
- **Real-vaxt üçün lazım olacaq yoxlamalar hələ dizayn edilməyib:** stale-quote, spread-blowout, tick-to-tick
  qiymət-fasiləsi.
- **`CSVDataProvider.validate()` hətta offline üçün belə tutarlı tətbiq olunmur** (Bug #53).
- **İkinci (çarpaz-yoxlama) feed yoxdur.**

### Fail-Safe

- **"Bütün mövqeləri bağla və dayan" panik-mexanizmi yoxdur** — icra qatı hələ yoxdur, amma FAZA 6 bunu açıq
  dizayn tələbi kimi daxil etməlidir.
- **Order-idempotentliyi/dublikat-qorunması dizaynı yoxdur.**
- **Proses/restart üzərindən mövqe-bərpası dizaynı yoxdur.** Maraqlıdır ki, bunun konseptual əcdadı —
  `IStateRepositoryPort` — köhnə `application/ports` qatının bir hissəsi kimi mövcud idi və FAZA 5-də ölü kod kimi
  SİLİNDİ. Bu boşluq FAZA 5 təmizliyinin bir yan-təsiridir.

---

## Hissə 4 — Yekun Prioritetləşdirilmiş Cədvəl

| № | Tip | Şiddət | Fayl/Sətir | Problem | Təklif olunan həll |
|---|---|---|---|---|---|
| #49 | Bug | 🔴 Kritik | `run_research_campaign.py:537-564` | Executive Summary hardcode/saxta metriklər göstərir | Real nəticələrdən hesabla; test faylı əlavə et |
| #50 | Bug | 🟠 Yüksək | `accumulation_breakout.py`, `nasdaq_midline_sweep.py`, `manipulation_reversal.py` | Sessiya reset-i data-gap-ə qarşı kövrəkdir | `opening_range_breakout.py`-in naxışını köçür |
| #51 | Bug | 🟠 Yüksək | `research/stability.py` | Bug #19/#21 düzəlişləri bu modula tətbiq olunmayıb | Bacı modullardan köçür, test əlavə et |
| #52 | Bug | 🟠 Yüksək | `run_backtest.py:147-148` | Bug #48 naxışı bu faylda da var | Bug #48 həlli ilə birgə həll et |
| #53 | Bug | 🟠 Yüksək | `run_diagnostics.py`, `run_research.py`, `run_research_campaign.py` | `validate()` tutarsız çağırılır | Məcburi et, ya da `DataEngine`-i qoş/sil |
| #54 | Bug | 🟠 Yüksək | `backtest/engine.py::run()` | Açıq qalan mövqe sükutla itir | Mark-to-market et, ya da sayğacla işarələ |
| #55 | Bug | 🟠 Orta-Yüksək | `mt5/history_downloader.py:93` | Chunking yoxdur | `data/download_history.py`-dən köçür |
| #56 | Bug | 🟡 Orta | `backtest/report.py:39-40, 342` | `average_win`/`average_loss` EXPIRED ilə çirklənir | Yalnız WIN/LOSS ilə hesabla |
| #57 | Bug | 🟡 Orta | `smc/pipeline.py:86-95` | Dublikat `OrderBlock` ID (empirik təsdiqləndi) | `extend()`-dən əvvəl ID yoxlaması |
| #58 | Bug | 🟡 Orta | `config/settings.py:58-59` | Policy stringləri yoxlanılmır | İcazəli dəyər siyahısına qarşı validasiya |
| #59 | Bug | 🟡 Orta | `opening_range_breakout.py:224` | Rejection reason dublikatı | Vahid enum üzvünə birləşdir |
| #60 | Bug | 🟡 Orta | `accumulation_breakout.py`, `nasdaq_midline_sweep.py` | Cross-field session yoxlaması yoxdur | `__post_init__`-ə sıra yoxlaması |
| #61 | Bug | 🟡 Orta (yatan) | `structure_engine.py::handle_upgrade` | Recency yoxlaması yoxdur | Müdafiə şərti + unit test |
| #62 | Bug | 🔵 Aşağı | `research_optimizer.py:106-123` | Naməlum açar sükutla yox sayılır | `ValueError` at |
| #63 | Bug | 🔵 Aşağı | `test_swing_detector.py` | Flaky performans testi | Tolerans/median-əsaslı qapı |
| #64 | Bug | 🔵 Aşağı | 4 strategiya faylı | Ölü rejection budaqları | Runtime yoxlamaları təmizlə |
| #65 | Bug | 🔵 Aşağı | `continuation.py:317-318` | Lazımsız `datetime.now()` qalıb | Sil |
| #66 | Bug | 🔵 Aşağı | `smc/breaker.py`, `bos.py`, `choch.py`, `trend.py` | Ölü/orfan kod | İnteqrasiya et ya da sil |
| #67 | Bug | 🔵 Aşağı | `README.md` | Köhnə istinadlar | Güncəllə |
| #68 | Bug | 🔵 Aşağı | `.gitignore`, `artifacts/` | Generasiya olunmuş fayllar commit olunub | Niyyəti aydınlaşdır |
| #69 | Bug | 🟡 Orta | `tests/test_research.py` | Yalnız düz data ilə test | Real ticarət ssenarisi əlavə et |
| #70 | Bug | 🔵 Aşağı | `mt5/connector.py` | Sıfır test əhatəsi | Test əlavə et |
| #71 | Bug | 🔵 Aşağı | `run_strategy_backtest.py::split_bars` | `split_ratio` yoxlanılmır | Sərhəd yoxlaması |
| #72 | Bug | 🔵 Aşağı (yatan) | `market_state_builder.py:35-44` | SMC keşləri sıfırlanmır | Refaktor zamanı nəzərə al |
| #73 | Bug | 🔵 Aşağı | `notifications/telegram.py`, `dashboard/app.py` | Stub docstring yanıltıcıdır | Aydın işarələ |
| #74 | Bug | 🔵 Aşağı | `tests/conftest.py` | Paylaşılan fixture yoxdur | Mərkəzi fixture-lər əlavə et |
| T1 | Təklif | 🟠 Yüksək | (yeni) | Canlı circuit breaker/kill-switch yoxdur | FAZA 6-nın hissəsi kimi dizayn et |
| T2 | Təklif | 🟠 Yüksək | (yeni) | Bildiriş/monitorinq/heartbeat yoxdur | Telegram-ı reallaşdır və breaker-lərə qoş |
| T3 | Təklif | 🟡 Orta | (yeni) | Log rotasiyası/struktur/korrelyasiya ID yoxdur | Loglama modulunu genişləndir |
| T4 | Təklif | 🟡 Orta | (yeni) | Real-vaxt data-keyfiyyəti monitorinqi yoxdur | FAZA 6 ilkin şərti kimi planlaşdır |
| T5 | Təklif | 🟠 Yüksək | (yeni) | Fail-safe/mövqe-bərpası dizaynı yoxdur | FAZA 6-nın ilk tələbi kimi |

---

## Qeyd: Əvvəlki Auditlə Fərq

Repo kökündəki `tradebot_technical_audit.md` köhnə bir auditdir (52/100 bal) və onun bir çox tapıntısı artıq HƏLL
OLUNUB — "iki paralel ölü arxitektura qatı", üç uyğunsuz risk-sizing implementasiyası, kök-səviyyəli debug
skriptləri, yalnız 2 strategiya olması kimi qeydlər hamısı FAZA 5/sonrakı komitlərlə düzəldilib. Bu audit bunları
TƏKRARLAMADI, əvəzinə cari kodu YENİDƏN yoxlayaraq bu tapıntıların doğrudan da tarixə qarışdığını təsdiqlədi (məs.
margin/leverage-aware sizing artıq mövcuddur). Eyni zamanda `walkthrough.md`/`task.md`-dakı bəzi "hələ açıqdır"
qeydlərin (Bug #19/#20/#21) əslində kodda artıq düzəldildiyi, sadəcə sənədin yenilənmədiyi aşkarlandı — VƏ bu
düzəlişin `research/stability.py`-ə tam ötürülmədiyi (Bug #51) tapıldı.

**Tövsiyə:** `walkthrough.md`/`task.md`-i bu auditin tapıntıları ilə sinxronlaşdırmaq, xüsusilə Bug #19/#20/#21-in
statusunu "düzəldildi (3/4 modulda)" kimi yeniləmək.

---

*Audit tarixi: 2026-07-16 · Yalnız oxuma, heç bir fayl dəyişdirilməyib · 5 paralel alt-araşdırma + müstəqil
repo-geniş yoxlamalar əsasında tərtib edilib.*
