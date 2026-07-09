"""Backtest driving execution engine."""

from typing import Protocol, runtime_checkable

from application.services.market_state_builder import MarketStateBuilder
from backtest.models import BacktestConfig, BacktestResult, BacktestTrade, TradeResult
from core.models import Bar, SignalDirection
from market_structure.structure_models import MarketState
from strategy.models import TradeSetup


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
    ) -> None:
        """Initializes the BacktestEngine.

        Args:
            config: Backtest configuration metrics.
            position_sizer: Position sizer strategy.
        """
        self.config = config
        self.position_sizer = position_sizer or SimplePositionSizer()

    def _effective_spread(self, candle: Bar) -> float:
        """Determines the spread to use, overriding with candle's spread if set."""
        if candle.spread > 0.0:
            return candle.spread
        return self.config.spread

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
        pending_setup_idx: int | None = None

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
            if account_blown:
                pending_setup = None
                pending_setup_idx = None
            if day_limit_reached:
                pending_setup = None
                pending_setup_idx = None

            if pending_setup is not None and active_trade is None:
                entry_low, entry_high = pending_setup.entry_zone

                sl_low, sl_high = pending_setup.stop_zone
                sl_price = sl_low if pending_setup.direction == SignalDirection.BUY else sl_high

                tp_low, tp_high = pending_setup.target_zone
                tp_price = tp_low if pending_setup.direction == SignalDirection.BUY else tp_high

                strategy_name = getattr(pending_setup, "strategy_name", "")

                spread = self._effective_spread(candle)
                slippage = self.config.slippage

                if pending_setup.direction == SignalDirection.BUY:
                    limit_price = entry_high
                    if candle.low <= limit_price:
                        # Entry triggered
                        entry_price = limit_price + spread / 2 + slippage
                        pos_size = self.position_sizer.calculate_size(
                            balance=balance,
                            risk_per_trade=self.config.risk_per_trade,
                            entry_price=entry_price,
                            stop_loss=sl_price,
                        )
                        if pos_size > 0:
                            active_trade = {
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
                else:  # SELL
                    limit_price = entry_low
                    if candle.high >= limit_price:
                        # Entry triggered
                        entry_price = limit_price - spread / 2 - slippage
                        pos_size = self.position_sizer.calculate_size(
                            balance=balance,
                            risk_per_trade=self.config.risk_per_trade,
                            entry_price=entry_price,
                            stop_loss=sl_price,
                        )
                        if pos_size > 0:
                            active_trade = {
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

                # Discard or keep pending setup depending on fill or expiry
                if active_trade is not None:
                    pending_setup = None
                    pending_setup_idx = None
                elif pending_setup_idx is not None and (idx - pending_setup_idx) >= self.config.pending_order_expiry_bars:
                    pending_setup = None
                    pending_setup_idx = None

            # 4. Generate next setups from current candle state N (to be executed on N+1)
            if account_blown:
                pending_setup = None
                pending_setup_idx = None
            if day_limit_reached:
                pending_setup = None
                pending_setup_idx = None

            if active_trade is None and pending_setup is None and not account_blown and not day_limit_reached:
                setups = strategy_engine.run(market_state)
                if setups:
                    pending_setup = setups[0]
                    pending_setup_idx = idx

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
        )

