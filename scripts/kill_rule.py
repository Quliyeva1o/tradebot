"""The live bot's pre-registered stop rule, measured on its real trades.

A stop rule nobody checks is not a rule. deploy/demo_roster.txt records, for each Demo bot,
thresholds fixed before its first live trade (the first on 2026-09-21) -- and until this module nothing read
them: the weekly report judged live PF by a deliberately loose band, and nothing scheduled it.

The rule is data, in deploy/kill_rules.json, so a change to it is a reviewed diff beside the roster
rather than an edit buried in code. tests/test_kill_rule.py pins the adopted numbers: the entire
value of a pre-registered rule is that it is not loosened once live trades arrive.

R is measured the way the replay that set the thresholds measured it -- net money over the money
lost at the initial stop:
  * net is every deal of the position (profit, swap, commission, fee), because the thresholds came
    from replay R net of all four;
  * the risk is the broker's own order_calc_profit from the fill to the stop the opening order
    carried, so neither contract size nor FX conversion is re-derived here;
  * a weekend-flat exit is neither TP nor SL, which is why R cannot come from the exit reason.

A trade whose stop cannot be found has unknown R, and the rule is then reported NOT EVALUATED.
Counting it as 0R would silently shrink the very drawdown the rule is measuring.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from execution.stop_and_reverse import is_reverse
from mt5.rates import BROKER_TZ

REPO = Path(__file__).parent.parent
RULES_FILE = REPO / "deploy" / "kill_rules.json"

# MetaTrader5's own values, repeated so the pairing below stays a pure function a test can drive
# without a terminal. tests/test_kill_rule.py asserts they still match the installed package.
DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 0, 1, 3
DEAL_TYPE_BUY = 0

OK, STOP, REVIEW, NOT_EVALUATED = "OK", "STOP", "REVIEW", "NOT_EVALUATED"


@dataclass(frozen=True)
class StopClause:
    within_trades: int
    max_drawdown_r: float


@dataclass(frozen=True)
class KillRule:
    task: str
    adopted: datetime  # real UTC; only trades opened at or after it count
    stops: tuple[StopClause, ...]
    checkpoint_at: int
    checkpoint_min_net_r: float

    @property
    def horizon(self) -> int:
        """The trade count after which every clause has had its say."""
        return max([s.within_trades for s in self.stops] + [self.checkpoint_at])


def load_rules(broker: str, path: Path = RULES_FILE) -> dict[str, KillRule]:
    """Every Demo bot's rule on `broker`'s account, keyed by task name. Empty when the file is missing.

    The file is keyed by broker first: since 2026-09-22 one task can trade on both accounts, and
    each deployment's rule comes from its own broker's replay -- the same launcher's thresholds
    are not the same numbers on CFI's prices as on FundingPips'.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    rules = {}
    for task, spec in raw.get(broker, {}).items():
        adopted = datetime.fromisoformat(spec["adopted"])
        if adopted.tzinfo is None:
            raise ValueError(f"{path.name}: {task} 'adopted' needs a UTC offset, got {spec['adopted']!r}")
        rules[task] = KillRule(
            task=task,
            adopted=adopted.astimezone(UTC),
            stops=tuple(StopClause(int(s["within_trades"]), float(s["max_drawdown_r"]))
                        for s in spec["stops"]),
            checkpoint_at=int(spec["checkpoint"]["at_trades"]),
            checkpoint_min_net_r=float(spec["checkpoint"]["min_net_r"]),
        )
    return rules


def deal_time_utc(epoch: int) -> datetime:
    """An MT5 deal time as real UTC.

    MT5 stamps deals with the broker's wall clock encoded as if it were UTC -- the quirk
    mt5/rates.py corrects for bars -- so a raw fromtimestamp() on CFI is three hours ahead of the
    real instant in summer. Deciding which trades fall after the adoption moment needs the real one.
    """
    naive = datetime.fromtimestamp(int(epoch), tz=UTC).replace(tzinfo=None)
    return naive.replace(tzinfo=BROKER_TZ).astimezone(UTC)


