# Tapşırıq İzləmə — fix/critical-state-bugs

## Status: FAZA 6 BİTDİ (kod FAZA 5-dən sonra bu sənəddə izlənilmədən yazılıb; 2026-08-24 sessiyasında aşkar edilib doğrulandı). FAZA 7 (real hesaba keçid)-dən əvvəl istifadəçi təsdiqi tələb olunur (canlı pulla bağlı).

## Bitmiş fazalar (toxunulmayıb, commit olunub)
- **FAZA 0 — Baseline**: 156 test (154 PASS + 2 FAIL) təsdiqləndi.
- **FAZA 1 — Kritik Korrektlik**: Bug #1 (swing upgrade propagation, 7754eaf), Bug #2 (batch vs incremental swing filtering, ea4f1cc).
- **FAZA 2 — Backtest Etibarlılığı**: Bug #7 (spread double-charge, cb1d923), Bug #5 (deterministik timestamp, 45f6549), Bug #8 (configurable pending order expiry, 05af8d... bax git log).
- **FAZA 3 — Performans**: Bug #3 (incremental liquidity, 11d2447 + 0b6509e), Bug #13 (configurable zone pruning, c4c434b), SwingGraph node access optimallaşdırması (1a8251e).
- Test sayı bu fazaların sonunda: 166/166 PASS (əsl baseline 156, +10 yeni test).
- **FAZA 3.5 — Analytics & Diagnostics**: `RejectionReason` enum (14 üzv) + `StrategyDiagnostics` sinfi (`strategy/diagnostics.py`), `strategy_engine.get_diagnostics()` aqreqasiyası, `run_backtest.py`-də loglama. Commit: `5b4ee26`. 161 (mühit məhdudiyyəti ilə) + 27 yeni = 188 PASS.
- **FAZA 4 / Bug #9 — Stale-break gating**: `max_break_age_bars` (default=`None`), `broken_swing.index` proxy (istifadəçi seçimi), `MarketState.bar_count()` əlavəsi. Commit: `6646991`. 188 + 7 = 195 PASS.
- **FAZA 4 / Bug #10 — Nearest/most-recent OB & FVG seçimi**: `_select_best_order_block`/`_select_best_fvg` helper-ləri, model dəyişikliyi yoxdur. Commit: `5de6f53`. 195 + 7 = **202 PASS, 0 FAIL** (hazırkı say).
- **FAZA 4 / Duplicate + R:R gate yoxlaması**: Kod nəzərdən keçirildi, audit-in "correct" qeydini təsdiqlədim — `_proposed_keys` yoxlaması R:R gate-dən sonra, TradeSetup yaradılmazdan əvvəl işləyir, yan-keçid yoxdur. Əlavə dəyişiklik tələb olunmadı.
- **FAZA 6 — Canlı Ticarətə Hazırlıq**: `IBroker`/`MT5Broker`/`PositionSizer`/kill-switch/`DailyRiskTracker` — task.md-də izlənilmədən yazılıb (commit `26b4f08`..`49de680`..`0c31662`), 2026-08-24 sessiyasında aşkar edilib doğrulandı. Ətraflı aşağıda.

## Qalan iş

### FAZA 4 — Strategiya Keyfiyyəti (BİTDİ)
- [x] Bug #9: Stale break gating — BİTDİ, commit `6646991`.
- [x] Bug #10: OB/FVG nearest/most-recent seçimi — BİTDİ, commit `5de6f53`.
- [ ] Bug #11: OB/FVG/break eyni displacement leg-ə aid olmalıdır. **İstifadəçi qərarı ilə TƏXİRƏ SALINDI** — real backtest datası ilə kalibrasiya edildikdən sonra ayrıca ele alınacaq. Bax `walkthrough.md`.
- [x] Duplicate setup + R:R gate yoxlanıldı — düzgün işləyir, dəyişiklik tələb olunmadı.

