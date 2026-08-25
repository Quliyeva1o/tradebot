# Walkthrough of Bug fixes

We have successfully resolved Bug #1, Bug #2, Bug #7, Bug #5, Bug #8, Bug #3, Bug #13, and the SwingGraph node access copy-overhead.

## Bug #1: Propagating Upgraded Swings
In `MarketStateBuilder.append_bar`, we propagated swing classification upgrades from `detect_incremental` to `MarketStructureEngine` using `self.structure_engine.handle_upgrade(result.upgraded_swing)` before processing any new swings.

## Bug #2: Aligning Incremental Swing Filtering with Batch Semantics
We implemented swing replacement logic in `detect_incremental` to prevent consecutive duplicate swing types from violating the alternating high/low invariant.

## Bug #7: Correcting Spread Cost Semantics
We corrected a double-charging spread bug in `BacktestEngine` where the full spread width was being charged on both entry and exit. We modified the formulas to charge `spread / 2` on entry and `spread / 2` on exit, resulting in exactly one full spread round-trip transaction cost.

## Bug #5: Deterministic TradeSetup Timestamps
In `BullishContinuationStrategy.evaluate` and `BearishContinuationStrategy.evaluate`, we replaced `datetime.now()` with `market_state.get_latest_bar().timestamp`. This ensures that setup timestamps match the historical context and that backtesting remains fully deterministic.

## Bug #8: Configurable Pending Order Expiry
We introduced a configurable limit order expiry parameter `pending_order_expiry_bars` to `BacktestConfig` to allow setups to remain active waiting to be filled for multiple bars, rather than being discarded after exactly 1 bar.

## Bug #3: Incremental Liquidity Pool Detection
We replaced the $O(n^2 \log n)$ full re-clustering and sweep scanning bottleneck in `LiquidityDetector` with an incremental algorithm that caches pools and sweep states. Full re-clustering is only performed when the swing graph actually changes, and sweep scanning is performed incrementally only on new swings since the last check.

## Bug #13: Configurable Mitigated Zone Pruning
We implemented a configurable pruning mechanism (`max_zone_age_bars`) in `SMCPipeline` to remove old, mitigated zones (Order Blocks and Fair Value Gaps) to prevent memory leaks and keep active tracking overhead O(1) per zone.

## SwingGraph Node Access Copy-Overhead (Step 3.3)
We optimized `SwingGraph` node retrieval on hot paths. The original `.nodes` property returned a full copy of the underlying swings list. We added copy-free and partial copy helpers (`recent_nodes`, `node_count`, and `last_node`) and replaced hot path references in `SwingDetector` with these optimized calls, yielding a massive performance speedup.

### Changes Made

### 1. Swing Graph Model
#### [MODIFY] [structure_models.py](file:///Users/renaquliyeva/Desktop/tradebot/market_structure/structure_models.py)
Added optimized copy-free methods:
- `recent_nodes(self, n: int) -> list[Swing]`
- `node_count(self) -> int`
- `last_node(self) -> Swing | None`

### 2. Swing Detector
#### [MODIFY] [swing_detector.py](file:///Users/renaquliyeva/Desktop/tradebot/market_structure/swing_detector.py)
Updated upgrade checks to use `graph.recent_nodes(5)` and candidate checks to use `graph.last_node()`.

### 3. Tests
#### [MODIFY] [test_domain_models.py](file:///Users/renaquliyeva/Desktop/tradebot/tests/test_domain_models.py)
- Updated nodes length assertion to use `graph.node_count()`.
- Appended `test_swing_graph_optimized_nodes_access` containing micro-benchmarks comparing `nodes[-5:]` copy slicing vs `recent_nodes(5)` over a 10,000 node graph.

## Verification Results
- Target tests: **PASS**
- Micro-benchmark result: **recent_nodes(5) is ~450x faster than nodes[-5:]** (0.0085 seconds vs 3.8300 seconds for 100k iterations on a 10k node graph).
- All 167 tests passed successfully.

---

# FAZA 3.5 — Analytics & Diagnostics

## Məqsəd
`BullishContinuationStrategy` və `BearishContinuationStrategy`-nin niyə az (və ya heç) setup yaratmadığını görmək üçün, hər `evaluate()` çağırışında hansı qapının (gate) namizədi rədd etdiyini ölçən aşağı-xərcli sayğac sistemi.

## Rədd nöqtələrinin siyahısı (təsdiqdən keçib)
Hər iki strategiyada 14 paralel rədd nöqtəsi identifikasiya olundu (bax `strategy/continuation.py`): trend, premium/discount zona mövcudluğu, zona uyğunluğu, break tarixçəsi, break tipi (BOS), break-in swing tipi, son bar mövcudluğu, OB uyğunluğu, FVG uyğunluğu, liquidity sweep, displacement, risk məsafəsi müsbətliyi, R:R gate, dublikat setup.

## Dəyişikliklər

### 1. [ADD] [strategy/diagnostics.py](file:///Users/renaquliyeva/Desktop/tradebot/strategy/diagnostics.py)
- `RejectionReason` enum — 14 üzv, hər biri `continuation.py`-dəki bir `return None` nöqtəsinə uyğundur.
- `StrategyDiagnostics` sinfi — `evaluations`, `setups_generated` sayğacları və `rejections: Counter[RejectionReason]`. `record_evaluation()`, `record_rejection()`, `record_setup_generated()`, `reset()`, `summary()` metodları. Xərc: hər `evaluate()` çağırışında bir neçə dict artırımı — mövcud struktur/SMC axtarışlarının yanında əhəmiyyətsizdir.

### 2. [MODIFY] [strategy/continuation.py](file:///Users/renaquliyeva/Desktop/tradebot/strategy/continuation.py)
- Hər iki strategiyanın `__init__`-inə `self.diagnostics = StrategyDiagnostics()` əlavə olundu.
- `evaluate()`-in əvvəlində `self.diagnostics.record_evaluation()`.
- Hər 14 `return None` sətri `return self._reject(RejectionReason.X)` ilə əvəz olundu (`_reject` həm sayğacı artırır, həm `None` qaytarır — davranış eynidir, yalnız yan-effekt əlavə olunub).
- Setup uğurla yaradılanda `self.diagnostics.record_setup_generated()`.
- `reset()` indi `_proposed_keys`-lə yanaşı `self.diagnostics.reset()` də çağırır — çünki `reset()` hər backtest run-ın əvvəlində bir dəfə çağırılır (`BacktestEngine.run`), deməli diaqnostika "bir backtest run üçün rədd bölgüsü" semantikasını daşıyır.

### 3. [MODIFY] [strategy/strategy_engine.py](file:///Users/renaquliyeva/Desktop/tradebot/strategy/strategy_engine.py)
- `get_diagnostics()` metodu əlavə olundu — qeydiyyatdan keçmiş bütün strategiyalardan (yalnız `diagnostics` atributu olanlardan, duck-typing ilə, mövcud `hasattr(strategy, "reset")` pattern-inə uyğun) `{index}_{ClassName}: summary()` şəklində aqreqasiya edir. Index prefiksi eyni sinifdən bir neçə instance qeydiyyatdan keçəndə key toqquşmasının qarşısını alır.

### 4. [MODIFY] [run_backtest.py](file:///Users/renaquliyeva/Desktop/tradebot/run_backtest.py)
- `execute_backtest()`-də simulyasiya bitəndən sonra `strategy_engine.get_diagnostics()` çağırılır və nəticə JSON kimi loglanır (`logger.info`). Əlavə komputasiya yoxdur — sadəcə artıq yığılmış sayğacların formatlanması.

### 5. [ADD] [tests/test_strategy_diagnostics.py](file:///Users/renaquliyeva/Desktop/tradebot/tests/test_strategy_diagnostics.py)
27 yeni test: `StrategyDiagnostics` özəyi (4 test), Bullish strategiyasının bütün 14 rədd nöqtəsindən 12-si + uğurlu yol + reset (14 test), Bearish üçün 3 nümunə gate (simmetriya təsdiqi üçün), `StrategyEngine.get_diagnostics()` aqreqasiyası (3 test, o cümlədən eyni sinifdən iki instance-ın key toqquşmaması).

## Mühit qeydi (plandan kənar problem)
Bu sessiyada `pip install reportlab` internet olmadığı üçün uğursuz oldu. `tests/test_research.py` (5 test, `research/` paketi vasitəsilə `reportlab`-dan asılıdır) collect oluna bilmədi. Bu, `main`-dən miras qalan `05ba88a "Walk-Forward + Optimization + Monte Carlo"` commit-inə aiddir və FAZA 0 baseline-ından sonra, bizim bu sessiyadakı işimizdən tamamilə asılı olmayaraq mövcuddur. Tam test dəsti bu faylı istisna edərək işə salınıb.

## Doğrulama
- Baza (bu sessiyada, `test_research.py` istisna, reportlab mühit məhdudiyyəti): **161 PASS** (166 elan edilmiş ümumi say − 5 collect-oluna-bilməyən = 161, riyaziyyat üst-üstə düşür).
- Yeni testlər: **+27**
- Sonrakı say: **188 PASS, 0 FAIL** ✅ (161 + 27 = 188, riyaziyyat düzdür)
- Mövcud `tests/test_strategy_engine.py` (əvvəlki davranış testləri) dəyişməz saxlanıldı və hamısı keçdi — geriyə uyğunluq qorunub.

---

# FAZA 4 — Strategiya Keyfiyyəti

## Bug #9: Configurable Stale-Break Gating

### Qərar prosesi
Audit-in tövsiyəsi: "Add recency gating to the structure-break rule (#9)". `StructureBreak`
modelində break-in öz bar-mövqeyini saxlayan sahə yoxdur (yalnız `timestamp` və
`broken_swing`). İki yanaşma müzakirə olundu:
1. `StructureBreak`-ə `bar_index` sahəsi əlavə etmək (dəqiq, amma `check_structural_break()`
   siqnaturuna toxunur, 2 production call-site + 7 test call-site-a təsir edir).
2. `last_break.broken_swing.index`-i proxy kimi istifadə etmək (0 model dəyişikliyi).

**İstifadəçi 2-ci variantı seçdi.** İmplementasiyadan əvvəl, proxy-nin real break-yaşını
nə qədər şişirtdiyini ölçmək tələb olundu (real tarixi CSV data repoda yoxdur —
`data/history/` boşdur), ona görə real `MarketStateBuilder → SwingDetector →
MarketStructureEngine` pipeline-i 4 bazar rejimi profili × 5 seed (cəmi 3000 bar × 20 run,
3002 real `StructureBreak`) üzərində sintetik qiymət seriyası ilə işə salındı:

| Metrik | Dəyər (bar) |
|---|---|
| min | 7 |
| median | 14 |
| mean | 17.3 |
| p90 | 31 |
| max (outlier) | 95 |

Nəticə: proxy real break-yaşını tipik olaraq **14-17 bar** şişirdir (heç vaxt aşağı
qiymətləndirmir — konservativ tərəf xətası). İstifadəçi bu ölçmədən sonra
**`max_break_age_bars` default-unu `None` (gating deaktiv, köhnə davranış) saxlamağı**
seçdi — konkret ədəd real backtest data ilə kalibrasiya edildikdən sonra təyin ediləcək.

### Dəyişikliklər

#### 1. [MODIFY] [strategy/continuation.py](file:///Users/renaquliyeva/Desktop/tradebot/strategy/continuation.py)
- `StrategyConfig`-ə və hər iki strategiyanın `__init__`-inə `max_break_age_bars: int | None = None` əlavə olundu.
- Yeni "Rule 9: Break Recency Check" — `latest_bar` təsdiqləndikdən sonra: `swing_age = latest_idx - last_break.broken_swing.index`; `max_break_age_bars` təyin olunubsa və `swing_age` onu keçirsə, `RejectionReason.STALE_BREAK` ilə rədd edilir.
- Əlavə təmizlik: Rule 7 (displacement) və yeni Rule 9 eyni `latest_idx`-ə ehtiyac duyur — əvvəllər `len(market_state.bars) - 1` iki dəfə (hər biri tam siyahı köçürməsi ilə) hesablanırdı; indi bir dəfə `market_state.bar_count()` ilə hesablanıb hər ikisində istifadə olunur.

#### 2. [MODIFY] [market_structure/structure_models.py](file:///Users/renaquliyeva/Desktop/tradebot/market_structure/structure_models.py)
- `MarketState.bar_count() -> int` əlavə olundu (`SwingGraph.node_count()`-un FAZA 3.3-dəki presedentinə uyğun) — `len(self._bars)`, siyahı köçürmədən.

#### 3. [MODIFY] [strategy/diagnostics.py](file:///Users/renaquliyeva/Desktop/tradebot/strategy/diagnostics.py)
- `RejectionReason.STALE_BREAK` əlavə olundu.

### Doğrulama
- Yeni testlər: **+7** (`tests/test_strategy_diagnostics.py`): default None gating-i söndürür, tam sərhəddə (age == limit) keçir, sərhəddən 1 bar yuxarıda (age == limit+1) rədd edir, `StrategyConfig` overlay yolu, bearish güzgü testləri.
- `tests/test_domain_models.py::test_market_state_root`-a `bar_count()` assertion-ları əlavə olundu (yeni test funksiyası yox, mövcud testin genişlənməsi).
- 188 (əvvəlki) + 7 (yeni) = **195 PASS, 0 FAIL** ✅
- Commit: `6646991`

## Bug #10: Nearest/Most-Recent OB & FVG Selection

### Qərar prosesi
Audit: "Select nearest/most-recent OB/FVG, not first-in-list (#10)". Köhnə kod OB/FVG
siyahısını gəzib **ilk uyğun gələni** seçirdi (`break` ilk uyğunluqda), sonrakı daha
yaxşı namizədlərə baxmadan. İstifadəçiyə 2 kriteriya təqdim olundu (yalnız ən yeni,
yoxsa qiymətə ən yaxın + bərabərlikdə ən yeni tiebreak) — **ikincini seçdi**, model
dəyişikliyi tələb olunmadığı üçün.

### Dəyişikliklər

#### [MODIFY] [strategy/continuation.py](file:///Users/renaquliyeva/Desktop/tradebot/strategy/continuation.py)
- İki modul-səviyyəli helper əlavə olundu (Bullish/Bearish arasında paylaşılır, çünki məntiq istiqamətdən başqa eynidir):
  - `_select_best_order_block(order_blocks, direction, price)` — bütün uyğun (unmitigated, düzgün istiqamət, qiymət daxilində) namizədləri toplayır, `bar_index` maksimuma görə seçir. **Qeyd:** OB üçün "daxilində" şərti = məsafə həmişə 0-dır bütün namizədlər üçün, ona görə əməli olaraq yeganə diskriminator recency-dir.
  - `_select_best_fvg(fair_value_gaps, direction, price, proximity_threshold)` — bütün uyğun namizədləri toplayır, `(məsafə, -end_index)` üzrə sıralayır — ən yaxın əvvəl, bərabərlikdə ən yeni.
- Hər iki strategiyanın Rule 4 (OB) və Rule 5 (FVG) blokları bu helper-lərə köçürüldü.

### Doğrulama
- Yeni testlər: **+7** (`tests/test_continuation_ob_fvg_selection.py`): OB recency tiebreak, mitigated/səhv-istiqamət OB-ların istisnası, FVG-də "yaxın uzaqdan üstündür" halı, bərabər-məsafə tiebreak, mitigated/proximity-dən-kənar FVG-lərin istisnası.
- Mövcud testlərdə (tək OB/FVG olan fixture-lar) davranış dəyişmədi — 195 köhnə test hamısı keçdi.
- 195 (əvvəlki) + 7 (yeni) = **202 PASS, 0 FAIL** ✅
- Commit: `5de6f53`

## Bug #11: Eyni Displacement Leg Tələbi — TƏXİRƏ SALINDI

İstifadəçi qərarı: bu, ən yüksək qeyri-müəyyənlik daşıyan dəyişiklikdir (SMC-də "leg"
anlayışının kanonik tərifi yoxdur, backtest nəticələrini kəskin restriktiv edə bilər).
Hazırda tətbiq edilmir, real backtest datası ilə kalibrasiya edildikdən sonra ayrıca
ele alına bilər. FAZA 5-ə keçilir.

## Duplicate Setup / R:R Gate — Yoxlama Nəticəsi