@dataclass(frozen=True)
class LiveTrade:
    position: int
    opened: datetime  # real UTC
    long: bool
    net: float  # account currency, every deal of the position
    risk: float | None  # account currency lost at the initial stop; None when no stop was found

    @property
    def r(self) -> float | None:
        return self.net / self.risk if self.risk else None


def pair_trades(
    deals: Iterable,
    *,
    symbol: str,
    prefix: str,
    since: datetime,
    stop_of: Callable[[int], float | None],
    risk_of: Callable[[bool, float, float, float], float | None],
    to_utc: Callable[[int], datetime] = deal_time_utc,
) -> list[LiveTrade]:
    """Closed positions on `symbol` that one bot opened at or after `since`, oldest first.

    `deals` are MT5 deal records. A position counts when its single opening deal carries `prefix`
    and is not a --reverse-on-stop leg (a different trade with its own 0.5R target). Positions
    still open are left out: the rule is measured on closed R only.

    stop_of(order_ticket) returns the stop the opening order carried, or None.
    risk_of(long, volume, entry, stop) returns the account-currency loss at that stop.
    """
    by_position: dict[int, list] = defaultdict(list)
    for deal in deals:
        by_position[deal.position_id].append(deal)

    trades = []
    for position, group in by_position.items():
        opening = [d for d in group if d.entry == DEAL_ENTRY_IN]
        closing = [d for d in group if d.entry in (DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY)]
        if len(opening) != 1 or not closing:
            continue
        first = opening[0]
        if first.symbol != symbol or not first.comment.startswith(prefix) or is_reverse(first.comment, prefix):
            continue
        opened = to_utc(first.time)
        if opened < since:
            continue
        long = first.type == DEAL_TYPE_BUY
        net = sum(d.profit + d.swap + d.commission + getattr(d, "fee", 0.0) for d in group)
        stop = stop_of(first.order)
        risk = risk_of(long, first.volume, first.price, stop) if stop else None
        trades.append(LiveTrade(position=position, opened=opened, long=long, net=net,
                                risk=abs(risk) if risk else None))
    return sorted(trades, key=lambda t: t.opened)


def max_drawdown(rs: Sequence[float]) -> float:
    """Deepest fall below the running peak of cumulative R, the flat start counting as a peak."""
    cumulative = peak = worst = 0.0
    for r in rs:
        cumulative += r
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return worst


@dataclass(frozen=True)
class ClauseResult:
    label: str
    breached: bool
    detail: str


@dataclass(frozen=True)
class Verdict:
    status: str
    n: int
    net_r: float | None
    below_peak_r: float | None  # where the bot stands now, not the worst it has been
    clauses: tuple[ClauseResult, ...]
    unknown: tuple[int, ...]  # positions whose R could not be computed


def evaluate(rule: KillRule, trades: Sequence[LiveTrade]) -> Verdict:
    """The rule's verdict on these trades, which must already be the ones since adoption, oldest first.

    Each stop tests the worst drawdown inside its own first `within_trades` trades, so a drawdown
    that happens at trade 45 counts against the 80-trade stop but not the 40-trade one. Once a
    clause is breached it stays breached: the worst drawdown in a window can only grow.
    """
    unknown = tuple(t.position for t in trades if t.r is None)
    if unknown:
        return Verdict(NOT_EVALUATED, len(trades), None, None, (), unknown)

    rs = [t.r for t in trades]
    n = len(rs)
    clauses = []
    for stop in rule.stops:
        worst = max_drawdown(rs[:stop.within_trades])
        breached = worst > stop.max_drawdown_r
        seen = min(n, stop.within_trades)
        verdict = "ASILDI" if breached else f"ok, {stop.max_drawdown_r - worst:.1f}R qalir"
        clauses.append(ClauseResult(
            f"DD > {stop.max_drawdown_r:g}R ilk {stop.within_trades} tradede", breached,
            f"max {worst:.1f}R ({seen}/{stop.within_trades} trade) -> {verdict}"))

    label = f"{rule.checkpoint_at}-ci tradede cemi < {rule.checkpoint_min_net_r:+g}R"
    if n >= rule.checkpoint_at:
        net_at = sum(rs[:rule.checkpoint_at])
        breached = net_at < rule.checkpoint_min_net_r
        clauses.append(ClauseResult(label, breached,
                                    f"cemi {net_at:+.1f}R -> {'ASILDI' if breached else 'kecdi'}"))
    else:
        clauses.append(ClauseResult(label, False, f"{rule.checkpoint_at - n} trade qalib"))

    if any(c.breached for c in clauses):
        status = STOP
    elif n >= rule.horizon:
        status = REVIEW
    else:
        status = OK
    total = sum(rs)
    peak = max([0.0] + [sum(rs[:i + 1]) for i in range(n)])
    return Verdict(status, n, total, peak - total, tuple(clauses), ())


