# Tapşırıq İzləmə — fix/critical-state-bugs

## Status: FAZA 5 BİTDİ. FAZA 6-ya keçməzdən əvvəl istifadəçi təsdiqi tələb olunur (canlı pulla bağlı).

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

### FAZA 6 — Canlı Ticarətə Hazırlıq (⚠️ CANLI PULLA BAĞLI — başlamazdan əvvəl istifadəçi təsdiqi tələb olunur)
- [ ] MT5 connector → real `IExecutionProvider` (bu interfeys FAZA 5-də silindi, FAZA 6-da yenidən — bu dəfə faktiki `mt5/connector.py`-ə bağlı şəkildə — yazılacaq)
- [ ] Margin/leverage-aware position sizing

## Paralel iş — Strategiya Çərçivəsi (FAZA 0-6-dan ayrı roadmap, bax `walkthrough.md`)

- **Bug #16 (TƏXİRƏ SALINDI, kod dəyişməyib)**: `MarketStructureEngine` tam rebuild-in partial-rebuild-ə optimallaşdırılması. 17-19% performans qazancı Bug #1/#2-nin snapshot/restore kövrəkliyi riski ilə tərəziləndi, correctness riski üstün tutuldu — YALNIZ sənədləşdirildi, kod yazılmadı. Bax `walkthrough.md`.
- [x] **Bug #25**: `BacktestEngine`-in `setups[0]` seçib qalan konflikt setup-ları sükutla atması müşahidə edilə bilən edildi (`conflicting_setups_dropped` sayğacı + `conflict_policy` parametri). Commit `6b58b8f`. DÜZƏLDİLDİ.
- [x] **Bug #26**: `.env`-də yanlış `MT5_LOGIN` dəyəri bütün importları (MT5-ə aidiyyəti olmayanlar da daxil) çökdürürdü — `_parse_mt5_login()` ilə qorundu. Commit `1739f4f`. DÜZƏLDİLDİ.
- [x] **Bug #27**: `TradeSetup.strategy_name` heç vaxt doldurulmurdu (həmişə `""`), per-trade atributsiyanı bərpaolunmaz edirdi — 5 strategiyanın hamısında təyin edildi. Commit `66d173e`. DÜZƏLDİLDİ.
- [x] **Strategiya #4 (OrderBlockRetestStrategy)**: mövcud SMC pipeline OB-lərini yenidən istifadə edən 4-cü strategiya əlavə edildi, `is_mitigated`-dən müstəqil `_used_ob_ids` təkrar-istifadə qoruyucusu ilə. 9 yeni test. Commit `33c1d6c`. ƏLAVƏ EDİLDİ.