### FAZA 5 — Arxitektura Təmizliyi (BİTDİ)
- [x] `hasattr(result, 'upgraded_swing')` sadələşdirməsi — `IncrementalSwingResult` həmişə qaytarılır, müdafiə kodu ölü idi.
- [x] `application/ports/*`, `application/dto/*`, `TradingCoordinator`, `core/interfaces.py`, `strategy/base_strategy.py` sil — grep ilə istifadəsizlik təsdiqləndi. Commit `fffc97e`.
- [x] `risk/position_size.py`, `risk/risk_reward.py` sil (NotImplementedError atır, heç yerdə import olunmur). Eyni commit.
- [x] Root-level debug faylları və duplikat testlər sil (`pyproject.toml`-da `testpaths=["tests"]` olduğu üçün test sayına təsir etmədi). Eyni commit.
- [x] `indicators/` paketi (atr/ema/macd/rsi/sma, orphan) — istifadəçi qərarı ilə SİLİNDİ. `smc/displacement.py`-dəki ayrıca, production-da işlədilən ATR-ə toxunulmadı (pandas asılılığı gətirməmək üçün). Commit `2df8e0a`.
- Test sayı: 202 → 199 (ölü təbəqə testləri) → **176 PASS, 0 FAIL** (indicators/ testləri) — hazırkı say.

**Mühit qeydi (bloklayıcı deyil)**: `run_backtest.py` import edərkən `ModuleNotFoundError: No module named 'MetaTrader5'` aşkarlandı (Windows-only SDK, macOS-da mövcud deyil). Bizim dəyişikliklərimizlə əlaqəsi yoxdur (grep ilə təsdiqləndi). Bax `walkthrough.md`.

### FAZA 6 — Canlı Ticarətə Hazırlıq (BİTDİ — kod FAZA 5-dən sonra bu sənəddə izlənilmədən yazılıb, 2026-08-24 sessiyasında aşkar edilib doğrulandı)
- [x] MT5 connector → real `IExecutionProvider`: `execution/interfaces.py`-də `IBroker` protokolu, `execution/mt5_broker.py`-də tam realizasiya (`place_order`/`cancel_order`/`close_position`/`get_open_positions`/`get_account_info`/`get_symbol_constraints`), connect-retry + exponential backoff + kill-switch ilə. Commit `26b4f08` (skelet) → `49de680` (final). `execution/paper_broker.py` — real order göndərməyən paralel realizasiya (`--paper` rejimi üçün).
- [x] Margin/leverage-aware position sizing: `execution/position_sizer.py` — brokerin öz `contract_size`/`tick_size`/`tick_value`-una görə real lot ölçüsü hesablayır (risk % → real lot), `TradeManager.open_trade()`-ə inteqrasiya olunub. Commit `49de680`.
- [x] Risk infrastruktur (orijinal FAZA 6 siyahısında yox idi, eyni məqsədə xidmət edir): fayl-əsaslı kill-switch (`risk/kill_switch.py`) + gündəlik zərər izləyicisi (`risk/daily_risk_tracker.py`, defolt `MAX_DAILY_LOSS_PCT=5%`), hər ikisi live loop-a bağlanıb. Commit `31c04a6`.
- [x] İki qatlı demo-hesab təhlükəsizlik zolağı: `.env`-də `MT5_ACCOUNT_TYPE=demo` tələbi + MT5-in öz `account_info().trade_mode`-unun yoxlanması — `run_live_demo.py` (commit `db0f65e`) və `run_live_accumulation_breakout.py`-da (commit `0c31662`) eyni şəkildə.
- [x] MT5 sifariş-göndərmə edge-case-ləri: comment uzunluq limiti (29 simvol, commit `7d9dc0b`) və filling-mode seçimi (`_resolve_type_filling`) real demo hesabda təsdiqlənib.

**Mühit qeydi (bloklayıcı deyil, əvvəlki qeydin təkrarı)**: `MetaTrader5` paketi Windows-only olduğu üçün Linux/macOS-da import xətası verir; `tests/conftest.py` bunu stub modul ilə həll edir (bax aşağıda FAZA 6.5 — stub-un özündə bir boşluq tapılıb düzəldilib).

### FAZA 6.5 — Doğrulama və test-mühiti düzəlişi (bu sessiya, 2026-08-24)
- Bütün test dəsti (1074 test) təcrid olunmuş Python 3.12 mühitində işlədildi (bu maşında `MetaTrader5` Windows-only olduğu üçün birbaşa mümkün deyildi — real Windows mühitini simulyasiya etmək üçün ayrıca venv qurulub).
- İlk nəticə: **1035 PASS, 38 FAIL, 1 XFAIL**. Bütün 38 uğursuzluq iki səbəbə endirildi (real kod xətası TAPILMADI):
  1. `tests/conftest.py`-dəki `MetaTrader5` stub-u FAZA 6 zamanı əlavə olunan bəzi sabit/metodları daşımırdı: `ORDER_FILLING_FOK/IOC/RETURN`, `ACCOUNT_TRADE_MODE_DEMO/CONTEST/REAL`, `copy_rates_from_pos`, `order_check`.
  2. `test_nasdaq_midline_sweep_regression.py` — sandbox-da `data/history/USTEC_M5.csv` olmadığı üçün (real maşında mövcuddur, orada bu test problemsiz keçməlidir).
