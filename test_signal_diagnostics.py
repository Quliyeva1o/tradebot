import sys
from pathlib import Path

root = Path(__file__).parent
sys.path.insert(0, str(root))

from data.csv_provider import CSVDataProvider
from application.services.market_state_builder import MarketStateBuilder
from strategy.continuation import BullishContinuationStrategy, BearishContinuationStrategy

def main():
    provider = CSVDataProvider(filepath="data/history/EURUSD_H1_2020.csv")
    bars = provider.load()[:3000]  # Son 3000 bar
    print(f"Loaded {len(bars)} bars\n")

    builder = MarketStateBuilder(symbol="EURUSD", timeframe="H1")
    builder.initialize(bars)

    state = builder.market_state
    print(f"Swings: {len(state.swing_graph.nodes)}")
    print(f"Trend: {state.structure_state.trend}\n")

    bullish = BullishContinuationStrategy()
    bearish = BearishContinuationStrategy()

    signals = []
    for bar in bars[-500:]:  # Son 500 bar
        setup = bullish.evaluate(state)
        if setup:
            signals.append(setup)

        setup = bearish.evaluate(state)
        if setup:
            signals.append(setup)

    print(f"Total Setups Generated: {len(signals)}")
    for s in signals[:5]:
        print(f"  {s.direction} setup at {s.setup_id}")

if __name__ == "__main__":
    main()