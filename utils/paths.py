"""Project-relative filesystem paths shared across research and data-export modules."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_artifacts_dir() -> Path:
    """Returns the directory research/backtest modules should write output artifacts to.

    Defaults to a repo-relative `artifacts/` directory so the project runs
    unmodified on any machine. Set TRADEBOT_ARTIFACTS_DIR to override
    (e.g. to isolate CI or test output).

    Returns:
        Path to the artifacts directory. Does not create it.
    """
    override = os.environ.get("TRADEBOT_ARTIFACTS_DIR")
    if override:
        return Path(override)
    return PROJECT_ROOT / "artifacts"
