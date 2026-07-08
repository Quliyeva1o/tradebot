import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from core.models import Timeframe
from data.csv_provider import CSVDataProvider
from application.services.market_state_builder import MarketStateBuilder

def main():
    csv_path = Path("data/history/EURUSD_H1_2020.csv")
    bars = CSVDataProvider(filepath=csv_path).load()
    print(f"Loaded {len(bars)} bars")

    builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.H1)
    builder.initialize(history=bars)

    print("\n=== StateBuilder vəziyyəti ===")
    print("Structure Engine:", builder.structure_engine)
    print("SMC Pipeline:", builder.smc_pipeline)
    print("Swing Detector:", builder.swing_detector)

    # İlk 100 bar-da state yoxla
    for i in range(min(100, len(bars))):
        builder.append_bar(bars[i])
        state = builder.market_state
        
        if i % 20 == 0:
            print(f"Bar {i:3d}: trend={getattr(state, 'trend', None)}, "
                  f"structure={getattr(state, 'structure', None)}, "
                  f"swings={len(getattr(state, 'swings', [])) if hasattr(state, 'swings') else 0}")

    print("\nSon state:")
    state = builder.market_state
    print(f"Trend: {getattr(state, 'trend', None)}")
    print(f"Structure: {getattr(state, 'structure', None)}")
    print(f"Swings count: {len(getattr(state, 'swings', []))}")

if __name__ == "__main__":
    main()