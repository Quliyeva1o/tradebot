"""ICT A+ DEMO order bot: turns the paper book's A+ setups into resting limit orders on the DEMO account.

    python -m ict_lab.execute --feed cfi --symbols NDX100 GBPUSD                 dry run: prints what it would do
    python -m ict_lab.execute --feed cfi --symbols NDX100 GBPUSD --place-orders  real orders on the DEMO account
    ... --loop                                                                    stay resident, act on every new minute

What it trades is exactly what ict_lab.live books on paper (same detectors, same grades, same first-candidate-per-window
rule), restricted to A+ and a fixed R multiple (default 2R). Per window the bot does what the backtest's trade did:

  - a setup that is still waiting for its limit gets a resting limit order with the stop and the target on the order;
    an OTE (fib) setup's limit follows the impulse, so the order is cancelled and re-placed when that level moves;
  - a setup the paper book declares dead (zone broken, target reached first, window over) gets its order cancelled;
  - the stop and the target live at the broker; a position still open at 16:00 New York is closed at market;
  - setups first seen already filled, or older than --max-age-min, are NEVER chased.

It differs from the backtest in ways that are measured, not hidden (every one is logged as an event):
  - ny_pm setups are skipped (the paper book carries them overnight, the backtest flattened at 16:00);
  - `market`-mode setups (1.5% of A+ trades) are skipped;
  - the first candidate of a window blocks later ones until it dies, because live cannot see the future;
  - stops closer than 5 spreads are refused (as the backtest does), and a trade the risk cap or the minimum lot
    would turn into more than --max-lot-risk-mult times the intended risk is skipped, with the reason logged.

SAFETY. Orders are only placed with --place-orders, only when .env says MT5_ACCOUNT_TYPE=demo AND the terminal
reports a DEMO account, only on a HEDGING account (several models can be open on one symbol), and never while the
kill switch is active. Without --place-orders nothing is sent, not even a cancel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5  # noqa: N813

import ict_lab.live as L
from execution.interfaces import IBroker
from execution.models import OrderRequest
from execution.position_sizer import PositionSizer
from ict_lab.core import FLAT, MIN_RISK_SPREADS, Mirror, build_setups
from ict_lab.strategies import CFGS
from core.models import OrderType
from utils.logging import setup_logger

NY = ZoneInfo("America/New_York")
PREFIX = "ict"
BOOK_GRADE = "A+"
EVENTS = Path(__file__).resolve().parent.parent / "logs" / "ict_exec_events.jsonl"
STATE_DIR = Path(__file__).resolve().parent.parent / "risk"

logger = setup_logger("ict_execute", log_to_file=True)


def tag_of(setup_id: str) -> str:
    """The order/position comment that ties a broker object to its setup: 13 characters, well inside MT5's limit."""
    return PREFIX + hashlib.sha1(setup_id.encode()).hexdigest()[:10]


def ny_minutes_to_utc(minutes: int) -> datetime:
    """New York wall-clock minutes since 1970-01-01 (day*1440 + minute of day) as a real UTC instant."""
    naive = datetime(1970, 1, 1) + timedelta(minutes=int(minutes))
    return naive.replace(tzinfo=NY).astimezone(UTC)


@dataclass(frozen=True)
class Candidate:
    """The one candidate of a (symbol, model, window) group the paper book is following, with what is needed to trade it."""

    sid: str
    name: str            # ict_lab symbol name, e.g. NDX100
    ticker: str          # the broker's name for it, e.g. US100_Spot
    strat: str
    win: str
    d: int               # +1 long, -1 short
    grade: str
    mode: str            # limit | fib | market
    sig_ts: int          # UTC epoch of the signal bar's close
    state: str           # pending | open | closed | dead | skip
    why: str
    limit: float | None  # where a resting order sits right now (pending only)
    sl: float
    expire_utc: datetime
    hold_utc: datetime
    floor: float         # the spread the backtest charged at minimum, in price units


