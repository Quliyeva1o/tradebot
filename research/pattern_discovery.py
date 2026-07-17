"""Guarded Pattern Discovery: systematic screening of a bounded SMC pattern
search space (see strategy.parametrized_smc.PatternCandidateConfig) with
staged overfitting guards before any candidate is allowed near out-of-sample
data.

Background: 8 prior strategies/extensions were tried in this project; only
1 (Midline Sweep, USTEC-only) survived, and only after in-sample results
that looked good collapsed on out-of-sample data more than once (ORB,
Turn-of-Month DE40, Midline Sweep DE40 itself) before that. Guarded Pattern
Discovery exists because a 240-candidate grid search makes that kind of
false discovery far more likely by construction (many comparisons -> some
will look good by chance alone), so the guards here are not optional
ceremony -- they are the actual point of this module.

Five phases:
    1. generate_candidates()      -- build the (up to) 288-cell search space
                                      once, collapsing the entry_point=FVG_EDGE
                                      / require_fvg=False contradiction by
                                      construction (240 valid candidates).
    2. screen_candidates()        -- ONE shared-pipeline backtest pass over
                                      in-sample data: MarketStateBuilder.
                                      append_bar()'s SMC pipeline update (the
                                      expensive part) runs once per bar,
                                      shared across every candidate, instead
                                      of once per candidate.
    3. apply_fdr_phase()          -- Benjamini-Hochberg FDR correction on a
                                      one-sample z-test of each candidate's
                                      per-trade R-multiples, after dropping
                                      candidates below MIN_SAMPLE_SIZE trades.
    4. chronological_stability_phase() -- half-splits the in-sample period
                                      for FDR survivors and requires both
                                      halves to independently show Profit
                                      Factor > 1.0 -- the same sanity check
                                      already applied by hand to DE40's
                                      Midline Sweep in-sample result.
    5. (NOT implemented here, deliberately) -- final, one-shot out-of-sample
       confirmation for whichever phase-4 survivors a human approves. Kept
       manual, per this project's established methodology: OOS data is
       touched at most once, and only after an explicit go/no-go decision.
"""

import argparse
import itertools
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import SimplePositionSizer
from backtest.models import BacktestConfig, BacktestResult, BacktestTrade, TradeResult
from core.models import Bar, SignalDirection, Timeframe
from smc.order_block import OBDirection
from strategy.parametrized_smc import (
    EntryPoint,
    ParametrizedSMCStrategy,
    PatternCandidateConfig,
    TrendFilterMode,
)

MIN_SAMPLE_SIZE = 30
FDR_Q = 0.05


# ---------------------------------------------------------------------------
# Phase 1: candidate generation
# ---------------------------------------------------------------------------


