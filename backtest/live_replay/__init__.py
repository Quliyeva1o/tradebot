"""Replays a deployed bot the way the VPS runs it, rather than the way a batch backtest does.

The batch scripts in scripts/ implement the strategies a second time and agree with the live
classes on only 58-76% of trading days. This package drives the live classes themselves
through the runners' poll clock and a broker that fills at ask/bid, holds the stop, gaps and
charges swap. See docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md.
"""