def collect(frames: dict, symbols: list[str], models: set[str], allow_ny_pm: bool, now_epoch: int) -> list[Candidate]:
    """Every group's active candidate, A+ only. Mirrors ict_lab.live.scan so the live orders and the paper book agree."""
    out: list[Candidate] = []
    twins: set[str] = set()
    for name in symbols:
        if name not in frames or L.SYMBOLS[name][1] not in frames:
            continue
        sym = L.make_sym(name, frames)
        mir = Mirror(sym)
        today = int(sym.m5.day[-1])
        lv = sym.levels(today)
        if lv is None or today not in sym.days:
            continue
        groups: dict[str, list] = {}
        for key, cfg in CFGS.items():
            for st in build_setups(sym, mir, cfg, today, lv):
                groups.setdefault(L.group_key(st), []).append(st)
        for cands in groups.values():
            cands.sort(key=lambda s: s.sig_ts)
            for st in cands:
                sid = L.setup_id(st)
                twin = f"{st.sym}|{st.strat}|{st.day}|{st.win}|{st.d}|{st.E:.6g}|{st.sl:.6g}"
                if twin in twins:
                    continue                     # the same zone found again on a later bar
                twins.add(twin)
                stat = L.status(st, sym, "r2")
                if st.grade == BOOK_GRADE and st.strat in models and (allow_ny_pm or st.win != "ny_pm"):
                    cfg = CFGS[st.strat]
                    wb = next(b for lab, _, b in cfg.windows if lab == st.win)
                    exp_to = st.day * 1440 + min(wb + cfg.fill_after, FLAT)
                    if st.win == "ny_pm":
                        exp_to = (st.day + 1) * 1440 + L.OVERNIGHT_UNTIL
                    hold_to = exp_to if st.win == "ny_pm" else st.day * 1440 + FLAT
                    out.append(Candidate(
                        sid=sid, name=st.sym, ticker=L.ticker(st.sym), strat=st.strat, win=st.win, d=st.d, grade=st.grade,
                        mode=st.mode, sig_ts=st.sig_ts, state=stat["state"], why=stat.get("why", ""),
                        limit=stat.get("limit"), sl=float(st.sl), expire_utc=ny_minutes_to_utc(exp_to),
                        hold_utc=ny_minutes_to_utc(hold_to), floor=L.floor_of(st.sym)))
                if stat["state"] in ("dead", "skip"):
                    continue     # a dead candidate is reported (so its order is cancelled) and the next one is considered
                break            # the first candidate that is pending / open / closed is this window's trade
    return out


class Market:
    """What the executor reads from the terminal besides the broker interface. A class so tests can replace it."""

    def __init__(self) -> None:
        self.override: dict[str, tuple[float, float]] = {}      # replay (--asof): quotes taken from the bars, not the live tick

    def quote(self, ticker: str) -> tuple[float, float] | None:
        if ticker in self.override:
            return self.override[ticker]
        tick = mt5.symbol_info_tick(ticker)
        return None if tick is None else (float(tick.bid), float(tick.ask))

    def digits(self, ticker: str) -> int:
        return int(mt5.symbol_info(ticker).digits)

    def stops_level_price(self, ticker: str) -> float:
        info = mt5.symbol_info(ticker)
        return float(info.trade_stops_level) * float(info.point)

    def position_result(self, position_id: str) -> float | None:
        """Net profit (money) of a closed position from the deal history; None when the terminal has none yet."""
        try:
            deals = mt5.history_deals_get(position=int(position_id))
        except Exception:
            return None
        if not deals:
            return None
        return float(sum(d.profit + d.commission + d.swap + d.fee for d in deals))


class DryBroker:
    """Reads go through to the real broker; writes are only reported. What --place-orders absent means."""

    def __init__(self, inner: IBroker, report) -> None:
        self._inner = inner
        self._report = report

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def place_order(self, order):
        self._report("dry_place", symbol=order.symbol, type=order.order_type.name, volume=order.volume, price=order.price,
                     sl=order.stop_loss, tp=order.take_profit, comment=order.comment)
        from execution.models import OrderResult
        return OrderResult(success=True, order_id="dry", position_id="dry", price=order.price or 0.0, volume=order.volume)

    def cancel_order(self, order_id):
        self._report("dry_cancel", order_id=order_id)
        return True

    def close_position(self, position_id):
        self._report("dry_close", position_id=position_id)
        from execution.models import OrderResult
        return OrderResult(success=True, order_id="dry", position_id=position_id)


