"""MT5 Historical Data Downloader."""

from datetime import datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5  # noqa: N813
import pandas as pd

from mt5.connector import MT5Connector
from utils.logging import setup_logger

logger = setup_logger("history_downloader")

TIMEFRAME_MAPPING = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

TIMEFRAME_DELTA = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}


class MT5HistoryDownloader:
    """Download historical OHLCV data from MT5 terminal and export to CSV."""

    def __init__(self, connector: MT5Connector | None = None) -> None:
        """Initializes the downloader.

        Args:
            connector: Active MT5 connector instance. If None, a new one is created.
        """
        self.connector = connector or MT5Connector()

    def download(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
    ) -> str | None:
        """Downloads rates and exports to data/history/ directory.

        Args:
            symbol: Trading instrument symbol (e.g. "EURUSD").
            timeframe: Timeframe interval (e.g. "H1", "M15").
            start_date: Start date for download window.
            end_date: End date for download window.

        Returns:
            The file path of the exported CSV file, or None if download failed.
        """
        # Map timeframe to MT5 constant
        tf_key = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        if tf_key not in TIMEFRAME_MAPPING:
            logger.error("Unsupported timeframe: %s", tf_key)
            return None
        mt5_tf = TIMEFRAME_MAPPING[tf_key]

        # Ensure connector is connected
        if not self.connector.connect():
            logger.error("Failed to establish MT5 connection.")
            return None

        try:
            # Verify symbol exists and is selected
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                # Attempt to select the symbol
                if not mt5.symbol_select(symbol, True):
                    logger.error("Symbol %s is not available in the MT5 terminal.", symbol)
                    return None
                symbol_info = mt5.symbol_info(symbol)

            if symbol_info is None:
                logger.error("Symbol info could not be retrieved for %s.", symbol)
                return None

            logger.info("Fetching rates for %s [%s] from %s to %s...", symbol, tf_key, start_date, end_date)

            # Copy rates
            rates = mt5.copy_rates_range(symbol, mt5_tf, start_date, end_date)
            if rates is None or len(rates) == 0:
                logger.error("No historical rates returned from MT5 terminal for %s.", symbol)
                return None

            logger.info("Retrieved %d rates from MT5 terminal.", len(rates))

            # Export directory
            export_dir = Path("c:/Users/Microsol/Desktop/trade/data/history")
            export_dir.mkdir(parents=True, exist_ok=True)

            # Build filename format e.g. EURUSD_H1_2026.csv
            file_name = f"{symbol}_{tf_key}_{start_date.year}.csv"
            file_path = export_dir / file_name

            # Convert to DataFrame to process headers cleanly
            df = pd.DataFrame(rates)
            # rename 'time' to 'timestamp'
            df.rename(columns={"time": "timestamp"}, inplace=True)
            # Convert timestamp to human readable datetime in local/UTC format
            # MT5 timestamps are Unix epoch in seconds
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

            # Output specific columns: timestamp, open, high, low, close, tick_volume, spread, real_volume
            required_cols = ["timestamp", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
            # Check if columns are missing
            for col in required_cols:
                if col not in df.columns:
                    df[col] = 0.0

            df = df[required_cols]

            # Save to CSV
            df.to_csv(file_path, index=False)
            logger.info("Successfully exported history to %s", file_path)

            # Verify integrity
            self._verify_integrity(df, tf_key)

            return str(file_path)

        finally:
            self.connector.disconnect()

    def _verify_integrity(self, df: pd.DataFrame, tf_key: str) -> None:
        """Verifies integrity of the downloaded data and prints metrics."""
        if len(df) == 0:
            logger.warning("Empty data export.")
            return

        first_ts = df["timestamp"].iloc[0]
        last_ts = df["timestamp"].iloc[-1]
        num_candles = len(df)

        print("\n=== Export Integrity Report ===")
        print(f"First Timestamp: {first_ts}")
        print(f"Last Timestamp:  {last_ts}")
        print(f"Total Candles:   {num_candles}")

        # Gap detection
        if num_candles > 1 and tf_key in TIMEFRAME_DELTA:
            delta = TIMEFRAME_DELTA[tf_key]
            gaps = []
            for i in range(1, len(df)):
                diff = df["timestamp"].iloc[i] - df["timestamp"].iloc[i - 1]
                # If difference is larger than timeframe interval (allowing for weekends)
                # Weekends: Friday 22:00 to Sunday 22:00 is ~48 hours. Let's flag gaps larger than 3 intervals or > 48 hours
                if diff > delta * 3 and diff > timedelta(hours=50):
                    # Standard weekend, skip reporting as gap
                    continue
                elif diff > delta:
                    gaps.append((df["timestamp"].iloc[i - 1], df["timestamp"].iloc[i], diff))

            if gaps:
                print(f"Detected {len(gaps)} potential missing bar gaps (excluding standard weekends):")
                for start, end, gap_duration in gaps[:5]:
                    print(f"  Gap between {start} and {end} (Duration: {gap_duration})")
                if len(gaps) > 5:
                    print(f"  ... and {len(gaps) - 5} more gaps.")
            else:
                print("No unexpected gaps detected (data sequence is continuous).")
        print("===============================\n")