_STATUS_TEXT = {
    OK: "OK",
    STOP: "DAYAN -- qayda pozuldu, botu dayandir (deploy/demo_roster.txt-den cixar)",
    REVIEW: "qaydanin butun pencereleri bitdi -- yeniden baxis lazimdir",
}


def report_lines(rule: KillRule, trades: Sequence[LiveTrade]) -> list[str]:
    """The weekly report's section for this rule, in its own ASCII Azerbaijani."""
    verdict = evaluate(rule, trades)
    lines = [f"   STOP QAYDASI ({rule.adopted:%Y-%m-%d %H:%M} UTC-den, deploy/kill_rules.json)"]
    if verdict.status == NOT_EVALUATED:
        positions = ", ".join(str(p) for p in verdict.unknown)
        lines.append(f"      {len(verdict.unknown)} trade-in stopu tapilmadi (position {positions})")
        lines.append("   >>> VEZIYYET: QIYMETLENDIRILMEDI -- R-i bilinmeyen trade 0R sayilmir, elle yoxla")
        return lines
    lines.append(f"      canli trade: {verdict.n}   cemi {verdict.net_r:+.1f}R   "
                 f"indi pikden asagi {verdict.below_peak_r:.1f}R")
    lines.extend(f"      {c.label:<32}: {c.detail}" for c in verdict.clauses)
    lines.append(f"   >>> VEZIYYET: {_STATUS_TEXT[verdict.status]}")
    return lines


def fetch_live_trades(rule: KillRule, symbol: str, prefix: str) -> list[LiveTrade]:
    """The closed trades on the connected account that this rule counts. Needs a running terminal."""
    import MetaTrader5 as mt5  # noqa: N813 -- imported here so the pure functions above need no terminal

    from mt5.connector import initialize_terminal

    # This checkout's own terminal. Two brokers share the VPS, and a bare mt5.initialize() could
    # read the other one's account, find none of this bot's trades, and report "keep trading".
    if not initialize_terminal():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        # MT5 reads these bounds as broker wall clock, which on CFI runs ahead of UTC, so pad both
        # ends by a day; pair_trades applies the exact boundary once each deal time is converted.
        deals = mt5.history_deals_get(rule.adopted - timedelta(days=1),
                                      datetime.now(UTC) + timedelta(days=1)) or []

        def stop_of(order_ticket: int) -> float | None:
            orders = mt5.history_orders_get(ticket=order_ticket) or []
            return float(orders[0].sl) if orders and orders[0].sl else None

        def risk_of(long: bool, volume: float, entry: float, stop: float) -> float | None:
            kind = mt5.ORDER_TYPE_BUY if long else mt5.ORDER_TYPE_SELL
            return mt5.order_calc_profit(kind, symbol, volume, entry, stop)

        return pair_trades(deals, symbol=symbol, prefix=prefix, since=rule.adopted,
                           stop_of=stop_of, risk_of=risk_of)
    finally:
        mt5.shutdown()