Audit qeydi: "Duplicate trades: Guarded reasonably well via `_proposed_keys =
{(ob.id, break.id)}` and `reset()` on engine reset — this specific mechanism looks
correct." Kodu yenidən nəzərdən keçirdim: `_proposed_keys` yoxlaması hər iki
strategiyada (`continuation.py`) R:R gate-dən DƏRHAL sonra, `TradeSetup` yaradılmazdan
əvvəl işləyir — yəni R:R gate BÜTÜN uğur yolunda (yeganə return-success nöqtəsi) tətbiq
olunur, heç bir yan-keçid yoxdur. `test_strategy_duplicate_setup_guard` və
`test_strategy_risk_reward_gate` (mövcud, `test_strategy_engine.py`) bunu artıq
doğrulayır. Əlavə düzəliş tələb olunmur — audit-in "correct" qeyd etdiyi doğrudur.

---

# FAZA 5 — Arxitektura Təmizliyi

## 1. `hasattr` sadələşdirməsi
[MODIFY] [application/services/market_state_builder.py](file:///Users/renaquliyeva/Desktop/tradebot/application/services/market_state_builder.py)

`SwingDetector.detect_incremental()`-in siqnaturu (`market_structure/swing_detector.py:195`)
`-> IncrementalSwingResult` (heç vaxt `None`, heç vaxt xam `Swing`) elan edir və bütün
`return` nöqtələri (3 ədəd) bunu təsdiqləyir. Deməli `append_bar()`-dəki
`hasattr(result, 'upgraded_swing')`, `hasattr(result, 'new_swing')`,
`elif hasattr(result, 'id')` (birbaşa Swing halı üçün) yoxlamaları ölü müdafiə kodu idi —
`IncrementalSwingResult` dataclass-ında bu sahələr default `None`/`False` ilə həmişə
mövcuddur. Sadələşdirildi: birbaşa `result.upgraded_swing`, `result.new_swing`,
`result.is_replacement` istifadə olunur.

## 2. Ölü memarlıq təbəqəsinin silinməsi (commit `fffc97e`)
Grep ilə təsdiqləndi ki, aşağıdakılardan HEÇ BİRİ `run_backtest.py`/`run_research.py`
tərəfindən istifadə olunmur, yalnız bir-birlərinə və öz testlərinə istinad edirlər:

- `application/ports/*` (inbound + outbound port protocolları)
- `application/dto/*` (yalnız ports təbəqəsi tərəfindən istifadə olunurdu)
- `application/services/trading_coordinator.py`
- `core/interfaces.py` (`IDataFeed`, `IExecutionProvider`, `IStrategy`)
- `strategy/base_strategy.py` (`core/interfaces.py`-nin YEGANƏ istifadəçisi idi, özü də heç yerdə subclass edilmirdi — `TradeSetupStrategy` protokolu ilə RƏQABƏTLİ, işlədilməyən ikinci abstraksiya)
- `tests/test_application_layer.py` (yalnız bu ölü təbəqəni test edirdi, 3 test)

`application/services/__init__.py` yeniləndi (`TradingCoordinatorService` export-u silindi).

**Qeyd — `core/interfaces.py` və FAZA 6:** `IExecutionProvider` FAZA 6-nın hədəfidir
("MT5 connector-u real IExecutionProvider-ə çevir"), amma hazırda `mt5/connector.py`
onu HEÇ İSTİFADƏ ETMİR (audit: "the current connector is a login helper, not an
execution engine"). İstifadəçinin öz FAZA 5 planı bu faylı açıq şəkildə silinməli kimi
adlandırdığı üçün (`"application/ports/*, core/interfaces.py, TradingCoordinator: sil"`)
silindi — FAZA 6 başlayanda real IExecutionProvider kontraktı yenidən (və bu dəfə faktiki
`mt5/connector.py`-ə bağlı şəkildə) yazılacaq.

## 3. Root-level debug/duplicate faylların silinməsi (eyni commit)
- `debug_market_state_builder.py`, `debug_swing_graph.py`, `manual_test.py` — `main()`-tipli
  manual skriptlər, `data/history/*.csv`-ə istinad edir (bu repoda mövcud deyil).
- `test_market_state.py`, `test_signal_diagnostics.py`, `test_swing_detector.py` (root) —
  `tests/test_market_state_builder.py` və `tests/test_swing_detector.py`-nin köhnə
  dublikatları.
- Doğrulama: `pyproject.toml`-da `testpaths = ["tests"]` olduğu üçün bu fayllar HEÇ VAXT
  pytest tərəfindən toplanmırdı (`--collect-only` ilə təsdiqləndi) — silinmə test sayına
  təsir etmədi.

## 4. `indicators/` paketinin silinməsi (commit `2df8e0a`)
Tapıntı: İKİ ayrı ATR implementasiyası var idi:
- `indicators/atr.py` — pandas-əsaslı, YALNIZ öz testi tərəfindən istifadə olunurdu.
- `smc/displacement.py::DisplacementDetector._calculate_tr_and_atr` — pandas-sız,
  list-əsaslı Wilder's ATR, **production-da aktiv istifadədədir** (`smc/pipeline.py`
  vasitəsilə).

`indicators/` paketinin bütün 5 modulu (atr, ema, macd, rsi, sma) grep ilə yoxlanıldı —
heç biri strategiya/backtest/SMC kodunda import olunmurdu, yalnız öz testləri onları
çağırırdı. İstifadəçiyə seçim təqdim olundu (saxla — gələcək kitabxana kimi, yoxsa sil).
**İstifadəçi silməyi seçdi.**

`smc/displacement.py`-dəki ATR-ə TOXUNULMADI — onu `indicators/atr.py` ilə birləşdirmək
pandas asılılığını domain/SMC koduna gətirərdi, bu isə audit-in tərif etdiyi "domain
purity"ni (pandas-sız core alqoritmlər) pozardı.

Silinən: `indicators/{atr,ema,macd,rsi,sma,__init__}.py` (6 fayl) +
`tests/test_{atr,ema,macd,rsi,sma}.py` (5 fayl, 23 test funksiyası).

## Doğrulama (FAZA 5, bütün addımlar)
- `hasattr` sadələşdirməsi: 202 → 202 PASS (davranış dəyişmədi).
- Ölü memarlıq təbəqəsi + root faylların silinməsi: 202 − 3 = **199 PASS, 0 FAIL** (yalnız ölü təbəqəni test edən 3 test silindi).
- `indicators/` silinməsi: 199 − 23 = **176 PASS, 0 FAIL** (hazırkı say).
- `run_backtest.py`/`run_research.py`/`strategy/*`/`backtest/*`/`application/**/*`-də silinən modullara qalan istinad yoxdur (grep ilə təsdiqləndi).
- Commit-lər: `fffc97e` (memarlıq + root təmizlik), `2df8e0a` (indicators/).

## Mühit qeydi (əlavə, plandan kənar, bloklayıcı deyil)
`run_backtest.py`-i import etmək cəhdi zamanı `mt5/history_downloader.py`-də
`ModuleNotFoundError: No module named 'MetaTrader5'` xətası aşkarlandı. Bu, Windows-only
MT5 SDK-sının macOS-da mövcud olmamasından qaynaqlanır (repo `c:/Users/Microsol/...`
yollarına görə əvvəllər Windows-da inkişaf etdirilib) — mənim bu sessiyadakı dəyişikliklərimlə
ƏLAQƏSİ YOXDUR (heç bir silinən modul bu importa təsir etmir, grep ilə təsdiqləndi). Fix
tələb olunmur, sadəcə qeyd üçün.

---

# Gələcək Təmizlik / Aşağı Prioritet

## Bug #18: `_classify_gap`-ə "daily_session_break" kateqoriyası — TƏXİRƏ SALINDI

Tapıntı: `data/download_history.py::detect_gaps` XAUUSD-in tam tarixi datasında (2022-04 →
bugün) 888 gap-i `unexplained_gap_possible_broker_interruption` kimi təsnif etdi. Araşdırma
göstərdi ki, bunların 93%-i eyni ~1 saat 15 dəqiqəlik pəncərədə (23:45/22:45 UTC → 01:00 UTC,
Bazar ertəsi–Cümə axşamı) 4 il boyu davamlı təkrarlanır — bu, broker nasazlığı deyil, spot
qızılın (XAUUSD) real gündəlik COMEX rollover/settlement fasiləsidir (FX cütlərində olmayan,
metal-a xas bir sessiya xüsusiyyəti). Validasiya sistemi düzgün işləyib: heç bir data
fabrikasiya olunmayıb, hər gap doğru loglanıb — bu, YALNIZ təsnifat etiketinin dəqiqliyi
məsələsidir, funksional təsiri yoxdur.

**Təxirə salınma səbəbi:** kosmetik/informativ, aşağı prioritet. İstifadəçi qərarı.

**Gələcək iş (edildikdə):** `_classify_gap`-ə yeni bir şərt əlavə et — gap müddəti təxminən
1-2.5 saat aralığındadırsa VƏ `previous_timestamp`-in saatı 21:00-23:59 UTC aralığındadırsa
VƏ `next_timestamp`-in saatı 00:00-01:30 UTC aralığındadırsa → `"daily_session_break"`.
Differential test tələb olunur (mövcud gap sayının/reason-larının dəyişmədiyini, yalnız
`unexplained_gap_possible_broker_interruption`-dan `daily_session_break`-ə keçən alt-çoxluğun
düzgün seçildiyini yoxlayan).

---

# `research/` Modulu Auditi — Tapıntılar (kod dəyişməyib, yalnız qeyd)

`research/` qovluğunun bütün modulları (walk_forward, research_optimizer, monte_carlo,
robustness, stability, dashboard) və `tests/test_research.py` audit edildi. Look-ahead bias
tapılmadı (walk_forward.py-də train/val bölgüsü düzgün, sıfır overlap), Monte Carlo/robustness
simulyasiyaları həqiqidir (sabit seed yoxdur, real təsadüfi resampling). Aşağıdakı 3 tapıntı
Bug #19/#20/#21 kimi qeydə alınıb, prioritet sırası ilə.

## Bug #19 (KRİTİK — Mərhələ A-dan ƏVVƏL həll olunmalı): Grid search-də max_iter tətbiq olunmur

`research/research_optimizer.py:64-66` — `max_iter` YALNIZ `method == "random"` olanda
işləyir. Defolt `method="grid"` üçün `itertools.product(*values)` (sətir 62) bütün
kombinasiyaları HEÇ BİR HƏDD OLMADAN işə salır. Real bir grid (5 strategiya + 7 backtest
parametri, hərəsi 8 dəyər) `8⁵ = 32,768` tam `BacktestEngine.run()` çağırışına bərabərdir —
xəbərdarlıq olmadan saatlarla/günlərlə işə düşə bilər.

**Düzəliş istiqaməti (hələ tətbiq edilməyib):** ya defolt `method`-u `"random"` et (max_iter
ilə məhdudlaşdırılmış), ya da `method="grid"` seçilərkən əvvəlcə kombinasiya sayını hesabla,
konfiqurasiya edilə bilən bir həddi aşarsa `ValueError`/xəbərdarlıq at — istifadəçi bilə-bilə
davam etməli, səssizcə saatlarla işə düşməsin.

## Bug #20 (Orta prioritet): dashboard.py-in gözlədiyi "best_pnl" açarı optimizer nəticəsində yoxdur

`research/dashboard.py:69` — `opt_data.get("best_pnl", 0.0)` oxuyur, amma
`ParameterOptimizer.optimize()` (research_optimizer.py:123) YALNIZ `best_params` qaytarır —
`"best_pnl"` açarı heç vaxt mövcud olmur. Nəticə: bu dəyər HƏMİŞƏ `0.0`-a defolt olur,
`dashboard.py:70`-dəki `best_pnl > 0` yoxlaması heç vaxt keçmir, **"Optimization Score" daimi
"N/A" qalır** — göndərilmiş, amma heç vaxt aktivləşməyən bir xüsusiyyət.

**Düzəliş istiqaməti (hələ tətbiq edilməyib):** `ParameterOptimizer.optimize()`-in qaytardığı
struktura `best_pnl` (və ya digər uyğun metrik) əlavə et, `dashboard.py`-in gözlədiyi API ilə
uyğunlaşdır. Test yoxdur — bu bug mövcud test suite tərəfindən tutulmur, düzəlişlə birlikdə
regression testi yazılmalıdır.

## Bug #21 (Yüksək prioritet, praktik əhəmiyyətli): research/ modulunda StrategyDiagnostics istifadə olunmur

Heç bir `research/` modulu `StrategyEngine.get_diagnostics()` (strategy_engine.py:49-65) və ya
`StrategyDiagnostics.summary()` çağırmır. Fold/sınaq 0 trade verəndə (SMC-nin sərt şərtləri ilə
tez-tez baş verir — bax: bu sessiyanın ADDIM 2 tapıntısı, "no_trend"/"wrong_zone" dominant rədd
səbəbləri), rədd səbəbi görünmür — FAZA 3.5-in diaqnostika xüsusiyyəti ilə inteqrasiya
EDİLMƏYİB, parametr axtarışı nəticələrinə etimadı azaldır.

**Düzəliş istiqaməti (hələ tətbiq edilməyib):** `walk_forward.py`, `research_optimizer.py`,
`robustness.py`-də hər fold/sınaq üçün `strategy_engine.get_diagnostics()`-i nəticə obyektinə
əlavə et, ki 0-trade halların səbəbi görünsün.

**Ümumi qeyd:** `tests/test_research.py`-in 5 testi hamısı 30-bar-lıq "flat/siqnalsız" dummy
fixture istifadə edir, sıfır real trade yaradır — yuxarıdakı 3 bug-ın heç biri mövcud test
suite tərəfindən tutulmur. Hər üçü tətbiq ediləndə yeni, real trade generasiya edən test
fixture-ları tələb olunacaq.

---

# Bug #22 — 0-Trade Kök-Səbəb Araşdırması və Düzəlişi (commit `b176eeb`)

EURUSD/GBPUSD/USDJPY tam tarixi datasında (4 il, 100k+ bar) HƏR İKİ continuation
strategiyasının **dəqiq 0 trade** yaratdığı aşkarlandı. Kök-səbəb araşdırması göstərdi ki,
`smc/mitigation.py::check_mitigation` "toxunma"nı (`bar.low <= zone.high`) "tam mitigasiya"
kimi işarələyirdi — riyazi olaraq isbat edildi ki, `bar.low <= bar.close` universal olaraq
doğru olduğu üçün, strategiyanın "qiymət zonadadır" şərti (`price <= ob.high`) AVTOMATİK
olaraq mitigation trigger şərtini də doğrulayırdı — yəni "zonadadır VƏ hələ mitigated deyil"
halı STRUKTURAL olaraq heç vaxt baş verə bilməzdi. Düzəliş: mitigation indi YALNIZ bar
zonanın ƏKS TƏRƏFİNDƏN TAM bağlananda (`close < zone.low` bullish üçün və s.) tetiklənir —
sadə toxunma/retest artıq zonanı etibarsız etmir. Detallar üçün commit mesajına bax.

## Bug #23 (Yüksək prioritet, AYRICA sessiya tələb edir — DÜZƏLDİLDİ, bax aşağıda "Bug #23 Düzəlişi"): trend/break asinxronluğu

Bug #22 araşdırması zamanı İKİNCİ, MÜSTƏQİL bir bloklayıcı problem aşkarlandı: konkret
nümunələrdə (məs. `2022-07-05`, 15+ ardıcıl bar) `structure_state.trend == BULLISH` olduğu
halda, `breaks_history[-1]` (son struktur break) **köhnə, LOW-a aid, əks-istiqamətli bir
CHoCH** idi (23-36 bar yaşında və artmaqda). Səbəb: `structure_state.trend`
(`MarketStructureEngine`-də swing HH/HL/LH/LL pattern-i əsasında) və `breaks_history`
(`check_structural_break`-də price-un major swing-i keçməsi əsasında) **iki müstəqil
mexanizmdir** — trend YENİ bir break hadisəsi olmadan, sadəcə swing əlaqələri dəyişərək
BULLISH-ə keçə bilər, halbuki tarixdəki SON break hələ də əvvəlki (bearish) rejimə aid,
köhnə bir CHoCH ola bilər. Nəticədə `strategy/continuation.py`-in Rule 2/3 yoxlaması
(`last_break.break_type != BOS` / `broken_swing.type != HIGH`) bu bar-ları rədd edir —
`last_break_not_bos` (EURUSD-də 2,813) və `break_wrong_swing_type` (861) rədd səbəblərinin
əsas mənbəyi budur.

**Niyə indi düzəldilmir:** bu, Bug #22-dən TAMAMİLƏ fərqli bir kateqoriyadır (mitigation
tərifi deyil, trend-hesablama ilə break-tarixçəsi arasındakı sinxronizasiya) və diqqətli,
ayrıca bir sessiya tələb edir — mövcud `max_break_age_bars` konfiqurasiya parametri (hazırda
`None`/sönük) staleness-i qismən həll edə bilər, amma `break_wrong_swing_type` üçün (köhnəlik
deyil, tam səhv NÖV) ayrı bir yanaşma lazımdır: məsələn, strategiyanın "son BOS"u YOX,
"cari trend istiqamətinə uyğun SON BOS"u axtarması. Bu, `MarketStructureEngine`/`breaks_history`
strukturuna toxunma tələb edə bilər, riskli bir dəyişiklikdir.

**Gələcək iş:** ayrıca sessiyada, Bug #22-nin düzəlişindən sonra yenidən ölçülməli (Bug #22
öz-özlüyündə bir çox bar-ı bu mərhələyə belə çatdırmayacaq ola bilər, ona görə real təsirini
görmək üçün əvvəlcə Bug #22-nin nəticələrini müşahidə etmək lazımdır).

**Doğrulama (Bug #22 tətbiqindən sonra, EURUSD tam tarixi, 99,950 bar):** `setups_generated`
0-dan **21 (Bullish) + 27 (Bearish) = 48 trade / 4 il**-ə qalxdı. `no_displacement`,
`duplicate_setup`, `rr_gate_failed` rədd səbəbləri İLK DƏFƏ görünür (əvvəllər 0 idi) — bar-ların
artıq bütün pipeline-ı (OB→FVG→likvidlik→displacement→R:R→duplikat) keçdiyini sübut edir.
AMMA `no_trend`/`wrong_zone`/`last_break_not_bos`/`break_wrong_swing_type` demək olar
dəyişməyib — **Bug #23 hələ də dominant maneədir**, Bug #22-dən qat-qat böyük təsirə malikdir.

## Bug #23 Düzəlişi (commit `ecaad0c`) və `max_break_age_bars` Ölçüsü

**Seçilən istiqamət:** əvvəlki dizayn təhlilindəki 5 istiqamətdən **2/4** (ən aşağı risk) —
`breaks_history[-1]`-i kor-koranə götürmək əvəzinə, strategiyalarda paylaşılan
`_find_latest_matching_bos()` helper-i (`strategy/continuation.py`) `breaks_history`-də geriyə
axtarıb **trend-ə uyğun istiqamətdə olan ən son BOS**-u tapır (CHoCH-ları və əks-istiqamətli
BOS-ları keçərək). `max_break_age_bars` (Bug #9) tapılan bu BOS-un yaşına dəyişiklik olmadan
tətbiq olunur (`last_break` dəyişəni artıq tapılan break-i saxlayır).

**Doğrulama (EURUSD tam tarixi, 99,950 bar):** `setups_generated` 48-dən **68**-ə qalxdı (+42%),
`last_break_not_bos` kəskin azaldı (Bullish 2,795→645, Bearish 2,656→438) — düzəlişin gözlənilən
effekti dəqiq təsdiqləndi.

**AMMA tam backtest (real trade simulyasiyası) gözlənilməyən nəticə göstərdi: gəlirlilik
YAXŞILAŞMADI, PİSLƏŞDİ:**

| Ssenari | Trade | Win Rate | PF | Net Profit | Max DD |
|---|---|---|---|---|---|
| Baseline (Bug #23-dən əvvəl) | 48 | 39.6% | **0.91** | -$280.09 | 8.84% |
| Bug #23 fix, yaş limiti yox | 68 | 32.4% | 0.65 | -$1,621.35 | 16.21% |

**`max_break_age_bars` ölçüsü:** `_find_latest_matching_bos`-un tapdığı BOS-ların yaş
paylanması ölçüldü (bütün 68 setup, bar sayı ilə) — min=11, p25=20, **median=29**, p75=41.2,
**p90=54.6**, max=99. Bu ölçüyə əsasən 3 namizəd (median=29, p75=41, p90=55) EURUSD tam
tarixində ayrıca tam backtest edildi:

| Ssenari | Trade | Win Rate | PF | Net Profit | Max DD |
|---|---|---|---|---|---|
| A (median, age≤29) | 35 | 31.4% | 0.60 | -$1,010.38 | 12.68% |
| B (p75, age≤41) | 51 | 31.4% | 0.61 | -$1,391.10 | 16.00% |
| C (p90, age≤55) | 61 | 34.4% | 0.72 | -$1,162.52 | 11.63% |

**Qərar: `max_break_age_bars` default `None` olaraq saxlanıldı, heç bir namizəd tətbiq
edilmədi.** Səbəb: **heç bir namizəd orijinal baseline-ı (PF 0.91) bərpa etmir** — ən yaxşısı
belə (C, age≤55, PF 0.72) ondan aşağı qalır, daha çox trade açır və daha yüksək drawdown
daşıyır. Üstəlik ən sərt namizəd (A, median=29) ən PİS PF-i verdi (0.60) — yəni sərtliyin
özü keyfiyyəti bərpa etmir, sadəcə sayı azaldır.

**Kök nəticə: yaş həddi kök problemi həll etmir, çünki problem BOS-un YAŞINDA deyil,
KEYFİYYƏTİNDƏDİR.** `_find_latest_matching_bos` strukturca "düzgün" (trend-ə uyğun istiqamətli
BOS) bar-ları tapır, amma bu BOS-un TAPILDIĞI kontekstin (hansı hərəkətin/leg-in nəticəsi
olduğunun) keyfiyyətini qiymətləndirmir — sadəcə "nə qədər köhnədir" sualına cavab verir,
"bu strukturca güclüdürmü" sualına yox.

**VACİB ƏLAQƏ — Bug #11-in prioriteti YENİDƏN qiymətləndirilməlidir.** Bu tapıntı, əvvəllər
təxirə salınmış [Bug #11: Eyni Displacement Leg Tələbi](#bug-11-eyni-displacement-leg-tələbi--təxirə-salındı)-nin
nə üçün lazım olduğunu göstərir: real backtest datası göstərir ki, sadə yaş-filtri (BOS nə
qədər köhnədir) kifayət deyil — BOS-un strukturla ƏLAQƏSİ (hansı displacement leg-ə aid
olduğu) əsl keyfiyyət göstəricisi ola bilər, sırf bar-sayı yaşı yox. Bug #11 Mərhələ A
refaktorundan sonra (yeni strategiya çərçivəsi daxilində) yenidən nəzərdən keçirilməlidir.

---

# Strategiya Çərçivəsi Roadmap-ı (Mərhələ A/B/C/D)

Bu bölmə istifadəçi ilə əvvəlki müzakirədən qeydə alınır ki, gələcək sessiyalarda kontekst
itməsin. Bu, FAZA 0-5-dən (arxitektura təmizliyi) VƏ FAZA 6-dan (real icra, `IExecutionProvider`)
AYRI, paralel bir roadmap-dır.

## Mərhələ A — Strategiya Çərçivəsi

Hazırkı tək-strategiya strukturunu (`BullishContinuationStrategy`/`BearishContinuationStrategy`)
**çoxlu strategiyanı** dəstəkləyən bir sistemə çevirmək:
- Ortaq `BaseStrategy` interfeysi
- Strategiya reyestri (registry)
- Config-əsaslı aktivləşdirmə (hansı strategiyaların işə düşəcəyi konfiqurasiya ilə idarə olunur)
- Mövcud `backtest/`, `risk/`, diaqnostika (`StrategyDiagnostics`) HƏR strategiya üçün
  AVTOMATİK işləməlidir (hər yeni strategiya üçün ayrıca inteqrasiya kodu yazılmamalıdır)

## Mərhələ B — Siqnal Mühərriki

Aktiv strategiyaları real-vaxt/son bar üzərində işlədib **"BUY/SELL + entry/SL/TP + səbəb"**
formatında siqnal verən YENİ komponent. **REAL ORDER GÖNDƏRMİR** — yalnız siqnal göstərir.
FAZA 6-dan (real icra) AYRIDIR, ondan ƏVVƏL gəlir.

## Mərhələ C — İstifadəçinin 2 Hazır Strategiyası

TradingView Pine Script-dən Python-a portlanacaq iki konkret strategiya:
- "5 Candle Accumulation Breakout Retest"
- "NASDAQ Midline Sweep"

Hər ikisi Mərhələ A-nın `BaseStrategy` interfeysinə uyğun tətbiq olunmalıdır.

## Mərhələ D — Avtomatik Pattern Kəşfi

Sistemin köhnə datanı təhlil edib, təkrarlanan pattern-ləri ÖZÜ tapıb strategiya kimi qeydə
alması. **MƏCBURİ qoruma qatı:** statistik əhəmiyyətlilik + out-of-sample doğrulama +
çoxlu-müqayisə düzəlişi (multiple-comparison correction, overfitting-ə qarşı).

**ŞƏRT:** Mərhələ D **YALNIZ** `research/` modulunun auditində tapılan Bug #19/#20/#21 TAM
həll olunduqdan SONRA başlana bilər (Bug #19-un kontrolsuz grid search-i və Bug #21-in
diaqnostika görünməzliyi, pattern-kəşfi kimi avtomatlaşdırılmış, nəzarətsiz axtarışda XÜSUSİLƏ
təhlükəlidir — səssizcə saatlarla işləyən VƏ nəyin niyə rədd edildiyini göstərməyən bir sistem
overfitting-i gizlədə bilər).

## Sıralama Qərarları (bloklayıcı aydınlaşdırmadan sonra)

1. **Bug #23** — Mərhələ A refaktorundan ƏVVƏL, hazırkı (köhnə) strukturda düzəldiləcək —
   təkrar iş riskindən (düzəlişi həm köhnə, həm yeni strukturda etmək) qaçmaq üçün.
2. **Bug #19/#20/#21** — Mərhələ A/B/C-ni BLOKLAMIR (bunlar `research/` paketindədir,
   `strategy/`-yə asılılığı yoxdur). YALNIZ Mərhələ D-dən əvvəl məcburidir.

---

# Mərhələ C — Strategiya #1: AccumulationBreakoutStrategy (commit `564b6d4`, `7ca0293`)

İstifadəçinin "5 Candle Accumulation Breakout Retest" Pine Script strategiyası
`strategy/accumulation_breakout.py`-a portlandı (`TradeSetupStrategy` interfeysinə uyğun,
sessiya-əsaslı state machine, [strategy/continuation.py](strategy/continuation.py)-dan fərqli
olaraq bar-lar arası daxili yaddaş saxlayır). Detallar üçün commit mesajlarına bax.

**Sessiya vaxtı düzəlişi (commit `7ca0293`):** ilkin versiya `session_start`/`session_end`-i
birbaşa UTC saat kimi müqayisə edirdi. İstifadəçi düzəldi: orijinal Pine sessiyası ("0930-1100")
əslində **NY birja vaxtı**dır, sabit UTC saat DEYİL. Düzəliş: `zoneinfo`/`America/New_York` ilə
hər bar-ın UTC vaxtı DST-ə görə (yay/qış) düzgün NY yerli vaxtına çevrilir
(`session_timezone` konfiqurasiya edilə bilir, default `"America/New_York"`).

## EURUSD Tam Tarixi Nəticəsi — Sessiya Müqayisəsi

Backtest EURUSD tam tarixində (99,950 bar) HƏM sabit-UTC (düzəlişdən əvvəlki, texniki səhv),
HƏM DƏ DST-düzgün (əsl NY, düzəlişdən sonrakı) versiyalarla aparıldı:

| Ssenari | Trade | Win Rate | PF | Net Profit | Max DD |
|---|---|---|---|---|---|
| **DST-düzgün (əsl NY), volume_filter=True** | 52 | 28.9% | 0.68 | -$1,225.39 | 15.31% |
| **DST-düzgün (əsl NY), volume_filter=False** | 70 | 34.3% | **0.87** | -$642.79 | 15.71% |
| _Arxiv — sabit-UTC (London-a təsadüf), volume_filter=True_ | 68 | 35.3% | 0.92 | -$393.24 | 9.78% |
| _Arxiv — sabit-UTC (London-a təsadüf), volume_filter=False_ | 79 | 27.9% | 0.65 | -$2,014.55 | 23.99% |

**Əsas nəticə (istifadəçinin qərarına görə): DST-düzgün NY-sessiya nəticələridir** — sabit-UTC
versiya strategiyanın əsl formasını əks etdirmir, texniki səhvin təsadüfi məhsuludur, qərar üçün
istifadə edilməməlidir.

**Gələcək araşdırma namizədi (İNDİ TƏQİB EDİLMİR):** sabit-UTC pəncərəsi (09:30-11:00 UTC, London
sessiyasına təsadüf edir) empirik olaraq NY sessiyasından xeyli üstün çıxdı (PF 0.92 vs 0.68,
volume_filter=True müqayisəsində). İstifadəçi qərarı: bu, **overfitting riski** daşıyır — çoxlu
konfiqurasiya sınağından təsadüfən yaxşı çıxan tək bir nəticədir, Mərhələ D-nin qoruma prinsiplərinə
(statistik əhəmiyyətlilik + out-of-sample doğrulama + çoxlu-müqayisə düzəlişi) uyğun diqqətli
sınaq TƏLƏB EDİR. İndi təqib edilmir, yalnız qeydə alınır.

## Strategiya #2 (NASDAQ Midline Sweep) üçün simvol yoxlaması

MT5 terminalı artıq quraşdırılıb və qoşulub (MetaQuotes-Demo hesabı, login `67660753`).
`mt5.symbols_get()` ilə broker-in tam simvol siyahısı (12,698 simvol) axtarıldı:
**`USTEC`** ("US Tech 100 Index") tapıldı — point=0.01, digits=2, tarixi M15 datası
(`mt5.copy_rates_from_pos`) real qiymətlərlə (~29,500-29,600 səviyyəsi) mövcuddur.
`USTECH100M` (mikro-lot variantı) da mövcuddur. Nəticə: Strategiya #2 EURUSD-ə uyğunlaşdırma
TƏLƏB ETMİR, `USTEC` üzərində birbaşa portlana bilər — amma `data/history/`-də hələ USTEC CSV-si
yoxdur, backtest üçün əvvəlcə `mt5/history_downloader.py` ilə endirilməlidir.

## Bug #24 / #24b — HƏLL EDİLDİ (commit `0782fbd`, `b5023c9`): `CSVDataProvider` "spread" sütununu oxumur

USTEC üçün M5 tarixi data endirilərkən (`data/download_history.py`) aşkarlandı ki, MT5-in
`spread` sahəsi points-dədir (qiymət vahidi deyil) — `_rows_to_bars()` bunu simvolun
`point` ölçüsünə (məs. EURUSD 0.00001, USTEC 0.01) vurmadan birbaşa `Bar.spread`-ə yazırdı
(commit `48b1a61`-də düzəldildi). Araşdırma zamanı daha dərin bir tapıntı üzə çıxdı:
`data/csv_provider.py::CSVDataProvider.load()` CSV oxuyarkən `target_columns` siyahısı ilə
"spread" sütununu tamamilə atır — nəticədə `Bar.spread` CSV-də nə yazılıb-yazılmamasından
asılı olmayaraq həmişə `0.0`-dır (birbaşa test edildi: EURUSD-in 99,950 bar-ının hamısı, xam
CSV-də 50,668-i qeyri-sıfır spread daşısa da, yükləndikdən sonra `Bar.spread == 0.0`). Bunun
nəticəsində `BacktestEngine._effective_spread()` hər trade üçün CSV-dəki real, zamanla
dəyişən spread-i deyil, sabit `BacktestConfig.spread` dəyərini işlədir.

**Araşdırma zamanı üzə çıxan əlavə tapıntı (Bug #24c, REAL DEYİL):** `EURUSD_M15`/`GBPUSD_M15`/
`USDJPY_M15`/`XAUUSD_M15` CSV-ləri (Bug #30 fix-indən ƏVVƏL endirilmiş, 2022 tarixli) spread
sütununda xam, ÇEVRİLMƏMİŞ MT5 points daşıyır (məs. USDJPY max=335) — buna qarşı fayl-səviyyəli
qoruma DEYİL, "spread"-in `target_columns`-a sadəcə əlavə edilməsi ilə YANAŞI M1/M5 (Bug #30-dan
SONRA endirilmiş) fayllarda bu problem yoxdur. USDJPY-nin `point` ölçüsünün (0.001, yoxsa 0.01)
düzgün istifadə olunduğu QİYMƏT DƏQİQLİYİ (3 ondalıq rəqəm) və spread-in miqyası (real broker
spread-lərinə uyğun, 0.4-3.2 pip) ilə DOLAYI TƏSDİQ EDİLDİ — Bug #24c ayrıca düzəliş TƏLƏB ETMİR.

**Düzəliş (commit `0782fbd` — Bug #24):**

- `CSVDataProvider.load()`-da `target_columns`-a "spread" əlavə edildi, `Bar` konstruksiyasında
  mövcud `getattr` fallback-i saxlanıldı (köhnə, spread sütunu olmayan CSV-lər üçün geriyə
  uyğunluq — `Bar.spread` yenə `0.0`-a düşür, sükut).
- `BacktestEngine._effective_spread()`-də HEÇ BİR dəyişiklik lazım olmadı — mexanizm onsuz da
  `candle.spread > 0.0` olduqda onu üstün tuturdu.
- Heç bir validasiya ƏLAVƏ EDİLMƏDİ (qərar: ən sadə, ən az-risk yol) — sütun sadəcə ötürülür.

**Düzəliş (commit `b5023c9` — Bug #24b):** NaN/mənfi spread dəyərləri artıq sükutla axmır —
`_sanitize_spread_column()` bunları loglanan xəbərdarlıqla `0.0`-a salır (bu bar üçün
`BacktestEngine` `config.spread`-ə düşür).

**Test:** `tests/test_csv_provider.py`-ə 6 differensial test əlavə olundu (sütunsuz köhnə CSV,
düzgün formatlı CSV, NaN spread, mənfi spread). Tam suite (503 test) reqressiyasız keçdi.

**Midline Sweep USTEC OOS nəticəsinin YENİDƏN yoxlanılması** (`--spread` arqumenti VERİLMƏDƏN,
sütun avtomatik oxundu): 106/106 trade EYNİ qaldı (siqnallar/vaxtlama dəyişməyib), PF
**1.0503 → 1.0510** (flat `--spread=1.0`-dan avtomatik, per-bar real spread-ə keçiddə). Bu,
gözlənilən, kiçik (0.0007 PF, ~$5 Net Profit) fərqdir — REQRESSİYA DEYİL, sadəcə sabit spread
əvəzinə hər bar-ın öz real spread-inin işlədilməsinin təbii nəticəsidir. Əvvəlki "Audit #17"
bölməsindəki **1.0958** rəqəmi bu düzəlişdən ƏVVƏLKİ, sənəd-daxili uyğunsuzluq idi (yəqin
sessiyalar arası, aralıq commit-lər səbəbindən) — **1.0510 indi DOĞRU, GÜNCƏL istinad rəqəmidir.**

**Status: BAĞLANDI.**

## Strategiya #3 (Opening Range Breakout) — USTEC/XAUUSD M1 Nəticəsi

`strategy/opening_range_breakout.py`-da portlandı (bax commit `37ed7b2`). M1 tarixi datası
yalnız broker limitinə görə ~3.3-3.5 ay geriyə gedir (USTEC: 2026-03-30-dan, XAUUSD:
2026-03-25-dan). 4 kombinasiya (2 simvol × 2 risk_reward) tam tarixdə backtest edildi:

| Ssenari | Trade | Win Rate | PF | Net Profit | Max DD |
|---|---|---|---|---|---|
| USTEC-2R | 52 | 26.9% | 0.66 | -$1,311.55 | 13.87% |
| **USTEC-3R** | 45 | 28.9% | **1.09** | **+$359.75** | 12.14% |
| XAUUSD-2R | 75 | 29.3% | 0.62 | -$2,032.37 | 23.01% |
| XAUUSD-3R | 73 | 21.9% | 0.67 | -$1,971.30 | 23.43% |

**VACİB QEYD — USTEC-3R (PF 1.09, +$359.75, 45 trade/~3.3 ay) YALNIZ İLKİN, KİÇİK-NÜMUNƏ
göstəricisidir, TƏSDİQ ÜÇÜN DEYİL.** Statistik əhəmiyyətlilik üçün minimum bunlar lazımdır:
(a) daha uzun tarixi dövr (broker limitinə görə hazırda mümkün deyil, YALNIZ vaxt keçdikcə
broker-in M1 tarixçəsi uzanacaq), (b) ya da eyni strategiyanın DİGƏR indekslər/simvollarda
(əgər mövcuddursa) təsdiqlənməsi. Bu nəticəyə əsasən HEÇ BİR kommersiya/canlı-ticarət qərarı
verilməməlidir.

Bug #28 ölçüsü: `pending_order_expiry_bars=1` default-unun bu strategiyaya təsiri ölçüldü
(`setups_generated` vs faktiki `total_trades`) — fərq cəmi ~2% (USTEC-2R: 53→52, USTEC-3R:
46→45), 15-20% həddindən çox aşağı. Dəyişiklik tələb olunmur, default saxlanıldı.

### USTEC-3R In-Sample / Out-of-Sample Bölgüsü (`research/run_strategy_backtest.py`)

Yuxarıdakı USTEC-3R tam-data nəticəsinin (PF 1.09, 45 trade) təsadüfi/overfitting olub-olmadığını
yoxlamaq üçün, EYNİ parametrlərlə (`risk_reward=3.0`, digər hər şey `run_strategy_backtest.py`
default-ları: spread=0.0002, commission=0, risk_per_trade=0.01, initial_balance=10000), heç bir
tənzimləmə edilmədən, data xronoloji olaraq 70/30 bölündü (`--split in_sample/out_of_sample`,
`--split-ratio 0.7`). Metodologiyanı təsdiqləmək üçün eyni skriptlə tam-data (`--split full`)
da YENİDƏN icra edildi:

| Metrika | Tam data (bu skriptlə YENİDƏN icra) | Tam data (əvvəlki sənəd, fərqli parametrlər) | In-Sample (ilk 70%) | Out-of-Sample (son 30%) |
|---|---|---|---|---|
| Trade sayı | 45 | 45 | 30 | 15 |
| Win Rate | 28.9% | 28.9% | 40.0% | 13.3% |
| Profit Factor | 1.18 | 1.09 | 1.92 | 0.47 |
| Net Profit | +$646.57 | +$359.75 | +$1,898.16 | -$690.38 |
| Max Drawdown | 11.41% | 12.14% | 4.90% | 10.47% |
| Tarix aralığı | 2026-03-30 → 2026-07-09 (~3.3 ay) | 2026-03-30 → ~2026-07-09 (~3.3 ay) | 2026-03-30 → 2026-06-09 | 2026-06-09 → 2026-07-09 |

**Qeyd — tam-data reproduksiyasında uyğunsuzluq:** trade sayı (45) və win rate (28.9%) əvvəlki
sənədləşdirilmiş nəticə ilə DƏQİQ üst-üstə düşür (eyni siqnallar/eyni qərarlar), AMMA PF (1.18 vs
1.09) və Net Profit ($646.57 vs $359.75) fərqlidir. Bunun ehtimal olunan səbəbi: `BacktestConfig.spread`
mütləq qiymət vahidindədir (Ask−Bid) və `run_strategy_backtest.py`-in default-u (`0.0002`) FX
cütləri üçün kalibrlənib (məs. EURUSD ~1.10 qiymət səviyyəsində 2 pip-ə bərabərdir). USTEC-in qiymət
səviyyəsi (~29,500) ilə müqayisədə 0.0002 mütləq spread demək olar ki sıfıra bərabərdir — əvvəlki
sənədləşdirilmiş nəticə isə görünür fərqli (USTEC-ə uyğun miqyaslanmış) spread/xərc parametrləri ilə
alınıb. Bu fərq mütləq PF rəqəmlərinə (1.18, 1.92, 0.47) təsir edir, AMMA in-sample/out-of-sample
müqayisəsi bu sessiyada EYNİ (bəlkə də qeyri-real dərəcədə aşağı) spread ilə, hər iki seqmentə
eyni şəkildə tətbiq olunduğu üçün, İKİSİ ARASINDAKI NİSBİ tənəzzül müşahidəsini etibarsız etmir.

### NasdaqMidlineSweepStrategy — Real Spread ilə USTEC M5 Analizi (Bug #24/#30 tətbiqi)

Bug #24 (`CSVDataProvider.load()` "spread" sütununu atır) hələ də deferred statusundadır (kod
dəyişməyib) — amma xam CSV-nin "spread" sütunu artıq düzgün qiymət-vahidindədir (Bug #30 fix-i:
MT5-in points-dəki dəyəri simvolun `point` ölçüsünə vurulub). `data/history/USTEC_M5.csv`-in
99,859 sətrindən ölçülüb: **median = 1.0, mean = 0.9743** (M1 datasındakı ilə eyni miqyas — 100
point × 0.01 point-size = 1.0, `tests/test_data_downloader.py:235` fixture-u ilə üst-üstə düşür).
Bu, `--spread 1.0` kimi `run_strategy_backtest.py`-ə birbaşa ötürüldü (default `0.0002` YOX).

`NasdaqMidlineSweepStrategy` default parametrlərlə (`{}`: range_size=10.0, risk_reward=2.0,
mid_buffer=5.0, body_multiplier=1.2, sma_period=20), USTEC M5 (2024-12-10 → 2026-07-09, ~19 ay,
99,859 bar) üzərində, EYNİ real spread ilə tam-data + 70/30 in-sample/out-of-sample bölgüsündə:

| Metrika | Tam data | In-Sample (ilk 70%) | Out-of-Sample (son 30%) |
|---|---|---|---|
| Trade sayı | 339 | 232 | 107 |
| Win Rate | 35.4% | 35.8% | 34.6% |
| Profit Factor | 1.03 | 1.05 | 0.99 |
| Net Profit | +$615.58 | +$664.24 | -$45.63 |
| Max Drawdown | 24.9% | — | — |
| Tarix aralığı | 2024-12-10 → 2026-07-09 | 2024-12-10 → 2026-02-05 | 2026-02-05 → 2026-07-09 |

Konsistentlik: In-Sample (232) + Out-of-Sample (107) = 339, tam-data ilə üst-üstə düşür. Bu ədəd
(339) istifadəçinin istinad etdiyi əvvəlki nəticə ilə də eynidir.

**ORB-dan fərq:** PF, in-sample-dən (1.05) out-of-sample-ə (0.99) YALNIZ xəfif azalıb — ORB-dakı
kimi (1.92→0.47) kəskin kollaps yoxdur.

#### Aylıq qruplaşma (339 trade, tam dövr)

| Ay | Trade | Win | Loss | Win Rate | Aylıq P&L |
|---|---:|---:|---:|---:|---:|
| 2024-12 | 6 | 1 | 5 | 16.7% | -$311.62 |
| 2025-01 | 15 | 4 | 11 | 26.7% | -$332.06 |
| 2025-02 | 19 | 9 | 10 | 47.4% | +$672.96 |
| 2025-03 | 20 | 3 | 17 | 15.0% | -$1,102.89 |
| 2025-04 | 20 | 6 | 14 | 30.0% | -$237.48 |
| 2025-05 | 21 | 4 | 17 | 19.0% | -$801.41 |
| 2025-06 | 21 | 9 | 12 | 42.9% | +$393.68 |
| 2025-07 | 10 | 3 | 7 | 30.0% | -$120.63 |
| 2025-09* | 15 | 6 | 9 | 40.0% | +$182.18 |
| 2025-10 | 22 | 12 | 10 | 54.5% | +$1,131.82 |
| 2025-11 | 18 | 8 | 10 | 44.4% | +$492.88 |
| 2025-12 | 21 | 11 | 10 | 52.4% | +$1,129.61 |
| 2026-01 | 21 | 5 | 16 | 23.8% | -$730.52 |
| 2026-02 | 20 | 11 | 9 | 55.0% | +$1,304.55 |
| 2026-03 | 22 | 10 | 12 | 45.5% | +$836.07 |
| 2026-04 | 21 | 6 | 15 | 28.6% | -$462.07 |
| 2026-05 | 18 | 3 | 15 | 16.7% | -$1,092.51 |
| 2026-06 | 22 | 8 | 14 | 36.4% | +$123.67 |
| 2026-07 | 7 | 1 | 6 | 14.3% | -$460.57 |

*2025-08-da trade yoxdur (data-da boşluq deyil, sadəcə setup baş verməyib).

#### Pik və pisləşmə başlanğıcı — split nöqtəsi ilə müqayisə

**Qlobal pik: 2026-04-15 (trade #282/339), kumulyativ P&L $2,577.48.** Bu, in-sample/out-of-sample
sərhədindən (2026-02-05) ~10 HƏFTƏ SONRA, yəni **out-of-sample dövrünün içərisində** baş verib —
ORB-dan fərqli olaraq, split nöqtəsi özü heç bir dönüş nöqtəsi ilə üst-üstə düşmür (split anındakı
kumulyativ P&L: son in-sample trade #232 = $664.27, ilk out-of-sample trade #233 = $555.90 —
kəskin bir sıçrayış/uçurum yoxdur, davamlı xətdir).

Pikdən sona qədər (trade #282 → #339, 57 trade): **15W/43L (win rate 25.9%)**, kumulyativ P&L
$2,577.48-dən $615.66-ya düşüb (-$1,961.82). Bu tənəzzül dövrü (2026-04-15 → 2026-07-09) aylıq
cədvəldə də görünür: aprel -$462, may -$1,093, iyun +$124 (qismən bərpa), iyul -$461.

**Nəticə (rəqəmlər, yozumsuz):** Split nöqtəsi (2026-02-05) ilə faktiki pisləşmənin başlanğıcı
(~2026-04-15) arasında ~10 həftəlik fərq var — ORB-dakı halın əksinə olaraq, burada in-sample/
out-of-sample bölgüsü təsadüfən pisləşmə anına düşməyib. Tam dövrün ilk yarısında (2025-03, 2025-05,
2026-01) da bənzər dərəcədə pis aylar mövcuddur (məs. 2025-03: -$1,102.89, 15% WR) — yəni yaxşı/pis
dövrlər arasında keçid ORB-dakı kimi TƏK bir kəskin sərhəd deyil, təkrarlanan dövriyyə xarakteri
daşıyır.

Tam 339 trade-lik xronoloji log (tarix, WIN/LOSS, P&L, kumulyativ P&L) CSV olaraq saxlanıldı:
[artifacts/ustec_midline_sweep_full_trade_log.csv](artifacts/ustec_midline_sweep_full_trade_log.csv).

### Midline Sweep Parametr Tənzimləməsi (yalnız in-sample) + Yekun Out-of-Sample Təsdiqi

Metodologiya: bütün parametr sınağı YALNIZ in-sample (232 trade, 2024-12-10 → 2026-02-05) datası
üzərində aparıldı; out-of-sample datasına YALNIZ BİR DƏFƏ, yekun təsdiq üçün toxunuldu.

**Grid (OFAT, 9 kombinasiya, tam faktorial 81 yerinə):** default + `range_size`(8/12),
`mid_buffer`(3/7), `body_multiplier`(1.0/1.5), `risk_reward`(1.5/2.5), hər dəfə YALNIZ bir
parametr default-dan fərqləndirilib, real spread=1.0 ilə:

| Kombinasiya | Trade | Win Rate | PF | Net Profit |
|---|---:|---:|---:|---:|
| default | 232 | 35.8% | 1.047 | +$664.24 |
| range_size=8 | 235 | 34.9% | 0.993 | -$92.94 |
| range_size=12 | 231 | 35.5% | 1.039 | +$568.40 |
| mid_buffer=3 | 232 | 35.8% | 1.047 | +$664.24 (default ilə eyni — bax aşağıdakı qeyd) |
| mid_buffer=7 | 232 | 35.8% | 1.047 | +$664.24 (default ilə eyni — bax aşağıdakı qeyd) |
| body_multiplier=1.0 | 237 | 34.2% | 0.966 | -$472.15 |
| **body_multiplier=1.5** | 227 | 36.1% | **1.068** | +$932.09 |
| risk_reward=1.5 | 233 | 40.8% | 0.960 | -$511.05 |
| risk_reward=2.5 | 231 | 30.7% | 1.043 | +$661.02 |

İstifadəçi in-sample nəticələrinə əsasən **`body_multiplier=1.5`**-i yekun out-of-sample təsdiqi
üçün seçdi (ən yüksək in-sample PF, 227 trade — statistik cəhətdən kifayət qədər böyük nümunə).

**Yekun, TƏKRARLANMAYAN out-of-sample testi** (`--split=out_of_sample --split-ratio=0.7
--spread=1.0 --params '{"body_multiplier": 1.5}'`):

| Metrika | Default (out-of-sample) | body_multiplier=1.5 (out-of-sample) |
|---|---:|---:|
| Trade sayı | 107 | 106 |
| Win Rate | 34.6% | 35.8% |
| Profit Factor | 0.99 | **1.05** |
| Net Profit | -$45.63 | **+$364.14** |
| Max Drawdown | 15.6% | 10.1% |

Bütün 5 metrikada (PF, Net Profit, Win Rate, Max DD, trade sayı davamlı qalıb) `body_multiplier=1.5`
default-u üstələyib — xüsusilə PF 0.99-dan (itki xətti) 1.05-ə (mənfəət xətti) keçib, Net Profit
mənfidən müsbətə dönüb. Metodologiyaya əsasən bu, YEKUN nəticədir — əlavə kombinasiya sınanmayacaq.

#### Gələcək təkmilləşdirmə qeydi: `mid_buffer` effektiv "ölü kod"-dur (kod dəyişməyib)

In-sample parametr grid axtarışında `mid_buffer=3` və `mid_buffer=7` nəticələri default-la (5)
onlarlıq kəsirə qədər EYNİ çıxdı (232 trade, PF 1.0470767758223678). Araşdırma (kod
`strategy/nasdaq_midline_sweep.py`) göstərdi ki, `mid_buffer` parametrinin özü düzgün qəbul
edilir və istifadə olunur (sətir 130, 224-225) — bug yoxdur. Səbəb riyazidir: setup yalnız HƏM
`close > mid + mid_buffer` (Rule 3, sətir 224) HƏM DƏ `close > mid + range_size` (Rule 4/sweep,
sətir 231-232, `range_size` default=10.0) doğru olanda yaranır. `mid_buffer < range_size` olan
istənilən halda (bütün sınanan dəyərlər: 3, 5, 7 — hamısı 10-dan kiçikdir), Rule 4-ün həddi
HƏMİŞƏ Rule 3-dən sərtdir, ona görə Rule 3 heç vaxt müstəqil rədd səbəbi olmur. Empirik təsdiq:
in-sample-də bütün 244 sweep-qapılı setup-un `close-mid` məsafəsi minimum 10.062-dir (heç biri
7-dən aşağı deyil) — kontrol testi (`mid_buffer=11`, range_size-dən yuxarı) isə 15 setup-u
blokladı, mexanizmin özünün işlək olduğunu təsdiqlədi.

**Nəticə: `mid_buffer` yalnız `range_size`-dan BÖYÜK dəyərlərdə effektivdir — hazırkı default-lar
altında (range_size=10, mid_buffer=5) bu parametr faktiki olaraq ölü koddur.** Gələcəkdə bu iki
parametr arasında ya bir invariant (`mid_buffer` `range_size`-ı üstələyə bilməz xəbərdarlığı və ya
avtomatik clamp) əlavə edilə bilər, ya da parametrin default aralığı yenidən nəzərdən keçirilə
bilər — kod indi dəyişməyib, yalnız sənədləşdirildi.

### `day_session_end` / `max_holding_bars` faktiki tətbiqi (USTEC Midline Sweep, tam data)

`research/run_strategy_backtest.py`-də strategiyanın `recommended_max_holding_bars()`-inin
`BacktestConfig.max_holding_bars`-a ötürülməsi məntiqi əvvəlki sessiyadan mövcud idi (sətir
223-235), amma bu günə qədər real testdə istifadə edilməmişdi. `BacktestEngine.run()`-un onu
faktiki tətbiq etdiyi təsdiqləndi (`backtest/engine.py:183-184`). USTEC M5, real spread=1.0, tam
data üzərində, `day_session_end=time(16,0)` (NYSE 6.5 saatlıq seans → `session_length_in_bars`
ilə 78 bar) ilə/olmadan müqayisə:

| Metrika | day_session_end=None (limitsiz, indiyədək bütün nəticələr) | day_session_end=16:00 (78 bar) |
|---|---:|---:|
| Trade sayı | 339 | 347 |
| Win Rate | 35.4% | 34.9% |
| Profit Factor | 1.03 | 1.05 |
| Net Profit | +$615.58 | +$1,126.93 |
| Max Drawdown | 24.9% | 24.9% (demək olar dəqiq eyni) |

Trade sayının 339-dan 347-yə artması gözlənilir (reqressiya deyil): limitsiz versiyada gecə/həftəsonu
boyu açıq qalan bəzi pozisiyalar indi seans sonunda məcburi bağlanır, bu da nəticəni (WIN/LOSS/EXPIRED
bölgüsünü) dəyişir. Yeni unit test (`tests/test_backtest_engine.py::test_max_holding_bars_forces_close_after_n_bars`,
sintetik data) bu məcburi bağlanma davranışını təsdiqləyir.

**Ayrıca kod dəyişikliyi:** In-sample tənzimləmə + yekun out-of-sample təsdiqinə əsasən (yuxarıdakı
bölmə), `NasdaqMidlineSweepStrategy`-nin `body_multiplier` default dəyəri `1.2`-dən `1.5`-ə
dəyişdirildi (`strategy/nasdaq_midline_sweep.py`). Tam suite (369 test) dəyişiklikdən sonra da
0 error/0 failure ilə keçdi.

---

# Bug #16 (TƏXİRƏ SALINDI, kod dəyişməyib): MarketStructureEngine Tam Rebuild Optimallaşdırılması

**Başlıq:** `MarketStructureEngine` tam rebuild-in optimallaşdırılması (performans, funksionallığa
təsiri yoxdur).

**Kontekst:** [application/services/market_state_builder.py](application/services/market_state_builder.py)-da
bir swing `is_replacement` halında əvəzlənəndə (yəni əvvəlki eyni-tipli swing daha ekstremal bir
swing ilə əvəz olunanda), hazırkı kod `self.structure_engine.reset()` çağırıb **bütün** swing
tarixini yenidən "oynadır" (hər swing-i yenidən `update()`-dən keçirir). Bu, bar-ların yalnız
~2%-ində baş verir, amma hər hadisə cari swing sayı ilə mütənasib xərcə malikdir — profiling-də
ümumi vaxtın ~17-19%-ni təşkil edən bir O(n²) mənbəyi kimi müəyyənləşdirilmişdi (Bug #15/#17
performans sessiyası, FAZA 3.3-dən sonra).

**Niyə təxirə salınıb:** Partial rebuild (yalnız dəyişən swing-dən sonrakı hissəni yenidən emal
etmək) nəzəri cəhətdən mümkün görünür, AMMA iki ciddi risk aşkarlanıb:
1. `handle_upgrade()` (Bug #1 düzəlişi) müstəqil şəkildə `structure_engine.last_major_high`/`low`
   göstəricilərini dəyişə bilər — bu, snapshot/restore sxemində "snapshot nə vaxt götürülüb,
   upgrade nə vaxt olub" sıralamasını kövrək edir.
2. Bu dəqiq ssenari artıq [tests/test_swing_detector_differential.py](tests/test_swing_detector_differential.py)-da
   (upgrade+replacement qarşılıqlı təsiri, Bug #1+#2 interaction) test olunub və "kövrək" olduğu
   sübut edilib — mövcud tam-rebuild yanaşması bu qarışıq halda DÜZGÜN nəticə verdiyini sübut
   edir, partial rebuild isə bunu YENİDƏN sübut etməli olacaq, uğursuz olarsa Bug #1/#2-nin
   qorunmasını poza bilər.

**Qərar:** Performans qazancı (17-19%) real olsa da, correctness riski ilə tərazidə YOXLAMA
(qeyri-mümkün) statusunda saxlanıldı — kod yazılmadı, YALNIZ sənədləşdirildi. Əgər gələcəkdə
(məsələn Walk-Forward/Monte Carlo mərhələsində) bu, YENİDƏN kritik performans maneəsi kimi üzə
çıxarsa, o zaman snapshot/restore məntiqini diqqətlə, əlavə differential testlərlə yenidən
qiymətləndirmək olar.

---

## Bug #25: Sükutla Atılan Çoxlu-Strategiya Konflikti (commit `6b58b8f`)

`BacktestEngine`, `strategy_engine.run()` eyni bar üzrə birdən çox setup qaytardıqda
`setups[0]`-ı götürüb qalanını sükutla atırdı — tək strategiya qeydiyyatdaykən problemsiz idi, amma
indi 4 strategiya (continuation×2, AccumulationBreakout, NasdaqMidlineSweep, OpeningRangeBreakout)
mövcud olduğundan, bir bar-da bir neçəsinin eyni anda işə düşməsi izsiz siqnal itkisinə səbəb ola
bilərdi.

Əlavə edildi: `BacktestResult.conflicting_setups_dropped` (policy-dən asılı olmayaraq `len(setups) >
1` olduqda artırılır — beləliklə atılma həmişə görünür), `BacktestEngine(conflict_policy=...)`
(default `"first"` əvvəlki davranışı dəqiq saxlayır — tam suite + yeni sıfır-reqressiya testi ilə
təsdiqləndi; `"log_and_first"` əlavə olaraq saxlanan/atılan `setup_id`-ləri xəbərdarlıq kimi
loglayır). Digər tie-break siyasətləri (məs. `confidence_score`-a görə seçim) konkret ehtiyac
yaranana qədər əlavə edilmədi.

4 yeni test: konflikt sayılır və `setups[0]` yenə qalib gəlir, tək setup heç vaxt konflikt
sayılmır, `log_and_first` xəbərdarlıq yaradır, default siyasət (log baxımından) səssiz qalır amma
yenə sayır.

**Status: DÜZƏLDİLDİ.**

## Bug #26: `MT5_LOGIN` Kövrəkliyi (commit `1739f4f`)

`Settings.MT5_LOGIN`-in `int(os.getenv(...))` çevrilməsi import vaxtı (dataclass field default
vasitəsilə) işləyirdi, `try/except` olmadan — `.env`-də yanlış (qeyri-rəqəm) `MT5_LOGIN` dəyəri
`import config.settings`-i, o cümlədən MT5 ilə heç bir əlaqəsi olmayan modulları da (məs.
`CSVDataProvider`) import edərkən `ValueError` ilə çökdürürdü.

`_parse_mt5_login()` funksiyası çıxarıldı — `ValueError`-u tutur, xarab xam dəyəri adlandıran
xəbərdarlıq loglayır, exception-u ötürmək əvəzinə `0`-a default edir.

6 test: helper birbaşa (düzgün sətir, `"0"`, yanlış mətn → `0` + logged warning), və
`importlib.reload()`-əsaslı iki test — `Settings.load()`-un özünün xarab `MT5_LOGIN` ilə uçdan-uca
sağ qaldığını və düzgün dəyəri bərpa etdiyini təsdiqləyir (hər biri `finally` blokunda modul
vəziyyətini bərpa edir ki, sonrakı testlər normal `Settings` sinfini görsün).

**Status: DÜZƏLDİLDİ.**

## Bug #27: `TradeSetup.strategy_name` Doldurulmurdu (commit `66d173e`)

`BacktestEngine` artıq dolan trade qeyd edərkən `getattr(pending_setup, "strategy_name", "")`
oxuyurdu, amma `TradeSetup` heç vaxt bu sahəni təyin etmirdi — hər `BacktestTrade.strategy_name`
həmişə sükutla `""` idi. `TradeSetup`-a `strategy_name: str = ""` əlavə edildi və hər konstruksiya
nöqtəsində (hər iki continuation strategiyası, `AccumulationBreakoutStrategy`,
`NasdaqMidlineSweepStrategy`, `OpeningRangeBreakoutStrategy`) `self.__class__.__name__` ilə təyin
olundu. Bir neçə strategiya birgə qeydiyyatda olduğundan, per-trade atributsiya
(`trades.csv`/backtest hesabatlarında) bundan sonra bərpaolunmaz idi.

Hər strategiya faylı üzrə bir setup-yaradan testə `strategy_name` assertion-u əlavə edildi — sahə
default dəyərə malik olduğundan tam suite başqa cür təsirlənmədi.

**Status: DÜZƏLDİLDİ.**

---

## Bug #29: `is_replacement` zamanı `breaks_history` korrupsiyası — TƏSDİQLƏNMİŞ (failing test ilə), DÜZƏLİŞ TƏTBİQ OLUNMAYIB

**Kontekst:** əvvəlki bir auditdə tapılmış tapıntı: `application/services/market_state_builder.py:64-72`-də
bir swing `is_replacement=True` halında əvəzlənəndə, `self.structure_engine.reset()` çağırılır və
BÜTÜN swing tarixi yenidən `update()`-dən keçirilir — AMMA `check_structural_break()` bu təkrar-
oynatma zamanı YENİDƏN ÇAĞIRILMIR. `MarketStructureEngine.reset()` (`structure_engine.py:110-143`)
`breaks_history`, `last_broken_high_id`, `last_broken_low_id` sahələrini sıfırlayır, replay isə
(yalnız `update()` çağırdığı üçün) bu üç sahəni HEÇ VAXT bərpa etmir.

### Tapıntının mahiyyəti (tam dizayn təhlili — kod dəyişməyib)

1. `reset()`-in sıfırladığı 17 sahədən 14-ü (`history`, `highs/lows_history`, `last_major/minor_*`,
   `current_hh/hl/lh/ll`, `last_*_relationship`, `current_trend`, `confirmations_count`,
   `processed_ids`, `last_index`) replay-in `update()` çağırışları ilə DÜZGÜN bərpa olunur. YALNIZ
   `breaks_history`, `last_broken_high_id`, `last_broken_low_id` bərpa olunmur — çünki bunlara
   YALNIZ `check_structural_break()` toxunur, replay isə onu çağırmır.
2. Nəticə: ƏVVƏLKİ break-lər (əvvəlki bar-larda düzgün aşkarlanmış) həmişəlik itir. Əlavə olaraq,
   `last_broken_*_id` unudulduğu üçün, əgər cari bar hələ də köhnə major high/low-dan kənardadırsa,
   `market_state_builder.py:74`-dəki tək `check_structural_break(bar)` çağırışı EYNİ swing üçün
   YENİ, DUBLİKAT bir break yaradır (əvvəlkindən GEC tarixli, potensial olaraq FƏRQLİ tipdə —
   BOS↔CHoCH, çünki `break_type` replay-dən sonrakı YENİ `current_trend`-ə görə hesablanır).
3. Praktiki təsir (`strategy/continuation.py::_find_latest_matching_bos`, Bug #23 düzəlişi): (a)
   YALANÇI-MƏNFİ — `breaks_history` boşaldığından sonra yeni break yığılana qədər real continuation
   setup-ları `NO_BREAK_HISTORY`/`BREAK_WRONG_SWING_TYPE` ilə səhvən rədd oluna bilər; (b) YALANÇI-
   MÜSBƏT (daha təhlükəli) — fantom təkrar-aşkarlanan break-in tipi YENİ trend kontekstinə görə
   `CHoCH`-dan `BOS`-a "sürüşərsə", `_find_latest_matching_bos()` bunu əsassız şəkildə etibarlı
   continuation-təsdiqi kimi qəbul edə bilər. Qismən qoruyucu: Rule 9 (`max_break_age_bars`)
   `broken_swing.index`-dən hesablandığı üçün (break-in öz indeksindən yox) bu konkret korrupsiyaya
   kor deyil.
4. Tezlik: `is_replacement` bar-ların ~2%-ində baş verir (Bug #16 profiling sessiyası). Amma faktiki
   korrupsiya YALNIZ `breaks_history` artıq boş olmadıqda maddi təsir göstərir — trend-li istənilən
   simvolda bu, demək olar ki, HƏR `is_replacement` hadisəsində baş verir (statik ilkin dövrlərdən
   başqa).
5. **Mövcud test şəbəkəsi bunu tuta bilmirdi:** `tests/test_swing_detector_differential.py:207`
   `len(incremental_structure.breaks_history) == len(batch_final_structure.breaks_history)` yoxlayır.
   AMMA `MarketStructureEngine.analyze()` (batch, sətir 336-350) `check_structural_break()`-i HEÇ
   ÇAĞIRMIR — `batch_final_structure.breaks_history` HƏMİŞƏ boşdur, assertion faktiki "0==0" yoxlayır.

### Düzəliş istiqamətləri (TƏTBİQ OLUNMAYIB — hər ikisinin əlavə riski var)

- **Variant A (qismən reset):** break-tracking sahələrini (`breaks_history`, `last_broken_*_id`)
  reset+replay-dən istisna et. Risk: əvəzlənən swing məhz `last_broken_high_id`-in istinad etdiyi
  swing olarsa, saxlanılan ID artıq qrafda olmayan bir ID-yə işarə edə bilər — `swing_detector.py`-də
  əvəzlənmənin ID-ni saxlayıb-saxlamadığı ƏLAVƏ araşdırma tələb edir.
- **Variant B (tam interleaved replay):** `check_structural_break()`-i də bar-bar təkrar-oynat.
  Risk: bu, Bug #16-da artıq QEYD OLUNMUŞ eyni kövrək sahəyə toxunur (`handle_upgrade()`/Bug #1
  interaksiyası, `test_swing_detector_differential.py`-nın artıq "kövrək" elan etdiyi ssenari) VƏ
  Bug #16-nın performans narahatlığını geri gətirir.

### Bu sessiyada edilən (YALNIZ test infrastrukturu, kod düzəlişi YOX)

1. **Differential test-i gücləndirmək üçün TƏKLİF** (kod yazılmadı, istifadəçi qərarını gözləyir):
   test-only "oracle" `MarketStructureEngine` — hər `is_replacement`-də bar+swing-i tam interleaved
   replay edərək HƏQİQİ ground-truth `breaks_history` qurur (Variant B-nin test-only versiyası,
   production kodu toxunmur), production-un (ucuz reset+update-only) engine-i ilə element-by-element
   (`break_type`/`broken_swing.id`/`timestamp`) müqayisə edilir.
2. **Real, işləyən "qırmızı" test yazıldı:**
   [`tests/test_market_state_builder.py::test_bug29_swing_replacement_corrupts_breaks_history`](tests/test_market_state_builder.py)
   — real `MarketStateBuilder`/`MarketStructureEngine`/`SwingDetector` vasitəsilə, 43 bar-lıq əl ilə
   qurulmuş ssenari: MAJOR high H1 (bar 20) → legitim BOS break (bar 25) → ƏLAQƏSİZ bir MINOR low
   swing-in (bar 32) daha ekstremal biri ilə əvəzlənməsi (bar 40-42, `is_replacement=True`) → H1-in
   ÖZÜ dəyişməsə də, `check_structural_break` bar 42-də EYNİ swing üçün YENİ, dublikat break yaradır.
   Assertion (orijinal bar-25 break-in `breaks_history`-də qalması) HAZIRKI KODLA UĞURSUZ OLUR —
   `@pytest.mark.xfail(strict=True)` ilə işarələnib ki, tam suite yaşıl qalsın, amma bug
   SƏNƏDLƏŞDİRİLMİŞ, TƏSDİQLƏNMİŞ statusda izlənilsin.

### Qərar (bağlanış)

Bu sessiyada NƏ Variant A, NƏ DƏ B tətbiq olunmur — hər ikisi əlavə araşdırma (ID-davamlılığı,
Bug #16 ilə kəsişmə) tələb edir. Addım 1-in (differential test gücləndirməsi, "oracle") tətbiqinə
də İNDİ başlanmır — bu, ayrıca, böyük bir iş paketidir, Variant A/B-dən biri seçiləndə lazım olacaq.

**Status: TƏSDİQLƏNMİŞ (xfail test ilə sübut edilib), DÜZƏLİŞ TƏTBİQ OLUNMAYIB — Variant A/B hər
ikisi əlavə araşdırma (ID-davamlılığı, Bug #16 ilə kəsişmə) tələb edir. Praktiki risk: trend-li
instrumentlərdə (USTEC/EURUSD) sıx-sıx, AMMA hələ HEÇ BİR canlı/backtest nəticəsində KONKRET,
SÜBUT EDİLMİŞ səhv NƏTİCƏYƏ rast gəlinməyib (yalnız NƏZƏRİ risk sübut edilib). Bug #29 bağlanır —
gələcəkdə (Mərhələ A refaktorunda, ya da real bir problem müşahidə edilsə) yenidən açılacaq.**

---

# Mərhələ C — Strategiya #4: OrderBlockRetestStrategy (commit `33c1d6c`)

Mövcud SMC pipeline-ının artıq aşkarladığı Order Block-ları (`market_state.smc_state.order_blocks`)
yenidən aşkarlamaq əvəzinə birbaşa istifadə edir: bullish OB-in kənarı (`ob.high`) yuxarıdan
toxunulanda BUY, bearish OB-in kənarı (`ob.low`) aşağıdan toxunulanda SELL tetiklənir, giriş kənar
səviyyəsində, SL OB-in əks kənarında, sabit 2R hədəf (konfiqurasiya edilə bilər), sessiya
məhdudiyyəti yoxdur.

Hər Order Block bu strategiya tərəfindən ən çox bir dəfə ticarət edilir (`self._used_ob_ids` ilə
izlənir) — `OrderBlock.is_mitigated`-dən **qəsdən** müstəqil, çünki mitigasiya (Bug #22: qiymət
əks kənardan tam keçəndə) "bu strategiya artıq bu OB-də hərəkət edib" anlayışından fərqlidir və
`is_mitigated`-ə təkrar-istifadə qoruyucusu kimi güvənmək həm yanlış olardı (strategiya-spesifik
deyil), həm etibarsız (bu strategiyanın artıq ticarət etdiyi bir OB heç vaxt mitigasiya
olunmaya bilər).

Ortaq `RejectionReason` enum-undan istifadə edir (yeni üzvlər: `NO_ORDER_BLOCKS`,
`NO_TOUCH_DETECTED`, `OB_ALREADY_USED`; mövcud `NO_LATEST_BAR`, `NON_POSITIVE_RISK`,
`RR_GATE_FAILED`-i təkrar istifadə edir) və digər 4 strategiya kimi `TradeSetup.strategy_name`-i
(Bug #27) təyin edir.

9 unit test: bullish/bearish toxunma düzgün giriş/SL/TP ilə, order block yoxdur, toxunma yoxdur,
hər-OB-ə-bir-dəfə təkrar-istifadə rəddi (`is_mitigated`-dən müstəqilliyi açıq təsdiqləyərək), ilk OB
istifadə olunduqdan sonra ikinci toxunulmamış OB-in ticarətə açıq qalması, və degenerativ
(sıfır-risk) OB.

**Status: DÜZƏLDİLDİ (əlavə edildi).**

---

## NasdaqMidlineSweepStrategy — FX Universallıq Testi (EURUSD/GBPUSD/USDJPY)

USTEC-də validasiya olunmuş (`body_multiplier=1.5` default) Midline Sweep-in başqa instrumentlərə
ümumiləşib-ümumiləşmədiyi yoxlandı.

**Addım 1 — default parametrlərlə (real spread, hər simvolun öz median dəyəri: EURUSD=0.00002,
GBPUSD=0.00004, USDJPY=0.004), tam-data, M5:** hər üç FX cütündə **0 trade**. Kök səbəb:
`range_size=10.0`/`mid_buffer=5.0` MÜTLƏQ qiymət vahididir, USTEC-in (~29,500) miqyasına
kalibrlənib — FX-in (EURUSD ~1.08-1.14, GBPUSD ~1.28-1.34, USDJPY ~147-162) miqyasında zona
([mid±10]) demək olar ki, bütün mümkün qiymətləri əhatə edir, sweep şərti heç vaxt ödənilmir.

**Addım 2 — data-əsaslı miqyaslama (yalnız EURUSD, ilkin mümkünlük yoxlaması):** USTEC-in son
20 bar-lıq ATR-i (18.4316) ilə `range_size=10.0`-un nisbəti (0.5425) hesablandı, EYNİ nisbət
EURUSD-in öz ATR-inə (0.000206) tətbiq edildi: `range_size=0.000112`, `mid_buffer=0.000056`.

| Metrika | EURUSD In-Sample (70%) | EURUSD Out-of-Sample (30%) |
|---|---:|---:|
| Trade sayı | 219 | 100 |
| Win Rate | 34.7% | 31.0% |
| Profit Factor | 0.979 | 0.820 |
| Net Profit | -$295.93 | -$1,180.36 |
| Max Drawdown | 13.9% | 12.9% |

**NƏTİCƏ:** Midline Sweep-in EURUSD-də (miqyaslanmış parametrlərlə) sınağı: in-sample PF 0.979,
out-of-sample PF 0.820 — hər ikisi 1.0-dan aşağı, real edge yoxdur. Midline Sweep strategiyası
USTEC-ə XAS bir edge göstərir, FX-ə (ən azı EURUSD-ə) ÜMUMİLƏŞDİRİLMİR. GBPUSD/USDJPY sınanmadı,
bu nəticəyə görə əlavə vaxt sərf edilmədi.

---

## Qərar: Backtest Engine-in tək-simvol/tək-pozisiya arxitekturası İNDİ dəyişdirilmir

`BacktestEngine.run()` bir dəfəyə yalnız BİR simvol, BİR açıq pozisiya dəstəkləyir (çoxlu-simvol/
çoxlu-pozisiya paralel idarəetməsi yoxdur). Bu, İNDİ HƏLL EDİLMİR — bu, şüurlu bir qərardır,
unudulma deyil.

**Səbəb:** Hazırda YALNIZ 1 strategiya (Midline Sweep, USTEC) sübut edilmiş edge göstərir (bax:
yuxarıdakı `body_multiplier=1.5` in-sample/out-of-sample təsdiqi). Çoxlu-pozisiya arxitekturası
indi lazımsız mürəkkəblik əlavə edərdi (YAGNI prinsipi) — ikinci sübut edilmiş strategiya/simvol
olmadan bu işi görməyin praktiki qazancı yoxdur, yalnız baxım yükü artırar.

**Bu qərar YALNIZ o zaman yenidən nəzərdən keçiriləcək ki, EN AZI 2 MÜSTƏQİL, SÜBUT EDİLMİŞ
strategiya/simvol eyni vaxtda canlı/demo ticarətə hazır olsun.**

**Status: QƏRAR QEYD EDİLDİ (kod dəyişikliyi yoxdur).**

**Status: BAĞLANDI (mənfi nəticə sənədləşdirildi, kod dəyişməyib).**

---

## Audit #17: Margin/Leverage Modelləşdirilməsi — HƏLL EDİLDİ (commit `349ee31`)

`SimplePositionSizer` yalnız risk-əsaslı ölçü hesablayırdı (`risk_amount / |entry - SL|`), heç
bir leverage/margin məhdudiyyəti nəzərə almadan — real broker hesabında açıla bilməyəcək
ölçüdə pozisiyalar sakitcə "açılırdı" (bax `tradebot_technical_audit.md` #17).

**Dəyişiklik:**
- `BacktestConfig`-ə iki opsional sahə: `leverage: float | None = None`, `contract_size:
  float = 1.0`. `leverage=None` (default) marja yoxlamasını tamamilə söndürür — mövcud
  davranış dəyişmir (bütün əvvəlki config-lər/testlər bu sahələri təyin etmirdi).
- `BacktestEngine`: `pos_size` hesablandıqdan sonra, `active_trade` yaradılmazdan əvvəl,
  `required_margin = pos_size * contract_size * entry_price / leverage` yoxlanılır.
  `required_margin > balance` olduqda setup sakitcə rədd edilir (balans toxunulmur),
  `BacktestResult.margin_rejected_setups` sayğacı artırılır.
- 5 yeni test (`tests/test_backtest_engine.py`): default-un no-op olduğunu təsdiqləyən
  differensial test, BUY/SELL üçün rədd ssenariləri, sərhəd (boundary) ssenarisi. Tam suite
  (358 test) reqressiyasız keçdi.

**Real doğrulama (Midline Sweep, USTEC M5, OOS split 0.7, eyni parametrlər, 106 trade dəqiq
təkrar istehsal olundu):**

| | None | 20 (ESMA) | 15 | 10 | 5 | 2 |
|---|---:|---:|---:|---:|---:|---:|
| Trade sayı | 106 | 106 | 106 | 80 | 10 | 0 |
| Margin-rejected | 0 | 0 | 0 | 26 | 96 | 106 |
| Profit Factor | 1.0958 | 1.0958 | 1.0958 | 1.3047 | 0.8442 | 1.0000 |

**Nəticə:** 1:20 leverage-də (ESMA-nın real retail index tavanı) **TAM EYNİ nəticə** — 106
trade, PF 1.0958, 0 margin-rejected. Audit-in nəzəri riski (marja yoxlaması olmadan qeyri-real
pozisiyaların sakitcə "açılması") bu konkret strategiya/risk kombinasiyasında (risk_per_trade
1%) praktik təsir göstərmir — 1% risk sizing artıq kifayət qədər konservativdir ki, tələb
olunan marja heç vaxt balansı aşmır.

**Qeyd (Bug #24 bağlandıqdan sonra əlavə edilib):** yuxarıdakı cədvəldəki **PF 1.0958**
köhnəlmiş/səhv istinaddır — bu bölmənin özü ilə eyni parametrləri sənədləşdirən əvvəlki
`body_multiplier=1.5` OOS nəticəsi (yuxarıda, PF **1.05**) arasında ARTIQ sənəd-daxili
uyğunsuzluq var idi, Bug #24-dən asılı olmayaraq. Bug #24 düzəlişindən sonra bugünkü kodda
flat `--spread=1.0` ilə YENİDƏN doğrulandı: PF=**1.0503** (1.05-ə uyğun, 1.0958-ə YOX). Bax
Bug #24/#24b bölməsi — GÜNCƏL, DOĞRU istinad rəqəmi PF **1.0510**-dur (CSV-dən avtomatik
oxunan real spread ilə).

Aşağı leverage-lərin (1:10 və aşağı) nəticələri **müqayisə edilə bilməz**: rədd edilən setup
dərhal atılır (`pending_setup = None`), ona görə strategiya növbəti bardan yeni siqnal
axtarmağa dərhal başlayır — nəticədə fərqli, path-dependent bir trade ardıcıllığı yaranır (106
trade-in alt çoxluğu DEYİL). 1:10-dakı PF 1.3047 "yaxşılaşması" marjanın pis trade-ləri
seçici şəkildə süzdüyü demək DEYİL, sadəcə fərqli seçilmiş nümunədir; 1:5/1:2-də isə sample
ölçüsü (10 və 0 trade) statistik olaraq mənasızdır.

**Status: BAĞLANDI (kod dəyişikliyi tətbiq olunub, test edilib, real datada doğrulanıb).**

---

## Bug #48: `WalkForwardRunner` strategiya-nöqtəsi hardcode-dur — TƏXİRƏ SALINDI

**Kontekst:** Roadmap #9 (Midline Sweep üzərində Walk-Forward validasiyası) planlaşdırılarkən
`research/walk_forward.py` təhlil edildi.

**Tapıntı:** `WalkForwardRunner._simulate()` (walk_forward.py:114-125) strategiya seçimini
parametrləşdirmir — hər seqment simulyasiyasında birbaşa `BullishContinuationStrategy` və
`BearishContinuationStrategy`-ni (`strategy/continuation.py`) qeydiyyatdan keçirir:

```python
strategy_engine.register_strategy(BullishContinuationStrategy(config=strat_config))
strategy_engine.register_strategy(BearishContinuationStrategy(config=strat_config))
```

`run()`-un tip işarəsi də (`strat_config: StrategyConfig`) konkret olaraq
`strategy.continuation.StrategyConfig`-i gözləyir. Nəticədə `NasdaqMidlineSweepConfig`
(`strategy/nasdaq_midline_sweep.py`) ötürülsə, `BullishContinuationStrategy.__init__`-dəki
construction-time `isinstance(config, StrategyConfig)` yoxlaması (Bug #24-cü seriyanın
gücləndirdiyi validasiya) dərhal `TypeError: config must be a StrategyConfig, got
NasdaqMidlineSweepConfig` xətası atır. Əlavə struktur fərqi: Continuation İKİ ayrı class-a
(Bullish/Bearish) bölünüb, Midline Sweep isə TƏK class-dır (`long_ok`/`short_ok`-u özü idarə
edir) — deməli `_simulate()`-in register etdiyi strategiya sayı/tipi də dəyişməlidir.

Eyni hardcoding `research/research_optimizer.py::ParameterOptimizer`-də də mövcuddur (Roadmap #9
üçün bu, istifadə olunmayacaq, amma gələcəkdə qarışıqlıq yaratmasın deyə qeyd olunur).

**Yoxlanılıb, TƏHLÜKƏSİZ tapılıb (bu bloker BUNLARA aid deyil):**

- `_simulate()` hər çağırışda (fold başına 2 dəfə: train + val) tamamilə TƏZƏ
  `StrategyEngine` və təzə strategiya instansları yaradır — paylaşılan state yoxdur, `reset()`
  çağırışına ehtiyac yoxdur. Gələcək refaktor bu "hər çağırışda təzə instans" nümunəsini
  qorumalıdır.
- `search_space` parametri yalnız `ParameterOptimizer.optimize()`-a aiddir (məcburi arqument,
  default `None` deyil) və `WalkForwardRunner` bu class-ı heç idxal etmir — gizli grid-search
  riski yoxdur.

**Təxirə salınma səbəbi:** generic etmək (strategiya sinfini/factory-ni parametr kimi qəbul
edən refaktor) gözlədiyimizdən böyük iş çıxdı, ayrıca planlaşdırma tələb edir. İstifadəçi
qərarı: aşağı-orta prioritet, YALNIZ Roadmap #9-a HƏQİQƏTƏN keçmək istəyəndə həll ediləcək.

**Gələcək iş (edildikdə):** `WalkForwardRunner`-ə register olunacaq strategiya class(lar)ını
və uyğun config tipini xarici parametr (məsələn factory funksiyası) kimi ötürməyə imkan verən
bir refaktor, differensial testlə (mövcud Continuation davranışının dəyişmədiyini təsdiqləyən)
birlikdə.

**Əlavə tapıntı — fold sayı gözləniləndən fərqlidir:** USTEC M5 datası (19 ay, 99,859 bar)
üçün seçilmiş konfiqurasiya (Rolling, ~4 ay in-sample / ~1.7 ay out-of-sample, addım = OOS
uzunluğu → `train_size_pct≈0.2105`, `val_size_pct≈step_size_pct≈0.0895`) `WalkForwardRunner`-in
mexanikası ilə hesablananda **8 fold** verir, gözlənilən 10-11 yox. Hesablama: `train_size=
int(99859*0.2105)=21023`, `val_size=step_size=int(99859*0.0895)=8931`; rolling loop-da
`train_start=k*8931` üçün `val_end=train_start+21023+8931<=99859` şərti `k=7`-də (val_end=
92,471) təmin olunur, `k=8`-də (val_end=101,402) pozulur → `k=0..7`, yəni **8 fold**. Roadmap #9
yenidən açılanda bu ədəd əsas götürülməlidir (və ya fold sayını artırmaq üçün addımı OOS-dan
kiçildib yüngül örtüşməyə keçmək seçimi yenidən qiymətləndirilməlidir).

**Status: TƏXİRƏ SALINIB (kod dəyişikliyi tətbiq OLUNMAYIB — yalnız sənədləşdirmə).**
---

## Bug #49: `run_research_campaign.py` Executive Summary Saxta Ədədlər Göstərirdi — DÜZƏLDİLDİ (commit sonrakı)

**Tapıntı (2026-07-16 tam repo auditi):** `campaign_summary` dict-i (əvvəlki sətir 537-564)
`overall_score`, `robustness_score`, `walk_forward_score`, `monte_carlo_score`,
`optimization_score`, `profit_factor`, `sharpe`, `risk_of_ruin`, `best_params` sahələrinin
HAMISINI sabit literal olaraq hardcode edirdi (məs. `"profit_factor": 1.45`, `"sharpe": 1.25`) —
kampaniya real datada qazandırsın, ya da tam iflas etsin, PDF/MD Executive Summary HƏMİŞƏ EYNİ
ədədləri göstərirdi. `best_params` isə sətir 410-da artıq mövcud olan real `opt_results`-a
baxmayaraq ayrıca hardcode-lanmış `"RR=1.5, Buffer=5.0"` string-i idi.

**Düzəliş:**
- `_compute_walk_forward_score` / `_compute_optimization_score` / `_compute_monte_carlo_score` /
  `_compute_robustness_score` — hər biri müvafiq fazanın (`wf_results`/`opt_results`/
  `mc_results`/`rob_results`) real nəticələrindən sadə, sənədləşdirilmiş bir düstur üzrə
  0-100 aralığında hesablanır (məs. walk-forward score = müsbət val-nəticəli fold-ların faizi).
- `overall_score` bu 4 alt-skorun çəkili ortalamasıdır (0.30/0.25/0.25/0.20).
- `_compute_portfolio_metrics` — bütün simvolların tam-tarixi bar-larından TƏZƏDƏN, resume
  checkpoint-dən asılı olmadan (`campaign_state.json` fərdi ticarətləri saxlamır) birləşdirilmiş,
  xronoloji sıralanmış bir ticarət dəsti qurur və `BacktestReportGenerator` ilə real
  `profit_factor`/`sharpe_ratio` hesablayır.
- `_select_best_params_string` — `opt_results`-dan ən yüksək `best_pnl`-li simvolun HƏQİQİ
  parametrlərini formatlayır (artıq mövcud olan real datanı sadəcə Executive Summary-yə ötürür).
- `_build_strengths_and_weaknesses` — sərbəst NLG YOX, sadə if/else həddləri real hesablanmış
  metriklər üzərində (məs. `profit_factor > 1.2` → güclü tərəf, `< 1.0` → zəif tərəf sətri).
- `tests/test_research_campaign.py` (17 test) əlavə olundu: hər hesablama funksiyası ayrıca test
  edilir, VƏ iki sintetik ("yaxşı"/"pis") nəticə seti fərqli Executive Summary ədədləri
  verdiyini, köhnə sabit literallarla ÜST-ÜSTƏ DÜŞMƏDİYİNİ təsdiqləyir.

**Status: BAĞLANDI (kod dəyişikliyi tətbiq olunub, 17 yeni test ilə doğrulanıb, tam suite
regressiyasız keçir — 487 test, 486 PASS + 1 XFAIL).**

---

## Bug #54: Data Sonunda Açıq Qalan Pozisiya Sükutla İtirdi — DÜZƏLDİLDİ (commit sonrakı)

**Tapıntı (2026-07-16 tam repo auditi):** `BacktestEngine.run()`-un `for` dövrü bitdikdən dərhal
sonra metriklər hesablanırdı — əgər `active_trade` hələ AÇIQ idisə (`max_holding_bars`
təyin olunmayıbsa VƏ SL/TP heç toxunmayıbsa), bu pozisiya `closed_trades`-ə HEÇ VAXT
əlavə olunmurdu. Nəticədə `final_balance`, `win_rate`, `profit_factor`, `total_trades` bu
pozisiyanın nəticəsini tamamilə görməzdən gəlirdi — heç bir sayğac da bunu göstərmirdi.

**Düzəliş (`backtest/engine.py`):** dövr bitdikdən sonra, əgər `active_trade is not None`-dursa,
son bar-ın (`candles[-1]`) CLOSE qiymətindən (spread/slippage ilə, EXPIRED yolundakı eyni
düstur) MƏCBURİ bağlanır, `TradeResult.EXPIRED` kimi `closed_trades`-ə əlavə olunur, balans və
max-drawdown mühasibatlığı yenilənir. `BacktestResult.force_closed_at_data_end` (0 və ya 1) sahə
əlavə olundu ki, bu halın baş verdiyi müşahidə oluna bilsin (`conflicting_setups_dropped`/
`margin_rejected_setups` ilə eyni şəffaflıq nümunəsi).

**Test:** `tests/test_backtest_engine.py`-ə 3 yeni test əlavə olundu —
`test_open_position_force_closed_at_data_end` (yeni davranış), differensial
`test_fully_resolved_backtest_is_unaffected_by_data_end_force_close` (SL/TP ilə əvvəlcədən tam
bağlanan backtest-in nəticəsi BAYT-BAYTA dəyişməyib) və `test_no_force_close_when_no_position_was_ever_open`
(heç bir pozisiya açılmadıqda yeni kod yolu no-op-dur).

**Midline Sweep USTEC OOS nəticəsinin Bug #54 düzəlişindən SONRA yenidən doğrulanması**
(eyni əmr: `--strategy midline_sweep --data-file data/history/USTEC_M5.csv --timeframe M5
--params '{"body_multiplier": 1.5}' --split out_of_sample --split-ratio 0.7`, `--spread`
arqumenti verilmədən, CSV-nin real spread sütunu avtomatik oxunur):

| Metrika | Bug #54-dən ƏVVƏL (sənədləşdirilmiş) | Bug #54-dən SONRA (bu sessiya) |
|---|---:|---:|
| Trade sayı | 106 | **106 (DƏYİŞMƏYİB)** |
| Profit Factor | 1.0510 | **1.0509977 ≈ 1.0510 (DƏYİŞMƏYİB)** |
| Net Profit | ~$374 (sənədləşdirilməmiş dəqiq rəqəm) | **+$379.24** |
| Max Drawdown | sənədləşdirilməyib | 10.08% |
| Win Rate | sənədləşdirilməyib | 35.85% |

**NƏTİCƏ: trade sayı 106-dan 107-yə DƏYİŞMƏDİ.** Bu, Bug #54-ün YANLIŞ olduğu demək DEYİL —
əksinə, bu konkret ssenaridə (USTEC M5, 70/30 OOS bölgüsü, `body_multiplier=1.5`) son bar-da
`active_trade` artıq `None` idi (son pozisiya bu OOS seqmentinin son bar-ından ƏVVƏL SL/TP və ya
digər yolla artıq bağlanmışdı) — deməli Bug #54-ün düzəltdiyi kod yolu bu konkret backtest
üçün İŞƏ DÜŞMƏDİ. Fix real, test edilib və düzgündür (yuxarıdakı 3 yeni test bunu sübut edir),
sadəcə bu XÜSUSİ, əvvəllər sənədləşdirilmiş nəticəyə təsadüfən təsir etmədi. **Əvvəllər
sənədləşdirilmiş PF 1.0510 / 106 trade rəqəmi bununla YENİDƏN TƏSDİQLƏNİR, etibarsız deyil.**

**Status: BAĞLANDI (kod dəyişikliyi tətbiq olunub, 3 yeni test ilə doğrulanıb, Midline Sweep
USTEC OOS nəticəsi yenidən doğrulanıb və dəyişməyib, tam suite regressiyasız keçir — 490 test,
489 PASS + 1 XFAIL).**
---

## Bug #48/#52 — BİRLƏŞDİRİLDİ, TƏXİRƏ SALINDI (istifadəçi qərarı, 2026-07-16)

**Qərar:** Bug #48 (`WalkForwardRunner`-in strategiya-nöqtəsi hardcode-dur) və Bug #52
(`run_backtest.py`-in eyni naxışı daşıdığı, tam repo auditində aşkarlanan tapıntı) BİRLİKDƏ, TƏK
bir gələcək iş paketi kimi təxirə salınır.

**Səbəb:** Hər ikisi eyni kök-problemdən (strategiya qeydiyyatının hardcode olması) qaynaqlanır.
Bunları AYRI-AYRI, fərqli naxışlarla həll etmək gələcəkdə uyğunsuzluq riski yaradar — məsələn
`run_backtest.py`-in baseline/regressiya mexanizmi (`artifacts/baselines/baseline_v1.json`)
strategiya-spesifikdir (hazırda yalnız Continuation üçün mənalıdır), bu isə `WalkForwardRunner`-in
generic ediləcəyi refaktordan MÜSTƏQİL, əlavə bir dizayn qərarı (baseline-ların strategiya üzrə
açarlanması) tələb edir. Bug #52-nin ayrıca təhlili (bax yuxarı, bu sənəddə) göstərdi ki, bu iş
`research/run_strategy_backtest.py`-dəki `STRATEGY_REGISTRY` naxışının sadəcə köçürülməsindən
qat-qat böyükdür: YAML sxem dəyişikliyi (`config/backtest.yaml`-a `strategy`/`strategy_params`
sahələri), Continuation-un cüt-qeydiyyat xüsusiyyətinin (Bullish+Bearish, paylaşılan
`StrategyConfig`) digər 5 tək-class strategiyadan fərqli idarə olunması, və baseline-ların
strategiya üzrə açarlanması — bunların hamısı ayrıca planlaşdırma tələb edir.

**TƏXİRƏ SALINIB** — YALNIZ Roadmap #9-a (Walk-Forward/Monte Carlo validasiyasını Midline Sweep və
digər strategiyalara həqiqətən keçirmək) keçmək istəyəndə, VAHİD bir dizaynla həll ediləcək:
`WalkForwardRunner` VƏ `run_backtest.py` üçün EYNİ strategiya-registry naxışı, VƏ strategiya-üzrə
baseline açarlaması, TƏK bir refaktor sessiyasında, differensial testlərlə (mövcud Continuation
davranışının dəyişmədiyini təsdiqləyən) birlikdə.

**Status: TƏXİRƏ SALINIB (kod dəyişikliyi tətbiq OLUNMAYIB — yalnız sənədləşdirmə).**

---

## Bug #51 — `research/stability.py`-ə Bug #19/#21 ötürülməsi — DÜZƏLDİLDİ

**Tapıntı:** `research_optimizer.py`, `walk_forward.py`, `robustness.py` artıq Bug #19-un
(`max_grid_combinations` ölçü-qoruması) və Bug #21-in (diaqnostika/`top_rejection_reasons`
0-trade şəffaflığı) düzəlişlərini daşıyırdı, amma `ParameterStabilityAnalyzer` bunların HEÇ
BİRİNİ almamışdı.

**Düzəliş:** `run()`-a `max_grid_combinations` (default 100) parametri əlavə olundu —
`len(lookback_grid) * len(buffer_grid)` bu həddi keçəndə `ValueError` atılır. `_simulate()` indi
`strategy_engine.get_diagnostics()`-i də qaytarır; `_export_artifacts()` 0-trade xanalar üçün
yeni `stability_report.md` yaradır (bacı modulların `*_report.md` formatına uyğun).

**Əlavə tapıntı (test yazarkən üzə çıxdı):** `research/stability.py` heç vaxt özü
`matplotlib.use("Agg")` çağırmırdı (yalnız `run_research_campaign.py` edirdi) — bu, test
təcridində flaky nəticəyə səbəb olurdu. Düzəldildi.

**Test:** 5 yeni test (`tests/test_research.py`) — grid-daxili davranış dəyişməyib (differensial),
grid-limiti aşanda `ValueError`, override ilə böyük grid icazəli, 0-trade diaqnostikası, trade-li
xanalarda hesabat yaradılmır.

**Status: BAĞLANDI (commit `5876ba3`).**

---

## Bug #53 — `CSVDataProvider.validate()` tutarsız çağırılması — DÜZƏLDİLDİ

**Tapıntı:** `run_backtest.py` və `research/run_strategy_backtest.py` `provider.validate(bars)`-ı
`load()`-dan dərhal sonra çağırırdı, amma `run_diagnostics.py`, `run_research.py`, və
`run_research_campaign.py`-in demək olar bütün `CSVDataProvider(...).load()` çağırış nöqtələri
(Phase 1-6 + `_compute_portfolio_metrics`) çağırmırdı — mənfi/sıfır qiymətli və ya high&lt;low olan
bir CSV bu yollarda sükutla `MarketStateBuilder`/`StrategyEngine`/`BacktestEngine`-ə axa bilərdi.

**Düzəliş:** `run_diagnostics.py::run_diagnostics_for_symbol`, `run_research.py::check_and_get_data`,
və `run_research_campaign.py`-dəki 7 yükləmə nöqtəsindən 6-sı (Phase 1, `_compute_portfolio_metrics`,
Phase 2-6) indi `provider.validate()` çağırır. **İstisna:** `validate_data_quality()` (Phase 0-ın öz
keyfiyyət-hesabatı funksiyası) BİLƏRƏKDƏN toxunulmadı — o, artıq öz DAHA ƏTRAFLI, kəmiyyətləşdirilmiş
yoxlamalarını (dublikat sayı, invalid-OHLC sayı, weekend-gap sayı) aparır; `validate()` əlavə etmək
onu İLK pozuntuda dayandırardı (bu funksiyanın öz dolğun hesabatını yaratmasının qarşısını alardı) —
bu, düzəliş deyil, reqressiya olardı.

**Test:** `tests/test_run_diagnostics.py`, yeni `tests/test_run_research.py`, və
`tests/test_research_campaign.py`-ə əlavə test — hər biri indi invalid OHLC datasını rədd etdiyini
təsdiqləyir.

**Status: BAĞLANDI (commit `ba59f51`).**

---

## Bug #55 — `mt5/history_downloader.py`-də chunking yoxdur — DÜZƏLDİLDİ

**Tapıntı:** `data/download_history.py::fetch_symbol_bars_chunked` MT5-in bar-limitini (
`copy_rates_range` ~62-74k bardan sonra `None` qaytarır) aşmaq üçün sorğuları chunk-layırdı, amma
`MT5HistoryDownloader.download()` (istehsalatda AKTİV istifadə olunan, `run_backtest.py`/
`run_research.py`/`run_research_campaign.py`-in avtomatik-endirmə fallback-ı) eyni sorğunu BİR
DƏFƏYƏ, chunk-sız edirdi.

**Düzəliş:** Paylaşılan `mt5/chunking.py` çıxarıldı (`TIMEFRAME_DELTA`, `iter_chunk_windows`) —
`data/download_history.py`-in `_iter_chunk_windows`/`_TIMEFRAME_DELTA`-sı indi bunun nazik
re-export-udur (mövcud testlərin `patch("data.download_history._iter_chunk_windows", ...)`
adlandırması TƏSİRLƏNMİR, çünki `mock.patch` modul atributunu əvəz edir, mənşəyindən asılı
olmayaraq). `MT5HistoryDownloader.download()` indi `iter_chunk_windows()` üzərindən iterasiya edir,
boş chunk-ları sükutla ötürür (bacı funksiya kimi), və CSV-ə yazmazdan əvvəl sərhəd-dublikatlarını
təmizləyir/xronoloji sıralayır (chunking-in özü gətirdiyi yeni risk).

**Test:** Yeni `tests/test_history_downloader.py` (7 test) — bu modulun əvvəllər SIFIR test
əhatəsi var idi.

**Status: BAĞLANDI (commit `89f7ad4`).**
---

## Turn-of-Month Seasonality — Event-Study Nəticəsi (USTEC, 2026-07-16)

**Data:** USTEC D1, MT5-dən birbaşa yükləndi (`data/download_history.py --timeframe D1`),
2022-07-06 -> 2026-07-16 (1003 bar, ~4 il). 0 dublikat, 0 sıralama xətası, 0 OHLC pozuntusu, 206 gap
(əksəriyyəti weekend/holiday, normal). **Bir GENİŞ data-fasiləsi aşkarlandı: 2025-07-16 ->
2025-09-09 (55 gün)** — bu, `turn_of_month_study.py`-in `max_gap_days` qoruması tərəfindən düzgün
aşkarlanıb bir hadisə kimi ATILDI (süni "2-aylıq gəlir" kimi yanlış hesablanmadı).

**Metod:** `research/turn_of_month_study.py` (pure event-study, TradeSetupStrategy DEYİL) — hər ay
dönümü üçün gün -1-in bağlanışından gün +N-in bağlanışına qədər faiz gəliri, N=1,3,5 üçün ayrı-ayrı.

**Nəticə:**

| N (gün) | Hadisə sayı | Orta gəlir | Median | Std | t-statistiku | Müsbət/Mənfi |
|---|---:|---:|---:|---:|---:|---|
| 1 | 46 | +0.17% | +0.03% | 1.19% | +0.98 | 23/22 |
| 3 | 46 | -0.002% | +0.20% | 2.62% | -0.01 | 24/22 |
| 5 | 46 | -0.13% | +0.23% | 3.28% | -0.27 | 25/21 |

**QİYMƏTLƏNDİRMƏ: effekt USTEC-in bu ~4 illik datasında STATİSTİK CƏHƏTDƏN İNANDIRICI GÖRÜNMÜR.**
Heç bir N dəyəri üçün `|t| > 1.96` (adi 5% həddi) həddinə çatmır — N=1 ən yaxın (t=+0.98), amma hələ
də çox aşağıdadır. N=3/N=5 üçün orta gəlir demək olar sıfıra bərabər və ya MƏNFİDİR.

**Qeyd olunmalı məhdudiyyətlər (effektin "olmadığı" qəti sübutu DEYİL):**
- **Nümunə ölçüsü kiçikdir** (n=46) — akademik ədəbiyyatda sitat gətirilən tədqiqatlar adətən
  onilliklər üzrə yüzlərlə müşahidə istifadə edir.
- **Tək instrument, qeyri-adi rejim** — USTEC (Nasdaq-100 CFD) yalnız, VƏ məhz bu 4 il (2022 bear
  bazarı + 2023-2024 AI-güdümlü bull run) ümumi indeks bazarı üçün "normal" mövsümi rejimi əks
  etdirməyə bilər.
- **55-günlük data-fasiləsi** faktiki müşahidə sayını azaldıb (əks halda ~47-48 ola bilərdi).

**Qərar (istifadəçi ilə razılaşdırılmış şərtə əsasən — "yalnız effekt inandırıcı görünsə,
TradeSetupStrategy-ə keçəcəyik"):** hazırkı nəticələr bu şərti ÖDƏMİR.
**TradeSetupStrategy versiyasına HƏLƏLİK KEÇİLMİR.**

**Status: TƏDQİQAT APARILDI, EFFEKT BU DATADA TƏSDİQLƏNMƏDİ. Gələcək variantlar (istifadəçi
seçimi gözlənilir): (a) daha uzun tarixçə/başqa broker mənbəyi ilə yenidən sına, (b) digər equity-
indeks simvollarında (əgər mövcuddursa) sına, (c) bu tədqiqat istiqamətini burada saxla.**
---

## Turn-of-Month Seasonality — İSTİQAMƏT RƏSMİ OLARAQ BAĞLANDI (istifadəçi qərarı, 2026-07-16)

**Sınaq:** `research/turn_of_month_study.py` (pure event-study aləti) ilə 6 indeksdə
(USTEC, US30, US500, DE40, UK100, JPN225), 3 hold-period-lə (N=1/3/5 ticarət günü) sınandı —
cəmi 18 test.

**Nəticə:** Çoxlu-test korreksiyası (18 test, 5% həddində sırf təsadüfən ~0.9 "yalan müsbət"
gözlənilir) tətbiq edildikdə, **heç bir nəticə inandırıcı deyil**. DE40 (N=3, t=+2.10) ən güclü
namizəd idi (n=76, tam tarix aralığı), amma **xronoloji yarı-yarı sağlamlıq yoxlaması** göstərdi ki:

| Yarı | n | Orta gəlir | t-statistiku |
|---|---:|---:|---:|
| Birinci (2020-01 → 2023-02) | 38 | +1.11% | **+2.88** |
| İkinci (2023-03 → 2026-06) | 38 | +0.003% | **+0.01** |

Effekt TAMAMİLƏ 2020-2023 alt-dövrünə aiddir, 2023-2026 dövründə TAM YOX OLUB — bu, davamlı struktur
effekt deyil, rejim-spesifik (ehtimal ki COVID-bərpası/ultra-aşağı-faiz dövrünə xas) artefaktdır.
UK100-ün "güclü" nəticəsi (t=+3.71, N=3) də ən kiçik (n=29) və ən şübhəli (qeyri-adi bitən, 2026-05-15)
nümunədən gəlirdi — etibarlı sayılmadı.

**QƏRAR: İSTİQAMƏT BAĞLANDI, əlavə sınaq planlaşdırılmır.** Fərqli data mənbəyinə keçid (əvvəllər
təklif olunan (a) variantı) araşdırılmayacaq — xərc/fayda baxımından artıq dəyməz.

**Alət saxlanılır:** `research/turn_of_month_study.py` SİLİNMƏYİB — gələcəkdə fərqli bir seasonality
fikri (məsələn, ilin müəyyən ayları, həftənin günü effektləri və s.) üçün YENİDƏN İSTİFADƏ OLUNA
BİLƏN, ümumi bir event-study alətidir, dəyərli qalır.

**Status: BAĞLANDI.**

---

## TrendVolumeConfirmationStrategy — IN-SAMPLE-də BAĞLANDI, OOS-a APARILMADI (istifadəçi qərarı, 2026-07-17)

**Strategiya:** `strategy/trend_volume_confirmation.py` — mövcud `MarketStructureEngine`-in
`structure_state.trend`-i istifadə edərək (BULLISH→BUY, BEARISH→SELL, RANGE/TRANSITION/UNKNOWN→heç nə),
son N=20 bar-ın orta həcmindən `volume_multiplier` qat yüksək bağlanan bar-da giriş, son əks-istiqamətli
MAJOR swing-dən faiz-buferli SL (`stop_buffer_pct`, default 0.1%), sabit `risk_reward` TP (trailing yox).
Gün-daxili məhdudiyyət yoxdur — trend olduğu hər an qiymətləndirilir.

**Sınaq:** USTEC-in tam M5 tarixçəsi (99,859 bar), YALNIZ IN-SAMPLE (ilk 70%, 69,901 bar,
2024-12-10 → 2026-02-05), `research/run_strategy_backtest.py` ilə. Diagnostics göstərdi ki, gate-lər
düzgün işləyir (`NO_MAJOR_SWING_FOR_SL` heç vaxt tetiklənmədi — trend olan hər anda SL üçün major swing
mövcud idi), problem strukturaldır, gate-lərin özündə xəta deyil.

| Parametrlər (dəyişən) | Trades | Win Rate | Profit Factor | Net Profit | Max DD |
|---|---:|---:|---:|---:|---:|
| Default (volume_multiplier=1.5, risk_reward=2.0) | 362 | 33.15% | 0.924 | -1,728.94 | 22.06% |
| volume_multiplier=2.0 | 203 | 33.00% | 0.903 | -1,247.20 | 21.02% |
| volume_multiplier=2.5 | 73 | 28.77% | 0.751 | -1,226.82 | 16.89% |
| risk_reward=1.5 | 434 | 39.63% | 0.920 | -2,015.06 | 28.42% |
| risk_reward=1.0 | 533 | 48.41% | 0.875 | -3,188.19 | 34.27% |

**Müşahidələr:**
- Bütün 5 konfiqurasiyada Profit Factor 1.0-dan AŞAĞI qalıb — heç biri breakeven-i keçməyib.
- `volume_multiplier`-i artırmaq (daha "keyfiyyətli" spike axtarmaq) əksinə pisləşdirdi: win rate
  33.15%→28.77%, PF 0.924→0.751. Ekstremal həcm partlayışları daha az proqnozlaşdırıcı çıxdı (çox
  güman gecikmiş/panik-tipli hərəkətləri tutur).
- `risk_reward`-u azaltmaq win rate-i xeyli artırdı (33.15%→48.41%), amma breakeven WR eşiyi də eyni
  templə yüksəldi (1/(1+RR): RR=2.0→33.3%, RR=1.0→50%) — PF yenə də 1.0-ın altında qaldı, üstəlik
  trade sayının artması (362→533) kumulyativ spread xərcini artırıb net profit-i daha da pisləşdirdi
  (-1,728.94 → -3,188.19).
- Nəticə strategiyanın GECİKMİŞ GİRİŞ problemi olduğuna işarə edir: trend+həcm şərti təsdiqləndiyi
  anda hərəkətin bir hissəsi artıq baş vermiş olur, TP-yə çatmaq üçün RR nə qədər aşağı endirilsə,
  o qədər tez-tez, kiçik itkilərlə "az-az udmaq, çox-çox uduzmaq" dinamikası davam edir.

**Qərar (istifadəçi ilə əvvəlcədən razılaşdırılmış sərhədə əsasən — "əgər HEÇ BİR RR dəyəri PF-i
1.0-ın ÜZƏRİNƏ ÇIXARMASA, strategiya OOS-a APARILMADAN BAĞLANACAQ"):** heç bir sınanmış konfiqurasiya
bu şərti ödəmədi.

**Status: IN-SAMPLE-də BELƏ MƏNFİ QALDI, çoxlu tənzimləmə cəhdindən sonra (default, volume_multiplier
2.0/2.5, risk_reward 1.0/1.5) heç biri PF>1.0 vermədi, struktural zəiflik (gecikmiş giriş) aşkarlandı,
OOS resursu israf edilmədi. BAĞLANDI — out-of-sample sınağı APARILMADI.**

**Kod saxlanılır:** `strategy/trend_volume_confirmation.py`, `RejectionReason.NO_MAJOR_SWING_FOR_SL`,
və `research/run_strategy_backtest.py`-dəki `trend_volume_confirmation` qeydiyyatı SİLİNMƏYİB — unit
testlər (`tests/test_trend_volume_confirmation.py`) yaşıl qalır, gələcəkdə fərqli bir giriş məntiqi
(məs. həcm-təsdiqli giriş amma gecikməni azaldan alternativ tetikleyici) üçün əsas kimi istifadə oluna
bilər.

---

## NasdaqMidlineSweepStrategy — İndekslərarası Universallıq Testi (US30/DE40/UK100/JPN225, 2026-07-17)

USTEC-də `body_multiplier=1.5` ilə sübut edilmiş Midline Sweep-in digər 4 indeks CFD-inə
ümumiləşib-ümumiləşmədiyi yoxlandı (əvvəlki [FX universallıq testi](#nasdaqmidlinesweepstrategy--fx-universallıq-testi-eurusdgbpusdusdjpy)
ilə eyni metodologiya: ATR-nisbəti ilə `range_size`/`mid_buffer` miqyaslama, amma bu dəfə FX əvəzinə
digər indeks CFD-ləri).

### Addım 1 — M5 Data Yükləmə

`data/download_history.py --timeframe M5` (MT5, 2020-01-01 → 2026-07-17 sorğusu, broker retention
faktiki aralığı təyin etdi):

| Simvol | Bar sayı | Tarix aralığı | Qeyd |
|---|---:|---|---|
| US30 | 100,000 | 2024-12-16 → 2026-07-16 | 100k limit (broker/terminal tavanı) |
| DE40 | 100,000 | 2024-11-06 → 2026-07-16 | 100k limit |
| UK100 | 100,000 | 2021-07-29 → **2026-05-15** | Data 2026-05-15-də kəsilir — Turn-of-Month tədqiqatında (D1) da eyni anomaliya qeyd olunmuşdu, broker-tərəfli boşluqdur |
| JPN225 | 100,108 | 2025-02-14 → 2026-07-16 | — |
| USTEC (istinad, əvvəlcədən mövcud) | 99,859 | 2024-12-10 → 2026-07-09 | — |

UK100-ün 100k bar tavanı onu 2021-ə qədər geri aparır (günə ~57 bar — digərlərindən (~162-193/gün)
xeyli az, deməli UK100 M5-də bazar saatları broker tərəfindən daha dar əhatə olunub). Nəticə: UK100-ün
in-sample dövrü **2021-2022**-dir, digər bütün simvolların (2024-2026) tamamilə fərqli bazar rejimidir.

### Addım 2 — ATR(20) Miqyaslama

USTEC-in son 20 bar ATR-i bu sessiyada yenidən ölçüldü: **18.355** (əvvəlki FX testindəki 18.4316-ya
yaxın, kiçik fərq son bar sürüşməsindən). Nisbətlər: `range_size/ATR = 0.5448`, `mid_buffer/ATR = 0.2724`.

| Simvol | ATR(20) | Miqyaslanmış `range_size` | Miqyaslanmış `mid_buffer` |
|---|---:|---:|---:|
| US30 | 37.56 | 20.463 | 10.232 |
| DE40 | 30.645 | 16.696 | 8.348 |
| UK100 | 9.52 | 5.187 | 2.593 |
| JPN225 | 75.8 | 41.297 | 20.648 |

(`body_multiplier` kod bazasında artıq default **1.5**-dir — USTEC-də sübut edilmiş dəyər default
olaraq təyin edilib, əlavə ötürməyə ehtiyac olmadı. `risk_reward=2.0`, `sma_period=20` də default.)

### Addım 3 — In-Sample (70%) Nəticələri (hər simvolun öz median spread-i ilə)

| Simvol | Spread | Trades | Win Rate | **Profit Factor** | Net Profit | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| USTEC (istinad, `body_multiplier=1.2` default-ilə əvvəlki nəticə) | — | 232 | 35.8% | 1.047 | +$664.24 | — |
| US30 | 1.2 | 227 | 30.4% | **0.818** | -$2,304.01 | 35.1% |
| **DE40** | 0.7 | 213 | 38.5% | **1.202** | **+$3,209.58** | 8.8% |
| UK100 (2021-2022 dövrü!) | 1.0 | 260 | 33.8% | **0.912** | -$1,419.69 | 19.9% |
| JPN225 | 6.0 | 243 | 33.3% | **0.905** | -$1,556.60 | 24.7% |

4 simvoldan yalnız **DE40** aydın müsbət nəticə göstərdi (PF 1.202). US30/UK100/JPN225 hamısı
PF < 1.0 — **istifadəçi qərarına əsasən bu 3 simvol IN-SAMPLE-də RƏDD EDİLDİ, OOS-a APARILMADI.**

### DE40 Sağlamlıq Yoxlaması — Xronoloji Yarı-Bölgü (əsl OOS DEYİL, mövcud in-sample datanın təkrar emalı)

DE40-un 213 in-sample trade-i "PF 1.202 HƏR İKİ yarıda sabitdirmi" sualına ucuz cavab üçün
xronoloji olaraq ikiyə bölündü (Turn-of-Month-dakı DE40 anomaliyası — effekt yalnız bir alt-dövrə
aid idi — səbəbiylə DE40-un bu layihədə İKİNCİ dəfə "ən yaxşı" çıxması əlavə diqqət tələb etdi):

| Yarı | Trades | Win Rate | **Profit Factor** | Net Profit | Max DD | Tarix aralığı |
|---|---:|---:|---:|---:|---:|---|
| Birinci | 94 | 38.3% | **1.194** | +$1,249.03 | 8.8% | 2024-11-06 → 2025-05-28 |
| İkinci | 119 | 38.7% | **1.208** | +$1,742.86 | 8.0% | 2025-05-28 → 2026-02-03 |

**Hər iki yarı EYNİ İSTİQAMƏTDƏDİR** (hər ikisi PF>1.0, oxşar win rate/Max DD) — Turn-of-Month-dakı
DE40 davranışından (effekt tamamilə bir alt-dövrə aid, digərində tam yox) FƏRQLİ olaraq, burada
effekt hər iki alt-dövrdə sabit qaldı. İstifadəçi bunu inandırıcı sayıb DE40-u YEKUN OOS testinə
apardı.

### DE40 — YEKUN, BİRDƏFƏLİK Out-of-Sample Təsdiq Testi

`--strategy midline_sweep --data-file data/history/DE40_M5.csv --timeframe M5 --params
'{"range_size": 16.695723, "mid_buffer": 8.347862}' --spread 0.7 --split out_of_sample
--split-ratio 0.7` (heç bir əlavə tənzimləmə edilmədən, əvvəlki in-sample-də seçilmiş parametrlərlə):

| Metrika | In-Sample (70%) | Out-of-Sample (30%, YEKUN) |
|---|---:|---:|
| Trade sayı | 213 | 105 |
| Win Rate | 38.5% | **32.4%** |
| Profit Factor | 1.202 | **0.932** |
| Net Profit | +$3,209.58 | **-$506.90** |
| Max Drawdown | 8.8% | 16.8% |
| Tarix aralığı | 2024-11-06 → 2026-02-03 | 2026-02-03 → 2026-07-16 |

**NƏTİCƏ: OOS-da edge TUTMADI.** In-sample PF 1.202-dən out-of-sample PF 0.932-yə düşüb (1.0
həddinin altına), net profit müsbətdən (+$3,209.58) mənfiyə (-$506.90) dönüb, win rate 38.5%-dən
32.4%-ə düşüb. Xronoloji yarı-bölgü sağlamlıq yoxlaması (hər iki yarı PF>1.0) DÜZGÜN idi ONUN ÖZ
sərhədləri daxilində (in-sample datanın öz-özünə tutarlılığını göstərdi) — amma bu, əsl (toxunulmamış)
out-of-sample datasına ÜMUMİLƏŞMƏ zəmanəti vermədi. Bu, EURUSD-dəki nəticəyə bənzəyir (in-sample
PF 0.979 → OOS PF 0.820, [yuxarıda](#nasdaqmidlinesweepstrategy--fx-universallıq-testi-eurusdgbpusdusdjpy)) —
in-sample-də görünən müsbət siqnal out-of-sample-də təsdiqlənmədi.

**Yekun qərar: Midline Sweep strategiyası USTEC-ə XAS bir edge göstərir, digər indekslərə (US30,
DE40, UK100, JPN225) ÜMUMİLƏŞDİRİLMİR.** DE40 in-sample-də ən inandırıcı namizəd idi (o cümlədən
xronoloji yarı-bölgü sınağını keçdi), amma YEKUN, birdəfəlik out-of-sample testində uğursuz oldu.

**Status: BAĞLANDI.** 4 simvolun heç biri (US30/UK100/JPN225 in-sample-də, DE40 out-of-sample-də)
Midline Sweep-i USTEC-dən kənara ümumiləşdirmədi. Əlavə indeks CFD-i planlaşdırılmır.
