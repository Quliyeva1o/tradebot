# SMC Modular Forex Trading Framework

An institutional-grade, modular quantitative trading framework designed in Python 3.12+. The system architecture decouples analytical concepts (like Smart Money Concepts, Technical Indicators, and Market Structure Analysis) from specific execution brokers (e.g. MetaTrader 5) or historical backtesters.

## Project Structure

*   `config/`: Framework system configuration settings.
*   `core/`: Core models, structures, and interfaces (protocols).
*   `data/`: Directory for raw, cleaned, or cached market data files.
*   `docs/`: Developer documentation and architectural blueprints.
*   `examples/`: Sample scripts and integration examples.
*   `logs/`: Application logging outputs.
*   `tests/`: Unit and integration test suites.
*   `utils/`: Core utilities (logging setup, formatters, etc.).
*   `mt5/`: MetaTrader 5 execution wrappers and connector utilities.
*   `market_structure/`: Structural algorithms (Swing highs/lows, trend, BOS, CHoCH).
*   `smc/`: Smart Money Concepts (FVG, Order Blocks, Liquidity, Breakers).
*   `indicators/`: Vectorized indicators (SMA, EMA, RSI, ATR, MACD).
*   `strategy/`: Strategy base definitions and unified driving engines.
*   `risk/`: Risk management systems (Position sizing, Risk/Reward calculations).
*   `backtest/`: Chronological backtest simulators.
*   `dashboard/`: Analytical user interface.
*   `notifications/`: Integration with alert mechanisms (Telegram).

## Development Setup

1.  **Clone the workspace** and verify Python version:
    ```bash
    python --version  # Requires Python 3.12+
    ```

2.  **Create and activate a virtual environment**:
    ```bash
    python -m venv .venv
    # Windows:
    .venv\Scripts\activate
    # macOS/Linux:
    source .venv/bin/activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Copy the environment file** and configure settings:
    ```bash
    cp .env.example .env
    ```

## Tooling and Validation

We use Ruff, Black, Mypy, and Pytest to enforce code quality. Run validation using:

*   **Format Verification**:
    ```bash
    black --check .
    ```

*   **Linting**:
    ```bash
    ruff check .
    ```

*   **Type Verification**:
    ```bash
    mypy .
    ```

*   **Unit Tests**:
    ```bash
    pytest
    ```
# tradebot
