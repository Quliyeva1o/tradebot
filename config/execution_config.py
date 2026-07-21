"""Execution-mode configuration.

Selects which IBroker implementation a (future) live trading loop wires up.
Only "paper" (execution.paper_broker.PaperBroker) has an implementation as
of Sprint 2 -- "live" (execution.mt5_broker.MT5Broker, from Sprint 1) is
accepted as a valid value so the env var can be pre-set ahead of time, but
nothing constructs a broker from this config yet: consistent with Sprint 2's
scope, ExecutionConfig is not consumed anywhere in a production code path
(see execution/paper_broker.py's module docstring).
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

VALID_EXECUTION_MODES = frozenset({"paper", "live"})


@dataclass(frozen=True)
class ExecutionConfig:
    """Read-only execution-mode configuration loaded from EXECUTION_MODE."""

    execution_mode: str = os.getenv("EXECUTION_MODE", "paper")

    def __post_init__(self) -> None:
        """Validates execution_mode against the known, closed set of values.

        Mirrors config/settings.py Settings.__post_init__'s validation of
        DUPLICATE_POLICY/MISSING_VALUE_POLICY (a frozenset-membership check
        raising ValueError) rather than core/validation.py's
        require_positive/require_non_negative -- those only apply to numeric
        fields, and execution_mode is a closed-set string, exactly like
        Settings' own policy fields.

        Raises:
            ValueError: If execution_mode is not one of VALID_EXECUTION_MODES.
        """
        if self.execution_mode not in VALID_EXECUTION_MODES:
            raise ValueError(
                f"execution_mode={self.execution_mode!r} is invalid; "
                f"must be one of {sorted(VALID_EXECUTION_MODES)}."
            )

    @classmethod
    def load(cls) -> "ExecutionConfig":
        """Loads and returns an instance of ExecutionConfig."""
        return cls()
