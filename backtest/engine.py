"""Backtest driving execution engine."""

from typing import Protocol, runtime_checkable

from application.services.market_state_builder import MarketStateBuilder
from backtest.models import BacktestConfig, BacktestResult, BacktestTrade, TradeResult
from core.models import Bar, SignalDirection
from market_structure.structure_models import MarketState
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_entry_price, resolve_stop_and_target
from utils.logging import setup_logger

logger = setup_logger("backtest_engine")


@runtime_checkable
class IStrategyEvaluator(Protocol):
    """Protocol for strategy orchestrator/evaluator."""

    def run(self, market_state: MarketState) -> list[TradeSetup]:
        """Runs strategies on market_state and returns a list of TradeSetups."""
        ...

    def reset(self) -> None:
        """Resets the internal state of all managed strategy modules."""
        ...


class PositionSizer(Protocol):
    """Protocol for isolated position sizing logic."""

    def calculate_size(
        self,
        balance: float,
        risk_per_trade: float,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        """Calculates size of position (lots/units)."""
        ...


class SimplePositionSizer:
    """Isolated position sizer that calculates units based on price distance risk."""

    def calculate_size(
        self,
        balance: float,
        risk_per_trade: float,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        risk_amount = balance * risk_per_trade
        price_distance = abs(entry_price - stop_loss)
        if price_distance == 0:
            return 0.0
        return risk_amount / price_distance


class BacktestEngine:
    """Simulates market environments, feeding historical rates chronologically."""

    def __init__(
        self,
        config: BacktestConfig,
        position_sizer: PositionSizer | None = None,
        conflict_policy: str = "first",
    ) -> None:
        """Initializes the BacktestEngine.

        Args:
            config: Backtest configuration metrics.
            position_sizer: Position sizer strategy.
            conflict_policy: How to resolve multiple strategies proposing a
                setup on the same bar. "first" (default) keeps the existing
                behavior (take setups[0], silently ignore the rest) --
                dropped setups are always counted in
                BacktestResult.conflicting_setups_dropped regardless of
                policy, so the silent-drop is at least observable. "log_and_first"
                additionally logs a warning for every conflicting bar. Only
                "first" and "log_and_first" are implemented; other values
                behave like "first".
        """
        self.config = config
        self.position_sizer = position_sizer or SimplePositionSizer()
        self.conflict_policy = conflict_policy

    def _effective_spread(self, candle: Bar) -> float:
        """Determines the spread to use, overriding with candle's spread if set."""
        if candle.spread > 0.0:
            return candle.spread
        return self.config.spread

    def _margin_ok(self, pos_size: float, entry_price: float, balance: float) -> bool:
        """Checks whether balance covers the required margin for a position.

        Always True when config.leverage is None (margin checking disabled,
        preserving prior behavior).
        """
        if self.config.leverage is None:
            return True
        required_margin = pos_size * self.config.contract_size * entry_price / self.config.leverage
        return required_margin <= balance

    def run(
        self,
        candles: list[Bar],
        strategy_engine: IStrategyEvaluator,
        market_state_builder: MarketStateBuilder,
    ) -> BacktestResult:
        """Runs the historical simulation.

        Args:
            candles: List of historical candlestick bars.
            strategy_engine: Strategy orchestrator.
            market_state_builder: Builder orchestrating MarketState upgrades.

        Returns:
            The BacktestResult summary.
        """
        # Reset builder and state
        market_state_builder.initialize([])

        # Reset strategy engines
        if hasattr(strategy_engine, "reset"):
            strategy_engine.reset()

        if hasattr(market_state_builder, "smc_pipeline"):
            market_state_builder.smc_pipeline.max_zone_age_bars = getattr(
                self.config, "max_zone_age_bars", None
            )

        balance = self.config.initial_balance
        peak_balance = balance
        max_drawdown = 0.0

        # Simulation state
        active_trade: dict | None = None
        closed_trades: list[BacktestTrade] = []
        pending_setup: TradeSetup | None = None

        # Circuit breaker / state tracking
        account_blown = False
        blown_at_trade_index = None
        current_day = None
        daily_start_balance = balance
        daily_realized_pnl = 0.0
        daily_loss_limit_hits = 0
        day_limit_reached = False
        stopped_early = False
        stop_reason = None
        conflicting_setups_dropped = 0
        margin_rejected_setups = 0

        for idx, candle in enumerate(candles):
            # 1. Update MarketStateBuilder first
            market_state = market_state_builder.append_bar(candle)

            # Check daily boundary and reset daily metrics
            candle_date = candle.timestamp.date()
            if current_day != candle_date:
                current_day = candle_date
                daily_start_balance = balance
                daily_realized_pnl = 0.0
                day_limit_reached = False

            # If account is blown, cancel any pending setups and continue building market state
            if account_blown:
                pending_setup = None
                continue

            # 2. Check open trades exits first (using N's high/low)
            if active_trade is not None:
                # Increment bar counter
                active_trade["bars_held"] += 1
                sl_hit = False
                tp_hit = False
                expired = False

                sl = active_trade["stop_loss"]
                tp = active_trade["take_profit"]

                if active_trade["direction"] == SignalDirection.BUY:
                    if candle.low <= sl:
                        sl_hit = True
                    if candle.high >= tp:
                        tp_hit = True
                else:  # SELL
                    if candle.high >= sl:
                        sl_hit = True
                    if candle.low <= tp:
                        tp_hit = True

                # Conditional TP extension (opt-in, TradeSetup.conditional_tp_extension_*):
                # if the original TP is touched within N bars of entry, extend it
                # instead of closing -- exactly once per trade. sl_hit takes
                # precedence (a same-candle SL+TP conflict is still resolved as a
                # loss below), so extension only applies when SL was not also hit.
                if (
                    tp_hit
                    and not sl_hit
                    and active_trade["tp_extension_bars"] is not None
                    and not active_trade["tp_extension_applied"]
                    and active_trade["bars_held"] <= active_trade["tp_extension_bars"]
                ):
                    active_trade["take_profit"] = active_trade["tp_extension_price"]
                    active_trade["tp_extension_applied"] = True
                    tp = active_trade["take_profit"]
                    tp_hit = False

                # Expiration check
                if self.config.max_holding_bars is not None:
                    if active_trade["bars_held"] >= self.config.max_holding_bars:
                        expired = True

                # Exit logic & fill calculation
                spread = self._effective_spread(candle)
                slippage = self.config.slippage

                if sl_hit and tp_hit:
                    # Same-candle SL/TP conflict: conservatively assume SL hit first
                    if active_trade["direction"] == SignalDirection.BUY:
                        exit_price = sl - spread / 2 - slippage
                    else:
                        exit_price = sl + spread / 2 + slippage
                    result = TradeResult.LOSS
                elif sl_hit:
                    if active_trade["direction"] == SignalDirection.BUY:
                        exit_price = sl - spread / 2 - slippage
                    else:
                        exit_price = sl + spread / 2 + slippage
                    result = TradeResult.LOSS
                elif tp_hit:
                    if active_trade["direction"] == SignalDirection.BUY:
                        exit_price = tp - spread / 2 - slippage
                    else:
                        exit_price = tp + spread / 2 + slippage
                    result = TradeResult.WIN
                elif expired:
                    if active_trade["direction"] == SignalDirection.BUY:
                        exit_price = candle.close - spread / 2 - slippage
                    else:
                        exit_price = candle.close + spread / 2 + slippage
                    result = TradeResult.EXPIRED
                else:
                    exit_price = None
                    result = None

                if exit_price is not None and result is not None:
                    # Calculate PnL
                    pos_size = active_trade["position_size"]
                    entry_p = active_trade["entry_price"]

                    if active_trade["direction"] == SignalDirection.BUY:
                        gross_pnl = (exit_price - entry_p) * pos_size
                    else:
                        gross_pnl = (entry_p - exit_price) * pos_size

                    # Commission Model
                    if self.config.commission_per_lot is not None:
                        commission = self.config.commission_per_lot * pos_size
                    else:
                        commission = self.config.commission

                    net_pnl = gross_pnl - commission

                    # Calculate R-multiple
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
                    closed_trades.append(trade)

                    # Update balance
                    balance += net_pnl

                    # --- Negative Balance Protection ---
                    if balance <= 0.0:
                        balance = 0.0
                        account_blown = True
                        blown_at_trade_index = len(closed_trades) - 1

                    # --- Equity Floor / Max Drawdown Circuit Breaker ---
                    peak_balance = max(peak_balance, balance)
                    current_dd = (
                        (peak_balance - balance) / peak_balance if peak_balance > 0 else 0.0
                    )
                    max_drawdown = max(max_drawdown, current_dd)

                    if self.config.max_equity_drawdown_pct is not None and current_dd > self.config.max_equity_drawdown_pct:
                        stopped_early = True
                        stop_reason = f"Max equity drawdown limit of {self.config.max_equity_drawdown_pct * 100}% exceeded ({current_dd * 100:.2f}%)"
                        active_trade = None
                        break

                    # --- Max Daily Loss Circuit Breaker ---
                    daily_realized_pnl += net_pnl
                    if self.config.max_daily_loss_pct is not None:
                        daily_loss_limit = daily_start_balance * self.config.max_daily_loss_pct
                        if daily_realized_pnl <= -daily_loss_limit:
                            if not day_limit_reached:
                                day_limit_reached = True
                                daily_loss_limit_hits += 1

                    active_trade = None

            # 3. Handle pending trade execution (look-ahead bias avoidance: enter on N+1)
            #
            # A MARKET order, unconditionally, on the very next bar after the
            # setup was found -- exactly what execution/trade_manager.py's
            # open_trade() actually does live (always OrderType.BUY_MARKET/
            # SELL_MARKET via broker.place_order(), see
            # execution/fill_simulator.simulate_market_fill(), which every
            # real/paper order goes through). There is no resting limit order
            # anywhere in this codebase's live execution path: a setup never
            # waits for price to revisit its entry_zone, and never expires
            # unfilled -- it fills on bar N+1's open (plus spread/slippage)
            # or not at all (if bar N+1 doesn't exist, e.g. the setup was
            # found on the last historical bar).
            #
            # entry_zone is used ONLY to size the position (sizing_entry_price
            # below), via strategy.risk_reward.resolve_entry_price() -- the
            # identical function TradeManager.open_trade() calls, since live
            # must estimate a risk-distance to size the order BEFORE it has a
            # real fill price back from the broker. The zone plays no part in
            # whether/when the trade actually fills.
            if account_blown:
                pending_setup = None
            if day_limit_reached:
                pending_setup = None

            if pending_setup is not None and active_trade is None:
                sl_price, tp_price = resolve_stop_and_target(pending_setup)
                sizing_entry_price = resolve_entry_price(pending_setup)
                strategy_name = getattr(pending_setup, "strategy_name", "")

                spread = self._effective_spread(candle)
                slippage = self.config.slippage

                if pending_setup.direction == SignalDirection.BUY:
                    entry_price = candle.open + spread / 2 + slippage
                else:
                    entry_price = candle.open - spread / 2 - slippage

                pos_size = self.position_sizer.calculate_size(
                    balance=balance,
                    risk_per_trade=self.config.risk_per_trade,
                    entry_price=sizing_entry_price,
                    stop_loss=sl_price,
                )
                if pos_size > 0 and self._margin_ok(pos_size, entry_price, balance):
                    active_trade = {
                        "entry_time": candle.timestamp,
                        "direction": pending_setup.direction,
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
                        "tp_extension_bars": pending_setup.conditional_tp_extension_bars,
                        "tp_extension_price": pending_setup.conditional_tp_extension_price,
                        "tp_extension_applied": False,
                    }
                elif pos_size > 0:
                    margin_rejected_setups += 1

                pending_setup = None

            # 4. Generate next setups from current candle state N (to be executed on N+1).
            # pending_setup is always None here -- step 3 above unconditionally
            # resolves whatever it saw (fill or margin-reject) every bar, so
            # nothing can still be waiting by this point.
            if active_trade is None and not account_blown and not day_limit_reached:
                setups = strategy_engine.run(market_state)
                if setups:
                    if len(setups) > 1:
                        dropped = len(setups) - 1
                        conflicting_setups_dropped += dropped
                        if self.conflict_policy == "log_and_first":
                            logger.warning(
                                "Bar %d (%s): %d strategies proposed setups; keeping "
                                "setups[0] (%s), dropping %d conflicting setup(s): %s",
                                idx,
                                candle.timestamp,
                                len(setups),
                                setups[0].setup_id,
                                dropped,
                                [s.setup_id for s in setups[1:]],
                            )
                    pending_setup = setups[0]

        # Bug #54 fix: a position still open when the candle series ends (no
        # max_holding_bars configured, or SL/TP simply never touched) was previously
        # dropped silently -- it never reached closed_trades, so final_balance and
        # every reported metric (win_rate, profit_factor, total_trades) ignored it.
        # Force-close it at the last candle's close price (mark-to-market) instead,
        # and surface that this happened via force_closed_at_data_end.
        force_closed_at_data_end = 0
        if active_trade is not None and candles:
            last_candle = candles[-1]
            spread = self._effective_spread(last_candle)
            slippage = self.config.slippage
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

            if self.config.commission_per_lot is not None:
                commission = self.config.commission_per_lot * pos_size
            else:
                commission = self.config.commission

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
                exit_bar_index=len(candles) - 1,
                trade_duration=(last_candle.timestamp - active_trade["entry_time"]).total_seconds(),
                entry_spread=active_trade["entry_spread"],
                exit_spread=spread,
            )
            closed_trades.append(trade)
            balance += net_pnl

            if balance <= 0.0:
                balance = 0.0
                account_blown = True
                blown_at_trade_index = len(closed_trades) - 1

            peak_balance = max(peak_balance, balance)
            current_dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0.0
            max_drawdown = max(max_drawdown, current_dd)

            force_closed_at_data_end = 1
            active_trade = None

        # Calculate metrics for BacktestResult
        total_profit = balance - self.config.initial_balance
        total_trades = len(closed_trades)
        wins = sum(1 for t in closed_trades if t.result == TradeResult.WIN)
        win_rate = (wins / total_trades) if total_trades > 0 else 0.0

        gross_profit = sum(t.pnl for t in closed_trades if t.pnl > 0)
        gross_loss = sum(abs(t.pnl) for t in closed_trades if t.pnl < 0)
        profit_factor = (
            (gross_profit / gross_loss)
            if gross_loss > 0
            else float("inf")
            if gross_profit > 0
            else 1.0
        )

        return BacktestResult(
            trades=tuple(closed_trades),
            total_profit=total_profit,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            profit_factor=profit_factor,
            initial_balance=self.config.initial_balance,
            final_balance=balance,
            account_blown=account_blown,
            blown_at_trade_index=blown_at_trade_index,
            daily_loss_limit_hits=daily_loss_limit_hits,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            conflicting_setups_dropped=conflicting_setups_dropped,
            margin_rejected_setups=margin_rejected_setups,
            force_closed_at_data_end=force_closed_at_data_end,
        )

