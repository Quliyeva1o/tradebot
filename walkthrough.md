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

MT5 terminalı artıq quraşdırılıb və qoşulub (MetaQuotes-Demo hesabı, login `5052764320`).
`mt5.symbols_get()` ilə broker-in tam simvol siyahısı (12,698 simvol) axtarıldı:
**`USTEC`** ("US Tech 100 Index") tapıldı — point=0.01, digits=2, tarixi M15 datası
(`mt5.copy_rates_from_pos`) real qiymətlərlə (~29,500-29,600 səviyyəsi) mövcuddur.
`USTECH100M` (mikro-lot variantı) da mövcuddur. Nəticə: Strategiya #2 EURUSD-ə uyğunlaşdırma
TƏLƏB ETMİR, `USTEC` üzərində birbaşa portlana bilər — amma `data/history/`-də hələ USTEC CSV-si
yoxdur, backtest üçün əvvəlcə `mt5/history_downloader.py` ilə endirilməlidir.

## Bug #24 (aşağı-orta prioritet, TƏXİRƏ SALINDI): `CSVDataProvider` "spread" sütununu oxumur

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

**Niyə indi düzəldilmir:** funksional problem yaratmır (Bug #22/#23/`max_break_age_bars`
EURUSD nəticələri bu səbəbdən təsirlənməyib — sabit spread konfiqurasiyası istənilən halda
tətbiq olunurdu), sadəcə backtest dəqiqliyini artıra bilər (real, zamanla dəyişən spread
simulyasiyası). Aşağı-orta prioritet, ayrıca funksionallıq artımı kimi baxılmalıdır.

**Gələcək iş (edildikdə):** `CSVDataProvider.load()`-da `target_columns`-a "spread" əlavə
et, `Bar` konstruksiyasında `getattr` fallback-ini saxlamaqla (köhnə, spread sütunu olmayan
CSV-lər üçün geriyə uyğunluq). Differential test tələb olunur: spread sütunlu və sütunsuz
CSV-lərin hər ikisinin düzgün yükləndiyini yoxlayan.

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
