"""The replay's weekend cutoff must be the live runner's, minute for minute.

run_live_nasdaq_orb.weekend_flat_due decides when OrbBreakoutwf_XAUUSD_Paper goes flat; the
replay keeps its own copy of the rule so the engine does not import a runner. This pins the two
together across the weeks when Europe and New York change clocks on different dates.
"""

from datetime import UTC, datetime, timedelta

import run_live_nasdaq_orb
from backtest.live_replay.engine import weekend_flat_due

# Thursday starts, each spanning Friday's cutoff and the weekend; three of them are the weeks
# when only one side of the Atlantic has changed its clocks.
WEEKS = [datetime(2025, 10, 23, tzinfo=UTC), datetime(2025, 10, 30, tzinfo=UTC),
         datetime(2026, 3, 12, tzinfo=UTC), datetime(2026, 3, 19, tzinfo=UTC),
         datetime(2026, 3, 26, tzinfo=UTC), datetime(2026, 9, 10, tzinfo=UTC)]


def test_the_replay_and_the_runner_agree_on_every_minute() -> None:
    checked = 0
    for start in WEEKS:
        moment = start
        while moment < start + timedelta(days=5):
            # the replay reads the clock from its bars' broker, the runner from .env: same clock here
            replay = weekend_flat_due(int(moment.timestamp()), run_live_nasdaq_orb.BROKER_TZ)
            assert replay == run_live_nasdaq_orb.weekend_flat_due(moment), moment
            moment += timedelta(minutes=1)
            checked += 1
    assert checked == len(WEEKS) * 5 * 1440
