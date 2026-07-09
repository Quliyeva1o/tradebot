# Tapşırıq İzləmə — fix/critical-state-bugs

## Status: FAZA 3.5 bitdi, FAZA 4-ə keçilir

## Bitmiş fazalar (toxunulmayıb, commit olunub)
- **FAZA 0 — Baseline**: 156 test (154 PASS + 2 FAIL) təsdiqləndi.
- **FAZA 1 — Kritik Korrektlik**: Bug #1 (swing upgrade propagation, 7754eaf), Bug #2 (batch vs incremental swing filtering, ea4f1cc).
- **FAZA 2 — Backtest Etibarlılığı**: Bug #7 (spread double-charge, cb1d923), Bug #5 (deterministik timestamp, 45f6549), Bug #8 (configurable pending order expiry, 05af8d... bax git log).
- **FAZA 3 — Performans**: Bug #3 (incremental liquidity, 11d2447 + 0b6509e), Bug #13 (configurable zone pruning, c4c434b), SwingGraph node access optimallaşdırması (1a8251e).
- Test sayı bu fazaların sonunda: 166/166 PASS (əsl baseline 156, +10 yeni test).

## FAZA 3.5 — Analytics & Diagnostics (BİTDİ, hələ commit edilməyib)
Məqsəd: `strategy/continuation.py`-dəki hər bir rədd qapısının (gate) nə qədər namizədi süzdüyünü izləyən aşağı-xərcli sayğac sistemi.

Edilənlər:
- `strategy/diagnostics.py` (yeni) — `RejectionReason` enum (14 üzv) + `StrategyDiagnostics` sinfi.
- `strategy/continuation.py` — hər iki strategiyada 14 rədd nöqtəsi indi `RejectionReason` ilə qeyd olunur; `reset()` diaqnostikanı da təmizləyir.
- `strategy/strategy_engine.py` — `get_diagnostics()` metodu, bütün qeydiyyatdan keçmiş strategiyalardan aqreqasiya.
- `run_backtest.py` — simulyasiya bitəndən sonra diaqnostika JSON kimi loglanır.
- `tests/test_strategy_diagnostics.py` (yeni, 27 test).

Test nəticəsi: 161 (mühit məhdudiyyəti ilə, bax aşağı) + 27 yeni = **188 PASS, 0 FAIL**.

**Mühit qeydi (plandan kənar, bloklayıcı deyil)**: Bu maşında internet yoxdur, `reportlab` quraşdırıla bilmədi → `tests/test_research.py` (5 test) collect oluna bilmir. Bu, bizim FAZA 3.5 işimizdən asılı olmayan, `main`-dən miras qalan əvvəlki bir commit-in (`05ba88a`) asılılıq problemidir. Tam təfərrüat `walkthrough.md`-də.

**Növbəti addım**: Bu fazanı commit et (`feat: add rejection-reason diagnostics for continuation strategies (FAZA 3.5)`), sonra FAZA 4-ə keç.

## Qalan iş

### FAZA 4 — Strategiya Keyfiyyəti (tövsiyə olunur, məcburi deyil)
- [ ] Bug #9: Stale break gating (`max_break_age_bars`)
- [ ] Bug #10: OB/FVG seçimi — ilk-uyğun yox, ən yaxın/ən yeni
- [ ] Bug #11: OB/FVG/break eyni displacement leg-ə aid olmalıdır
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
