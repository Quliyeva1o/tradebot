# Tapşırıq İzləmə — fix/critical-state-bugs

## Status: FAZA 4 davam edir (Bug #9 bitdi, Bug #10/#11 istifadəçi qərarı gözləyir)

## Bitmiş fazalar (toxunulmayıb, commit olunub)
- **FAZA 0 — Baseline**: 156 test (154 PASS + 2 FAIL) təsdiqləndi.
- **FAZA 1 — Kritik Korrektlik**: Bug #1 (swing upgrade propagation, 7754eaf), Bug #2 (batch vs incremental swing filtering, ea4f1cc).
- **FAZA 2 — Backtest Etibarlılığı**: Bug #7 (spread double-charge, cb1d923), Bug #5 (deterministik timestamp, 45f6549), Bug #8 (configurable pending order expiry, 05af8d... bax git log).
- **FAZA 3 — Performans**: Bug #3 (incremental liquidity, 11d2447 + 0b6509e), Bug #13 (configurable zone pruning, c4c434b), SwingGraph node access optimallaşdırması (1a8251e).
- Test sayı bu fazaların sonunda: 166/166 PASS (əsl baseline 156, +10 yeni test).
- **FAZA 3.5 — Analytics & Diagnostics**: `RejectionReason` enum (14 üzv) + `StrategyDiagnostics` sinfi (`strategy/diagnostics.py`), `strategy_engine.get_diagnostics()` aqreqasiyası, `run_backtest.py`-də loglama. Commit: `5b4ee26`. 161 (mühit məhdudiyyəti ilə) + 27 yeni = 188 PASS.
- **FAZA 4 / Bug #9 — Stale-break gating**: `max_break_age_bars` (default=`None`), `broken_swing.index` proxy (istifadəçi seçimi), `MarketState.bar_count()` əlavəsi. Commit: `6646991`. 188 + 7 = **195 PASS, 0 FAIL** (hazırkı say).

## Qalan iş

### FAZA 4 — Strategiya Keyfiyyəti (davam edir)
- [x] Bug #9: Stale break gating (`max_break_age_bars`) — BİTDİ, commit `6646991`.
- [ ] Bug #10: OB/FVG seçimi — ilk-uyğun yox, ən yaxın/ən yeni. İstifadəçidən dizayn təsdiqi gözlənilir (AskUserQuestion dismiss edildi, yenidən soruşulacaq).
- [ ] Bug #11: OB/FVG/break eyni displacement leg-ə aid olmalıdır. İstifadəçidən dizayn təsdiqi gözlənilir (ən riskli dəyişiklik, backtest nəticələrini kəskin dəyişə bilər).
- [ ] Duplicate setup bug-ın təkrar yoxlanılması + R:R gate-in bütün yollarda tətbiqinin təsdiqi

### FAZA 5 — Arxitektura Təmizliyi
- [ ] `application/ports/*`, `core/interfaces.py`, `TradingCoordinator` sil (əvvəlcə grep ilə istifadə yoxlanılacaq)
- [ ] `risk/position_size.py`, `risk/risk_reward.py` sil (NotImplementedError atır)
- [ ] Root-level debug faylları və duplikat testlər sil/köçür
- [ ] `hasattr(result, 'upgraded_swing')` sadələşdirməsi
- [ ] İki ATR implementasiyası / orphan `indicators/` paketi qərarı

### FAZA 6 — Canlı Ticarətə Hazırlıq (aşağı prioritet, bu sessiyada məcburi deyil, əvvəlcədən xəbərdarlıq tələb edir)
- [ ] MT5 connector → real `IExecutionProvider`
- [ ] Margin/leverage-aware position sizing
