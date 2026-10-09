import sys
from pathlib import Path

sys.path.insert(0, r"C:\tradebot")
SCR = Path(sys.argv[1])
broker = sys.argv[2]
import scripts.live_replay_backtest as m

m.REPORT_PATH = SCR / f"sweep_report_{broker}.md"        # never overwrite the tracked report
sys.argv = ["x", "--configs", "OrbSweep_GER40_Paper,OrbSweep_XAUUSD_Paper", "--broker", broker, "--out", str(SCR / f"sweep_{broker}"),
            "--no-ablation", "--no-old", "--validation-note", str(SCR / f"sweep_val_{broker}.md")]
m.main()
