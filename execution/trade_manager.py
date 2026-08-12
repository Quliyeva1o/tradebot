"""TradeManager: owns a single open trade's lifecycle bar-by-bar.

Replaces the implicit "check SL/TP every bar" logic embedded in
BacktestEngine.run() (a historical-replay concept, untouched this sprint)
with a live/paper-trading equivalent: places the entry via
IBroker.place_order(), then on each new bar compares the tracked position's
stop_loss/take_profit against that bar's high/low and closes via
IBroker.close_position() when either is hit. Same-bar SL/TP conflicts are
resolved the same conservative way BacktestEngine.run() resolves them (SL
takes precedence).

Consumes IBroker only -- no direct MT5Connector or PaperBroker-specific
code -- so it works unchanged against MT5Broker or PaperBroker (Dependency
Inversion, the same role StrategyEngine plays for TradeSetupStrategy
implementations).
"""

from core.models import Bar, OrderType, SignalDirection
from core.validation import require_positive
from execution.interfaces import IBroker
from execution.models import OrderRequest, OrderResult, TradeManagerAction
from execution.order import Order
from execution.position_sizer import PositionSizer
from execution.stop_engine import FixedStopEngine, StopEngine
from execution.take_profit_engine import FixedTakeProfitEngine, TakeProfitEngine
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_entry_price
from utils.logging import setup_logger

logger = setup_logger("trade_manager", log_to_file=True)


