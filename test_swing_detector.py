import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from core.models import Timeframe
from data.csv_provider import CSVDataProvider
from market_structure.swing_detector import SwingDetector
from market_structure.swing_models import SwingConfig

def main():
    csv_path = Path("data/history/EURUSD_H1_2020.csv")
    bars = CSVDataProvider(filepath=csv_path).load()[:500]  # ilk 500 bar
    print(f"Testing with {len(bars)} bars")

    # Default config ilə yoxla
    config = SwingConfig(left_bars=5, right_bars=5, allow_equal_highs=True)
    detector = SwingDetector(config=config)

    swings = detector.detect_batch(bars)
    print(f"\nDetected swings: {len(swings)}")

    for s in swings[:10]:
        print(f"  {s.type} at bar {s.index} price={s.price:.5f}")

    # Fərqli config ilə yoxla (daha həssas)
    config2 = SwingConfig(left_bars=3, right_bars=3, allow_equal_highs=True, filter_enabled=False)
    detector2 = SwingDetector(config=config2)
    swings2 = detector2.detect_batch(bars)
    print(f"\nMore sensitive config swings: {len(swings2)}")

if __name__ == "__main__":
    main()