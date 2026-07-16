"""Analytics and report generation layer for historical simulation results."""

from typing import Any

import numpy as np

from backtest.models import BacktestResult, BacktestTrade, TradeResult
from backtest.report_models import DirectionMetrics, PerformanceMetrics, SetupMetrics
from core.models import SignalDirection


class BacktestReportGenerator:
    """Processes raw BacktestResult trades to calculate professional analytics and metrics."""

    def generate(self, result: BacktestResult) -> PerformanceMetrics:
        """Calculates comprehensive performance metrics for a backtest run.

        Args:
            result: Simulated backtesting execution results.

        Returns:
            The calculated PerformanceMetrics.
        """
        trades = result.trades
        total_trades = len(trades)

        winning_trades = sum(1 for t in trades if t.result == TradeResult.WIN)
        losing_trades = sum(1 for t in trades if t.result == TradeResult.LOSS)
        expired_trades = sum(1 for t in trades if t.result == TradeResult.EXPIRED)

        win_rate = (winning_trades / total_trades) if total_trades > 0 else 0.0
        loss_rate = (losing_trades / total_trades) if total_trades > 0 else 0.0

        gross_profit, gross_loss = self._calculate_gross_profit_loss(trades)
        net_profit = sum(t.pnl for t in trades)

        profit_factor = self._calculate_profit_factor(gross_profit, gross_loss)

        average_win, average_loss = self._calculate_win_loss_averages(trades)
        expectancy = (win_rate * average_win) - (loss_rate * average_loss)

        average_r = sum(t.r_multiple for t in trades) / total_trades if total_trades > 0 else 0.0

        # Median R
        r_multiples = sorted([t.r_multiple for t in trades])
        if r_multiples:
            n = len(r_multiples)
            if n % 2 == 1:
                median_r = r_multiples[n // 2]
            else:
                median_r = (r_multiples[n // 2 - 1] + r_multiples[n // 2]) / 2.0
        else:
            median_r = 0.0

        largest_win, largest_loss = self._calculate_largest_win_loss(trades)

        equity_curve = self.calculate_equity_curve(result)
        max_drawdown = self._calculate_drawdown(equity_curve)

        max_consec_wins, max_consec_losses = self._calculate_consecutive_streaks(trades)

        # 1. Holding Metrics
        bars_held_list = [t.bars_held for t in trades]
        average_holding_bars = sum(bars_held_list) / total_trades if total_trades > 0 else 0.0
        if bars_held_list:
            sorted_bars = sorted(bars_held_list)
            n_bars = len(sorted_bars)
            if n_bars % 2 == 1:
                median_holding_bars = float(sorted_bars[n_bars // 2])
            else:
                median_holding_bars = (sorted_bars[n_bars // 2 - 1] + sorted_bars[n_bars // 2]) / 2.0
            max_holding_bars = max(bars_held_list)
            min_holding_bars = min(bars_held_list)
        else:
            median_holding_bars = 0.0
            max_holding_bars = 0
            min_holding_bars = 0

        winners_durations = [t.trade_duration for t in trades if t.result == TradeResult.WIN]
        losers_durations = [t.trade_duration for t in trades if t.result == TradeResult.LOSS]
        average_winner_duration = sum(winners_durations) / len(winners_durations) if winners_durations else 0.0
        average_loser_duration = sum(losers_durations) / len(losers_durations) if losers_durations else 0.0

        # 2. Risk Metrics (Calmar, Sharpe, Sortino)
        avg_drawdown, longest_dd_duration, recovery_time, dd_dist = self._calculate_advanced_drawdown_metrics(result)
        sharpe, sortino, calmar = self._calculate_ratios(result, net_profit, max_drawdown)

        # 3. Distribution Metrics
        buy_trades = sum(1 for t in trades if t.direction == SignalDirection.BUY)
        sell_trades = sum(1 for t in trades if t.direction == SignalDirection.SELL)
        tp_exits = winning_trades
        sl_exits = losing_trades
        expired_exits = expired_trades

        rr_list = []
        for t in trades:
            risk = abs(t.entry_price - t.stop_loss)
            reward = abs(t.take_profit - t.entry_price)
            if risk > 0:
                rr_list.append(reward / risk)

        average_rr = sum(rr_list) / len(rr_list) if rr_list else 0.0
        if rr_list:
            sorted_rr = sorted(rr_list)
            n_rr = len(sorted_rr)
            if n_rr % 2 == 1:
                median_rr = sorted_rr[n_rr // 2]
            else:
                median_rr = (sorted_rr[n_rr // 2 - 1] + sorted_rr[n_rr // 2]) / 2.0
        else:
            median_rr = 0.0

        # 4. Monthly statistics
        monthly_stats = self.calculate_monthly_statistics(result)

        return PerformanceMetrics(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            expired_trades=expired_trades,
            win_rate=win_rate,
            loss_rate=loss_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            average_r=average_r,
            average_win=average_win,
            average_loss=average_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            net_profit=net_profit,
            max_drawdown=max_drawdown,
            max_consecutive_wins=max_consec_wins,
            max_consecutive_losses=max_consec_losses,
            median_r=median_r,
            average_holding_bars=average_holding_bars,
            median_holding_bars=median_holding_bars,
            average_winner_duration=average_winner_duration,
            average_loser_duration=average_loser_duration,
            max_holding_bars=max_holding_bars,
            min_holding_bars=min_holding_bars,
            average_drawdown=avg_drawdown,
            longest_drawdown_duration=longest_dd_duration,
            recovery_time=recovery_time,
            drawdown_distribution=dd_dist,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            buy_trades=buy_trades,
            sell_trades=sell_trades,
            tp_exits=tp_exits,
            sl_exits=sl_exits,
            expired_exits=expired_exits,
            average_rr=average_rr,
            median_rr=median_rr,
            largest_winning_streak=max_consec_wins,
            largest_losing_streak=max_consec_losses,
            monthly_statistics=monthly_stats,
        )

    def calculate_equity_curve(self, result: BacktestResult) -> list[float]:
        """Calculates chronological equity curve points from simulation result.

        Args:
            result: Simulated backtesting results.

        Returns:
            A list of float numbers representing equity curve points.
        """
        curve = [result.initial_balance]
        current = result.initial_balance
        for t in result.trades:
            current += t.pnl
            curve.append(current)
        return curve

    def calculate_monthly_returns(self, result: BacktestResult) -> dict[str, float]:
        """Groups trading net profits by exited calendar month (format: YYYY-MM).

        Args:
            result: Simulated backtesting results.

        Returns:
            A dictionary mapping YYYY-MM strings to net profit value.
        """
        returns: dict[str, float] = {}
        for t in result.trades:
            key = t.exit_time.strftime("%Y-%m")
            returns[key] = returns.get(key, 0.0) + t.pnl
        return returns

    def calculate_monthly_statistics(self, result: BacktestResult) -> list[dict[str, Any]]:
        """Groups and calculates stats by calendar month.

        Args:
            result: Simulated backtesting results.

        Returns:
            A list of dictionaries containing metrics for each month.
        """
        groups: dict[str, list[BacktestTrade]] = {}
        for t in result.trades:
            month_key = t.exit_time.strftime("%Y-%m")
            groups.setdefault(month_key, []).append(t)

        monthly_stats = []
        for month_key in sorted(groups.keys()):
            month_trades = groups[month_key]
            total = len(month_trades)
            wins = sum(1 for t in month_trades if t.result == TradeResult.WIN)
            losses = sum(1 for t in month_trades if t.result == TradeResult.LOSS)
            win_rate = (wins / total) if total > 0 else 0.0

            gross_prof, gross_los = self._calculate_gross_profit_loss(tuple(month_trades))
            net_prof = sum(t.pnl for t in month_trades)
            pf = self._calculate_profit_factor(gross_prof, gross_los)

            avg_w, avg_l = self._calculate_win_loss_averages(tuple(month_trades))
            exp = (win_rate * avg_w) - ((1.0 - win_rate) * avg_l)

            avg_r = sum(t.r_multiple for t in month_trades) / total if total > 0 else 0.0
            l_win = max((t.pnl for t in month_trades if t.pnl > 0), default=0.0)
            l_loss = max((abs(t.pnl) for t in month_trades if t.pnl < 0), default=0.0)

            # Month drawdown (relative to peak in this month)
            curve = [result.initial_balance]
            curr = result.initial_balance
            for t in result.trades:
                curr += t.pnl
                if t.exit_time.strftime("%Y-%m") == month_key:
                    curve.append(curr)
            month_dd = self._calculate_drawdown(curve)

            monthly_stats.append({
                "Month": month_key,
                "Trades": total,
                "Win Rate": win_rate,
                "Net Profit": net_prof,
                "Profit Factor": pf,
                "Expectancy": exp,
                "Average R": avg_r,
                "Largest Win": l_win,
                "Largest Loss": l_loss,
                "Drawdown": month_dd,
            })
        return monthly_stats

    def calculate_direction_metrics(
        self, result: BacktestResult
    ) -> dict[SignalDirection, DirectionMetrics]:
        """Calculates metrics separately for BUY and SELL trade directions.

        Args:
            result: Simulated backtesting results.

        Returns:
            A dictionary mapping SignalDirection keys to DirectionMetrics stats.
        """
        metrics: dict[SignalDirection, DirectionMetrics] = {}
        for direction in (SignalDirection.BUY, SignalDirection.SELL):
            dir_trades = [t for t in result.trades if t.direction == direction]
            total = len(dir_trades)
            wins = sum(1 for t in dir_trades if t.result == TradeResult.WIN)
            losses = sum(1 for t in dir_trades if t.result == TradeResult.LOSS)
            win_rate = (wins / total) if total > 0 else 0.0

            gross_profit, gross_loss = self._calculate_gross_profit_loss(tuple(dir_trades))
            profit_factor = self._calculate_profit_factor(gross_profit, gross_loss)

            average_r = sum(t.r_multiple for t in dir_trades) / total if total > 0 else 0.0
            net_profit = sum(t.pnl for t in dir_trades)

            metrics[direction] = DirectionMetrics(
                direction=direction,
                trades=total,
                wins=wins,
                losses=losses,
                win_rate=win_rate,
                profit_factor=profit_factor,
                average_r=average_r,
                net_profit=net_profit,
            )
        return metrics

    def calculate_setup_metrics(self, result: BacktestResult) -> dict[str, SetupMetrics]:
        """Groups stats by setup_id (primary key) and secondary reason/strategy metadata.

        Args:
            result: Simulated backtesting results.

        Returns:
            A dictionary mapping setup_id keys to SetupMetrics stats.
        """
        groups: dict[str, list[BacktestTrade]] = {}
        for t in result.trades:
            groups.setdefault(t.setup_id, []).append(t)

        setup_metrics: dict[str, SetupMetrics] = {}
        for setup_id, trades_list in groups.items():
            total = len(trades_list)
            wins = sum(1 for t in trades_list if t.result == TradeResult.WIN)
            losses = sum(1 for t in trades_list if t.result == TradeResult.LOSS)
            win_rate = (wins / total) if total > 0 else 0.0

            gross_profit, gross_loss = self._calculate_gross_profit_loss(tuple(trades_list))
            profit_factor = self._calculate_profit_factor(gross_profit, gross_loss)

            average_r = sum(t.r_multiple for t in trades_list) / total if total > 0 else 0.0
            net_profit = sum(t.pnl for t in trades_list)

            first_trade = trades_list[0]
            setup_metrics[setup_id] = SetupMetrics(
                setup_id=setup_id,
                strategy_name=first_trade.strategy_name,
                trigger_reason=first_trade.trigger_reason,
                trades=total,
                wins=wins,
                losses=losses,
                win_rate=win_rate,
                profit_factor=profit_factor,
                average_r=average_r,
                net_profit=net_profit,
            )
        return setup_metrics

    # --- Private Helper Methods ---

    def _calculate_gross_profit_loss(
        self, trades: tuple[BacktestTrade, ...]
    ) -> tuple[float, float]:
        """Calculates gross profit and gross loss from a tuple of trades.

        Args:
            trades: A tuple of BacktestTrade objects.

        Returns:
            A tuple of (gross_profit, gross_loss).
        """
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = sum(abs(t.pnl) for t in trades if t.pnl < 0)
        return gross_profit, gross_loss

    def _calculate_win_loss_averages(
        self, trades: tuple[BacktestTrade, ...]
    ) -> tuple[float, float]:
        """Calculates the average win/loss PnL, strictly among trades whose
        `result` is WIN/LOSS respectively (Bug #56).

        Unlike gross_profit/gross_loss (used for profit_factor, which is
        correctly result-agnostic -- money made is money made regardless of
        why a trade closed), averaging "wins" must not include an EXPIRED
        trade that happened to close with a positive PnL: doing so inflates
        average_win above the true average among trades that actually hit
        take-profit (and symmetrically for average_loss/EXPIRED-negative).

        Args:
            trades: A tuple of BacktestTrade objects.

        Returns:
            A tuple of (average_win, average_loss). average_loss is positive
            (an absolute average), matching gross_loss's sign convention.
        """
        win_pnls = [t.pnl for t in trades if t.result == TradeResult.WIN]
        loss_pnls = [abs(t.pnl) for t in trades if t.result == TradeResult.LOSS]
        average_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
        average_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
        return average_win, average_loss

    def _calculate_profit_factor(self, gross_profit: float, gross_loss: float) -> float:
        """Calculates the profit factor from gross profit and gross loss.

        Args:
            gross_profit: Sum of all positive trade profits.
            gross_loss: Sum of all absolute trade losses.

        Returns:
            The profit factor (can be inf if gross_loss is 0 and gross_profit > 0).
        """
        if gross_loss > 0:
            return gross_profit / gross_loss
        if gross_profit > 0:
            return float("inf")
        return 1.0

    def _calculate_largest_win_loss(self, trades: tuple[BacktestTrade, ...]) -> tuple[float, float]:
        """Calculates the largest win and largest loss from a tuple of trades.

        Args:
            trades: A tuple of BacktestTrade objects.

        Returns:
            A tuple of (largest_win, largest_loss) where largest_loss is positive.
        """
        largest_win = max((t.pnl for t in trades if t.pnl > 0), default=0.0)
        largest_loss = max((abs(t.pnl) for t in trades if t.pnl < 0), default=0.0)
        return largest_win, largest_loss

    def _calculate_drawdown(self, equity_curve: list[float]) -> float:
        """Calculates the max drawdown from an equity curve.

        Args:
            equity_curve: A list of equity points.

        Returns:
            The max drawdown percentage (relative to peak).
        """
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for val in equity_curve:
            peak = max(peak, val)
            dd = ((peak - val) / peak) if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd

    def _calculate_advanced_drawdown_metrics(self, result: BacktestResult) -> tuple[float, float, float, dict[str, int]]:
        """Calculates average drawdown, longest drawdown duration, recovery time, and distribution."""
        trades = result.trades
        initial_balance = result.initial_balance
        if not trades:
            return 0.0, 0.0, 0.0, {"0-2%": 0, "2-5%": 0, "5-10%": 0, "10%+": 0}

        timeline = [(initial_balance, trades[0].entry_time)]
        curr = initial_balance
        for t in trades:
            curr += t.pnl
            timeline.append((curr, t.exit_time))

        drawdowns = []
        peak = initial_balance
        peak_time = timeline[0][1]
        in_drawdown = False
        drawdown_events = []

        for val, t_time in timeline[1:]:
            peak = max(peak, val)
            dd = (peak - val) / peak if peak > 0 else 0.0
            drawdowns.append(dd)

            if val >= peak:
                if in_drawdown:
                    # Drawdown recovery event
                    duration = (t_time - peak_time).total_seconds()
                    drawdown_events.append(duration)
                    in_drawdown = False
                peak_time = t_time
            else:
                in_drawdown = True

        # Handle ongoing drawdown duration up to last trade
        longest_dd_duration = 0.0
        if drawdown_events:
            longest_dd_duration = max(drawdown_events)

        if in_drawdown and len(timeline) > 1:
            ongoing_duration = (timeline[-1][1] - peak_time).total_seconds()
            longest_dd_duration = max(longest_dd_duration, ongoing_duration)
            # Add to list for max/longest logic but not for recovery metrics since it hasn't recovered

        avg_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
        recovery_time = sum(drawdown_events) / len(drawdown_events) if drawdown_events else 0.0

        # Drawdown distribution buckets
        dd_dist = {"0-2%": 0, "2-5%": 0, "5-10%": 0, "10%+": 0}
        for dd in drawdowns:
            dd_pct = dd * 100
            if dd_pct < 2.0:
                dd_dist["0-2%"] += 1
            elif dd_pct < 5.0:
                dd_dist["2-5%"] += 1
            elif dd_pct < 10.0:
                dd_dist["5-10%"] += 1
            else:
                dd_dist["10%+"] += 1

        return avg_drawdown, longest_dd_duration, recovery_time, dd_dist

    def _calculate_ratios(self, result: BacktestResult, net_profit: float, max_drawdown: float) -> tuple[float | None, float | None, float | None]:
        """Calculates Sharpe, Sortino, and Calmar ratios based on daily returns."""
        trades = result.trades
        if not trades:
            return None, None, None

        # Group PnL by day
        daily_pnl: dict[str, float] = {}
        for t in trades:
            day_str = t.exit_time.strftime("%Y-%m-%d")
            daily_pnl[day_str] = daily_pnl.get(day_str, 0.0) + t.pnl

        # Calculate daily returns based on initial balance
        daily_returns = [pnl / result.initial_balance for pnl in daily_pnl.values()]

        # Standard deviation and mean
        if len(daily_returns) <= 1:
            return None, None, None

        mean_return = np.mean(daily_returns)
        std_return = np.std(daily_returns, ddof=1)

        # Sharpe Ratio
        if std_return > 0:
            sharpe = (mean_return / std_return) * (252 ** 0.5)
        else:
            sharpe = None

        # Sortino Ratio
        downside_returns = [r for r in daily_returns if r < 0]
        if downside_returns:
            downside_std = np.std(downside_returns, ddof=1)
            if downside_std > 0:
                sortino = (mean_return / downside_std) * (252 ** 0.5)
            else:
                sortino = None
        else:
            sortino = None

        # Calmar Ratio
        duration_days = (trades[-1].exit_time - trades[0].entry_time).days
        if duration_days < 1:
            duration_days = 1

        annualized_return = (net_profit / result.initial_balance) * (365 / duration_days)
        if max_drawdown > 0:
            calmar = annualized_return / max_drawdown
        else:
            calmar = None

        return sharpe, sortino, calmar

    def _calculate_consecutive_streaks(self, trades: tuple[BacktestTrade, ...]) -> tuple[int, int]:
        """Calculates the maximum consecutive wins and losses.

        Args:
            trades: A tuple of BacktestTrade objects.

        Returns:
            A tuple of (max_consecutive_wins, max_consecutive_losses).
        """
        max_consec_wins = 0
        curr_consec_wins = 0
        max_consec_losses = 0
        curr_consec_losses = 0

        for t in trades:
            if t.result == TradeResult.WIN:
                curr_consec_wins += 1
                max_consec_wins = max(max_consec_wins, curr_consec_wins)
                curr_consec_losses = 0
            elif t.result == TradeResult.LOSS:
                curr_consec_losses += 1
                max_consec_losses = max(max_consec_losses, curr_consec_losses)
                curr_consec_wins = 0
            else:
                curr_consec_wins = 0
                curr_consec_losses = 0

        return max_consec_wins, max_consec_losses