- (1)-i `tests/conftest.py`-ə 13 sətirlik, davranışa təsir etməyən əlavə ilə düzəltdim (yalnız test-stub, production kodu toxunulmayıb). **Bu dəyişiklik diskə yazılıb, hələ commit edilməyib** — nəzərdən keçirib commit etmək lazımdır.
- Düzəlişdən sonra: **1072 PASS, 1 FAIL (yalnız yuxarıdakı (2) — sandbox-a xas, real maşında əhəmiyyətsiz), 1 XFAIL (Bug #29, artıq sənədləşdirilib və təxirə salınıb — market_state_builder.py-də swing-replacement/breaks_history bug-ı)**.
- Nəticə: FAZA 6-nın hər iki orijinal maddəsi faktiki tamamlanıb və indi tam test-doğrulanıb.

### FAZA 7 — Real Hesaba Keçid (⚠️ CANLI PULLA BAĞLI — başlamazdan əvvəl istifadəçi təsdiqi tələb olunur)
- [ ] `run_live_accumulation_breakout.py`-in öz module docstring-i xəbərdarlıq edir: `NyOpenAccumulationBreakoutStrategy` yalnız 13-91 treyd aralığında (test pəncərəsindən asılı) və ay-ay yüksək dəyişkənliklə backtest edilib — bir neçə güclü ay ümumi mənfəətin çoxunu daşıyıb. Real vəsaitdən (hətta demo hesabın oyun pulundan) əvvəl `--paper` rejimində uzun müddət sınanmalıdır.
- [ ] Uzun-müddətli paper-trading planı (müddət, minimum treyd sayı, uğur meyarları) istifadəçi ilə birlikdə müəyyənləşdirilməli.
- [ ] Yalnız bundan sonra, istifadəçinin açıq təsdiqi ilə, real hesaba keçid müzakirə oluna bilər.

## Paralel iş — Strategiya Çərçivəsi (FAZA 0-6-dan ayrı roadmap, bax `walkthrough.md`)

- **Bug #16 (TƏXİRƏ SALINDI, kod dəyişməyib)**: `MarketStructureEngine` tam rebuild-in partial-rebuild-ə optimallaşdırılması. 17-19% performans qazancı Bug #1/#2-nin snapshot/restore kövrəkliyi riski ilə tərəziləndi, correctness riski üstün tutuldu — YALNIZ sənədləşdirildi, kod yazılmadı. Bax `walkthrough.md`.
- [x] **Bug #25**: `BacktestEngine`-in `setups[0]` seçib qalan konflikt setup-ları sükutla atması müşahidə edilə bilən edildi (`conflicting_setups_dropped` sayğacı + `conflict_policy` parametri). Commit `6b58b8f`. DÜZƏLDİLDİ.
- [x] **Bug #26**: `.env`-də yanlış `MT5_LOGIN` dəyəri bütün importları (MT5-ə aidiyyəti olmayanlar da daxil) çökdürürdü — `_parse_mt5_login()` ilə qorundu. Commit `1739f4f`. DÜZƏLDİLDİ.
- [x] **Bug #27**: `TradeSetup.strategy_name` heç vaxt doldurulmurdu (həmişə `""`), per-trade atributsiyanı bərpaolunmaz edirdi — 5 strategiyanın hamısında təyin edildi. Commit `66d173e`. DÜZƏLDİLDİ.
- [x] **Strategiya #4 (OrderBlockRetestStrategy)**: mövcud SMC pipeline OB-lərini yenidən istifadə edən 4-cü strategiya əlavə edildi, `is_mitigated`-dən müstəqil `_used_ob_ids` təkrar-istifadə qoruyucusu ilə. 9 yeni test. Commit `33c1d6c`. ƏLAVƏ EDİLDİ.

## Midnight FVG canlı botu (MIDNIGHT_FVG_BOT_SPEC.md üzrə, bu sessiya)

`MIDNIGHT_FVG_BOT_SPEC.md`-də təsvir olunan tapşırıq yerinə yetirildi:
`strategy/midnight_fvg.py` (`MidnightFvgStrategy`) və `run_live_midnight_fvg.py`
yaradıldı, `strategy/ny_open_accumulation_breakout.py` /
`run_live_accumulation_breakout.py`-nin strukturu təkrarlanaraq (eyni
`TradeSetupStrategy` interfeysi, eyni iki-qatlı demo-hesab təhlükəsizlik
zolağı, eyni kill-switch/`--paper` state-izolyasiya pattern-i, öz ayrıca
`risk/kill_switch_midnight_fvg_paper.flag` /
`risk/daily_risk_state_midnight_fvg_paper.json` faylları ilə).

**Doğrulama**: `scripts/replay_live_strategy_check_midnight_fvg.py` (yeni,
`scripts/replay_live_strategy_check.py`-nin analoqu) canlı sinifi
`data/history/USTEC_M1.csv`-in mövcud tam tarixçəsi (2026-05-12 →
2026-08-21, ~3.3 ay) üzərindən şam-şam keçirdi və nəticəni
`scripts/first_fvg_backtest.py`-in eyni pəncərədəki batch nəticəsi ilə
tutuşdurdu: **65/65 trade, hər birinin giriş/SL/TP qiyməti və vaxtı tam eyni**
(bax bu iş zamanı düzəldilən 2 bug aşağıda — düzəlişlərdən SONRA əldə edilən
nəticə). `tests/test_midnight_fvg.py` (15 test) əlavə edildi.

**Bu sessiyada tapılıb düzəldilən 2 bug (ilk qaralama versiyasında, commit
edilməzdən əvvəl)**:
1. Eyni-şam "özünə-toxunma" bug-ı: FVG-ni tamamlayan (3-cü/son) şamın öz
   wick-i, tərifə görə, FVG-nin yaxın kənarına HƏMİŞƏ dəqiq bərabərdir —
   ona görə retest yoxlaması səhvən HƏMİN ŞAM ÜZƏRİNDƏ də işə düşürdü (FVG
   yarananda "ani" saxta giriş). Düzəliş: FVG-nin formalaşdığı elə həmin
   tick-də retest yoxlanılmır (`scripts/first_fvg_backtest.py`-in `b.ts >
   fvg_end_ts` — SƏRT `>` — şərtinə uyğun).
2. Gecəyarısı-kəsişən "quyruq" bug-ı: əvvəlki günün son 1-2 şamını yeni günün
   FVG-axtarış bufferinə "toxum" kimi əlavə etmə məntiqi (23:59→00:00→00:01
   kimi 3-şamlıq FVG-ni tutmaq üçün) HANSI VAXTDA olursa-olsun son 2 şamı
   götürürdü — data-da fasilə (gap) olduqda, həmin "quyruq" şamları
   gecəyarıya yaxın olmaya bilər və onların vaxtı təsadüfən [00:00,00:30)
   pəncərəsinə düşərsə, saxta FVG yarada bilərdi. Düzəliş:
   `scripts/first_fvg_backtest.py`-in `context_before`-un `ts.time() >=
   22:00` filtri kimi, "quyruq" yalnız 23:55-dən sonrakı şamlarla
   məhdudlaşdırıldı.

**Ayrıca tapılan, DÜZƏLDİLMƏMİŞ tapıntı (`run_live_accumulation_breakout.py`
haqqında, bax `run_live_midnight_fvg.py`-nin modul docstring-i)**: bu skript
`_evaluate_for_new_trade()`-də bütün lookback şamlarını əvvəlcə
`market_state`-ə əlavə edib SONRA `strategy.evaluate()`-i BİR DƏFƏ çağırır —
`scripts/replay_live_strategy_check.py`-in özünün istifadə etdiyi (və
düzgün olan) şam-şam replay pattern-indən fərqli olaraq. `main()` hər
invocation-da TƏZƏ strategiya obyekti yaratdığı üçün (proses davamlılığı
yoxdur), bu, `NyOpenAccumulationBreakoutStrategy`-nin çox-şamlı
akkumulyasiya pəncərəsinin (2-8 şam) HEÇ VAXT toplana bilməyəcəyi mənasına
gəlir — bot, olduğu kimi, HEÇ VAXT siqnal verə bilməz. `run_live_midnight_fvg.py`
bunu düzgün (şam-şam replay) yazıldı, amma `run_live_accumulation_breakout.py`
özü TOXUNULMADI (bu sessiyanın əhatəsindən kənarda) — FAZA 7-dən əvvəl bu
ayrıca araşdırılıb düzəldilməlidir.

**Qalıb (bu botun özü üçün)**: hələ demo/paper hesabda işə salınmayıb (spesifikasiyanın
təhlükəsizlik tələbinə uyğun, əvvəlcə `--paper` ilə uzun müddət sınanmalıdır).