class TradeManager:
    """Owns a single open trade's lifecycle: entry, bar-by-bar SL/TP tracking, exit."""

    def __init__(self, volume: float = 0.1, position_sizer: PositionSizer | None = None) -> None:
        """Initializes the TradeManager with no open trade.

        Args:
            volume: Fixed position size (lots/units) used for every
                open_trade() order when position_sizer is not given.
                TradeSetup carries no volume/position-size field of its own,
                so it is configured once per TradeManager instance.
            position_sizer: If given, takes precedence over `volume`:
                open_trade() computes a risk-based lot size from the
                broker's account balance and the symbol's real contract-size/
                tick-value constraints (see execution/position_sizer.py)
                instead of using the fixed `volume`.

        Raises:
            ValueError: If volume is not strictly positive.
        """
        require_positive(volume, "volume")
        self._volume = volume
        self._position_sizer = position_sizer
        self._broker: IBroker | None = None
        self._position_id: str | None = None
        self._direction: SignalDirection | None = None
        self._stop_loss: float | None = None
        self._take_profit: float | None = None
        self.current_order: Order | None = None
        self.last_open_result: OrderResult | None = None
        self.last_close_result: OrderResult | None = None

    @property
    def has_open_trade(self) -> bool:
        """Whether a trade is currently open and being tracked."""
        return self._position_id is not None

    def open_trade(
        self,
        setup: TradeSetup,
        broker: IBroker,
        stop_engine: StopEngine | None = None,
        take_profit_engine: TakeProfitEngine | None = None,
    ) -> Order:
        """Places the entry for setup via broker.place_order().

        Always a market order at setup.direction; stop_loss/take_profit are
        resolved via stop_engine/take_profit_engine -- the strategy still
        decides those values as part of building the TradeSetup (see
        strategy/risk_reward.py), these engines only extract the scalars a
        broker order needs, behind a swappable interface (see
        execution/stop_engine.py, execution/take_profit_engine.py).

        Args:
            setup: The TradeSetup to enter.
            broker: The IBroker to place the order (and later close it)
                through. Stored for the subsequent on_new_bar()/
                close_trade() calls -- see class docstring.
            stop_engine: Resolves setup's stop-loss price. Defaults to
                FixedStopEngine(), preserving the exact pre-Sprint-4
                behavior (calling strategy.risk_reward.resolve_stop_and_target()
                directly) with zero call-site changes required elsewhere.
            take_profit_engine: Resolves setup's take-profit price. Defaults
                to FixedTakeProfitEngine(), same zero-call-site-change
                guarantee as stop_engine.

        Returns:
            The Order tracking this entry: FILLED if broker.place_order()
            succeeded (a position is now tracked), REJECTED otherwise (no
            position tracked; on_new_bar() is a no-op until the next
            open_trade() call). Either way, the full OrderResult (retcode/
            comment included) is recorded on last_open_result for a caller
            that needs the venue's specific rejection reason.

        Raises:
            RuntimeError: If a trade is already open (call close_trade(), or
                let on_new_bar() close it, before opening another).
        """
        if self.has_open_trade:
            raise RuntimeError(
                f"TradeManager already has an open trade (position_id={self._position_id!r}); "
                "close it before opening another."
            )

        stop_engine = stop_engine if stop_engine is not None else FixedStopEngine()
        take_profit_engine = (
            take_profit_engine if take_profit_engine is not None else FixedTakeProfitEngine()
        )
        stop_loss = stop_engine.resolve_stop(setup)
        take_profit = take_profit_engine.resolve_take_profit(setup)
        order_type = (
            OrderType.BUY_MARKET if setup.direction == SignalDirection.BUY else OrderType.SELL_MARKET
        )

        if self._position_sizer is not None:
            account_info = broker.get_account_info()
            constraints = broker.get_symbol_constraints(setup.symbol)
            entry_price = resolve_entry_price(setup)
            volume = self._position_sizer.calculate_size(
                account_info.balance, entry_price, stop_loss, constraints
            )
        else:
            volume = self._volume

        request = OrderRequest(
            symbol=setup.symbol,
            order_type=order_type,
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=setup.setup_id,
        )

        result = broker.place_order(request)
        order = Order(order_id=result.order_id, request=request)
        self.current_order = order
        self.last_open_result = result

        if not result.success:
            order.reject()
            logger.error("open_trade: broker rejected entry for %s: %s", setup.symbol, result.comment)
            return order

        order.fill(result.price)

        self._broker = broker
        self._position_id = result.position_id
        self._direction = setup.direction
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        logger.info(
            "Opened trade %s for %s @ %.5f (sl=%.5f, tp=%.5f)",
            result.position_id,
            setup.symbol,
            result.price,
            stop_loss,
            take_profit,
        )
        return order

    def on_new_bar(self, bar: Bar) -> TradeManagerAction:
        """Checks the tracked open trade against its SL/TP levels for this bar.

        A no-op (returns HELD) if there is no currently open trade.

        Args:
            bar: The newly closed bar to check.

        Returns:
            TradeManagerAction.HELD if neither level was hit (or nothing is
            open). CLOSED_SL/CLOSED_TP if a level was hit and
            broker.close_position() confirmed the close. CLOSE_FAILED if a
            level was hit but the broker declined the close (see
            last_close_result for why) -- the trade is left tracked, so the
            next on_new_bar() call re-checks the same levels and retries the
            close against the still-open position. If both levels are
            touched within the same bar, SL takes precedence -- mirrors
            BacktestEngine.run()'s same-candle SL/TP conflict resolution
            (conservatively assume SL hit first).
        """
        if not self.has_open_trade:
            return TradeManagerAction.HELD

        sl_hit, tp_hit = self._check_levels(bar)
        if sl_hit:
            return self._close(TradeManagerAction.CLOSED_SL)
        if tp_hit:
            return self._close(TradeManagerAction.CLOSED_TP)
        return TradeManagerAction.HELD

    def close_trade(self) -> TradeManagerAction:
        """Manually closes the currently tracked open trade, if any.

        Returns:
            TradeManagerAction.CLOSED_MANUAL if a trade was open and the
            broker confirmed the close. HELD if there was nothing open to
            close. CLOSE_FAILED if the broker declined the close -- the
            trade is left tracked, so calling close_trade() again retries.
        """
        if not self.has_open_trade:
            return TradeManagerAction.HELD
        return self._close(TradeManagerAction.CLOSED_MANUAL)

    def _check_levels(self, bar: Bar) -> tuple[bool, bool]:
        """Direction-aware SL/TP hit check, matching BacktestEngine.run()'s convention."""
        assert self._stop_loss is not None and self._take_profit is not None
        if self._direction == SignalDirection.BUY:
            sl_hit = bar.low <= self._stop_loss
            tp_hit = bar.high >= self._take_profit
        else:
            sl_hit = bar.high >= self._stop_loss
            tp_hit = bar.low <= self._take_profit
        return sl_hit, tp_hit

    def _close(self, action: TradeManagerAction) -> TradeManagerAction:
        """Attempts the broker close, applying `action` only if the broker confirms it.

        A real close can be declined by MT5 (market closed, trading
        disabled, requote, a dropped connection, ...) exactly like a real
        open can -- see open_trade()'s own `if not result.success` handling,
        which this mirrors. Unconditionally reporting `action` regardless of
        the broker's response would tell the caller (and, transitively,
        trade_events.log) that a position was closed when it might still be
        open and unmanaged.

        Args:
            action: The CLOSED_* outcome to report if the broker confirms
                the close.

        Returns:
            `action` on a confirmed close (tracked state is cleared).
            TradeManagerAction.CLOSE_FAILED if the broker declines it --
            tracked state is deliberately left untouched (see
            last_close_result for the declined OrderResult) so the next
            on_new_bar()/close_trade() call retries against the same
            still-open position.
        """
        assert self._broker is not None and self._position_id is not None
        result = self._broker.close_position(self._position_id)
        self.last_close_result = result
        if not result.success:
            logger.error(
                "close_position failed for position %s (retcode=%s comment=%s); "
                "leaving the trade tracked so the next tick retries.",
                self._position_id,
                result.retcode,
                result.comment,
            )
            return TradeManagerAction.CLOSE_FAILED

        self._broker = None
        self._position_id = None
        self._direction = None
        self._stop_loss = None
        self._take_profit = None
        return action
