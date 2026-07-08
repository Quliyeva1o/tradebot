import sys
from pathlib import Path

root = Path(__file__).parent
sys.path.insert(0, str(root))

from data.csv_provider import CSVDataProvider
from application.services.market_state_builder import MarketStateBuilder

def main():
    provider = CSVDataProvider(filepath="data/history/EURUSD_H1_2020.csv")
    bars = provider.load()
    print(f"Loaded {len(bars)} bars\n")

    builder = MarketStateBuilder(symbol="EURUSD", timeframe="H1")
    builder.initialize(bars[:2000])  # Daha çox bar ilə test

    state = builder.market_state
    print(f"Final Swings Count: {len(state.swing_graph.nodes)}")
    print(f"Trend: {state.structure_state.trend}")
    
    # StructureState-in mövcud atributlarını yoxla
    print(f"StructureState attributes: {dir(state.structure_state)}")

    if state.swing_graph.nodes:
        print("\nFirst 5 swings:")
        for s in state.swing_graph.nodes[:5]:
            print(f"  {s.type} at index {s.index}, price={s.price}")
    else:
        print("\nNo swings!")

if __name__ == "__main__":
    main()