def generate_candidates() -> list[PatternCandidateConfig]:
    """Builds the full Guarded Pattern Discovery search space.

    2 (OB direction) x 2 (require_fvg) x 2 (require_liquidity_sweep) x
    3 (entry_point) x 4 (take_profit_r) x 3 (trend_filter) = 288 raw cells,
    minus the 48 contradictory cells (entry_point=FVG_EDGE with
    require_fvg=False -- collapsed by construction, not generated at all,
    per the approved search-space design) = 240 valid candidates.
    """
    candidates: list[PatternCandidateConfig] = []
    for ob_direction, require_fvg, require_sweep, entry_point, tp_r, trend_filter in itertools.product(
        [OBDirection.BULLISH, OBDirection.BEARISH],
        [True, False],
        [True, False],
        [EntryPoint.OB_EDGE, EntryPoint.OB_MID, EntryPoint.FVG_EDGE],
        [1.0, 1.5, 2.0, 3.0],
        [TrendFilterMode.ALIGNED, TrendFilterMode.COUNTER, TrendFilterMode.NONE],
    ):
        if entry_point == EntryPoint.FVG_EDGE and not require_fvg:
            continue
        candidate_id = (
            f"{ob_direction.value.lower()}_fvg{int(require_fvg)}_sweep{int(require_sweep)}_"
            f"{entry_point.value}_tp{tp_r}_{trend_filter.value}"
        )
        candidates.append(
            PatternCandidateConfig(
                candidate_id=candidate_id,
                ob_direction=ob_direction,
                require_fvg=require_fvg,
                require_liquidity_sweep=require_sweep,
                entry_point=entry_point,
                take_profit_r=tp_r,
                trend_filter=trend_filter,
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Phase 2: shared-pipeline screening
# ---------------------------------------------------------------------------


def _effective_spread(candle: Bar, config: BacktestConfig) -> float:
    """Determines the spread to use, overriding with candle's spread if set.

    Verbatim mirror of BacktestEngine._effective_spread.
    """
    if candle.spread > 0.0:
        return candle.spread
    return config.spread


def _process_exit(sim: dict, candle: Bar, idx: int, spread: float, slippage: float, config: BacktestConfig) -> None:
    """Checks/closes sim's active trade against this bar's SL/TP/expiry.

    Scoped mirror of BacktestEngine.run()'s step 2 (engine.py:172-328):
    supports SL/TP/max_holding_bars exits, flat and per-lot commission, and
    balance/peak_balance/max_drawdown bookkeeping. Deliberately omits
    conditional TP extension (ParametrizedSMCStrategy never sets
    conditional_tp_extension_bars/price, so it would always be a no-op) and
    the max_daily_loss_pct/max_equity_drawdown_pct circuit breakers
    (screen_candidates() rejects those config fields up front -- see its
    docstring).
    """
    active_trade = sim["active_trade"]
    if active_trade is None:
        return

    active_trade["bars_held"] += 1
    sl = active_trade["stop_loss"]
    tp = active_trade["take_profit"]
    sl_hit = False
    tp_hit = False

    if active_trade["direction"] == SignalDirection.BUY:
        if candle.low <= sl:
            sl_hit = True
        if candle.high >= tp:
            tp_hit = True
    else:
        if candle.high >= sl:
            sl_hit = True
        if candle.low <= tp:
            tp_hit = True

    expired = (
        config.max_holding_bars is not None and active_trade["bars_held"] >= config.max_holding_bars
    )

    if sl_hit:
        # Same-candle SL/TP conflict resolved conservatively as SL, same as BacktestEngine.
        exit_price = (
            sl - spread / 2 - slippage if active_trade["direction"] == SignalDirection.BUY else sl + spread / 2 + slippage
        )
        result = TradeResult.LOSS
    elif tp_hit:
        exit_price = (
            tp - spread / 2 - slippage if active_trade["direction"] == SignalDirection.BUY else tp + spread / 2 + slippage
        )
        result = TradeResult.WIN
    elif expired:
        exit_price = (
            candle.close - spread / 2 - slippage
            if active_trade["direction"] == SignalDirection.BUY
            else candle.close + spread / 2 + slippage
        )
        result = TradeResult.EXPIRED
    else:
        return  # still open

    pos_size = active_trade["position_size"]
    entry_p = active_trade["entry_price"]
    if active_trade["direction"] == SignalDirection.BUY:
        gross_pnl = (exit_price - entry_p) * pos_size
    else:
        gross_pnl = (entry_p - exit_price) * pos_size

    commission = (
        config.commission_per_lot * pos_size if config.commission_per_lot is not None else config.commission
    )
    net_pnl = gross_pnl - commission

    risk_dist = abs(entry_p - sl)
    r_multiple = net_pnl / (risk_dist * pos_size) if risk_dist > 0 else 0.0

    trade = BacktestTrade(
        entry_time=active_trade["entry_time"],
        exit_time=candle.timestamp,
        direction=active_trade["direction"],
        entry_price=entry_p,
        exit_price=exit_price,
        stop_loss=sl,
        take_profit=tp,
        result=result,
        pnl=net_pnl,
        r_multiple=r_multiple,
        symbol=active_trade.get("symbol", ""),
        setup_id=active_trade.get("setup_id", ""),
        strategy_name=active_trade.get("strategy_name", ""),
        trigger_reason=active_trade.get("trigger_reason", ""),
        confidence_score=active_trade.get("confidence_score", 0.0),
        bars_held=active_trade["bars_held"],
        position_size=pos_size,
        entry_bar_index=active_trade["entry_bar_index"],
        exit_bar_index=idx,
        trade_duration=(candle.timestamp - active_trade["entry_time"]).total_seconds(),
        entry_spread=active_trade["entry_spread"],
        exit_spread=spread,
    )
    sim["closed_trades"].append(trade)

    sim["balance"] += net_pnl
    # No explicit account_blown flag: once balance hits 0, SimplePositionSizer's
    # risk_amount = balance * risk_per_trade is 0, so calculate_size() returns 0
    # and _process_pending_fill()'s `if pos_size > 0` gate naturally blocks all
    # further entries -- the same end effect as BacktestEngine's account_blown
    # circuit breaker, without needing to track/branch on it separately.
    if sim["balance"] < 0.0:
        sim["balance"] = 0.0
    sim["peak_balance"] = max(sim["peak_balance"], sim["balance"])
    current_dd = (
        (sim["peak_balance"] - sim["balance"]) / sim["peak_balance"] if sim["peak_balance"] > 0 else 0.0
    )
    sim["max_drawdown"] = max(sim["max_drawdown"], current_dd)
    sim["active_trade"] = None


def _process_pending_fill(
    sim: dict,
    candle: Bar,
    idx: int,
    spread: float,
    slippage: float,
    config: BacktestConfig,
    sizer: SimplePositionSizer,
) -> None:
    """Fills sim's pending setup if this bar's range triggers the N+1 limit order.

    Scoped mirror of BacktestEngine.run()'s step 3 (engine.py:338-431):
    supports the pending-limit-order fill/expiry mechanics and
    SimplePositionSizer-based sizing. Omits margin checking (screen_candidates()
    rejects config.leverage is not None up front, so _margin_ok() would always
    return True anyway) and conditional TP extension fields (never set by
    ParametrizedSMCStrategy).
    """
    pending_setup = sim["pending_setup"]
    if pending_setup is None or sim["active_trade"] is not None:
        return

    entry_low, entry_high = pending_setup.entry_zone
    sl_low, sl_high = pending_setup.stop_zone
    sl_price = sl_low if pending_setup.direction == SignalDirection.BUY else sl_high
    tp_low, tp_high = pending_setup.target_zone
    tp_price = tp_low if pending_setup.direction == SignalDirection.BUY else tp_high
    strategy_name = getattr(pending_setup, "strategy_name", "")

    filled = False
    if pending_setup.direction == SignalDirection.BUY:
        limit_price = entry_high
        if candle.low <= limit_price:
            entry_price = limit_price + spread / 2 + slippage
            pos_size = sizer.calculate_size(sim["balance"], config.risk_per_trade, entry_price, sl_price)
            if pos_size > 0:
                sim["active_trade"] = {
                    "entry_time": candle.timestamp,
                    "direction": SignalDirection.BUY,
                    "entry_price": entry_price,
                    "stop_loss": sl_price,
                    "take_profit": tp_price,
                    "position_size": pos_size,
                    "bars_held": 0,
                    "symbol": pending_setup.symbol,
                    "setup_id": pending_setup.setup_id,
                    "strategy_name": strategy_name,
                    "trigger_reason": pending_setup.trigger_reason,
                    "confidence_score": pending_setup.confidence_score,
                    "entry_bar_index": idx,
                    "entry_spread": spread,
                }
                filled = True
    else:
        limit_price = entry_low
        if candle.high >= limit_price:
            entry_price = limit_price - spread / 2 - slippage
            pos_size = sizer.calculate_size(sim["balance"], config.risk_per_trade, entry_price, sl_price)
            if pos_size > 0:
                sim["active_trade"] = {
                    "entry_time": candle.timestamp,
                    "direction": SignalDirection.SELL,
                    "entry_price": entry_price,
                    "stop_loss": sl_price,
                    "take_profit": tp_price,
                    "position_size": pos_size,
                    "bars_held": 0,
                    "symbol": pending_setup.symbol,
                    "setup_id": pending_setup.setup_id,
                    "strategy_name": strategy_name,
                    "trigger_reason": pending_setup.trigger_reason,
                    "confidence_score": pending_setup.confidence_score,
                    "entry_bar_index": idx,
                    "entry_spread": spread,
                }
                filled = True

    if filled:
        sim["pending_setup"] = None
        sim["pending_setup_idx"] = None
    elif sim["pending_setup_idx"] is not None and (idx - sim["pending_setup_idx"]) >= config.pending_order_expiry_bars:
        sim["pending_setup"] = None
        sim["pending_setup_idx"] = None


def _force_close_at_end(
    sim: dict, last_candle: Bar, last_idx: int, spread: float, slippage: float, config: BacktestConfig
) -> None:
    """Force-closes a still-open trade at the last candle's close (Bug #54 parity)."""
    active_trade = sim["active_trade"]
    if active_trade is None:
        return

    sl = active_trade["stop_loss"]
    tp = active_trade["take_profit"]
    if active_trade["direction"] == SignalDirection.BUY:
        exit_price = last_candle.close - spread / 2 - slippage
    else:
        exit_price = last_candle.close + spread / 2 + slippage

    pos_size = active_trade["position_size"]
    entry_p = active_trade["entry_price"]
    if active_trade["direction"] == SignalDirection.BUY:
        gross_pnl = (exit_price - entry_p) * pos_size
    else:
        gross_pnl = (entry_p - exit_price) * pos_size

    commission = (
        config.commission_per_lot * pos_size if config.commission_per_lot is not None else config.commission
    )
    net_pnl = gross_pnl - commission
    risk_dist = abs(entry_p - sl)
    r_multiple = net_pnl / (risk_dist * pos_size) if risk_dist > 0 else 0.0

    trade = BacktestTrade(
        entry_time=active_trade["entry_time"],
        exit_time=last_candle.timestamp,
        direction=active_trade["direction"],
        entry_price=entry_p,
        exit_price=exit_price,
        stop_loss=sl,
        take_profit=tp,
        result=TradeResult.EXPIRED,
        pnl=net_pnl,
        r_multiple=r_multiple,
        symbol=active_trade.get("symbol", ""),
        setup_id=active_trade.get("setup_id", ""),
        strategy_name=active_trade.get("strategy_name", ""),
        trigger_reason=active_trade.get("trigger_reason", ""),
        confidence_score=active_trade.get("confidence_score", 0.0),
        bars_held=active_trade["bars_held"],
        position_size=pos_size,
        entry_bar_index=active_trade["entry_bar_index"],
        exit_bar_index=last_idx,
        trade_duration=(last_candle.timestamp - active_trade["entry_time"]).total_seconds(),
        entry_spread=active_trade["entry_spread"],
        exit_spread=spread,
    )
    sim["closed_trades"].append(trade)
    sim["balance"] += net_pnl
    if sim["balance"] < 0.0:
        sim["balance"] = 0.0
    sim["peak_balance"] = max(sim["peak_balance"], sim["balance"])
    current_dd = (
        (sim["peak_balance"] - sim["balance"]) / sim["peak_balance"] if sim["peak_balance"] > 0 else 0.0
    )
    sim["max_drawdown"] = max(sim["max_drawdown"], current_dd)
    sim["active_trade"] = None


def _build_result(sim: dict, config: BacktestConfig) -> BacktestResult:
    """Aggregates one candidate's closed trades into a BacktestResult.

    Same formulas as the tail of BacktestEngine.run() (engine.py:537-559).
    """
    closed_trades = sim["closed_trades"]
    total_profit = sim["balance"] - config.initial_balance
    total_trades = len(closed_trades)
    wins = sum(1 for t in closed_trades if t.result == TradeResult.WIN)
    win_rate = (wins / total_trades) if total_trades > 0 else 0.0

    gross_profit = sum(t.pnl for t in closed_trades if t.pnl > 0)
    gross_loss = sum(abs(t.pnl) for t in closed_trades if t.pnl < 0)
    profit_factor = (
        (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 1.0
    )

    return BacktestResult(
        trades=tuple(closed_trades),
        total_profit=total_profit,
        win_rate=win_rate,
        max_drawdown=sim["max_drawdown"],
        profit_factor=profit_factor,
        initial_balance=config.initial_balance,
        final_balance=sim["balance"],
    )


def screen_candidates(
    bars: list[Bar],
    candidates: list[PatternCandidateConfig],
    symbol: str,
    timeframe: Timeframe,
    backtest_config: BacktestConfig,
) -> dict[str, BacktestResult]:
    """Runs every candidate over `bars` in one shared-pipeline pass.

    The expensive part -- MarketStateBuilder.append_bar()'s SMC pipeline
    update -- runs exactly once per bar, shared across every candidate,
    instead of once per candidate (which is what calling BacktestEngine.run()
    once per candidate would do). Each candidate still gets its own fully
    independent position/pending-order state, mirroring BacktestEngine's
    trade lifecycle (see _process_exit/_process_pending_fill docstrings for
    the exact scope of what's supported).

    Args:
        bars: Chronological bar list to backtest over.
        candidates: The PatternCandidateConfig list to screen.
        symbol: Trading instrument, used to build the shared MarketState.
        timeframe: Bar timeframe.
        backtest_config: Shared backtest settings. Must have leverage,
            max_daily_loss_pct, and max_equity_drawdown_pct all None --
            margin checks and circuit breakers are out of scope for this
            research screening pass (raises ValueError otherwise).

    Returns:
        Mapping of candidate_id -> that candidate's BacktestResult.

    Raises:
        ValueError: If backtest_config sets leverage, max_daily_loss_pct, or
            max_equity_drawdown_pct.
    """
    if backtest_config.leverage is not None:
        raise ValueError(
            "screen_candidates does not support margin checking (leverage must be None)"
        )
    if backtest_config.max_daily_loss_pct is not None or backtest_config.max_equity_drawdown_pct is not None:
        raise ValueError(
            "screen_candidates does not support daily-loss/equity-drawdown circuit "
            "breakers (max_daily_loss_pct and max_equity_drawdown_pct must be None)"
        )

    state_builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe)
    state_builder.initialize([])
    state_builder.smc_pipeline.max_zone_age_bars = backtest_config.max_zone_age_bars

    sizer = SimplePositionSizer()
    sims: dict[str, dict] = {
        c.candidate_id: {
            "strategy": ParametrizedSMCStrategy(c),
            "balance": backtest_config.initial_balance,
            "peak_balance": backtest_config.initial_balance,
            "max_drawdown": 0.0,
            "active_trade": None,
            "pending_setup": None,
            "pending_setup_idx": None,
            "closed_trades": [],
        }
        for c in candidates
    }

    for idx, candle in enumerate(bars):
        market_state = state_builder.append_bar(candle)
        spread = _effective_spread(candle, backtest_config)
        slippage = backtest_config.slippage

        for sim in sims.values():
            _process_exit(sim, candle, idx, spread, slippage, backtest_config)
            _process_pending_fill(sim, candle, idx, spread, slippage, backtest_config, sizer)
            if sim["active_trade"] is None and sim["pending_setup"] is None:
                setup = sim["strategy"].evaluate(market_state)
                if setup is not None:
                    sim["pending_setup"] = setup
                    sim["pending_setup_idx"] = idx

    if bars:
        last_candle = bars[-1]
        spread = _effective_spread(last_candle, backtest_config)
        slippage = backtest_config.slippage
        for sim in sims.values():
            _force_close_at_end(sim, last_candle, len(bars) - 1, spread, slippage, backtest_config)

    return {candidate_id: _build_result(sim, backtest_config) for candidate_id, sim in sims.items()}


# ---------------------------------------------------------------------------
# Phase 3: multiple-comparison correction (Benjamini-Hochberg FDR)
# ---------------------------------------------------------------------------


def _standard_normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (no scipy/statsmodels dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def one_sample_z_test(values: list[float]) -> tuple[float, float]:
    """Two-tailed one-sample z-test of whether mean(values) differs from 0.

    Uses the normal approximation (valid once n is at least MIN_SAMPLE_SIZE,
    per the Central Limit Theorem) rather than an exact Student's t
    distribution: neither scipy nor statsmodels is installed in this
    environment (verified directly -- both raise ModuleNotFoundError), and
    at n >= 30 the two are close enough that hand-rolling an exact t CDF
    would be unneeded complexity.

    Args:
        values: Sample values (e.g. one candidate's per-trade R-multiples).

    Returns:
        (z_statistic, two_tailed_p_value). (nan, 1.0) if n < 2 or the sample
        has zero variance (no evidence either way).
    """
    n = len(values)
    if n < 2:
        return float("nan"), 1.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    if variance == 0.0:
        return float("nan"), 1.0
    std_err = math.sqrt(variance / n)
    z = mean / std_err
    p = 2.0 * (1.0 - _standard_normal_cdf(abs(z)))
    return z, p


def benjamini_hochberg(p_values: list[float], q: float = FDR_Q) -> list[bool]:
    """Benjamini-Hochberg step-up FDR procedure.

    Finds the largest rank k (p-values sorted ascending) such that
    p_(k) <= (k/m) * q, and marks every hypothesis at rank <= k as a
    discovery (rejects the null of "no edge").

    Args:
        p_values: p-value per candidate, in any order (matched positionally
            to the returned list).
        q: Target false discovery rate (e.g. 0.05).

    Returns:
        A list, same order/length as p_values, of booleans: True where that
        candidate is a discovery.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    threshold_rank = 0
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= (rank / m) * q:
            threshold_rank = rank
    discoveries = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= threshold_rank:
            discoveries[i] = True
    return discoveries


# ---------------------------------------------------------------------------
# Phases 2-4 orchestration
# ---------------------------------------------------------------------------


@dataclass
class ScreeningRecord:
    """One candidate's results across phases 2-4."""

    candidate_id: str
    config: PatternCandidateConfig
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    net_profit: float = 0.0
    mean_r_multiple: float = 0.0
    z_stat: float = float("nan")
    p_value: float = 1.0
    fdr_discovery: bool = False
    first_half_pf: float | None = None
    second_half_pf: float | None = None
    stable: bool = False


def run_screening_phase(
    bars: list[Bar],
    candidates: list[PatternCandidateConfig],
    symbol: str,
    timeframe: Timeframe,
    backtest_config: BacktestConfig,
) -> list[ScreeningRecord]:
    """Phase 2: one shared-pipeline pass, then per-candidate z-test p-value.

    Candidates below MIN_SAMPLE_SIZE trades get p_value=1.0 / z_stat=nan
    (never eligible to become an FDR discovery in apply_fdr_phase).
    """
    results = screen_candidates(bars, candidates, symbol, timeframe, backtest_config)
    records: list[ScreeningRecord] = []
    for c in candidates:
        result = results[c.candidate_id]
        r_multiples = [t.r_multiple for t in result.trades]
        if len(r_multiples) >= MIN_SAMPLE_SIZE:
            z, p = one_sample_z_test(r_multiples)
        else:
            z, p = float("nan"), 1.0
        mean_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
        records.append(
            ScreeningRecord(
                candidate_id=c.candidate_id,
                config=c,
                total_trades=len(result.trades),
                win_rate=result.win_rate,
                profit_factor=result.profit_factor,
                net_profit=result.total_profit,
                mean_r_multiple=mean_r,
                z_stat=z,
                p_value=p,
            )
        )
    return records


def apply_fdr_phase(records: list[ScreeningRecord], q: float = FDR_Q) -> list[ScreeningRecord]:
    """Phase 3: BH-FDR over candidates with >= MIN_SAMPLE_SIZE trades AND a
    positive mean R-multiple only.

    one_sample_z_test is two-tailed ("does the mean differ from zero"), so
    without the mean_r_multiple > 0 filter a candidate with a confidently
    NEGATIVE edge (consistently losing, not random) would also produce a
    tiny p-value and get flagged as a "discovery" -- statistically correct
    but useless for pattern discovery, which is only looking for profitable
    patterns. Restricting eligibility to positive-mean candidates makes this
    equivalent to a one-tailed test in the direction that actually matters,
    without changing the p-value formula itself.

    Mutates and returns `records` (sets .fdr_discovery in place).
    """
    eligible_idx = [
        i
        for i, r in enumerate(records)
        if r.total_trades >= MIN_SAMPLE_SIZE and r.mean_r_multiple > 0
    ]
    p_values = [records[i].p_value for i in eligible_idx]
    discoveries = benjamini_hochberg(p_values, q)
    for idx, is_discovery in zip(eligible_idx, discoveries, strict=True):
        records[idx].fdr_discovery = is_discovery
    return records


def chronological_stability_phase(
    records: list[ScreeningRecord],
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    backtest_config: BacktestConfig,
) -> list[ScreeningRecord]:
    """Phase 4: for FDR survivors only, half-split bars chronologically and
    require both halves' Profit Factor > 1.0 -- the same check already
    applied by hand to DE40's Midline Sweep in-sample result. Mutates and
    returns `records` (sets .first_half_pf/.second_half_pf/.stable in place).
    """
    survivors = [r for r in records if r.fdr_discovery]
    if not survivors:
        return records

    mid = len(bars) // 2
    first_half, second_half = bars[:mid], bars[mid:]
    survivor_configs = [r.config for r in survivors]

    first_results = screen_candidates(first_half, survivor_configs, symbol, timeframe, backtest_config)
    second_results = screen_candidates(second_half, survivor_configs, symbol, timeframe, backtest_config)

    for r in survivors:
        pf1 = first_results[r.candidate_id].profit_factor
        pf2 = second_results[r.candidate_id].profit_factor
        r.first_half_pf = pf1
        r.second_half_pf = pf2
        r.stable = pf1 > 1.0 and pf2 > 1.0
    return records


def run_phases_1_to_4(
    bars: list[Bar],
    symbol: str,
    timeframe: Timeframe,
    backtest_config: BacktestConfig,
    fdr_q: float = FDR_Q,
) -> list[ScreeningRecord]:
    """Runs the full guarded pipeline through phase 4 (chronological
    stability) and stops -- phase 5 (final OOS confirmation) is
    deliberately not automated here; a human picks which stability-phase
    survivors (if any) are worth spending the one-shot OOS test on.
    """
    candidates = generate_candidates()
    records = run_screening_phase(bars, candidates, symbol, timeframe, backtest_config)
    records = apply_fdr_phase(records, fdr_q)
    records = chronological_stability_phase(records, bars, symbol, timeframe, backtest_config)
    return records


def _print_report(records: list[ScreeningRecord]) -> None:
    total = len(records)
    with_min_sample = sum(1 for r in records if r.total_trades >= MIN_SAMPLE_SIZE)
    discoveries = [r for r in records if r.fdr_discovery]
    stable = [r for r in discoveries if r.stable]

    print(f"\n=== Guarded Pattern Discovery: Phases 1-4 Report ===")
    print(f"Candidates generated:              {total}")
    print(f"Candidates with >= {MIN_SAMPLE_SIZE} trades:        {with_min_sample}")
    print(f"FDR discoveries (Q={FDR_Q}):              {len(discoveries)}")
    print(f"Chronologically stable (both halves PF>1.0): {len(stable)}")

    if stable:
        print("\n--- Phase 4 survivors (candidates for a human OOS decision) ---")
        for r in stable:
            print(
                f"{r.candidate_id}: trades={r.total_trades} win_rate={r.win_rate:.3f} "
                f"pf={r.profit_factor:.3f} net_profit={r.net_profit:.2f} "
                f"mean_r={r.mean_r_multiple:.3f} p={r.p_value:.4f} "
                f"half1_pf={r.first_half_pf:.3f} half2_pf={r.second_half_pf:.3f}"
            )
    elif discoveries:
        print("\n--- FDR discoveries that failed the stability check (not shown as candidates) ---")
        for r in discoveries:
            print(
                f"{r.candidate_id}: trades={r.total_trades} pf={r.profit_factor:.3f} "
                f"half1_pf={r.first_half_pf:.3f} half2_pf={r.second_half_pf:.3f}"
            )
    else:
        print("\nNo FDR discoveries -- no candidate survived phase 3.")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: runs phases 1-4 against a CSV bar file's in-sample split."""
    parser = argparse.ArgumentParser(description="Guarded Pattern Discovery, phases 1-4.")
    parser.add_argument("--data-file", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--split-ratio", type=float, default=0.7)
    parser.add_argument("--spread", type=float, default=1.0)
    parser.add_argument("--initial-balance", type=float, default=10_000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    parser.add_argument("--fdr-q", type=float, default=FDR_Q)
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="If set, only screen the first N generated candidates (for quick timing checks).",
    )
    args = parser.parse_args(argv)

    from data.csv_provider import CSVDataProvider
    from research.run_strategy_backtest import split_bars

    provider = CSVDataProvider(filepath=args.data_file)
    all_bars = provider.load()
    provider.validate(all_bars)
    bars = split_bars(all_bars, "in_sample", args.split_ratio)

    symbol = Path(args.data_file).stem
    timeframe = Timeframe[args.timeframe]
    backtest_config = BacktestConfig(
        initial_balance=args.initial_balance,
        risk_per_trade=args.risk_per_trade,
        spread=args.spread,
        commission=0.0,
        slippage=0.0,
    )

    candidates = generate_candidates()
    if args.max_candidates is not None:
        candidates = candidates[: args.max_candidates]

    records = run_screening_phase(bars, candidates, symbol, timeframe, backtest_config)
    records = apply_fdr_phase(records, args.fdr_q)
    records = chronological_stability_phase(records, bars, symbol, timeframe, backtest_config)
    _print_report(records)


if __name__ == "__main__":
    main()
