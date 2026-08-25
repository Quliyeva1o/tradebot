"""Daily equity-loss tracking, feeding risk.kill_switch.activate_kill_switch().

Phase 6 (order-sending) infrastructure -- not called from anywhere yet.
Intended calling convention for the future order engine: once per cycle,
fetch account state via MT5Connector.fetch_account_info() (read-only), then
call DailyRiskTracker.check_and_update(account_info.equity).

equity (balance + floating P&L on open positions), not balance, is used
deliberately: balance only reflects realized P&L, so a risk circuit-breaker
based on it would miss a large adverse move on a still-open position until
it's actually closed -- exactly the scenario a daily-loss kill-switch
exists to catch early.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from config.settings import Settings
from risk.kill_switch import activate_kill_switch, is_trading_halted
from utils.logging import setup_logger

logger = setup_logger("daily_risk_tracker")

STATE_FILE = Path(__file__).parent / "daily_risk_state.json"


@dataclass
class DailyRiskTracker:
    """Tracks equity loss since the start of the current UTC calendar day.

    The UTC-calendar-day boundary is a deliberate simplification for this
    minimal version -- it does not align with a broker/NY trading-day
    boundary (mirrors the same limitation AccumulationBreakoutStrategy's
    session_timezone parameter exists to solve elsewhere; not solved here).

    Attributes:
        max_daily_loss_pct: Fraction of day-start equity that, if lost,
            triggers the kill-switch (e.g. 0.05 = 5%). Defaults to
            Settings.MAX_DAILY_LOSS_PCT when not given explicitly.
        state_file: Override for which day-start-equity baseline file to
            read/write. Defaults to the shared STATE_FILE. Pass a distinct
            path for a tracker whose equity is not comparable to the real
            account's -- e.g. a --paper trading run's virtual balance, which
            would otherwise corrupt (or be corrupted by) the real account's
            daily-loss baseline if both processes wrote to the same file.
        kill_switch_flag_path: Override for which kill-switch flag this
            tracker's own daily-loss trip reads/creates -- see
            risk.kill_switch.is_trading_halted()/activate_kill_switch(). Kept
            separate from state_file so a caller can isolate the baseline
            without also isolating the halt (or vice versa), though the two
            are set together in practice (see run_live_accumulation_breakout.py).
    """

    max_daily_loss_pct: float | None = None
    state_file: Path | None = None
    kill_switch_flag_path: Path | None = None

    def __post_init__(self) -> None:
        if self.max_daily_loss_pct is None:
            self.max_daily_loss_pct = Settings.load().MAX_DAILY_LOSS_PCT
        if self.state_file is None:
            self.state_file = STATE_FILE

    def check_and_update(self, current_equity: float) -> bool:
        """Compares current_equity against today's recorded day-start equity.

        On the first call of a new UTC calendar day -- or if no valid prior
        state exists at all (missing/corrupt state file) -- records
        current_equity as the new day-start baseline and returns False
        without checking the loss threshold, since there is nothing yet to
        compare it against.

        Args:
            current_equity: The account's current equity (balance + floating
                P&L), e.g. MT5Connector.fetch_account_info().equity.

        Returns:
            True if this call just newly activated the kill-switch (it was
            not already active before this call); False otherwise,
            including when the daily loss limit is breached but the
            kill-switch was already active from an earlier call.
        """
        today = datetime.now(UTC).date().isoformat()
        state = self._read_state()

        if state is None or state.get("date") != today:
            self._write_state(today, current_equity)
            return False

        day_start_equity = state.get("day_start_equity")
        if not isinstance(day_start_equity, int | float) or day_start_equity <= 0:
            logger.error(
                "Invalid day_start_equity in %s (%r); resetting today's baseline to %.2f.",
                self.state_file,
                day_start_equity,
                current_equity,
            )
            self._write_state(today, current_equity)
            return False

        loss_pct = (day_start_equity - current_equity) / day_start_equity
        if loss_pct < self.max_daily_loss_pct:
            return False

        was_halted_before = is_trading_halted(self.kill_switch_flag_path)
        reason = (
            f"Daily equity loss {loss_pct:.2%} >= limit {self.max_daily_loss_pct:.2%} "
            f"(day-start equity {day_start_equity:.2f}, current {current_equity:.2f})"
        )
        activate_kill_switch(reason, self.kill_switch_flag_path)
        return not was_halted_before

    def _read_state(self) -> dict | None:
        """Fail-open toward RESETTING the tracked baseline, not toward
        silently skipping the check or blindly halting.

        Any read/parse error is logged at ERROR (not WARNING) -- unlike
        live_signal_check's dedup-signature fail-open (where a missed
        duplicate check is harmless), silently losing daily-loss tracking
        continuity is a real degradation of a risk-safety feature and must
        be visible, even though the safe recovery behavior (reset to today,
        continue) is the same "never crash the caller" direction.
        """
        try:
            return json.loads(self.state_file.read_text())
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001 - see docstring: must never raise, but always logged
            logger.error("Could not read/parse %s: %s. Resetting today's baseline.", self.state_file, type(exc).__name__)
            return None

    def _write_state(self, date: str, day_start_equity: float) -> None:
        """Persists today's day-start equity. Never raises (logs ERROR on failure).

        A write failure here does not affect this call's own in-memory
        decision (there is nothing to check yet on a new-day call) -- it
        only means a LATER call may fail to find this state and reset again,
        degrading tracking continuity rather than corrupting it silently.
        """
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"date": date, "day_start_equity": day_start_equity}))
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not persist daily risk state to %s: %s", self.state_file, type(exc).__name__)
