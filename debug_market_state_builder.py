import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from core.models import Timeframe
from data.csv_provider import CSVDataProvider
from application.services.market_state_builder import MarketStateBuilder

def main():
    csv_path = Path("data/history/EURUSD_H1_2020.csv")
    bars = CSVDataProvider(filepath=csv_path).load()[:1000]  # ilk 1000 bar
    print(f"Testing with {len(bars)} bars")

    builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.H1)
    builder.initialize(history=bars)

    print("\n=== MarketStateBuilder DEBUG ===")
    print("Has structure_engine:", hasattr(builder, 'structure_engine'))
    print("Has smc_pipeline:", hasattr(builder, 'smc_pipeline'))

    # İlk 200 bar-ı emal et və iç vəziyyəti yoxla
    for i in range(min(200, len(bars))):
        builder.append_bar(bars[i])
        
        if i % 50 == 0 or i == 199:
            state = builder.market_state
            swings = getattr(state, 'swings', []) if state else []
            print(f"After bar {i:3d}: swings={len(swings)}, trend={getattr(state, 'trend', None)}")

    print("\nFinal state summary:")
    state = builder.market_state
    print(f"Total swings in state: {len(getattr(state, 'swings', []))}")
    print(f"Trend: {getattr(state, 'trend', None)}")
    print(f"Structure: {getattr(state, 'structure', None)}")

if __name__ == "__main__":
    main()