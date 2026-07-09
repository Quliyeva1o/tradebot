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