class Executor:
    def __init__(self, broker: IBroker, market: Market, args, state_path: Path, report=None) -> None:
        self.broker = broker
        self.market = market
        self.args = args
        self.state_path = state_path
        self.state = self._load()
        self._report = report or self._default_report
        self.sizer = PositionSizer(risk_per_trade_pct=args.risk_per_trade_pct)

    # ------------------------------------------------------------------ state / events
    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf8"))
        return {"setups": {}}

    def save(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        self.state["setups"] = {k: v for k, v in self.state["setups"].items() if v.get("placed_utc", "9") >= cutoff}
        tmp = self.state_path.with_suffix(".tmp")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self.state), encoding="utf8")
        os.replace(tmp, self.state_path)

    @staticmethod
    def _default_report(kind: str, **fields) -> None:
        rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": kind, **fields}
        line = json.dumps(rec, default=str)
        print(line, flush=True)
        try:
            EVENTS.parent.mkdir(parents=True, exist_ok=True)
            with EVENTS.open("a", encoding="utf8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def event(self, kind: str, **fields) -> None:
        self._report(kind, **fields)

    # ------------------------------------------------------------------ sizing / risk
    def _loss(self, constraints, distance: float, volume: float) -> float:
        return distance / constraints.tick_size * constraints.tick_value * volume

    def committed_risk(self, tickers: set[str], constraints_of) -> float:
        """Money at risk on our open positions plus our resting orders, measured to their stops."""
        total = 0.0
        for p in self.broker.get_open_positions():
            if p.comment.startswith(PREFIX) and p.stop_loss:
                total += self._loss(constraints_of(p.symbol), abs(p.open_price - p.stop_loss), p.volume)
        for tk in tickers:
            for o in self.broker.get_pending_orders(tk):
                if o.comment.startswith(PREFIX) and o.stop_loss:
                    total += self._loss(constraints_of(o.symbol), abs(o.price - o.stop_loss), o.volume)
        return total

    # ------------------------------------------------------------------ one cycle
    def reconcile(self, cands: list[Candidate], now: datetime) -> None:
        positions = {p.comment: p for p in self.broker.get_open_positions() if p.comment.startswith(PREFIX)}
        tickers = {c.ticker for c in cands} | {r["ticker"] for r in self.state["setups"].values() if r.get("ticker")}
        pending = {}
        for tk in tickers:
            for o in self.broker.get_pending_orders(tk):
                if o.comment.startswith(PREFIX):
                    pending[o.comment] = o
        cache: dict = {}

        def constraints_of(ticker: str):
            if ticker not in cache:
                cache[ticker] = self.broker.get_symbol_constraints(ticker)
            return cache[ticker]

        seen_tags = set()
        for c in cands:
            tag = tag_of(c.sid)
            seen_tags.add(tag)
            rec = self.state["setups"].get(c.sid)
            if rec is not None:
                rec["missing"] = 0
            order = pending.get(tag)
            if c.state == "pending":
                if order is not None:
                    self._follow_limit(c, rec, order, now, constraints_of)
                elif rec is None:
                    self._maybe_place(c, now, constraints_of, pending, positions, tickers)
                # rec exists and no order: it filled, was cancelled, or expired -- never re-placed
            elif c.state in ("dead", "closed"):
                # the paper book is finished with this setup; the broker's fills stay the truth for 'open'
                if order is not None:
                    why = c.why or c.state
                    if self._cancel(order, f"paper book says {c.state}: {why}") and rec is not None:
                        rec["status"] = "cancelled"
                        rec["reason"] = why
        # an order of ours whose setup is no longer in the book for two cycles running is not trusted
        for tag, order in pending.items():
            if tag in seen_tags:
                continue
            rec = next((r for r in self.state["setups"].values() if r.get("tag") == tag), None)
            if rec is None:
                self._cancel(order, "order of ours with no record")
                continue
            rec["missing"] = rec.get("missing", 0) + 1
            if rec["missing"] >= 2 and self._cancel(order, "setup left the book"):
                rec["status"] = "cancelled"
                rec["reason"] = "setup_gone"

    def _follow_limit(self, c: Candidate, rec: dict | None, order, now: datetime, constraints_of) -> None:
        """A fib (OTE) limit moves with the impulse: re-place the order when the level has moved a whole tick."""
        if c.mode != "fib" or c.limit is None or rec is None:
            return
        tick = constraints_of(c.ticker).tick_size
        if abs(round(c.limit, self.market.digits(c.ticker)) - order.price) < tick:
            return
        if self._cancel(order, f"fib limit moved {order.price} -> {c.limit}"):
            rec["mods"] = rec.get("mods", 0) + 1
            self.state["setups"].pop(c.sid, None)         # a fresh placement, same tag
            self._maybe_place(c, now, constraints_of, {}, {}, {c.ticker}, replacement=True, mods=rec["mods"])

    def _cancel(self, order, reason: str) -> bool:
        ok = self.broker.cancel_order(order.id)
        self.event("cancel", ticket=order.id, symbol=order.symbol, comment=order.comment, ok=ok, reason=reason)
        return bool(ok)

    def _maybe_place(self, c: Candidate, now: datetime, constraints_of, pending, positions, tickers,
                     replacement: bool = False, mods: int = 0) -> None:
        a = self.args
        age_min = (now.timestamp() - c.sig_ts) / 60
        if c.mode == "market":
            self.event("skip", sid=c.sid, reason="market_mode_not_traded")
            self.state["setups"][c.sid] = self._skipped(c, "market_mode")
            return
        if c.limit is None:
            self.event("skip", sid=c.sid, reason="no_limit_price", mode=c.mode)
            return
        if not replacement and age_min > a.max_age_min:
            self.event("skip", sid=c.sid, reason="stale_not_chased", age_min=round(age_min, 1))
            self.state["setups"][c.sid] = self._skipped(c, "stale_not_chased")
            return
        constraints = constraints_of(c.ticker)
        digits = self.market.digits(c.ticker)
        limit = round(c.limit, digits)
        sl = round(c.sl, digits)
        risk = abs(limit - sl)
        if (limit - sl) * c.d <= 0:
            self.event("skip", sid=c.sid, reason="limit_beyond_stop", limit=limit, sl=sl)
            self.state["setups"][c.sid] = self._skipped(c, "limit_beyond_stop")
            return
        quote = self.market.quote(c.ticker)
        if quote is None:
            self.event("skip", sid=c.sid, reason="no_quote")
            return
        bid, ask = quote
        spread = ask - bid
        eff_spread = max(spread, c.floor)
        if risk < MIN_RISK_SPREADS * eff_spread:
            self.event("skip", sid=c.sid, reason="stop_under_5_spreads", risk=risk, spread=spread)
            self.state["setups"][c.sid] = self._skipped(c, "stop_under_5_spreads")
            return
        if spread > a.max_spread_mult * c.floor:
            self.event("skip", sid=c.sid, reason="spread_too_wide", spread=spread, floor=c.floor)
            return                                                         # may pass: retry next cycle
        stops = self.market.stops_level_price(c.ticker)
        if (c.d == 1 and limit > ask - stops) or (c.d == -1 and limit < bid + stops):
            self.event("skip", sid=c.sid, reason="limit_too_close_to_market", limit=limit, bid=bid, ask=ask)
            return                                                         # may pass: retry next cycle
        tp = round(limit + c.d * a.tp_r * risk, digits)
        account = self.broker.get_account_info()
        volume = self.sizer.calculate_size(account.balance, limit, sl, constraints)
        sizing = self.sizer.last_sizing
        if volume <= 0 or sizing is None:
            self.event("skip", sid=c.sid, reason="no_size")
            return
        if sizing.risk_multiple > a.max_lot_risk_mult:
            self.event("skip", sid=c.sid, reason="min_lot_overshoots_risk", risk_multiple=round(sizing.risk_multiple, 2),
                       wanted=round(sizing.wanted_volume, 4), volume=volume)
            self.state["setups"][c.sid] = self._skipped(c, "min_lot_overshoots_risk")
            return
        committed = self.committed_risk(tickers, constraints_of)
        cap = account.balance * a.max_total_risk_pct
        if committed + sizing.actual_risk > cap:
            self.event("skip", sid=c.sid, reason="risk_cap", committed=round(committed, 2), this=round(sizing.actual_risk, 2),
                       cap=round(cap, 2))
            self.state["setups"][c.sid] = self._skipped(c, "risk_cap")
            return
        req = OrderRequest(symbol=c.ticker, order_type=OrderType.BUY_LIMIT if c.d == 1 else OrderType.SELL_LIMIT,
                           volume=volume, price=limit, stop_loss=sl, take_profit=tp, comment=tag_of(c.sid),
                           expires_at=c.expire_utc)
        try:
            result = self.broker.place_order(req)
        except (RuntimeError, ValueError) as exc:
            self.event("place_error", sid=c.sid, error=f"{type(exc).__name__}: {exc}")
            return
        rec = {"tag": tag_of(c.sid), "name": c.name, "ticker": c.ticker, "strat": c.strat, "win": c.win, "d": c.d,
               "limit": limit, "sl": sl, "tp": tp, "volume": volume, "ticket": result.order_id,
               "placed_utc": now.isoformat(), "expire_utc": c.expire_utc.isoformat(), "hold_utc": c.hold_utc.isoformat(),
               "status": "pending" if result.success else "rejected", "reason": "" if result.success else result.comment,
               "mods": mods, "risk_money": round(sizing.actual_risk, 2)}
        self.state["setups"][c.sid] = rec
        self.event("placed" if result.success else "rejected", sid=c.sid, ticket=result.order_id, symbol=c.ticker,
                   strat=c.strat, win=c.win, side="BUY" if c.d == 1 else "SELL", limit=limit, sl=sl, tp=tp, volume=volume,
                   risk_money=round(sizing.actual_risk, 2), risk_multiple=round(sizing.risk_multiple, 2),
                   age_min=round(age_min, 1), retcode=result.retcode, comment=result.comment)

    @staticmethod
    def _skipped(c: Candidate, reason: str) -> dict:
        return {"tag": tag_of(c.sid), "name": c.name, "ticker": c.ticker, "strat": c.strat, "win": c.win, "d": c.d,
                "placed_utc": datetime.now(UTC).isoformat(), "status": "skipped", "reason": reason}

    # ------------------------------------------------------------------ positions
    def manage_positions(self, now: datetime) -> None:
        """Follow our fills, close at the flat time, notice exits the broker made."""
        positions = {p.comment: p for p in self.broker.get_open_positions() if p.comment.startswith(PREFIX)}
        resting = {o.comment for tk in {r["ticker"] for r in self.state["setups"].values() if r.get("ticker")}
                   for o in self.broker.get_pending_orders(tk) if o.comment.startswith(PREFIX)}
        known_tags = set()
        for sid, rec in self.state["setups"].items():
            tag = rec.get("tag")
            known_tags.add(tag)
            pos = positions.get(tag)
            if pos is None and rec["status"] == "pending" and tag not in resting and not isinstance(self.broker, DryBroker):
                # neither an order nor a position: the broker expired it, someone cancelled it, or the placement never stuck
                expired = now >= datetime.fromisoformat(rec["expire_utc"])
                rec["status"] = "expired" if expired else "vanished"
                self.event(rec["status"], sid=sid, symbol=rec.get("ticker"), limit=rec.get("limit"))
            if pos is not None and rec["status"] in ("pending", "cancelled"):
                rec["status"] = "filled"
                rec["fill_price"] = pos.open_price
                rec["position_id"] = pos.id
                self.event("filled", sid=sid, symbol=pos.symbol, price=pos.open_price, limit=rec.get("limit"),
                           slippage=round((pos.open_price - rec["limit"]) * rec["d"], 6) if rec.get("limit") else None,
                           volume=pos.volume)
            if pos is not None and rec["status"] == "filled":
                if now >= datetime.fromisoformat(rec["hold_utc"]):
                    res = self.broker.close_position(pos.id)
                    self.event("flat_close", sid=sid, position_id=pos.id, ok=res.success, comment=res.comment)
                    if res.success:
                        rec["status"] = "closed"
                        rec["exit"] = "flat"
            elif pos is None and rec["status"] == "filled":
                rec["status"] = "closed"
                money = self.market.position_result(rec.get("position_id", ""))
                rec["exit"] = "broker"
                rec["result_money"] = money
                self.event("closed", sid=sid, position_id=rec.get("position_id"), result_money=money,
                           result_r=round(money / rec["risk_money"], 2) if money is not None and rec.get("risk_money") else None)
        # a position of ours the state does not know (state lost): flatten it at the end of its New York day
        for tag, pos in positions.items():
            if tag in known_tags:
                continue
            opened = (pos.timestamp if pos.timestamp.tzinfo is not None else pos.timestamp.replace(tzinfo=UTC)).astimezone(NY)
            ny_now = now.astimezone(NY)
            if opened.date() < ny_now.date() or ny_now.hour >= 16:
                res = self.broker.close_position(pos.id)
                self.event("flat_close_unknown", position_id=pos.id, symbol=pos.symbol, ok=res.success)

    # ------------------------------------------------------------------ entry
    def cycle(self, frames: dict, symbols: list[str], models: set[str], now: datetime) -> int:
        cands = collect(frames, symbols, models, self.args.allow_ny_pm, int(now.timestamp()))
        self.reconcile(cands, now)
        self.manage_positions(now)
        self.save()
        return len(cands)


# ---------------------------------------------------------------------- wiring

class DemoRequiredError(RuntimeError):
    """The bot is not verifiably pointed at a demo account (or at a hedging one): refuse to start."""


class NotDemoAccountError(DemoRequiredError):
    """The terminal's account says it is not a demo account. The runners stop everything on this one."""


def guard_demo(account_info, settings_account_type: str) -> None:
    """Both rails of the other Demo runners, plus hedging: the bot may hold several models on one symbol."""
    if settings_account_type.strip().lower() != "demo":
        raise DemoRequiredError(f"MT5_ACCOUNT_TYPE must be 'demo' in .env to place orders (got {settings_account_type!r}).")
    if account_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise NotDemoAccountError(f"the connected account reports trade_mode {account_info.trade_mode!r}, not DEMO.")
    info = mt5.account_info()
    if info is not None and info.margin_mode != mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING:
        raise DemoRequiredError(f"the account is not a HEDGING account (margin_mode={info.margin_mode}); concurrent models would merge.")


def fetch_frames(symbols: list[str]) -> dict:
    names = sorted(set(symbols) | {L.SYMBOLS[s][1] for s in symbols})
    frames = {}
    for nm in names:
        fr = L.fetch(mt5, nm)
        if fr is None:
            print(f"no data for {nm}", file=sys.stderr)
            continue
        frames[nm] = fr
    return frames


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ICT A+ DEMO order bot (see module docstring)")
    ap.add_argument("--feed", choices=sorted(L.FEEDS), default="cfi")
    ap.add_argument("--symbols", nargs="*", default=["NDX100", "GBPUSD"], help="ict_lab names (NDX100 = the broker's Nasdaq)")
    ap.add_argument("--models", nargs="*", default=sorted(CFGS), help="restrict to these models (default: all eleven)")
    ap.add_argument("--tp-r", type=float, default=2.0)
    ap.add_argument("--risk-per-trade-pct", type=float, default=0.0025)
    ap.add_argument("--max-total-risk-pct", type=float, default=0.02, help="open + resting risk across the bot, of balance")
    ap.add_argument("--max-lot-risk-mult", type=float, default=1.5, help="skip when the minimum lot would risk more than this x intended")
    ap.add_argument("--max-age-min", type=float, default=12.0, help="a setup older than this when first seen is not placed")
    ap.add_argument("--max-spread-mult", type=float, default=4.0, help="do not place while the spread is above this x the backtest floor")
    ap.add_argument("--allow-ny-pm", action="store_true", help="also trade ny_pm setups (the paper book carries them overnight)")
    ap.add_argument("--place-orders", action="store_true", help="send real orders to the DEMO account (default: dry run)")
    ap.add_argument("--loop", action="store_true", help="stay resident, act on every new closed minute")
    ap.add_argument("--asof", help='replay (dry run only): pretend it is this New York time, "YYYY-MM-DD HH:MM"')
    return ap


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    L.FEED = args.feed
    if args.asof:
        if args.place_orders:
            print("--asof is a replay and cannot place orders")
            return 2
        L.ASOF = int(datetime.strptime(args.asof, "%Y-%m-%d %H:%M").replace(tzinfo=NY).timestamp())
    path = L.FEEDS[args.feed]["path"]
    state_path = STATE_DIR / f"ict_exec_state_{args.feed}{'' if args.place_orders else '_dry'}.json"

    from execution.mt5_broker import MT5Broker
    from mt5.connector import MT5Connector, WrongBrokerError, WrongTerminalError, ensure_logged_into, initialize_terminal, resolve_ticker
    from risk.kill_switch import is_trading_halted

    if args.place_orders:
        from config.settings import Settings
        from risk.daily_risk_tracker import DailyRiskTracker
        from risk.kill_switch import activate_kill_switch
        if is_trading_halted():
            print("TRADING HALTED (kill-switch active)")
            return 0
        broker = MT5Broker(connector=MT5Connector())
        if not broker.connect():
            logger.error("Could not connect to MT5.")
            return 1
        try:
            profile, _ = resolve_ticker(args.symbols[0])
            ensure_logged_into(profile)
            for name in args.symbols:
                if resolve_ticker(name)[1] != L.ticker(name):
                    raise DemoRequiredError(f"ticker mismatch for {name}: .env broker says {resolve_ticker(name)[1]!r}, ict_lab says {L.ticker(name)!r}")
            account = broker.get_account_info()
            guard_demo(account, Settings.load().MT5_ACCOUNT_TYPE)
            DailyRiskTracker().check_and_update(account.equity, account.login)
        except (DemoRequiredError, WrongBrokerError, WrongTerminalError) as exc:
            logger.critical("REFUSING TO TRADE: %s", exc)
            if isinstance(exc, NotDemoAccountError):      # a live account behind a demo-only bot: stop everything
                activate_kill_switch(f"ict_lab.execute: {exc}")
            print(f"REFUSING TO TRADE: {exc}")
            return 1
        report_broker: IBroker = broker
    else:
        if not mt5.initialize(path=path):
            print("MT5 initialize failed", mt5.last_error(), file=sys.stderr)
            return 2
        report_broker = DryBroker(MT5Broker(connector=MT5Connector()), Executor._default_report)

    ex = Executor(report_broker, Market(), args, state_path)
    models = set(args.models)
    try:
        if not args.loop:
            return _once(ex, args, models)
        last = None
        while True:
            try:
                cur = tuple(_last_bar(L.ticker(n)) for n in args.symbols)
                if cur != last:
                    if _once(ex, args, models) is not None and all(cur):
                        last = cur
                time.sleep(3)
            except Exception as exc:  # a bad cycle must not kill the watcher
                print(f"{datetime.now():%H:%M:%S} cycle failed: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr, flush=True)
                time.sleep(10)
    finally:
        mt5.shutdown()


def _last_bar(ticker: str) -> int:
    mt5.symbol_select(ticker, True)
    r = mt5.copy_rates_from_pos(ticker, mt5.TIMEFRAME_M1, 1, 1)
    return int(r[0]["time"]) if r is not None and len(r) else 0


def _once(ex: Executor, args, models: set[str]) -> int:
    from mt5 import clock
    verdict = clock.measure(L.ticker(args.symbols[0]))
    if verdict.wrong:
        ex.event("blocked_clock_drift", detail=verdict.detail)
        return 0
    frames = fetch_frames(args.symbols)
    now = datetime.fromtimestamp(L.ASOF, UTC) if L.ASOF is not None else datetime.now(UTC)
    if L.ASOF is not None:
        for nm in args.symbols:
            if nm in frames:
                f = frames[nm]
                ex.market.override[L.ticker(nm)] = (float(f.c[-1]), float(f.c[-1] + max(f.sp[-1], L.floor_of(nm))))
    n = ex.cycle(frames, args.symbols, models, now)
    print(f"{datetime.now():%H:%M:%S} active A+ candidates: {n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
