"""Shared validation helpers for strategy configuration classes.

Small, dependency-free `__post_init__` checks for frozen config dataclasses,
so an invalid parameter (e.g. a negative risk_reward) fails fast at strategy
construction instead of surfacing many bars into a backtest as a silent
RejectionReason.RR_GATE_FAILED / NON_POSITIVE_RISK rejection.
"""


def require_positive(value: float, name: str) -> None:
    """Raises ValueError if value is not strictly positive.

    Args:
        value: The numeric value to check.
        name: The field name, used in the error message.

    Raises:
        ValueError: If value <= 0.
    """
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}")


def require_non_negative(value: float, name: str) -> None:
    """Raises ValueError if value is negative.

    Args:
        value: The numeric value to check.
        name: The field name, used in the error message.

    Raises:
        ValueError: If value < 0.
    """
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
