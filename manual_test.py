from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from application.adapters.dataframe_adapter import DataFrameSwingDetectorAdapter as SwingDetector
from market_structure.swing_models import SwingConfig


def create_sample_data(periods: int = 50) -> pd.DataFrame:
    """Generates a random walk dataset resembling OHLCV data."""
    np.random.seed(42)
    start_time = datetime(2023, 1, 1)

    # Generate random price changes
    returns = np.random.normal(0, 0.002, periods)
    prices = 1.1000 * np.exp(np.cumsum(returns))

    data = []
    for i in range(periods):
        base_price = prices[i]
        high = base_price + abs(np.random.normal(0, 0.001))
        low = base_price - abs(np.random.normal(0, 0.001))
        open_price = base_price + np.random.normal(0, 0.0005)
        close_price = base_price + np.random.normal(0, 0.0005)

        # Ensure High is highest and Low is lowest
        high = max(high, open_price, close_price)
        low = min(low, open_price, close_price)

        data.append(
            {
                "time": start_time + timedelta(hours=i),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": float(np.random.randint(100, 1000)),
            }
        )

    df = pd.DataFrame(data)
    # The detector expects 'time' column.
    return df


def run_manual_test() -> None:
    """Runs a manual validation test on simulated market data."""
    print("Generating sample OHLCV data (50 bars)...")
    df = create_sample_data(50)
    print(df.head())
    print("-" * 60)

    print("Configuring Swing Detector (left_bars=2, right_bars=2)...")
    config = SwingConfig(
        left_bars=2, right_bars=2, classification_enabled=True, filter_enabled=True
    )
    detector = SwingDetector(config=config)

    print("Detecting swings...")
    detector.detect(df)
    swings = detector.get_swings()

    print(f"\nTotal Swings Detected: {len(swings)}")
    print("-" * 60)
    for swing in swings:
        classification = swing.classification.name if swing.classification else "UNKNOWN"
        print(
            f"Index: {swing.index:02d} | Time: {swing.timestamp} | Type: {swing.type.name:<4} | Price: {swing.price:.5f} | Class: {classification}"
        )


if __name__ == "__main__":
    run_manual_test()
